"""Prefect 스케줄링 — 에스컬레이션 판정(순수) + monitor_cycle + flow 래핑."""

from __future__ import annotations

import h5py
import numpy as np
import pytest

from inframon.schedule import _escalated


# ── 에스컬레이션 (순수) ──
def test_escalated_ordering():
    assert _escalated("경고", "정상") is True
    assert _escalated("위험", "경고") is True
    assert _escalated("주의", "주의") is False        # 동일
    assert _escalated("정상", "위험") is False        # 하향
    assert _escalated("경고", None) is False          # 직전 없음
    assert _escalated("X", "정상") is False           # 미상 등급


def _project_with_insar(tmp_path, m=5):
    from inframon.contracts.io import ProjectStore
    from inframon.insar.track_reader import import_track_h5
    track = tmp_path / "track.h5"
    epochs = np.array([20240107, 20240119, 20240131, 20240212, 20240224][:m], dtype=np.int32)
    with h5py.File(track, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack([np.arange(12.0), np.zeros(12)]))
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=np.random.default_rng(0).normal(0, 2, (12, m)).astype(np.float32))
        f.create_dataset("coh", data=np.full(12, 0.8, dtype=np.float32))
    proj = tmp_path / "p.h5"
    with ProjectStore(proj, mode="w") as s:
        import_track_h5(s, track)
    return proj


# ── monitor_cycle (torch) ──
def test_monitor_cycle_runs(tmp_path):
    pytest.importorskip("torch")
    from inframon.schedule import monitor_cycle
    proj = _project_with_insar(tmp_path)
    r = monitor_cycle(proj, prev_level="정상", pinn_epochs=40)
    assert r["ok"] is True
    assert r["level"] in {"정상", "주의", "경고", "위험"}
    assert isinstance(r["escalated"], bool)
    assert r["function_states"]                         # 기능 상태 채워짐


def test_monitor_cycle_ingests_new_track(tmp_path):
    pytest.importorskip("torch")
    from inframon.contracts.io import ProjectStore
    from inframon.contracts.schema import InSAROutput
    from inframon.schedule import monitor_cycle
    proj = _project_with_insar(tmp_path, m=4)            # 처음 4시점
    new_track = tmp_path / "new.h5"
    with h5py.File(new_track, "w") as f:                 # 5시점으로 갱신
        f.create_dataset("pixel_lonlat", data=np.column_stack([np.arange(12.0), np.zeros(12)]))
        f.create_dataset("epochs", data=np.array([20240107, 20240119, 20240131, 20240212, 20240224],
                                                 dtype=np.int32))
        f.create_dataset("los_mm", data=np.zeros((12, 5), dtype=np.float32))
        f.create_dataset("coh", data=np.full(12, 0.8, dtype=np.float32))
    r = monitor_cycle(proj, track_h5=new_track, pinn_epochs=30)
    assert r["ok"] is True
    with ProjectStore(proj, mode="r") as s:
        assert s.read_meta("insar", InSAROutput).n_dates == 5   # 새 Track 인제스트됨


def test_monitor_cycle_bad_track(tmp_path):
    pytest.importorskip("torch")
    from inframon.schedule import monitor_cycle
    proj = _project_with_insar(tmp_path)
    bad = tmp_path / "bad.h5"
    with h5py.File(bad, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.zeros((3, 2)))   # los/epochs 없음
    r = monitor_cycle(proj, track_h5=bad, pinn_epochs=20)
    assert r["ok"] is False and r["errors"]


def test_build_flow(tmp_path):
    pytest.importorskip("prefect")
    from inframon.schedule import build_flow
    flow = build_flow(tmp_path / "p.h5")
    assert callable(flow)
