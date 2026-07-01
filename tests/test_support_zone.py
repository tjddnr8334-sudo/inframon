"""지지부 ZONE — 교대/교각 위치 산정 + buffer 내 점 식별 검증."""
from __future__ import annotations

import numpy as np

from inframon.insar.support_zone import support_positions, support_velocity, support_zone


def test_support_positions_abutments_and_piers():
    nodes = [(37.3685, 127.1084), (37.3685, 127.1096)]      # 동서 방향 다리
    pos = support_positions(nodes, n_piers=3)
    kinds = [k for _, _, k in pos]
    assert kinds.count("abutment") == 2
    assert kinds.count("pier") == 3
    # 교각은 두 교대 사이에 위치
    lons = [lo for _, lo, k in pos if k == "pier"]
    assert all(127.1084 < lo < 127.1096 for lo in lons)


def test_support_zone_counts_points_near_supports():
    nodes = [(37.3685, 127.1084), (37.3685, 127.1096)]
    mid = (37.3685, 127.1090)
    # 중앙 교각 근처 5점 + 멀리 5점
    near = np.array([[mid[1] + dx, mid[0]] for dx in np.linspace(-1e-4, 1e-4, 5)])
    far = np.array([[127.12 + dx, 37.40] for dx in np.linspace(0, 1e-3, 5)])
    ll = np.vstack([near, far])
    r = support_zone(ll, nodes, n_piers=3, buffer_m=30.0)
    assert r["n_support_points"] >= 5          # 중앙 근처 점은 지지부로 잡힘
    assert r["mask"][:5].all() and not r["mask"][5:].any()
    assert len(r["supports"]) == 5             # 교대2 + 교각3


def test_support_velocity_summary():
    days = np.arange(20) * 30.0
    los = np.outer(np.ones(6), -3.0 * days / 365.25)       # 6점 침하 -3mm/yr
    mask = np.array([True] * 6)
    v = support_velocity(los, days, mask)
    assert v["n"] == 6
    assert abs(v["mean_mm_yr"] - (-3.0)) < 1e-6
