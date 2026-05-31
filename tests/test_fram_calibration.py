"""Isotonic 캘리브레이션 — PAVA 정확성 + Morandi 라벨로 CRI→확률 보정 검증.

핵심 성질: 단조 변환이라 ROC-AUC(순위)는 보존, Brier(보정 오차)는 개선.
"""

from __future__ import annotations

import numpy as np

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.fram.calibration import IsotonicCalibrator, brier_score, isotonic_regression
from inframon.fram.real_engine import run_fram_real
from inframon.fram.synthetic import make_collapse_scenario, roc_auc
from inframon.pinn.engine import run_pinn


def test_pava_basic():
    assert np.allclose(isotonic_regression([1.0, 2.0, 3.0]), [1, 2, 3])     # 이미 단조
    assert np.allclose(isotonic_regression([3.0, 2.0, 1.0]), [2, 2, 2])     # 역순 → 평균
    assert np.allclose(isotonic_regression([1.0, 3.0, 2.0, 4.0]), [1, 2.5, 2.5, 4])


def test_pava_is_monotone_nondecreasing():
    rng = np.random.default_rng(0)
    y = rng.random(50)
    f = isotonic_regression(y)
    assert np.all(np.diff(f) >= -1e-12)


def _morandi_scores(tmp_path, seed=1):
    cfg = PipelineConfig(n_points=60, n_dates=36)
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        insar, failing = make_collapse_scenario(store, n_points=60, n_dates=36, seed=seed)
        pinn = run_pinn(store, insar, cfg)
        fram = run_fram_real(store, insar, pinn, cfg)
        cri = store.read_array(fram.CRI_ds)
    return cri.max(axis=1), failing            # 점별 최대 CRI, 라벨


def test_calibration_preserves_auc_and_improves_brier(tmp_path):
    scores, failing = _morandi_scores(tmp_path)
    cal = IsotonicCalibrator().fit(scores, failing)
    prob = cal.predict(scores)

    assert np.all(np.diff(cal.y_) >= -1e-12)                      # 매핑 단조 비감소
    auc_raw = roc_auc(scores, failing)
    auc_cal = roc_auc(prob, failing)
    # 단조 보정은 순위를 떨어뜨리지 않는다(역전 풀링→동률은 AUC 보존 또는 개선).
    assert auc_cal >= auc_raw - 1e-9
    # 보정은 학습데이터 Brier 를 (단조 제약하) 최소화 → 원 CRI-as-prob 보다 개선
    assert brier_score(prob, failing) <= brier_score(scores, failing) + 1e-9
    # 보정 확률 범위·극단 분리
    assert (prob >= 0).all() and (prob <= 1).all()
    assert prob[failing].mean() > prob[~failing].mean()


def test_calibrator_serialization_roundtrip(tmp_path):
    scores, failing = _morandi_scores(tmp_path)
    cal = IsotonicCalibrator().fit(scores, failing)
    cal2 = IsotonicCalibrator.from_dict(cal.to_dict())
    q = np.linspace(0, 1, 25)
    assert np.allclose(cal.predict(q), cal2.predict(q))


def test_fram_applies_calibrator_when_configured(tmp_path):
    """cfg.fram_calibrator 가 있으면 FRAM 이 보정 확률맵을 추가 산출한다."""
    scores, failing = _morandi_scores(tmp_path, seed=3)
    cal = IsotonicCalibrator().fit(scores, failing)

    cfg = PipelineConfig(n_points=60, n_dates=36)
    cfg.fram_calibrator = cal.to_dict()
    with ProjectStore(tmp_path / "q.h5", mode="w") as store:
        insar, _ = make_collapse_scenario(store, n_points=60, n_dates=36, seed=3)
        pinn = run_pinn(store, insar, cfg)
        fram = run_fram_real(store, insar, pinn, cfg)
        assert fram.calibrated_risk_ds is not None
        cal_map = store.read_array(fram.calibrated_risk_ds)
        cri = store.read_array(fram.CRI_ds)

    assert cal_map.shape == cri.shape
    assert (cal_map >= 0).all() and (cal_map <= 1).all()
    assert np.allclose(cal_map, cal.predict(cri), atol=1e-5)      # 매핑 그대로 적용


def test_fram_no_calibrator_leaves_field_none(tmp_path):
    cfg = PipelineConfig(n_points=40, n_dates=20)
    with ProjectStore(tmp_path / "r.h5", mode="w") as store:
        insar, _ = make_collapse_scenario(store, n_points=40, n_dates=20, seed=4)
        pinn = run_pinn(store, insar, cfg)
        fram = run_fram_real(store, insar, pinn, cfg)
    assert fram.calibrated_risk_ds is None
