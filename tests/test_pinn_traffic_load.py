"""균일하중→교통변조: EI 식별 유효하중이 교통 활하중 기반인지."""
from __future__ import annotations
import numpy as np
import pytest
from inframon.pinn.real_engine import _effective_load_for_ei, LIVE_LOAD_PER_LANE_N_M
from inframon.structure import BridgeProfile


def test_no_traffic_uses_selfweight():
    prof = BridgeProfile(load_per_len=1.5e4, width_m=12.0)
    q, basis = _effective_load_for_ei(prof, use_traffic=False, traffic=None)
    assert q == 1.5e4 and "자중" in basis


def test_traffic_uses_live_load():
    # 폭 12m → 3~4차로, 교통 피크 2배
    prof = BridgeProfile(load_per_len=1.0e4, width_m=12.0)
    traffic = np.array([1.0, 1.0, 2.0, 1.0])          # 피크/평균 = 2/1.25 = 1.6
    q, basis = _effective_load_for_ei(prof, use_traffic=True, traffic=traffic)
    n_lanes = round(12.0 / 3.5)                        # ≈ 3
    peak = 2.0 / traffic.mean()
    assert q == pytest.approx(LIVE_LOAD_PER_LANE_N_M * n_lanes * peak)
    assert "교통 활하중" in basis and "차로" in basis
    assert q > prof.load_per_len                       # 활하중이 자중보다 큼(이 예)


def test_lanes_from_width_default():
    prof = BridgeProfile(load_per_len=1.0e4, width_m=None)   # 폭 미상 → 기본 2차로
    q, _ = _effective_load_for_ei(prof, use_traffic=True, traffic=np.array([1.0, 1.0]))
    assert q == pytest.approx(LIVE_LOAD_PER_LANE_N_M * 2 * 1.0)
