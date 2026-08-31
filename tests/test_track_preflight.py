"""Track H5 투입 사전검증(preflight) — 실데이터 인제스트 게이트.

정상 파일은 ready, 각종 결함(누락·형상불일치·적은 점·날짜파싱·coherence·NaN)은 차단
오류 또는 경고로 잡되 **절대 예외를 내지 않는다**(리포트로 안전 처리).
"""

from __future__ import annotations

import h5py
import numpy as np

from inframon.insar.track_preflight import preflight_track_h5


def _write_track(path, *, lonlat=None, n_points=12, n_dates=6, epochs=None,
                 coh=None, los=None, height=False, crs=None):
    if lonlat is None:
        # 투영 좌표(EPSG:5179 풍) — 경위도로 오인되지 않게 큰 값
        lonlat = np.column_stack([300000 + np.arange(n_points) * 2.0,
                                  600000 + np.zeros(n_points)])
    if epochs is None:
        epochs = np.array([20200101, 20200113, 20200125, 20200206, 20200218, 20200302],
                          dtype=np.int32)[:n_dates]
    if coh is None:
        coh = np.full(n_points, 0.8, dtype=np.float32)
    if los is None:
        los = np.zeros((n_points, n_dates), dtype=np.float32)
    with h5py.File(path, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.asarray(lonlat, dtype=np.float64))
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=np.asarray(los, dtype=np.float32))
        f.create_dataset("coh", data=np.asarray(coh, dtype=np.float32))
        if height:
            f.create_dataset("height", data=np.zeros(len(lonlat), dtype=np.float32))
        if crs:
            f.attrs["crs"] = crs
    return path


def test_preflight_ready(tmp_path):
    p = _write_track(tmp_path / "good.h5", height=True, crs="EPSG:5179")
    rep = preflight_track_h5(p)
    assert rep.is_ready
    assert not rep.errors
    assert rep.n_points == 12 and rep.n_dates == 6
    assert rep.date_first == "20200101" and rep.date_last == "20200302"
    assert rep.has_height and rep.crs == "EPSG:5179"
    assert rep.to_dict()["is_ready"] is True


def test_preflight_missing_file(tmp_path):
    rep = preflight_track_h5(tmp_path / "nope.h5")
    assert not rep.is_ready
    assert any("파일이 없습니다" in e for e in rep.errors)


def test_preflight_missing_dataset(tmp_path):
    p = tmp_path / "nolos.h5"
    with h5py.File(p, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.zeros((5, 2)))
        f.create_dataset("epochs", data=np.array([20200101, 20200113], dtype=np.int32))
        f.create_dataset("coh", data=np.full(5, 0.8, dtype=np.float32))
    rep = preflight_track_h5(p)
    assert not rep.is_ready
    assert any("los_mm" in e for e in rep.errors)


def test_preflight_shape_mismatch(tmp_path):
    p = _write_track(tmp_path / "bad.h5", n_points=10, n_dates=4,
                     coh=np.full(7, 0.8, dtype=np.float32))   # coh 7 ≠ 10
    rep = preflight_track_h5(p)
    assert not rep.is_ready
    assert any("coherence 점수" in e for e in rep.errors)


def test_preflight_too_few_points(tmp_path):
    p = _write_track(tmp_path / "few.h5", n_points=1, n_dates=4,
                     lonlat=np.array([[300000.0, 600000.0]]),
                     coh=np.full(1, 0.8, dtype=np.float32),
                     los=np.zeros((1, 4), dtype=np.float32))
    rep = preflight_track_h5(p)
    assert not rep.is_ready
    assert any("측정점" in e for e in rep.errors)


def test_preflight_bad_epochs(tmp_path):
    p = _write_track(tmp_path / "baddate.h5", n_dates=3,
                     epochs=np.array([b"2020-01", b"xx", b"y"]))
    rep = preflight_track_h5(p)
    assert not rep.is_ready
    assert any("epochs" in e for e in rep.errors)


def test_preflight_coherence_out_of_range_warns(tmp_path):
    p = _write_track(tmp_path / "coh.h5", n_points=8, n_dates=4,
                     coh=np.full(8, 1.7, dtype=np.float32), height=True, crs="EPSG:5179")
    rep = preflight_track_h5(p)
    assert rep.is_ready                                  # 경고일 뿐 차단 아님
    assert any("coherence 가 [0,1]" in w for w in rep.warnings)


def test_preflight_los_nan_warns(tmp_path):
    los = np.zeros((8, 4), dtype=np.float32)
    los[0, 0] = np.nan
    p = _write_track(tmp_path / "nan.h5", n_points=8, n_dates=4, los=los,
                     height=True, crs="EPSG:5179")
    rep = preflight_track_h5(p)
    assert rep.is_ready
    assert rep.los_finite_frac < 1.0
    assert any("NaN/Inf" in w for w in rep.warnings)


def test_preflight_no_height_and_no_crs_warn(tmp_path):
    p = _write_track(tmp_path / "plain.h5")               # height/crs 없음
    rep = preflight_track_h5(p)
    assert rep.is_ready
    assert any("고도" in w for w in rep.warnings)
    assert any("CRS" in w for w in rep.warnings)


def test_preflight_detects_geographic_coords(tmp_path):
    # 경위도(작은 ptp) → looks_geographic
    lonlat = np.column_stack([127.05 + np.arange(10) * 1e-4, 36.5 + np.zeros(10)])
    p = _write_track(tmp_path / "geo.h5", n_points=10, n_dates=4, lonlat=lonlat)
    rep = preflight_track_h5(p)
    assert rep.looks_geographic


def test_preflight_corrupt_file_no_crash(tmp_path):
    p = tmp_path / "corrupt.h5"
    p.write_bytes(b"not an hdf5 file")
    rep = preflight_track_h5(p)
    assert not rep.is_ready
    assert rep.errors                                    # 예외 없이 오류로 보고


# ── 대상 교량을 실제로 담고 있는가 (target 좌표를 준 경우만) ──
def _geo_track(path, *, center=(37.32, 127.10), spread_deg=0.0002, n=40, **kw):
    """경위도 좌표 트랙 — center 주변 spread_deg 안에 n 점."""
    rng = np.linspace(-spread_deg, spread_deg, n)
    lonlat = np.column_stack([center[1] + rng, center[0] + rng * 0.5])
    return _write_track(path, lonlat=lonlat, n_points=n, **kw)


def test_target_absent_keeps_old_behavior(tmp_path):
    """좌표를 주지 않으면 공간 검사를 하지 않는다 — 기존 호출부가 그대로 동작한다."""
    p = _geo_track(tmp_path / "t.h5", height=True, crs="EPSG:4326")
    rep = preflight_track_h5(p)
    assert rep.is_ready and rep.target is None
    assert rep.n_within_deck is None


def test_target_far_from_points_blocks(tmp_path):
    """대상 반경 30m 안에 점이 0개면 '이 교량의 트랙이 아니다' 로 차단한다.

    6km 광역 PS 필드가 교량 트랙 행세를 하며 ✅ 를 받던 것을 막는 검사다.
    """
    p = _geo_track(tmp_path / "far.h5", center=(37.32, 127.10), height=True)
    rep = preflight_track_h5(p, target=(37.40, 127.20))    # ~11km 떨어진 교량
    assert not rep.is_ready
    assert any("0개" in e for e in rep.errors)
    assert rep.n_within_deck == 0 and rep.dist_min_m > 1000


def test_target_within_deck_passes(tmp_path):
    p = _geo_track(tmp_path / "near.h5", center=(37.32, 127.10), height=True)
    rep = preflight_track_h5(p, target=(37.32, 127.10))
    assert rep.is_ready and rep.n_within_deck > 0
    assert rep.dist_min_m < 30.0


def test_wide_field_warns_when_bridge_is_a_speck(tmp_path):
    """교량 30m 내가 1% 미만이면 '광역 필드' 경고 — 차단은 아니되 잘라 쓰라고 말한다."""
    n = 2000
    rng = np.random.default_rng(0)
    lonlat = np.column_stack([127.10 + rng.uniform(-0.03, 0.03, n),
                              37.32 + rng.uniform(-0.03, 0.03, n)])
    lonlat[:3] = [127.10, 37.32]                            # 교량 위 3점만
    p = _write_track(tmp_path / "wide.h5", lonlat=lonlat, n_points=n, height=True)
    rep = preflight_track_h5(p, target=(37.32, 127.10))
    assert rep.is_ready                                     # 차단은 아니다
    assert any("광역 필드" in w for w in rep.warnings)
    assert rep.extent_km is not None and rep.extent_km[0] > 4.0


# ── 위상 언래핑 — λ/4 에 갇힌 산출물은 물리적 의미가 없다 ──
def test_wrapped_los_is_blocked(tmp_path):
    """LOS 가 ±λ/4 안에서 균일하면 언래핑 안 된 산출물로 보고 차단한다."""
    from inframon.insar.track_preflight import LOS_WRAP_LIMIT_MM

    rng = np.random.default_rng(1)
    los = rng.uniform(-LOS_WRAP_LIMIT_MM, LOS_WRAP_LIMIT_MM, (40, 6))
    p = _geo_track(tmp_path / "wrapped.h5", los=los, height=True)
    rep = preflight_track_h5(p)
    assert not rep.is_ready and rep.looks_wrapped
    assert any("언래핑" in e for e in rep.errors)


def test_small_real_signal_is_not_called_wrapped(tmp_path):
    """실제 변위가 작아 |LOS|max 가 λ/4 미만이어도, 0 근처에 몰려 있으면 래핑이 아니다."""
    rng = np.random.default_rng(2)
    los = rng.normal(0.0, 2.0, (40, 6))                     # σ=2mm — λ/4 안이지만 0 집중
    p = _geo_track(tmp_path / "small.h5", los=los, height=True)
    rep = preflight_track_h5(p)
    assert not rep.looks_wrapped and rep.is_ready


def test_unwrapped_signal_passes(tmp_path):
    """λ/4 를 넘는 점이 하나라도 있으면 언래핑된 산출물이다."""
    rng = np.random.default_rng(3)
    los = rng.normal(0.0, 30.0, (40, 6))
    p = _geo_track(tmp_path / "unwrapped.h5", los=los, height=True)
    rep = preflight_track_h5(p)
    assert not rep.looks_wrapped and rep.is_ready
    assert rep.los_abs_max > 13.87


def test_file_wavelength_sets_the_wrap_limit(tmp_path):
    """파일이 자기 λ 를 기록하면 그 λ/4 로 판정한다(엔진별 λ 차이 흡수)."""
    lam_m = 0.05546576                                      # snap_backend.WAVELENGTH_M
    limit = lam_m * 1000.0 / 4.0
    rng = np.random.default_rng(4)
    los = rng.uniform(-limit, limit, (40, 6))
    los.flat[0] = limit                                     # 정확히 λ/4 에 닿는 점
    p = _geo_track(tmp_path / "lam.h5", los=los, height=True)
    with h5py.File(p, "a") as f:
        f.attrs["RADAR_WAVELENGTH"] = lam_m
    rep = preflight_track_h5(p)
    assert rep.looks_wrapped, rep.los_abs_max
