"""asc+desc 융합 — 순방향 모델 역산 + 폴백 + real_engine 융합 경로."""

from __future__ import annotations

import h5py
import numpy as np
import pytest

from inframon.insar import fusion
from inframon.insar.fusion import FusionError, fuse_asc_desc, los_from_components
from inframon.insar.track_reader import TrackData

# Sentinel-1 근사 기하 — asc/desc heading, 동일 입사각.
HEAD_ASC, HEAD_DESC, INC = -12.0, -168.0, 38.0


def _track(lonlat, los, labels, coh=None, inc=INC, heading=0.0):
    n, m = los.shape
    base = labels[0]
    from datetime import datetime
    t0 = datetime.strptime(str(base), "%Y%m%d")
    days = np.array([(datetime.strptime(str(x), "%Y%m%d") - t0).days for x in labels], float)
    return TrackData(
        lonlat=np.asarray(lonlat, float), los=np.asarray(los, np.float32), dates=days,
        date_labels=np.asarray([str(x) for x in labels], dtype="S8"),
        coherence=(np.full(n, 0.8, np.float32) if coh is None else coh),
        incidence=np.full(n, inc, np.float32), heading=heading)


def _synthetic_pair(seed=0):
    """동일 지점·겹치는 시간의 asc/desc 한 쌍을 알려진 (U,H)로 순방향 생성."""
    rng = np.random.default_rng(seed)
    # 종축=동서(점들이 경도 방향으로 분포) → 축 방위각 A≈90°
    lon = 127.10 + np.linspace(0, 0.003, 6)
    lat = np.full(6, 37.36)
    lonlat = np.column_stack([lon, lat])
    axis_az = 90.0
    lam_a, lam_d = fusion.look_azimuth(HEAD_ASC), fusion.look_azimuth(HEAD_DESC)

    # 알려진 연직 U·종축 H(선형 시계열). desc 는 asc 구간을 포괄(시점은 어긋남)해
    # 시간보간으로 4개 asc 공통시점이 모두 살아남게 한다.
    a_labels = [20200101, 20200113, 20200125, 20200206]   # 20200101 기준 일수 [0,12,24,36]
    d_labels = [20191230, 20200118, 20200212]             # 기준 일수 [-2, 17, 42] (asc 포괄)

    def lin(d):  # 20200101 기준 일수 → (U[mm] 침하, H[mm] 열팽창), 선형
        return -3.0 * (d / 12.0), 2.0 * (d / 12.0)

    a_days, d_days = [0, 12, 24, 36], [-2, 17, 42]
    a_los = np.zeros((6, 4), np.float32)
    d_los = np.zeros((6, 3), np.float32)
    for k, day in enumerate(a_days):
        u, h = lin(day)
        a_los[:, k] = los_from_components(u, h, INC, lam_a, axis_az)
    for k, day in enumerate(d_days):
        u, h = lin(day)
        d_los[:, k] = los_from_components(u, h, INC, lam_d, axis_az)

    asc = _track(lonlat, a_los, a_labels, heading=HEAD_ASC)
    # desc 점을 살짝(수 m) 흔들어도 최근접 정합되게
    jitter = rng.normal(0, 0.00001, size=lonlat.shape)
    desc = _track(lonlat + jitter, d_los, d_labels, heading=HEAD_DESC)
    return asc, desc, lin, a_days


def test_fusion_recovers_known_components():
    asc, desc, lin, a_days = _synthetic_pair()
    res = fuse_asc_desc(asc, desc)
    assert res.meta["ok"] and res.meta["method"] == "asc_desc_fusion"
    assert res.meta["n_fused"] == 6 and res.meta["n_common_dates"] == 4
    # 모든 점·시점에서 알려진 U,H 복원(선형이라 보간 오차 0)
    for k, day in enumerate(a_days):
        u, h = lin(day)
        assert np.allclose(res.vertical[:, k], u, atol=1e-2)
        assert np.allclose(res.longitudinal[:, k], h, atol=1e-2)


def test_fusion_falls_back_without_incidence():
    asc, desc, _, _ = _synthetic_pair()
    asc.incidence = None
    with pytest.raises(FusionError, match="입사각"):
        fuse_asc_desc(asc, desc)


def test_fusion_falls_back_without_heading():
    asc, desc, _, _ = _synthetic_pair()
    desc.heading = None
    with pytest.raises(FusionError, match="heading"):
        fuse_asc_desc(asc, desc)


def test_fusion_falls_back_no_time_overlap():
    asc, desc, _, _ = _synthetic_pair()
    # desc 를 2년 뒤로 → 시간 겹침 없음
    desc.date_labels = np.asarray(["20220104", "20220116", "20220128"], dtype="S8")
    with pytest.raises(FusionError, match="공통 시점"):
        fuse_asc_desc(asc, desc)


def test_fusion_falls_back_no_spatial_match():
    asc, desc, _, _ = _synthetic_pair()
    desc.lonlat = desc.lonlat + 1.0   # ~100km 이동 → 정합 불가
    with pytest.raises(FusionError, match="정합점"):
        fuse_asc_desc(asc, desc)


# ───────────────────────── real_engine 융합 경로 ─────────────────────────
def _write_track_h5(path, td):
    with h5py.File(path, "w") as f:
        f.create_dataset("pixel_lonlat", data=td.lonlat.astype(np.float64))
        epochs = np.asarray(td.date_labels).astype(str).astype(np.int64)
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=td.los)
        f.create_dataset("coh", data=td.coherence)
        f.create_dataset("incidenceAngle", data=td.incidence)
        f.attrs["heading"] = float(td.heading)


def test_real_engine_uses_fusion(tmp_path):
    """desc 소스를 주면 real 엔진이 융합 경로로 종축+연직(/insar/vertical)을 채운다."""
    from inframon.config import PipelineConfig
    from inframon.contracts.io import ProjectStore
    from inframon.cv.engine import run_cv
    from inframon.insar.real_engine import run_insar_real

    # CV ROI 안에 들어오도록 픽셀좌표로 점 배치(identity 정합) — asc/desc 동일 위치
    cfg = PipelineConfig()
    proj = tmp_path / "p.h5"
    store = ProjectStore(proj, mode="w").__enter__()
    try:
        cv = run_cv(store, cfg)
        cv.geometry.azimuth_angle = 90.0
        roi = store.read_array(cv.roi_mask_ds)
        inside = np.argwhere(roi > 0)
        sel = inside[np.random.default_rng(5).choice(len(inside), 6, replace=False)]
        lonlat = np.column_stack([sel[:, 1], sel[:, 0]]).astype(float)  # (col,row) 픽셀
        axis_az = fusion._axis_azimuth_deg(lonlat)
        lam_a, lam_d = fusion.look_azimuth(HEAD_ASC), fusion.look_azimuth(HEAD_DESC)

        a_labels = [20200101, 20200113, 20200125, 20200206]
        d_labels = [20191230, 20200118, 20200212]   # asc 구간 포괄
        a_los = np.zeros((6, 4), np.float32)
        d_los = np.zeros((6, 3), np.float32)
        for k, day in enumerate([0, 12, 24, 36]):
            a_los[:, k] = los_from_components(-3 * day / 12, 2 * day / 12, INC, lam_a, axis_az)
        for k, day in enumerate([-2, 17, 42]):
            d_los[:, k] = los_from_components(-3 * day / 12, 2 * day / 12, INC, lam_d, axis_az)

        asc = _track(lonlat, a_los, a_labels, heading=HEAD_ASC)
        desc = _track(lonlat, d_los, d_labels, heading=HEAD_DESC)
        a_h5, d_h5 = tmp_path / "asc.h5", tmp_path / "desc.h5"
        _write_track_h5(a_h5, asc)
        _write_track_h5(d_h5, desc)
        cfg.insar_source_h5 = str(a_h5)
        cfg.insar_source_desc_h5 = str(d_h5)

        out = run_insar_real(store, cv, cfg)
        src = store.read_json_attr("insar", "insar_source")
        vert = store.read_array("/insar/vertical")
        lon = store.read_array(out.longitudinal_ds)
    finally:
        store.__exit__(None, None, None)

    assert src["longitudinal_method"] == "asc_desc_fusion"
    assert src["fusion"]["ok"] is True
    assert out.vertical_ds == "/insar/vertical"   # 계약 필드로 편입됨
    assert out.n_dates == 4                     # 공통 시점(asc 타임라인)
    assert vert.shape == lon.shape
    # 마지막 시점에서 종축≈6mm, 연직≈-9mm
    assert np.allclose(lon[:, -1], 6.0, atol=1e-1)
    assert np.allclose(vert[:, -1], -9.0, atol=1e-1)


def test_real_engine_fusion_fallback_to_single(tmp_path):
    """desc 에 입사각이 없으면 융합 실패 → 단일 궤도 처리로 폴백(이유 기록)."""
    from inframon.config import PipelineConfig
    from inframon.contracts.io import ProjectStore
    from inframon.cv.engine import run_cv
    from inframon.insar.real_engine import run_insar_real

    cfg = PipelineConfig()
    proj = tmp_path / "p.h5"
    store = ProjectStore(proj, mode="w").__enter__()
    try:
        cv = run_cv(store, cfg)
        roi = store.read_array(cv.roi_mask_ds)
        inside = np.argwhere(roi > 0)
        sel = inside[np.random.default_rng(6).choice(len(inside), 6, replace=False)]
        lonlat = np.column_stack([sel[:, 1], sel[:, 0]]).astype(float)
        a_labels = [20200101, 20200113, 20200125, 20200206]
        d_labels = [20200104, 20200116, 20200128]

        with h5py.File(tmp_path / "asc.h5", "w") as f:
            f.create_dataset("pixel_lonlat", data=lonlat)
            f.create_dataset("epochs", data=np.asarray(a_labels, np.int64))
            f.create_dataset("los_mm", data=np.zeros((6, 4), np.float32))
            f.create_dataset("coh", data=np.full(6, 0.8, np.float32))
            f.create_dataset("incidenceAngle", data=np.full(6, INC, np.float32))
            f.attrs["heading"] = HEAD_ASC
        with h5py.File(tmp_path / "desc.h5", "w") as f:   # 입사각 없음 → 융합 불가
            f.create_dataset("pixel_lonlat", data=lonlat)
            f.create_dataset("epochs", data=np.asarray(d_labels, np.int64))
            f.create_dataset("los_mm", data=np.zeros((6, 3), np.float32))
            f.create_dataset("coh", data=np.full(6, 0.8, np.float32))

        cfg.insar_source_h5 = str(tmp_path / "asc.h5")
        cfg.insar_source_desc_h5 = str(tmp_path / "desc.h5")
        out = run_insar_real(store, cv, cfg)
        src = store.read_json_attr("insar", "insar_source")
    finally:
        store.__exit__(None, None, None)

    # 융합 시도했으나 실패 → 단일(입사각 deprojection) 경로, 이유 기록
    assert src["fusion"]["ok"] is False
    assert "입사각" in src["fusion"]["reason"]
    assert src["longitudinal_method"] == "deprojection_incidence"
    assert out.vertical_ds is None
    assert out.n_dates == 4
