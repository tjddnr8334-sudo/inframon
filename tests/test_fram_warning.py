"""FRAM 경보 보완 — 기능별 상태(①)·보정 경보 근거(③)·전방 lead_time(④)."""

from __future__ import annotations

import numpy as np
import pytest

from inframon.config import PipelineConfig
from inframon.contracts.schema import FRAM_FUNCTIONS
from inframon.fram.real_engine import _forecast_to_threshold, _function_states
from inframon.orchestrator.pipeline import run_pipeline


# ── ① 기능별 상태 ──
def test_function_states_thresholds():
    M = 8
    V = np.zeros((4, M))
    V[0, -2:] = 0.9      # 위험 (>=0.66)
    V[1, -2:] = 0.5      # 주의 (>=0.33)
    V[2, -2:] = 0.1      # 정상
    V[3, -2:] = 0.7      # 위험
    st = _function_states(V, ["thermal", "load", "bearing", "foundation"])
    assert st == {"thermal": "위험", "load": "주의", "bearing": "정상", "foundation": "위험"}


# ── ④ 전방 lead_time 예측 ──
def test_forecast_to_threshold_rising():
    dates = np.arange(10, dtype=float)
    rising = 0.1 + 0.05 * dates                     # slope 0.05 → 0.85 도달은 t=15
    lead = _forecast_to_threshold(rising, dates, 0.85)
    assert lead == pytest.approx(6.0, abs=0.5)      # t=9 에서 약 6일 뒤


def test_forecast_to_threshold_none_cases():
    dates = np.arange(10, dtype=float)
    assert _forecast_to_threshold(np.full(10, 0.2), dates, 0.85) is None   # 상승 아님
    assert _forecast_to_threshold(np.full(10, 0.9), dates, 0.85) is None   # 이미 도달
    assert _forecast_to_threshold(np.array([0.1, 0.2]), dates[:2], 0.85) is None  # 점 부족


# ── 통합: 경보에 신규 필드 ──
def test_warning_has_function_states_and_cri_basis(tmp_path):
    cfg = PipelineConfig(n_points=40, n_dates=18,
                         engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    fram = run_pipeline(tmp_path / "p.h5", cfg)
    w = fram.warning
    assert set(w.function_states) == set(FRAM_FUNCTIONS)
    assert all(s in {"정상", "주의", "위험"} for s in w.function_states.values())
    assert w.basis == "cri"                          # 캘리브레이터 없음 → 원시 CRI
    # 전방 예측 필드 존재(값은 None 또는 양수)
    assert w.lead_time_forecast_days is None or w.lead_time_forecast_days > 0


def test_warning_basis_calibrated(tmp_path):
    from inframon.fram.calibration import IsotonicCalibrator
    cal = IsotonicCalibrator().fit(np.array([0.1, 0.2, 0.8, 0.9]), np.array([0, 0, 1, 1]))
    cfg = PipelineConfig(n_points=30, n_dates=12,
                         engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    cfg.fram_calibrator = cal.to_dict()
    fram = run_pipeline(tmp_path / "p.h5", cfg)
    assert fram.warning.basis == "calibrated_probability"   # 보정확률 기준 경보
