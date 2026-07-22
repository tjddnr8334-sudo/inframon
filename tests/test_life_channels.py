"""사용성 채널 + 공간 응집 집계 — 검열·군집 규칙이 이 기능의 핵심이다."""
from __future__ import annotations

import numpy as np

from inframon.life.aggregate import cohesive_min
from inframon.life.channels import (
    SUB_ABSOLUTE,
    SUB_CENSORED,
    SUB_DIFFERENTIAL,
    serviceability,
)


def _grid(n_side: int = 8, step: float = 10.0) -> np.ndarray:
    """n_side × n_side 격자[m] — 이웃 반경이 유도되도록 균일 배치."""
    g = np.arange(n_side) * step
    xx, yy = np.meshgrid(g, g)
    return np.stack([xx.ravel(), yy.ravel()], axis=1).astype(float)


def _t(m: int = 30, years: float = 4.0) -> np.ndarray:
    return np.linspace(0.0, years, m)


def test_healthy_bridge_is_fully_censored():
    """열화 없는 교량 → 전부 검열. 깨지면 모든 건강 교량에 가짜 수명이 붙는다."""
    xy = _grid()
    t = _t()
    rng = np.random.default_rng(1)
    disp = rng.normal(0, 0.8, (xy.shape[0], t.shape[0]))
    res = serviceability(t, disp, xy=xy, point_limit_mm=np.full(xy.shape[0], 25.0),
                         angular_limit=1 / 500)
    assert res["censored"].mean() > 0.9
    assert (res["sublimit"] == SUB_CENSORED).mean() > 0.9
    rsl, meta = cohesive_min(res["rsl"], xy, radius_m=res["meta"]["neighbor_radius_m"])
    assert rsl is None and "reason" in meta


def test_uniform_settlement_absolute_limit_governs():
    """교량 전체가 균일하게 연 2mm 침하 → 속도차가 없어 절대 한계가 지배.

    한계 25mm / 2mm·yr⁻¹ → 잔존수명 ≈ 12.5년.
    """
    xy = _grid()
    t = _t()
    n = xy.shape[0]
    rng = np.random.default_rng(2)
    disp = (-2.0 * t)[None, :] + rng.normal(0, 0.05, (n, t.shape[0]))
    res = serviceability(t, disp, xy=xy, point_limit_mm=np.full(n, 25.0),
                         angular_limit=1 / 500)
    assert np.allclose(res["rate"], 2.0, atol=0.05)
    assert (res["sublimit"] == SUB_ABSOLUTE).all()      # 균일 → 부등침하 없음
    rsl, _ = cohesive_min(res["rsl"], xy, radius_m=res["meta"]["neighbor_radius_m"])
    assert rsl is not None and abs(rsl - 12.5) < 1.0
    lo, _ = cohesive_min(res["rsl_lower"], xy, radius_m=res["meta"]["neighbor_radius_m"])
    assert lo <= rsl                                    # 하한이 항상 더 보수적


def test_settling_zone_boundary_is_governed_by_differential():
    """정지 지반에 접한 침하 구역 — 경계에서 부등침하가 절대 침하보다 먼저 걸린다.

    2mm/yr 속도차가 10m 간격이면 각변위 2e-4 rad/yr → 1/500 까지 10년으로,
    절대 한계(25mm/2mm·yr⁻¹ = 12.5년)보다 이르다. 이것이 물리적으로 옳다.
    """
    xy = _grid()
    t = _t()
    n = xy.shape[0]
    rng = np.random.default_rng(2)
    disp = rng.normal(0, 0.05, (n, t.shape[0]))
    zone = np.where((xy[:, 0] <= 20) & (xy[:, 1] <= 20))[0]         # 인접 9점
    disp[zone] += (-2.0 * t)[None, :]
    res = serviceability(t, disp, xy=xy, point_limit_mm=np.full(n, 25.0),
                         angular_limit=1 / 500)
    assert np.allclose(res["rate"][zone], 2.0, atol=0.05)
    assert (res["sublimit"][zone] == SUB_DIFFERENTIAL).any()
    assert res["censored"][zone].sum() == 0                          # 침하 구역은 전부 검출
    rsl, _ = cohesive_min(res["rsl"], xy, radius_m=res["meta"]["neighbor_radius_m"])
    assert rsl is not None and rsl < 12.5                            # 절대 한계보다 이르다


def test_isolated_fast_point_is_rejected_by_cohesion():
    """고립된 단일 급속 침하점은 교량 대표값을 지배하지 못한다(노이즈 배제)."""
    xy = _grid()
    t = _t()
    n = xy.shape[0]
    rng = np.random.default_rng(3)
    disp = rng.normal(0, 0.05, (n, t.shape[0]))
    disp[0] += (-20.0 * t)                              # 한 점만 극단
    res = serviceability(t, disp, xy=xy, point_limit_mm=np.full(n, 25.0),
                         angular_limit=1 / 500)
    assert np.isfinite(res["rsl"][0])                   # 점 단위로는 잡힌다
    rsl, meta = cohesive_min(res["rsl"], xy, radius_m=res["meta"]["neighbor_radius_m"],
                             min_cluster=3)
    assert rsl is None                                  # 교량 대표값으로는 승격 안 됨
    assert "군집 없음" in meta["reason"] or "최소군집" in meta["reason"]


def test_differential_settlement_can_govern():
    """절대 침하는 여유가 큰데 이웃 간 속도차가 커서 부등침하가 먼저 걸리는 경우."""
    xy = _grid(n_side=6, step=5.0)
    t = _t()
    n = xy.shape[0]
    rng = np.random.default_rng(4)
    disp = rng.normal(0, 0.02, (n, t.shape[0]))
    left = xy[:, 0] <= 5.0
    disp[left] += (-3.0 * t)[None, :]                   # 좌측만 침하 → 경계에서 큰 속도차
    res = serviceability(t, disp, xy=xy, point_limit_mm=np.full(n, 2000.0),  # 절대는 사실상 무한
                         angular_limit=1 / 500)
    assert (res["sublimit"] == SUB_DIFFERENTIAL).any()
    gov = res["sublimit"] == SUB_DIFFERENTIAL
    assert np.isfinite(res["rsl"][gov]).all()


def test_over_limit_points_have_zero_life():
    xy = _grid(n_side=4)
    t = _t()
    n = xy.shape[0]
    disp = (-1.0 * t)[None, :] * np.ones((n, 1))
    res = serviceability(t, disp, xy=xy, point_limit_mm=np.full(n, 25.0),
                         angular_limit=1 / 500, consumed_mm=30.0)   # 이미 30mm 소진
    assert (res["rsl"] == 0.0).all()
    assert res["meta"]["n_over_limit"] == n


def test_acceleration_shortens_life():
    """가속 열화가 감지되면 선형 외삽보다 짧은(보수적) 값이 나와야 한다."""
    xy = _grid(n_side=5)
    t = _t()
    n = xy.shape[0]
    rng = np.random.default_rng(5)
    lin = (-1.0 * t)[None, :] + rng.normal(0, 0.02, (n, t.shape[0]))
    acc = (-1.0 * t - 0.4 * t ** 2)[None, :] + rng.normal(0, 0.02, (n, t.shape[0]))
    lim = np.full(n, 25.0)
    r_lin = serviceability(t, lin, xy=xy, point_limit_mm=lim, angular_limit=1 / 500)
    r_acc = serviceability(t, acc, xy=xy, point_limit_mm=lim, angular_limit=1 / 500)
    assert r_acc["accel"].all() and not r_lin["accel"].any()
    assert np.median(r_acc["rsl"]) < np.median(r_lin["rsl"])


def test_cohesive_min_needs_connected_points_not_just_count():
    """개수만 채우고 서로 떨어져 있으면 군집이 아니다."""
    xy = np.array([[0.0, 0.0], [1000.0, 0.0], [2000.0, 0.0], [3000.0, 0.0]])
    vals = np.array([5.0, 6.0, 7.0, 8.0])
    rsl, meta = cohesive_min(vals, xy, radius_m=10.0, min_cluster=3)
    assert rsl is None
    rsl2, _ = cohesive_min(vals, xy, radius_m=5000.0, min_cluster=3)
    assert rsl2 == 7.0                                  # 연결되면 3번째 점에서 성립
