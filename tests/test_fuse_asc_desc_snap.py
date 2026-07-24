"""⑦ SNAP asc+desc 연직분해 연동 — 융합 복원 + 단일 폴백(부족·기하)."""
from __future__ import annotations
import numpy as np
import h5py
from inframon.insar import snap_backend as sb
from inframon.insar.fusion import los_from_components, look_azimuth


def _write_track(path, lonlat, los, dates, coh, inc, heading):
    with h5py.File(path, "w") as f:
        f.create_dataset("pixel_lonlat", data=lonlat.astype(np.float64))
        f.create_dataset("epochs", data=np.array([int(d) for d in dates], dtype=np.int32))
        f.create_dataset("los_mm", data=los.astype(np.float32))
        f.create_dataset("coh", data=coh.astype(np.float32))
        f.create_dataset("incidenceAngle", data=inc.astype(np.float32))
        f.attrs["HEADING"] = float(heading)


def test_fuse_recovers_vertical(tmp_path):
    # E-W 배열 점(축≈90°), 알려진 연직 U·종축 H → asc/desc LOS forward → 융합 복원
    N, M = 25, 6
    lon = np.linspace(127.10, 127.11, N); lat = np.full(N, 37.32)
    ll = np.column_stack([lon, lat])
    dates = [20240107, 20240119, 20240131, 20240212, 20240224, 20240307]
    rng = np.random.default_rng(0)
    U = np.cumsum(rng.normal(0, 1, (N, M)), axis=1)     # 연직
    H = np.cumsum(rng.normal(0, 0.5, (N, M)), axis=1)   # 종축
    inc = np.full(N, 39.0)
    axis = 90.0
    los_a = los_from_components(U, H, 39.0, look_azimuth(-13.0), axis)
    los_d = los_from_components(U, H, 39.0, look_azimuth(-167.0), axis)
    _write_track(tmp_path / "asc.h5", ll, los_a, dates, np.full(N, 0.8), inc, -13.0)
    _write_track(tmp_path / "desc.h5", ll, los_d, dates, np.full(N, 0.8), inc, -167.0)
    r = sb.fuse_snap_asc_desc(tmp_path / "asc.h5", tmp_path / "desc.h5",
                              tmp_path / "vert.h5", min_desc_epochs=5)
    assert r["mode"] == "fused"
    with h5py.File(tmp_path / "vert.h5") as f:
        Urec = f["vertical_mm"][()]
        assert "horizontal_mm" in f
    # 복원 연직이 진짜 U 와 상관(정합·보간 오차 허용)
    assert np.corrcoef(Urec.ravel(), U.ravel())[0, 1] > 0.9


def test_single_fallback_few_desc(tmp_path):
    N = 10
    ll = np.column_stack([np.linspace(127.10, 127.11, N), np.full(N, 37.32)])
    _write_track(tmp_path / "asc.h5", ll, np.zeros((N, 8)), list(range(20240101, 20240109)),
                 np.full(N, 0.8), np.full(N, 39.0), -13.0)
    _write_track(tmp_path / "desc.h5", ll, np.zeros((N, 3)), [20240101, 20240113, 20240125],
                 np.full(N, 0.8), np.full(N, 39.0), -167.0)
    r = sb.fuse_snap_asc_desc(tmp_path / "asc.h5", tmp_path / "desc.h5", min_desc_epochs=5)
    assert r["mode"] == "single" and "부족" in r["reason"]


def test_single_fallback_no_incidence(tmp_path):
    N = 10
    ll = np.column_stack([np.linspace(127.10, 127.11, N), np.full(N, 37.32)])
    dates = list(range(20240101, 20240107))
    # 입사각 없는 desc → FusionError → 단일
    with h5py.File(tmp_path / "asc.h5", "w") as f:
        f.create_dataset("pixel_lonlat", data=ll); f.create_dataset("epochs", data=np.array(dates))
        f.create_dataset("los_mm", data=np.zeros((N, 6))); f.create_dataset("coh", data=np.full(N, .8))
        f.create_dataset("incidenceAngle", data=np.full(N, 39.0)); f.attrs["HEADING"] = -13.0
    with h5py.File(tmp_path / "desc.h5", "w") as f:
        f.create_dataset("pixel_lonlat", data=ll); f.create_dataset("epochs", data=np.array(dates))
        f.create_dataset("los_mm", data=np.zeros((N, 6))); f.create_dataset("coh", data=np.full(N, .8))
        f.attrs["HEADING"] = -167.0                      # incidence 없음
    r = sb.fuse_snap_asc_desc(tmp_path / "asc.h5", tmp_path / "desc.h5", min_desc_epochs=5)
    assert r["mode"] == "single"
