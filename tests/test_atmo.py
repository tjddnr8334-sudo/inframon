"""InSAR 정확도 보정(atmo) — 기준점·온도회귀·고도상관 대기보정 검증."""
from __future__ import annotations

import numpy as np

from inframon.insar.atmo import (
    REF_MIN_COHERENCE,
    correct_los_field,
    height_correlated_correction,
    most_stable_index,
    reference_correction,
    select_reference_point,
    temporal_decompose,
)


def test_reference_correction_zeros_ref():
    los = np.arange(12.0).reshape(3, 4)
    out = reference_correction(los, 1)
    assert np.allclose(out[1], 0.0)                     # 기준점은 0
    assert out.shape == los.shape


def test_most_stable_index_prefers_low_variance():
    rng = np.random.default_rng(0)
    los = rng.normal(0, 5, (10, 20))
    los[3] = np.linspace(0, 0.1, 20)                    # 거의 안 변하는 점
    assert most_stable_index(los) == 3


def test_default_ref_coherence_is_098():
    assert REF_MIN_COHERENCE == 0.98


def test_select_reference_point_requires_098():
    rng = np.random.default_rng(1)
    los = rng.normal(0, 3, (6, 20))
    los[4] = np.linspace(0, 0.05, 20)                   # 초안정 점
    coh = np.array([0.6, 0.7, 0.95, 0.97, 0.99, 0.90])  # 0.98 넘는 건 idx4(0.99)
    rp = select_reference_point(los, coh)               # min_coh=0.98 기본
    assert rp["meets_threshold"] is True
    assert rp["index"] == 4 and rp["coherence"] == 0.99
    assert rp["n_candidates"] == 1


def test_select_reference_point_lowest_std_among_eligible():
    # 0.98 이상 후보 여럿 → 시간변동 최소 선택
    los = np.zeros((4, 10))
    los[0] = np.linspace(0, 10, 10)                     # 변동 큼
    los[2] = np.linspace(0, 0.1, 10)                    # 변동 작음(선택)
    coh = np.array([0.99, 0.5, 0.985, 0.5])
    rp = select_reference_point(los, coh)
    assert rp["meets_threshold"] is True and rp["index"] == 2   # 0.985·저변동


def test_select_reference_point_fallback_when_none_meet():
    los = np.random.default_rng(2).normal(0, 1, (5, 10))
    coh = np.array([0.5, 0.6, 0.9, 0.7, 0.8])           # 아무도 0.98 미달
    rp = select_reference_point(los, coh)
    assert rp["meets_threshold"] is False
    assert rp["index"] == 2 and rp["coherence"] == 0.9  # 최고 coherence 폴백
    assert rp["n_candidates"] == 0


def test_find_reference_point_empty_pairs():
    from inframon.insar.snap_backend import find_reference_point
    assert find_reference_point([]) is None            # 간섭도 없음 → None


def test_temporal_decompose_recovers_velocity_and_thermal():
    rng = np.random.default_rng(1)
    M = 30
    days = np.arange(M) * 24.0
    t = days / 365.25
    T = 15 + 12 * np.sin(2 * np.pi * t)
    los = (-6.0 * t)[None, :] + 0.4 * (T - T.mean())[None, :] + rng.normal(0, 0.15, (40, M))
    r = temporal_decompose(los, days, T)
    assert abs(float(np.mean(r["velocity_mm_yr"])) - (-6.0)) < 0.3       # 속도 복원
    assert abs(float(np.mean(r["thermal_coef"])) - 0.4) < 0.1           # 열계수 복원
    assert r["used_temperature"] is True
    assert r["deformation"].shape == los.shape


def test_temporal_decompose_without_temperature():
    days = np.arange(10) * 30.0
    los = np.outer(np.ones(5), -2.0 * days / 365.25)
    r = temporal_decompose(los, days, None)
    assert not r["used_temperature"]
    assert np.allclose(r["velocity_mm_yr"], -2.0, atol=1e-6)


def test_height_correlated_correction_removes_stratified():
    rng = np.random.default_rng(2)
    h = np.linspace(0, 100, 50)
    los = (0.05 * h)[:, None] * np.ones((1, 8)) + rng.normal(0, 0.01, (50, 8))  # 고도상관
    out = height_correlated_correction(los, h)
    assert out["corrected"].shape == los.shape
    assert np.abs(out["corrected"]).mean() < np.abs(los).mean()          # 상관성분 감소
    assert np.allclose(out["slope_mm_per_m"], 0.05, atol=0.02)


def test_correct_los_field_reduces_common_mode():
    # 공통 대기 램프(전 점 동일) + 점별 안정 → 기준점 정합이 공통성분 제거
    rng = np.random.default_rng(5)
    M = 24
    common = rng.normal(0, 4, M)                         # 전 점 공통 대기(시간변동 큼)
    los = np.tile(common, (12, 1)) + rng.normal(0, 0.1, (12, M))
    coh = np.full(12, 0.99)
    res = correct_los_field(los, coherence=coh, height=None)
    assert "reference" in res["meta"]["applied"]
    assert res["meta"]["temporal_std_after_mm"] < res["meta"]["temporal_std_before_mm"]
    assert res["meta"]["temporal_std_reduction_pct"] > 50    # 공통성분 대부분 제거
    assert res["corrected"].shape == los.shape


def test_correct_los_field_applies_height_when_spread():
    rng = np.random.default_rng(6)
    h = np.linspace(0, 80, 40)
    los = (0.03 * h)[:, None] * np.ones((1, 10)) + rng.normal(0, 0.05, (40, 10))
    res = correct_los_field(los, coherence=np.full(40, 0.99), height=h)
    assert "height_correlated" in res["meta"]["applied"]
    assert res["meta"]["height_spread_m"] == 80.0


def test_correct_los_field_skips_height_when_flat():
    los = np.random.default_rng(7).normal(0, 1, (8, 10))
    res = correct_los_field(los, coherence=np.full(8, 0.99), height=np.zeros(8))
    assert "height_correlated" not in res["meta"]["applied"]
    assert "height_correlated_skipped" in res["meta"]


def test_correct_los_field_disabled_steps_noop():
    los = np.random.default_rng(8).normal(0, 1, (6, 10)).astype(np.float32)
    res = correct_los_field(los, reference=False, height_corr=False)
    assert res["meta"]["applied"] == []
    assert np.allclose(res["corrected"], los)
