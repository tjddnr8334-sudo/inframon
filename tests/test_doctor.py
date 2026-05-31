"""readiness doctor — 환경·데이터 준비도 진단 (부작용 없음)."""

from __future__ import annotations

import importlib.util

import h5py
import numpy as np

from inframon.doctor import format_report, run_doctor


def _present(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def test_doctor_core_ok():
    rep = run_doctor()
    assert rep.core_ok                                  # numpy/h5py/pydantic 는 테스트 환경에 있음
    names = {d.name for d in rep.deps}
    assert {"numpy", "h5py", "pydantic", "torch", "rasterio", "pyproj"} <= names
    # capability 가 실제 설치 상태와 일치
    assert rep.capabilities["pinn_real"] == _present("torch")
    assert rep.capabilities["crs_reprojection"] == _present("pyproj")
    assert rep.capabilities["pipeline_demo"] is True


def test_doctor_report_serializable_and_formatted():
    rep = run_doctor()
    d = rep.to_dict()
    assert d["core_ok"] is True and "capabilities" in d and "deps" in d
    text = format_report(rep)
    assert "readiness doctor" in text and "의존성" in text and "판정" in text


def test_doctor_with_track_preflight(tmp_path):
    p = tmp_path / "track.h5"
    with h5py.File(p, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack(
            [300000 + np.arange(10) * 2.0, 600000 + np.zeros(10)]))
        f.create_dataset("epochs", data=np.array([20230101, 20230113, 20230125, 20230206],
                                                 dtype=np.int32))
        f.create_dataset("los_mm", data=np.zeros((10, 4), dtype=np.float32))
        f.create_dataset("coh", data=np.full(10, 0.8, dtype=np.float32))
        f.attrs["crs"] = "EPSG:5179"
    rep = run_doctor(p)
    assert rep.track is not None and rep.track["is_ready"] is True
    assert rep.track["n_points"] == 10 and rep.track["n_dates"] == 4
    assert "Track preflight" in format_report(rep)


def test_doctor_with_bad_track(tmp_path):
    p = tmp_path / "bad.h5"
    with h5py.File(p, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.zeros((3, 2)))   # los/epochs/coh 없음
    rep = run_doctor(p)
    assert rep.track is not None and rep.track["is_ready"] is False
    assert rep.track["errors"]


def test_doctor_missing_path_noted(tmp_path):
    rep = run_doctor(tmp_path / "does_not_exist")
    assert any("경로가 없습니다" in n for n in rep.notes)
    assert rep.core_ok                                  # 환경 진단 자체는 정상
