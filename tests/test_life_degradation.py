"""잔존수명 통계 코어 — 강건 회귀·신뢰구간·유의성·가속 검정."""
from __future__ import annotations

import numpy as np
import pytest

from inframon.life.degradation import (
    adverse_rate,
    quadratic_acceleration,
    significant,
    theil_sen,
    time_to_limit_quadratic,
)


def _t(m: int, years: float = 4.0) -> np.ndarray:
    return np.linspace(0.0, years, m)


def test_theil_sen_recovers_slope():
    t = _t(40)
    rng = np.random.default_rng(0)
    y = (-2.0 * t)[None, :] + rng.normal(0, 0.05, (6, 40))
    fit = theil_sen(t, y)
    assert np.allclose(fit["slope"], -2.0, atol=0.05)
    assert np.all(fit["lo"] < fit["slope"]) and np.all(fit["slope"] < fit["hi"])


def test_theil_sen_robust_to_outliers():
    """unwrapping error 모사 — 큰 이상치에서 최소자승보다 안정해야 한다."""
    t = _t(40)
    y = (-1.0 * t)[None, :].repeat(1, axis=0).copy()
    y[0, ::7] += 50.0                                   # 이상치 주입
    ts = theil_sen(t, y)["slope"][0]
    ols = np.polyfit(t, y[0], 1)[0]
    assert abs(ts - (-1.0)) < abs(ols - (-1.0))         # Theil–Sen 이 더 가깝다
    assert abs(ts - (-1.0)) < 0.15


def test_significance_censors_flat_noise():
    """추세 없는 잡음 → CI 가 0 을 포함 → 검열. 이게 깨지면 건강한 점에 가짜 수명이 붙는다."""
    rng = np.random.default_rng(3)
    t = _t(30)
    y = rng.normal(0, 1.0, (200, 30))                   # 순수 잡음
    fit = theil_sen(t, y)
    sig = significant(fit["lo"], fit["hi"])
    assert sig.mean() < 0.10                            # 95% CI → 오탐 10% 미만


def test_significance_detects_real_trend():
    rng = np.random.default_rng(4)
    t = _t(30)
    y = (-3.0 * t)[None, :] + rng.normal(0, 0.3, (50, 30))
    fit = theil_sen(t, y)
    assert significant(fit["lo"], fit["hi"]).all()


def test_adverse_rate_is_sign_free():
    t = _t(20)
    y = np.stack([2.0 * t, -2.0 * t])                   # 융기·침하 모두 이상 거동
    rate, hi = adverse_rate(theil_sen(t, y))
    assert np.allclose(rate, 2.0, atol=0.02)
    assert np.all(hi >= rate)


def test_theil_sen_requires_four_epochs():
    with pytest.raises(ValueError, match="4시점"):
        theil_sen(_t(3), np.zeros((2, 3)))


def test_quadratic_acceleration_detects_and_ignores():
    t = _t(30)
    rng = np.random.default_rng(7)
    accel = (-1.0 * t - 0.5 * t ** 2)[None, :] + rng.normal(0, 0.05, (5, 30))
    linear = (-1.0 * t)[None, :] + rng.normal(0, 0.05, (5, 30))
    assert quadratic_acceleration(t, accel)["accel"].all()
    assert not quadratic_acceleration(t, linear)["accel"].any()


def test_quadratic_time_to_limit_is_shorter_than_linear():
    b, c, margin = np.array([1.0]), np.array([0.5]), np.array([10.0])
    tq = time_to_limit_quadratic(b, c, margin)[0]
    assert tq < margin[0] / b[0]                        # 가속하므로 선형보다 빨리 도달
    assert abs(1.0 * tq + 0.5 * tq ** 2 - 10.0) < 1e-6  # 방정식을 실제로 만족


def test_quadratic_time_to_limit_zero_rate_is_inf():
    out = time_to_limit_quadratic(np.array([0.0]), np.array([0.0]), np.array([5.0]))
    assert np.isinf(out[0])
