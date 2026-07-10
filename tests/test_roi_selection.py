"""도심지 가중 ROI 선정 — built-up 파싱·후보 밀도·교량 포함(네트워크 격리)."""

from __future__ import annotations

import pytest

from inframon.insar import roi_selection as rs
from inframon.insar.roi_selection import RoiResult, fetch_builtup, select_roi

BLAT, BLON = 37.3219, 127.1083


def test_fetch_builtup_parses_center():
    data = {"elements": [
        {"type": "way", "center": {"lat": 37.32, "lon": 127.10}},
        {"type": "way", "center": {"lat": 37.33, "lon": 127.11}},
        {"type": "way"},                       # center 없음 → 제외
    ]}
    pts = fetch_builtup(BLAT, BLON, 3000, query_fn=lambda ql: data)
    assert len(pts) == 2
    assert pts[0] == (127.10, 37.32)


def test_select_roi_contains_bridge_and_picks_dense():
    # 교량 북동쪽에 조밀한 건물 클러스터, 남서쪽은 희소
    import numpy as np
    rng = np.random.default_rng(0)
    dense = [(BLON + 0.01 + rng.normal(0, 0.002), BLAT + 0.01 + rng.normal(0, 0.002))
             for _ in range(400)]
    sparse = [(BLON - 0.02 + rng.normal(0, 0.005), BLAT - 0.02 + rng.normal(0, 0.005))
              for _ in range(10)]
    roi = select_roi(BLAT, BLON, sizes_km=(2.0, 3.0, 4.0), builtup=dense + sparse, grid=7)
    assert isinstance(roi, RoiResult)
    assert roi.contains_bridge is True                  # 교량 포함
    w, s, e, n = roi.bbox
    # 조밀 클러스터(BLON+0.01,BLAT+0.01) 쪽으로 ROI 가 치우침
    assert (w + e) / 2 > BLON and (s + n) / 2 > BLAT
    assert roi.n_buildings > 100                        # 도심 다수 포함


def test_select_roi_wkt():
    pts = [(BLON, BLAT)] * 5
    roi = select_roi(BLAT, BLON, sizes_km=(2.0,), builtup=pts, grid=3)
    assert roi.wkt().startswith("POLYGON((")
    assert roi.size_km == 2.0


def test_select_roi_empty_builtup_returns_zero():
    roi = select_roi(BLAT, BLON, sizes_km=(2.0,), builtup=[], grid=3)
    assert roi.n_buildings == 0 and roi.contains_bridge is True   # 빈 도심 → 0점 ROI


def test_select_roi_no_sizes_raises():
    with pytest.raises(ValueError):
        select_roi(BLAT, BLON, sizes_km=(), builtup=[(BLON, BLAT)], grid=3)
