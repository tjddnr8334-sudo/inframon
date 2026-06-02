"""Morandi 류 붕괴 전조 합성 검증 (게이트 G5: ROC-AUC≥0.9).

가속 침하를 주입한 failing 점들을 FRAM CRI 가 구분하는지 ROC 로 검증한다.
합성이지만 물리적으로 동기화 — 가속 변위가 FRAM 속도/가속/발산 항을 끌어올려
CRI 가 failing 점에서 높아져야 한다.
"""

from __future__ import annotations

import numpy as np

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.fram.real_engine import run_fram_real
from inframon.fram.synthetic import make_collapse_scenario, roc_auc
from inframon.pinn.engine import run_pinn


def test_roc_auc_helper_basic():
    # 완전 분리: 양성 점수 > 음성 점수 → AUC=1; 역전 → 0; 동일 → 0.5
    assert roc_auc(np.array([3.0, 4.0, 5.0]), np.array([0, 0, 1], dtype=bool)) == 1.0
    assert roc_auc(np.array([5.0, 4.0, 1.0]), np.array([0, 0, 1], dtype=bool)) == 0.0
    assert roc_auc(np.array([2.0, 2.0]), np.array([1, 0], dtype=bool)) == 0.5


def test_fram_detects_collapse_precursor_roc(tmp_path):
    """failing 점(가속 침하)을 CRI 가 ROC-AUC≥0.9 로 구분한다."""
    cfg = PipelineConfig(n_points=60, n_dates=36)
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        insar, failing = make_collapse_scenario(store, n_points=60, n_dates=36, seed=1)
        pinn = run_pinn(store, insar, cfg)
        fram = run_fram_real(store, insar, pinn, cfg)
        cri = store.read_array(fram.CRI_ds)

    score = cri.max(axis=1)                       # 점별 최대 위험
    auc = roc_auc(score, failing)
    assert auc >= 0.9, f"CRI 의 붕괴 전조 판별 AUC={auc:.3f} < 0.9"
    # failing 점의 평균 CRI 가 건전 점보다 뚜렷이 높아야
    assert score[failing].mean() > score[~failing].mean()


def test_collapse_precursor_rises_in_time(tmp_path):
    """failing 점의 '건전점 대비 초과 CRI'가 시간에 따라 증가(전조 심화).

    계절 변동은 모든 점·시점에 공통이라 건전점과의 차이로 빼면 상쇄되고, 가속 침하의
    전조 신호만 남는다 — 발생 전 ≈0 → 후반 양(+).
    """
    cfg = PipelineConfig(n_points=50, n_dates=30)
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        insar, failing = make_collapse_scenario(store, n_points=50, n_dates=30, seed=2)
        pinn = run_pinn(store, insar, cfg)
        fram = run_fram_real(store, insar, pinn, cfg)
        cri = store.read_array(fram.CRI_ds)

    fail_cri, heal_cri = cri[failing], cri[~failing]
    excess_t = fail_cri.mean(axis=0) - heal_cri.mean(axis=0)  # [M] 시점별 failing 초과위험
    # 가속 침하 발생 후 초과위험 피크가 발생 전(초반 정적 구간)보다 뚜렷이 높다.
    # (R_div 가 예측 발산을 onset 에서 잡아 전조 출현 — 단조 상승이 아닌 피크 형태)
    assert excess_t[3:].max() > excess_t[:3].mean() + 1e-3
