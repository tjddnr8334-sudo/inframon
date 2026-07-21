"""CRI 정상범위(reference range) 캘리브레이션 — 건강 인구 대비 판독(라벨 불필요)."""

from __future__ import annotations

import numpy as np
import pytest

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.fram.real_engine import run_fram_real
from inframon.fram.reference_range import (
    BANDS,
    ReferenceRange,
    fit_reference_range,
)
from inframon.fram.synthetic import make_collapse_scenario
from inframon.pinn.engine import run_pinn


def test_fit_reference_range_robust_stats():
    rng = np.random.default_rng(0)
    healthy = np.clip(rng.normal(0.2, 0.05, 2000), 0, 1)     # 건강 인구 CRI ~0.2
    ref = fit_reference_range(healthy)
    assert ref.p50 < ref.p97_5 < ref.p99 <= ref.abnormal_high  # 경계 단조
    assert 0.15 < ref.median < 0.25
    assert ref.n == 2000
    # 왕복 직렬화
    assert ReferenceRange.from_dict(ref.to_dict()).to_dict() == ref.to_dict()


def test_bands_monotonic_in_cri():
    ref = fit_reference_range(np.clip(np.random.default_rng(1).normal(0.2, 0.05, 500), 0, 1))
    # CRI 가 커질수록 밴드가 정상→주의→경고→위험 로 단조 상승
    order = {b: i for i, b in enumerate(BANDS)}
    vals = np.array([0.0, ref.p97_5 + 1e-6, ref.p99 + 1e-6, ref.abnormal_high + 1e-6])
    bands = ref.band(vals)
    idx = [order[b] for b in bands]
    assert idx == sorted(idx) and idx[0] == 0 and idx[-1] == 3


def test_robust_z_and_percentile():
    ref = fit_reference_range(np.clip(np.random.default_rng(2).normal(0.2, 0.05, 1000), 0, 1))
    assert abs(ref.robust_z(ref.median)) < 1e-6            # 중앙값 z=0
    assert ref.robust_z(ref.median + 10) > 0              # 위쪽 양수
    assert 40 < ref.percentile_of(ref.p50) < 60           # 중앙값 ~50 백분위
    assert ref.percentile_of(ref.hi + 1) == 100.0


def test_classify_distribution_shift_not_single_outlier():
    """판정은 **분포이동 유의성** 기반: 대부분 정상 + 단일 이상점 하나는 유의한 이동이
    아니므로 등급은 정상(밴드카운트엔 이상점이 잡히되 교량경보로 과대판독하지 않음)."""
    ref = fit_reference_range(np.clip(np.random.default_rng(3).normal(0.2, 0.05, 1000), 0, 1))
    vals = np.concatenate([np.full(50, 0.2), [0.99]])
    r = ref.classify(vals)
    assert r["level"] == "정상"                            # 단일 이상점 ≠ 분포이동
    assert r["worst_cri"] == pytest.approx(0.99)
    assert r["n_out_of_range"] >= 1                        # 밴드카운트엔 잡힘(운영자 점검용)
    assert sum(r["band_counts"].values()) == 51
    assert "tail_excess" in r


def test_classify_flags_significant_tail_excess():
    """반대로 다수 점이 이상범위를 넘어 **분포가 유의하게 이동**하면 경고/위험."""
    ref = fit_reference_range(np.clip(np.random.default_rng(3).normal(0.2, 0.05, 1000), 0, 1))
    # 200 정상 + 30 점이 abnormal_high 훌쩍 초과 → 유의한 초과
    vals = np.concatenate([np.full(200, 0.2), np.full(30, min(0.99, ref.abnormal_high + 0.1))])
    r = ref.classify(vals)
    assert r["level"] in ("경고", "위험")


def test_empty_raises():
    with pytest.raises(ValueError):
        fit_reference_range([])


def test_regime_mismatch_flags_bad_comparison():
    ref = fit_reference_range([0.1, 0.2, 0.3], source="x")
    ref.regime = {"noise_mm": 10.0, "span_days": 540.0, "n_epochs": 24}
    assert ref.regime_mismatch(noise_mm=10.0, span_days=540.0) is None   # 동일 조건 OK
    assert ref.regime_mismatch(noise_mm=30.0, span_days=540.0)           # 노이즈 3배 → 경고
    assert ref.regime_mismatch(noise_mm=10.0, span_days=200.0)           # 기간 짧음 → 경고


def _run(tmp_path, seed, ref_dict, **kw):
    cfg = PipelineConfig(n_points=60, n_dates=24)
    cfg.fram_reference_range = ref_dict
    with ProjectStore(tmp_path / f"p{seed}.h5", mode="w") as store:
        insar, failing = make_collapse_scenario(store, n_points=60, n_dates=24, seed=seed, **kw)
        pinn = run_pinn(store, insar, cfg)
        fram = run_fram_real(store, insar, pinn, cfg)
        assess = store.read_json_attr("fram", "reference_range")
        cri = store.read_array(fram.CRI_ds)
    return fram, assess, failing, cri


def test_reference_range_integration_severity_escalates(tmp_path):
    """정상범위 통합: 붕괴 침하가 클수록 등급·정상범위밖 점수가 단조 증가."""
    from inframon.fram.reference_range import build_default_reference_range
    values, regime = None, None
    # 실 규모(노이즈 10mm) 건강 코호트로 기준치 적합
    ref = build_default_reference_range(n_bridges=12, seed=0)

    order = {b: i for i, b in enumerate(BANDS)}
    prev_out = -1
    prev_lvl = -1
    for accel in (20.0, 120.0, 400.0):                    # 침하 강도 ↑
        fram, assess, failing, cri = _run(tmp_path, 5, ref.to_dict(),
                                          noise_mm=10.0, accel_mm=accel, seasonal_mm=4.0)
        assert fram.warning.basis == "reference_range"
        assert assess["level"] == fram.warning.level
        # failing 점 판독이 강도에 따라 악화(정상범위밖 개수 비감소, 등급 비감소)
        fassess = ref.classify(cri[failing].max(1))
        assert fassess["n_out_of_range"] >= prev_out
        assert order[fassess["level"]] >= prev_lvl
        prev_out, prev_lvl = fassess["n_out_of_range"], order[fassess["level"]]
    assert prev_out > 0                                    # 강한 붕괴는 정상범위를 벗어난다


def test_fit_from_projects_real_cohort(tmp_path):
    """실측 건강 교량 project.h5 목록의 /fram/CRI 로 현장 정상범위를 적합(regime 기록)."""
    from inframon.fram.reference_range import fit_reference_range_from_projects

    paths = []
    for seed in (1, 2, 3):
        cfg = PipelineConfig(n_points=30, n_dates=24,
                             engines={"cv": "stub", "insar": "stub", "pinn": "real", "fram": "real"})
        out = tmp_path / f"hb{seed}.h5"
        with ProjectStore(out, mode="w") as store:
            insar, _ = make_collapse_scenario(store, n_points=30, n_dates=24, seed=seed,
                                              noise_mm=10.0, accel_mm=0.0)   # 손상 없음=건강
            pinn = run_pinn(store, insar, cfg)
            run_fram_real(store, insar, pinn, cfg)
        paths.append(str(out))
    ref = fit_reference_range_from_projects(paths)
    assert ref.n > 0 and ref.regime["n_bridges"] == 3
    assert ref.regime["noise_mm"] is not None and ref.regime["span_days"] is not None
    assert ref.p50 < ref.p97_5 <= ref.abnormal_high
    assert "field_healthy" in ref.source


def test_fit_from_projects_empty_raises(tmp_path):
    from inframon.fram.reference_range import fit_reference_range_from_projects
    with pytest.raises(ValueError):
        fit_reference_range_from_projects([str(tmp_path / "nope.h5")])


def test_reference_range_absent_keeps_absolute_basis(tmp_path):
    """정상범위 미지정 시 경보 근거는 기존 절대임계(cri)로 불변."""
    cfg = PipelineConfig(n_points=40, n_dates=24)
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        insar, _ = make_collapse_scenario(store, n_points=40, n_dates=24, seed=1)
        pinn = run_pinn(store, insar, cfg)
        fram = run_fram_real(store, insar, pinn, cfg)
    assert fram.warning.basis == "cri"
