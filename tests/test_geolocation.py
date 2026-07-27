"""PS/DS 지오로케이션 쉬프트 보정 — 높이 오차가 만드는 수평 밀림.

물리가 틀리면 오히려 두 배 밀리므로, 왕복(보정 후 다시 밀면 원위치)·크기·방향을 고정한다.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from inframon.insar.geolocation import (
    apply_correction,
    diagnose,
    height_shift,
    los_ground_unit,
)


def test_shift_magnitude_is_dh_over_tan_theta():
    """밀림 크기 = δh / tan(θ). 39° 에서 δh=10m → 12.35m."""
    hs = height_shift(np.array([10.0]), 39.0, -13.0)
    assert hs["magnitude_m"][0] == pytest.approx(10.0 / math.tan(math.radians(39.0)), abs=1e-6)


def test_shift_scales_with_incidence():
    """입사각이 작을수록(더 비스듬) 같은 δh 가 더 크게 밀린다."""
    m29 = height_shift(np.array([10.0]), 29.0, 0.0)["magnitude_m"][0]
    m46 = height_shift(np.array([10.0]), 46.0, 0.0)["magnitude_m"][0]
    assert m29 > m46


def test_los_ground_unit_is_normalized():
    e, n = los_ground_unit(-13.0)
    assert math.hypot(e, n) == pytest.approx(1.0, abs=1e-9)


def test_look_side_flips_direction():
    r = los_ground_unit(-13.0, "right")
    left = los_ground_unit(-13.0, "left")
    assert left[0] == pytest.approx(-r[0]) and left[1] == pytest.approx(-r[1])


def test_correction_is_exact_roundtrip_lonlat():
    """참위치에 밀림을 넣었다가 보정하면 정확히 원위치로 돌아온다(경위도)."""
    rng = np.random.default_rng(0)
    n = 60
    true = np.column_stack([np.full(n, 127.109) + rng.normal(0, 1e-4, n),
                            np.full(n, 37.3685) + rng.normal(0, 1e-4, n),
                            np.zeros(n)])
    dh = rng.uniform(5, 15, n)
    inc, heading = 39.0, -13.0
    # 밀림을 인위로 넣어 geocoded 를 만든다(보정의 역)
    hs = height_shift(dh, inc, heading)
    m_lon = 111_320 * math.cos(math.radians(37.3685))
    m_lat = 111_320
    geoc = true.copy()
    geoc[:, 0] += hs["shift_en_m"][:, 0] / m_lon
    geoc[:, 1] += hs["shift_en_m"][:, 1] / m_lat
    out = apply_correction(geoc, dh, inc, heading, crs_is_lonlat=True, set_height=False)
    err = np.hypot((out["xyz"][:, 0] - true[:, 0]) * m_lon,
                   (out["xyz"][:, 1] - true[:, 1]) * m_lat)
    # 경위도↔미터는 위도별 스케일 근사(median lat)라 µm 수준 잔차가 남는다 — 물리는 정확.
    # (투영 미터 좌표에서는 정확히 0 — test_correction_projected_meters 참조.)
    assert err.max() < 1e-3                       # 원위치 복원(1mm 이내)


def test_correction_projected_meters():
    """투영 미터 좌표에서도 밀림을 그대로 뺀다."""
    n = 10
    dh = np.full(n, 8.0)
    true = np.column_stack([np.full(n, 200000.0), np.full(n, 550000.0), np.zeros(n)])
    hs = height_shift(dh, 39.0, -13.0)
    geoc = true.copy()
    geoc[:, :2] += hs["shift_en_m"]
    out = apply_correction(geoc, dh, 39.0, -13.0, crs_is_lonlat=False, set_height=False)
    assert np.allclose(out["xyz"][:, :2], true[:, :2], atol=1e-9)


def test_height_is_set_to_dem_plus_error():
    n = 5
    dh = np.array([5.0, 10.0, 0.0, -2.0, 12.0])
    base = np.full(n, 30.0)
    xyz = np.column_stack([np.full(n, 127.1), np.full(n, 37.4), np.zeros(n)])
    out = apply_correction(xyz, dh, 39.0, -13.0, crs_is_lonlat=True,
                           set_height=True, base_height=base)
    assert np.allclose(out["xyz"][:, 2], base + dh)


def test_diagnose_flags_large_shift():
    """상부구조(큰 δh)면 데크 폭 대비 밀림이 커 보정 권장."""
    dh = np.abs(np.random.default_rng(0).normal(0, 8, 200)) + 5
    d = diagnose(dh, 39.0, -13.0, deck_width_m=20.0)
    assert d["needs_correction"] and d["fraction_beyond_half_deck"] > 0.1
    assert "보정 권장" in d["verdict"]


def test_diagnose_small_shift_not_flagged():
    dh = np.random.default_rng(1).normal(0, 0.5, 200)
    d = diagnose(dh, 39.0, -13.0, deck_width_m=20.0)
    assert not d["needs_correction"]


def test_stats_reported():
    dh = np.array([5.0, 10.0, 15.0])
    out = apply_correction(np.column_stack([np.full(3, 127.1), np.full(3, 37.4), np.zeros(3)]),
                           dh, 39.0, -13.0, crs_is_lonlat=True)
    m = out["meta"]
    assert m["shift_max_abs_m"] >= m["shift_mean_abs_m"]
    assert m["crs"] == "lonlat" and "δh" in m["note"]


# ── 인제스트 통합 ────────────────────────────────────────────────────────
def _track_with_geometry(path, *, with_demerr, n=40, seed=0):
    """dem_error·입사각·heading 을 갖춘(또는 일부 뺀) Track H5."""
    import h5py
    rng = np.random.default_rng(seed)
    lon = np.full(n, 127.109) + rng.normal(0, 2e-4, n)
    lat = np.full(n, 37.3685) + rng.normal(0, 2e-4, n)
    with h5py.File(path, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack([lon, lat]))
        f.create_dataset("epochs", data=np.array([20200101, 20200113, 20200125], dtype=np.int32))
        f.create_dataset("los_mm", data=rng.normal(0, 2, (n, 3)).astype(np.float32))
        f.create_dataset("coh", data=rng.uniform(0.6, 0.95, n).astype(np.float32))
        f.create_dataset("incidenceAngle", data=np.full(n, 39.0, np.float32))
        f.attrs["HEADING"] = -13.0
        if with_demerr:
            f.create_dataset("dem_error", data=rng.uniform(5, 15, n).astype(np.float32))
    return path


def test_read_track_picks_up_dem_error(tmp_path):
    from inframon.insar.track_reader import read_track_h5
    td = read_track_h5(_track_with_geometry(tmp_path / "t.h5", with_demerr=True))
    assert td.dem_error is not None and td.dem_error.shape[0] == 40
    assert td.incidence is not None and td.heading == pytest.approx(-13.0)


def test_import_applies_geoloc_when_data_present(tmp_path):
    from inframon.contracts.io import ProjectStore
    from inframon.contracts.schema import InSAROutput
    from inframon.insar.track_reader import import_track_h5

    src = _track_with_geometry(tmp_path / "t.h5", with_demerr=True)
    out = tmp_path / "p.h5"
    with ProjectStore(out, mode="a") as s:
        import_track_h5(s, src, geoloc_correct=True)
        geo = s.read_json_attr("insar", "track_source")["geolocation"]
        ins = s.read_meta("insar", InSAROutput)
        xyz = s.read_array(ins.xyz_ds)
    assert geo["applied"] is True and geo["shift_mean_abs_m"] > 0
    # 보정으로 z 가 δh(5~15m)로 채워졌다
    assert xyz[:, 2].max() > 4.0


def test_import_skips_geoloc_without_dem_error(tmp_path):
    """dem_error 가 없으면 조용히 넘어가지 않고 사유를 남긴다."""
    from inframon.contracts.io import ProjectStore
    from inframon.insar.track_reader import import_track_h5

    src = _track_with_geometry(tmp_path / "t.h5", with_demerr=False)
    out = tmp_path / "p.h5"
    with ProjectStore(out, mode="a") as s:
        import_track_h5(s, src, geoloc_correct=True)
        geo = s.read_json_attr("insar", "track_source")["geolocation"]
    assert geo["applied"] is False and "dem_error" in geo["reason"]


def test_import_geoloc_off_by_default(tmp_path):
    """플래그를 안 주면 geolocation 메타가 None(기존 동작 불변)."""
    from inframon.contracts.io import ProjectStore
    from inframon.insar.track_reader import import_track_h5

    src = _track_with_geometry(tmp_path / "t.h5", with_demerr=True)
    out = tmp_path / "p.h5"
    with ProjectStore(out, mode="a") as s:
        import_track_h5(s, src)
        assert s.read_json_attr("insar", "track_source")["geolocation"] is None


def test_bad_incidence_does_not_produce_nan():
    """비물리 입사각(0·NaN·>90)은 쉬프트 0 으로 안전 처리 — 좌표에 NaN 을 심지 않는다.

    회귀: 100 케이스 벤치에서 입사각 0° 가 δh/tan0=inf → NaN 좌표를 만들던 걸 잡았다.
    """
    n = 20
    dh = np.full(n, 10.0)
    inc = np.array([39.0] * 15 + [0.0, 95.0, -5.0, np.nan, 200.0])   # 5개 비물리
    xyz = np.column_stack([np.full(n, 127.1), np.full(n, 37.5), np.zeros(n)])
    out = apply_correction(xyz, dh, inc, -13.0, crs_is_lonlat=True)
    assert np.isfinite(out["xyz"]).all()          # NaN/inf 없음
    assert out["meta"]["n_bad_incidence"] == 5
    # 정상 입사각 점은 여전히 보정됨
    shift = height_shift(dh, inc, -13.0)
    assert shift["magnitude_m"][0] > 0 and shift["magnitude_m"][-1] == 0.0
