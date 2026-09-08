"""처리 오프셋(B) — 지면 점 vs OSM 도로선. 대칭이면 0, 밀려 있으면 그 벡터."""

from __future__ import annotations

import numpy as np

from inframon.insar.ground_offset import MIN_POINTS, apply, estimate

LAT, LON = 37.3685, 127.1090
K = 111_320.0 * np.cos(np.radians(LAT))


def _grid_roads():
    """동서 3개 · 남북 3개 도로(각 1 km) — 격자."""
    roads = []
    for off in (-200, 0, 200):
        roads.append([(LAT + off / 111_320, LON - 500 / K), (LAT + off / 111_320, LON + 500 / K)])
        roads.append([(LAT - 500 / 111_320, LON + off / K), (LAT + 500 / 111_320, LON + off / K)])
    return roads


def _points_on_roads(n=600, seed=0, shift=(0.0, 0.0), jitter=6.0):
    """도로 위에 대칭으로 흩어진 지면 점 + 전체 shift(m)."""
    rng = np.random.default_rng(seed)
    pts = []
    for _ in range(n):
        if rng.random() < .5:
            y = rng.choice([-200, 0, 200]) + rng.normal(0, jitter); x = rng.uniform(-450, 450)
        else:
            x = rng.choice([-200, 0, 200]) + rng.normal(0, jitter); y = rng.uniform(-450, 450)
        pts.append((x + shift[0], y + shift[1]))
    P = np.array(pts)
    return np.column_stack([LON + P[:, 0] / K, LAT + P[:, 1] / 111_320])


def test_대칭이면_오프셋은_0():
    ll = _points_on_roads()
    off = estimate(ll, _grid_roads(), center_lat=LAT, center_lon=LON)
    assert off.n_points >= MIN_POINTS
    assert not off.significant and off.norm_m < 3.0


def test_전체가_밀려_있으면_그_벡터를_되찾는다():
    ll = _points_on_roads(shift=(8.0, -6.0))
    off = estimate(ll, _grid_roads(), center_lat=LAT, center_lon=LON)
    assert off.significant
    assert abs(off.dx_m - 8.0) < 2.5 and abs(off.dy_m + 6.0) < 2.5


def test_apply_는_유의할_때만_되돌린다():
    ll = _points_on_roads(shift=(8.0, -6.0))
    off = estimate(ll, _grid_roads(), center_lat=LAT, center_lon=LON)
    back = apply(ll, off, center_lat=LAT)
    off2 = estimate(back, _grid_roads(), center_lat=LAT, center_lon=LON)
    assert off2.norm_m < 3.0
    ll0 = _points_on_roads()
    off0 = estimate(ll0, _grid_roads(), center_lat=LAT, center_lon=LON)
    assert np.array_equal(apply(ll0, off0, center_lat=LAT), ll0)


def test_점이_적으면_추정하지_않는다():
    ll = _points_on_roads(n=40)
    off = estimate(ll, _grid_roads(), center_lat=LAT, center_lon=LON)
    assert not off.significant and "부족" in off.meta.get("reason", "")
