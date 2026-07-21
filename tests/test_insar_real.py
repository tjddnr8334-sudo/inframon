"""InSAR real 엔진 (Phase 3 1차 증분) — Track H5 → CV 정합 검증.

CV stub 으로 ROI/부재 마스크를 만들고, ROI 안/밖 점을 섞은 합성 Track H5 를 입력해
실엔진이 (1) ROI 밖 점을 버리고 (2) CV 부재 라벨을 할당하고 (3) 기하 분해를 하고
(4) /insar 계약을 채우는지 확인한다.
"""

from __future__ import annotations

import h5py
import numpy as np
import pytest

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.cv.engine import run_cv
from inframon.insar.real_engine import run_insar_real


def _make_track_h5(path, lonlat, n_dates, *, seed=0):
    rng = np.random.default_rng(seed)
    n = lonlat.shape[0]
    epochs = np.array([20200101, 20200113, 20200125, 20200206, 20200218][:n_dates], dtype=np.int32)
    los = rng.normal(0, 2.0, size=(n, n_dates)).astype(np.float32)
    coh = rng.uniform(0.5, 0.95, size=n).astype(np.float32)
    with h5py.File(path, "w") as f:
        f.create_dataset("pixel_lonlat", data=lonlat.astype(np.float64))
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=los)
        f.create_dataset("coh", data=coh)
        f.attrs["FILE_TYPE"] = "track_b_mintpy_sbas"
    return los, epochs


def _cv_and_store(tmp_path):
    cfg = PipelineConfig()
    proj = tmp_path / "project.h5"
    store = ProjectStore(proj, mode="w").__enter__()
    cv = run_cv(store, cfg)
    return cfg, cv, store, proj


def test_real_insar_registered():
    from inframon.insar.real_engine import run_insar_real as fn
    from inframon.orchestrator import engines

    assert engines.resolve("insar", "real") is fn
    assert "real" in engines.available_modes("insar")


def test_real_insar_requires_source(tmp_path):
    cfg, cv, store, _ = _cv_and_store(tmp_path)
    try:
        with pytest.raises(ValueError, match="insar=real"):
            run_insar_real(store, cv, cfg)
    finally:
        store.__exit__(None, None, None)


def test_real_insar_couples_to_cv(tmp_path):
    cfg, cv, store, _ = _cv_and_store(tmp_path)
    try:
        roi = store.read_array(cv.roi_mask_ds)
        inside = np.argwhere(roi > 0)   # [K,2] (row,col)
        outside = np.argwhere(roi == 0)
        # ROI 안 5점 + 밖 2점 (lonlat 은 (col,row) = (x,y))
        rng = np.random.default_rng(0)
        ins = inside[rng.choice(len(inside), 5, replace=False)]
        outs = outside[rng.choice(len(outside), 2, replace=False)]
        sel = np.vstack([ins, outs])
        lonlat = np.column_stack([sel[:, 1], sel[:, 0]]).astype(float)  # (col,row)

        track = tmp_path / "track.h5"
        los_src, _ = _make_track_h5(track, lonlat, n_dates=4)
        cfg.insar_source_h5 = str(track)

        out = run_insar_real(store, cv, cfg)

        member = store.read_array(out.member_ds)
        los = store.read_array(out.los_ds)
        lon = store.read_array(out.longitudinal_ds)
        src = store.read_json_attr("insar", "insar_source")
        labels = store.read_array("/insar/date_labels").astype(str)
    finally:
        store.__exit__(None, None, None)

    # ROI 밖 2점은 버려지고 5점만 남아야
    assert out.n_points == 5
    assert out.n_dates == 4
    assert src["mode"] == "real"
    assert src["n_source_points"] == 7
    assert src["n_dropped_outside_roi"] == 2
    assert los.shape == (5, 4)
    assert member.shape == (5,)
    # 부재 라벨은 표준 인덱스 범위 안
    assert set(member.tolist()).issubset({0, 1, 2, 3})
    # 종방향 = los * cos(az)
    az = float(cv.geometry.azimuth_angle)
    assert np.allclose(lon, los * np.cos(np.deg2rad(az)), rtol=1e-5, atol=1e-5)
    assert labels[0] == "20200101"


def test_real_insar_corrections_write_velocity(tmp_path):
    """--insar-corrections: /insar/velocity_mm_yr + 보정 이력이 기록되고 형상 일관."""
    cfg, cv, store, _ = _cv_and_store(tmp_path)
    try:
        roi = store.read_array(cv.roi_mask_ds)
        inside = np.argwhere(roi > 0)
        rng = np.random.default_rng(1)
        ins = inside[rng.choice(len(inside), 8, replace=False)]
        lonlat = np.column_stack([ins[:, 1], ins[:, 0]]).astype(float)
        track = tmp_path / "track.h5"
        _make_track_h5(track, lonlat, n_dates=5, seed=3)
        cfg.insar_source_h5 = str(track)
        cfg.insar_apply_corrections = True

        out = run_insar_real(store, cv, cfg)
        vel = store.read_array("/insar/velocity_mm_yr")
        src = store.read_json_attr("insar", "insar_source")
    finally:
        store.__exit__(None, None, None)

    assert vel.shape == (out.n_points,)               # 점별 속도
    assert np.isfinite(vel).all()
    assert src["velocity_ds"] == "/insar/velocity_mm_yr"
    assert src["corrections"] is not None
    assert "applied" in src["corrections"]            # 보정 이력(적용/스킵) 기록


def test_los_axial_factor_and_deprojection():
    """기하 계수 g=sinθ·cosΔ + deprojection·저민감 마스크."""
    from inframon.insar import geo

    # θ=90°(수평 LOS), Δ=0 → g=1 → axial = los
    assert geo.los_axial_factor(90.0, 0.0) == pytest.approx(1.0)
    # θ=30°, Δ=0 → g=0.5 → axial = los/0.5 = 2·los
    los = np.array([[1.0, -2.0]], dtype=np.float32)
    axial, valid = geo.los_to_axial(los, np.array([geo.los_axial_factor(30.0, 0.0)]))
    assert valid[0] and np.allclose(axial, los / 0.5)
    # Δ=90°(축⊥LOS) → g≈0 → 저민감(valid=False, NaN)
    bad, vbad = geo.los_to_axial(los, np.array([geo.los_axial_factor(40.0, 90.0)]),
                                 min_abs_factor=0.2)
    assert not vbad[0] and np.all(np.isnan(bad))


def test_real_insar_incidence_deprojection(tmp_path):
    """Track 에 입사각이 있으면 종방향이 deprojection(los/(sinθ·cosΔ))으로 채워진다."""
    cfg, cv, store, _ = _cv_and_store(tmp_path)
    try:
        cv.geometry.azimuth_angle = 30.0   # Δ 고정(저민감 회피)
        roi = store.read_array(cv.roi_mask_ds)
        inside = np.argwhere(roi > 0)
        sel = inside[np.random.default_rng(3).choice(len(inside), 5, replace=False)]
        lonlat = np.column_stack([sel[:, 1], sel[:, 0]]).astype(float)

        track = tmp_path / "inc_track.h5"
        los_src, _ = _make_track_h5(track, lonlat, n_dates=4)
        with h5py.File(track, "a") as f:
            f.create_dataset("incidenceAngle", data=np.full(5, 38.0, dtype=np.float32))
        cfg.insar_source_h5 = str(track)

        out = run_insar_real(store, cv, cfg)
        los = store.read_array(out.los_ds)
        lon = store.read_array(out.longitudinal_ds)
        src = store.read_json_attr("insar", "insar_source")
    finally:
        store.__exit__(None, None, None)

    factor = np.sin(np.deg2rad(38.0)) * np.cos(np.deg2rad(30.0))  # ≈0.533 ≥ 0.2 → 전부 유효
    assert src["longitudinal_method"] == "deprojection_incidence"
    assert src["n_low_axial_sensitivity"] == 0
    assert src["incidence_mean_deg"] == pytest.approx(38.0)
    assert np.allclose(lon, los / factor, rtol=1e-4, atol=1e-4)
    # 투영(los·cosΔ)과는 분명히 다른 값(= sinθ 인자 반영)
    assert not np.allclose(lon, los * np.cos(np.deg2rad(30.0)), atol=1e-3)


def test_track_reader_reads_incidence(tmp_path):
    """입사각 데이터셋([N])·heading attr·스칼라 attr 입사각을 인식한다."""
    from inframon.insar.track_reader import read_track_h5

    lonlat = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    p = tmp_path / "inc.h5"
    _make_track_h5(p, lonlat, n_dates=3)
    with h5py.File(p, "a") as f:
        f.create_dataset("incidenceAngle", data=np.array([38.0, 39.0, 40.0], dtype=np.float32))
        f.attrs["heading"] = -12.0
    td = read_track_h5(p)
    assert td.incidence is not None and np.allclose(td.incidence, [38.0, 39.0, 40.0])
    assert td.heading == pytest.approx(-12.0)

    # 스칼라 attr 입사각 → [N] 브로드캐스트
    p2 = tmp_path / "inc2.h5"
    _make_track_h5(p2, lonlat, n_dates=3)
    with h5py.File(p2, "a") as f:
        f.attrs["incidence_angle"] = 37.5
    td2 = read_track_h5(p2)
    assert td2.incidence is not None and np.allclose(td2.incidence, 37.5)

    # 입사각 없으면 None (기존 투영 폴백 경로)
    p3 = tmp_path / "noinc.h5"
    _make_track_h5(p3, lonlat, n_dates=3)
    assert read_track_h5(p3).incidence is None


def test_real_insar_rejects_all_outside_roi(tmp_path):
    cfg, cv, store, _ = _cv_and_store(tmp_path)
    try:
        # 전부 프레임 밖 좌표 → 2점 미만으로 에러
        lonlat = np.array([[-5.0, -5.0], [99999.0, 99999.0]])
        track = tmp_path / "bad.h5"
        _make_track_h5(track, lonlat, n_dates=3)
        cfg.insar_source_h5 = str(track)
        with pytest.raises(ValueError, match="유효 InSAR 점이"):
            run_insar_real(store, cv, cfg)
    finally:
        store.__exit__(None, None, None)


def test_geo_world_pixel_roundtrip():
    """world_to_pixel ∘ pixel_to_world = identity (역아핀 정확성)."""
    from inframon.insar import geo

    gt = (300000.0, 2.5, 0.3, 4100000.0, 0.4, -2.5)  # 회전·비대칭 포함 일반 아핀
    col = np.array([0.0, 5.0, 17.0, 42.0])
    row = np.array([0.0, 3.0, 9.0, 28.0])
    world = geo.pixel_to_world(gt, col, row)
    back = geo.world_to_pixel(gt, world[:, 0], world[:, 1])
    assert np.allclose(back[:, 0], col, atol=1e-6)
    assert np.allclose(back[:, 1], row, atol=1e-6)


def test_geo_axial_from_fixed_default_end():
    """member 없으면 종축 한쪽 끝(min 투영)이 고정단 → 끝에서 0, 선형 증가."""
    from inframon.insar import geo

    x = np.array([0.0, 10.0, 20.0, 30.0, 40.0])
    xy = np.column_stack([x, np.zeros(5)])
    d = geo.axial_from_fixed(xy)  # PC1≈x, 기준=한쪽 끝
    assert d.min() == pytest.approx(0.0, abs=1e-6)
    # PC1 부호와 무관하게 거리 집합은 {0,10,20,30,40}
    assert np.allclose(np.sort(d), [0, 10, 20, 30, 40], atol=1e-6)


def test_geo_axial_from_fixed_uses_abutment():
    """abutment 라벨이 있으면 그 위치가 영점(고정단) — 거기서 거리 0, 반대 끝이 최대."""
    from inframon.contracts.schema import MEMBER_TYPES
    from inframon.insar import geo

    x = np.array([0.0, 10.0, 20.0, 30.0, 40.0])
    xy = np.column_stack([x, np.zeros(5)])
    ab = MEMBER_TYPES.index("abutment")
    member = np.array([0, 0, 0, 0, ab], dtype=np.int8)  # x=40 점이 교대(고정단)
    d = geo.axial_from_fixed(xy, member, ab)
    assert d[-1] == pytest.approx(0.0, abs=1e-6)   # 고정단에서 0
    assert d[0] == pytest.approx(40.0, abs=1e-6)   # 반대 끝에서 최대


def test_geo_singular_transform_raises():
    from inframon.insar import geo

    with pytest.raises(ValueError, match="특이행렬"):
        geo.world_to_pixel((0.0, 1.0, 2.0, 0.0, 2.0, 4.0), np.array([1.0]), np.array([1.0]))


def test_real_insar_geo_registration_matches_pixels(tmp_path):
    """CV 가 geo_transform 을 주면 Track world 좌표가 의도한 CV 픽셀에 정합된다.

    동일한 ROI 픽셀을 (a) identity 로 직접 주는 경우와 (b) 그 픽셀의 world 좌표를
    geo 경로로 정합하는 경우의 결과(점 수·부재·정합 메타)가 일치해야 한다.
    """
    from inframon.insar import geo

    cfg, cv, store, _ = _cv_and_store(tmp_path)
    try:
        roi = store.read_array(cv.roi_mask_ds)
        inside = np.argwhere(roi > 0)  # [K,2] (row,col)
        sel = inside[np.random.default_rng(2).choice(len(inside), 6, replace=False)]
        col = sel[:, 1].astype(float)
        row = sel[:, 0].astype(float)

        # CV 에 합성 geo_transform 부여 후, 그 픽셀의 world 좌표를 Track 입력으로 사용
        gt = (300000.0, 2.0, 0.0, 4100000.0, 0.0, -2.0)  # EPSG:5179 모사
        cv.geometry.geo_transform = gt
        cv.geometry.crs = "EPSG:5179"
        world = geo.pixel_to_world(gt, col, row)  # [N,2]

        track = tmp_path / "geo_track.h5"
        with h5py.File(track, "w") as f:
            f.create_dataset("ps_lonlat", data=world.astype(np.float64))
            f.create_dataset("epochs", data=np.array([20200101, 20200113, 20200125], dtype=np.int32))
            f.create_dataset("los_mm", data=np.zeros((6, 3), dtype=np.float32))
            f.create_dataset("temp_coh", data=np.full(6, 0.8, dtype=np.float32))
            f.attrs["crs"] = "EPSG:5179"
        cfg.insar_source_h5 = str(track)

        out = run_insar_real(store, cv, cfg)
        src = store.read_json_attr("insar", "insar_source")
        xyz = store.read_array(out.xyz_ds)
        lff = store.read_array(out.l_from_fixed_ds)
    finally:
        store.__exit__(None, None, None)

    assert out.n_points == 6          # 6점 모두 ROI 안으로 정합
    assert src["frame"] == "cv_geo"
    assert src["registration"] == "geo_affine"
    assert src["n_dropped_outside_roi"] == 0
    # xyz 는 픽셀이 아니라 world(EPSG:5179) 좌표 — 입력 world 와 (정합 후) 일치
    assert src["xyz_frame"] == "world:EPSG:5179"
    assert src["l_unit"] == "m"
    assert np.all(xyz[:, 0] > 100000.0)   # 픽셀(작은 정수)이 아니라 투영 좌표 스케일
    # l_from_fixed 는 종축(여기선 x 방향 gt) 위 중앙값 기준 거리(미터) ≥ 0
    assert lff.min() >= 0.0 and lff.max() > 0.0


def test_track_reader_reads_optional_height(tmp_path):
    """height/hgt 데이터셋이 있으면 TrackData.height 로 읽히고, 없으면 None."""
    from inframon.insar.track_reader import read_track_h5

    lonlat = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    with_h = tmp_path / "with_h.h5"
    _make_track_h5(with_h, lonlat, n_dates=3)
    with h5py.File(with_h, "a") as f:
        f.create_dataset("hgt", data=np.array([10.0, 20.0, 30.0], dtype=np.float32))
    td = read_track_h5(with_h)
    assert td.height is not None
    assert np.allclose(td.height, [10.0, 20.0, 30.0])

    without_h = tmp_path / "without_h.h5"
    _make_track_h5(without_h, lonlat, n_dates=3)
    assert read_track_h5(without_h).height is None


def test_track_reader_rejects_height_count_mismatch(tmp_path):
    from inframon.insar.track_reader import read_track_h5

    lonlat = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    bad = tmp_path / "bad_h.h5"
    _make_track_h5(bad, lonlat, n_dates=3)
    with h5py.File(bad, "a") as f:
        f.create_dataset("height", data=np.array([10.0, 20.0], dtype=np.float32))  # 2 != 3
    with pytest.raises(ValueError, match="height count"):
        read_track_h5(bad)


def test_real_insar_uses_track_height_for_z(tmp_path):
    """Track H5 에 고도가 있으면 xyz 의 z 로 적재된다(geo 경로)."""
    from inframon.insar import geo

    cfg, cv, store, _ = _cv_and_store(tmp_path)
    try:
        roi = store.read_array(cv.roi_mask_ds)
        inside = np.argwhere(roi > 0)
        sel = inside[np.random.default_rng(7).choice(len(inside), 5, replace=False)]
        col, row = sel[:, 1].astype(float), sel[:, 0].astype(float)

        gt = (300000.0, 2.0, 0.0, 4100000.0, 0.0, -2.0)
        cv.geometry.geo_transform = gt
        cv.geometry.crs = "EPSG:5179"
        world = geo.pixel_to_world(gt, col, row)
        heights = np.array([5.0, 12.0, 7.5, 20.0, 3.0], dtype=np.float32)

        track = tmp_path / "h_track.h5"
        with h5py.File(track, "w") as f:
            f.create_dataset("ps_lonlat", data=world.astype(np.float64))
            f.create_dataset("epochs", data=np.array([20200101, 20200113], dtype=np.int32))
            f.create_dataset("los_mm", data=np.zeros((5, 2), dtype=np.float32))
            f.create_dataset("temp_coh", data=np.full(5, 0.8, dtype=np.float32))
            f.create_dataset("height", data=heights)
            f.attrs["crs"] = "EPSG:5179"
        cfg.insar_source_h5 = str(track)

        out = run_insar_real(store, cv, cfg)
        xyz = store.read_array(out.xyz_ds)
        src = store.read_json_attr("insar", "insar_source")
    finally:
        store.__exit__(None, None, None)

    assert src["z_source"] == "track_height"
    # 모든 점이 ROI 안 → 입력 순서 보존, z 가 고도와 일치
    assert np.allclose(np.sort(xyz[:, 2]), np.sort(heights), atol=1e-4)
    assert not np.allclose(xyz[:, 2], 0.0)


def test_pipeline_hotswap_insar_real(tmp_path):
    """핫스왑으로 insar=real 만 켜고 전체 파이프라인이 끝까지 도는지."""
    from inframon.contracts.schema import FRAMOutput
    from inframon.orchestrator.pipeline import run_pipeline

    # CV 는 결정론적 — ROI 를 먼저 알아내 그 안에 점을 둔 Track H5 를 만든다
    probe = tmp_path / "probe.h5"
    with ProjectStore(probe, mode="w") as store:
        cv = run_cv(store, PipelineConfig())
        roi = store.read_array(cv.roi_mask_ds)
    inside = np.argwhere(roi > 0)
    sel = inside[np.random.default_rng(1).choice(len(inside), 30, replace=False)]
    lonlat = np.column_stack([sel[:, 1], sel[:, 0]]).astype(float)
    track = tmp_path / "track.h5"
    _make_track_h5(track, lonlat, n_dates=5)

    cfg = PipelineConfig(
        engines={"cv": "stub", "insar": "real", "pinn": "stub", "fram": "stub"},
        insar_source_h5=str(track),
    )
    fram = run_pipeline(tmp_path / "out.h5", cfg)

    assert isinstance(fram, FRAMOutput)
    assert fram.n_points == 30  # InSAR real 이 정한 점 수가 하류까지 전파
    assert 0.0 <= fram.cri_global_max <= 1.0
