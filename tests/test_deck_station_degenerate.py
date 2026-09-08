"""데크 station 이 **뭉개지는 것**을 잡는다 — 조용히 상수가 되면 열 분리가 죽는다.

실제로 겪은 것: 청양교 산출물의 `/insar/deck_station` 이 48점 전부 106.631 m 였다.
`project_to_polyline` 은 폴리라인 밖 점을 세그먼트 끝(tt=0/1)으로 자르므로, 데크선이
점군과 다른 곳에 있으면 **모든 점이 같은 station** 을 받고도 오류 없이 성공한다.

그 station 이 `l_from_fixed`(고정단 거리)가 된다. 상수면 공간 기울기가 없어 열팽창을
분리할 수 없고, 열 성분이 0 으로 죽으면서 모든 변위가 침하·하중·이상으로 몰린다.
청양교에서 변형률 −0.32(콘크리트 파괴 0.003의 100배)가 그렇게 나왔다.
"""

from __future__ import annotations

import numpy as np

from inframon.insar.deck_geometry import (MIN_STATION_SPREAD_FRAC, deck_station,
                                          deck_station_checked, project_to_polyline)

# 동서 100 m 데크(위도 36.45)와 그 위에 흩어진 점들
DECK = [(36.45, 126.80), (36.45, 126.801119)]


def _points(n=40, seed=0, lon0=126.80, span=0.001119):
    rng = np.random.default_rng(seed)
    t = rng.uniform(0.0, 1.0, n)
    return np.column_stack([lon0 + t * span,
                            36.45 + rng.normal(0.0, 5.0 / 111_000.0, n)])


def test_맞는_데크선이면_station_이_퍼진다():
    st, meta = deck_station_checked(_points(), DECK)
    assert meta["source"] == "polyline"
    assert meta["degenerate"] is False
    assert np.ptp(st) > 80.0
    assert len(np.unique(np.round(st, 3))) > 10


def test_엉뚱한_데크선이면_뭉개짐을_잡아낸다():
    """데크선이 점군에서 멀면 모든 점이 폴리라인 끝으로 잘려 station 이 상수가 된다."""
    pts = _points()
    far = [(36.50, 126.90), (36.50, 126.901119)]      # 점군에서 ~9 km
    raw, _ = project_to_polyline(pts, far)
    assert np.ptp(raw) < 1e-6, "전제: 원 투영은 상수를 낸다"

    st, meta = deck_station_checked(pts, far)
    assert meta["degenerate"] is True
    assert meta["source"] == "principal_curve"
    assert "뭉갰다" in meta["reason"]
    assert np.ptp(st) > 50.0, "대체값은 점군이 뻗은 길이를 담아야 한다"


def test_뭉개진_station_은_상수가_아니게_대체된다():
    """상수 station 이 그대로 나가면 l_from_fixed 가 상수가 되어 열 분리가 죽는다."""
    far = [(36.50, 126.90), (36.50, 126.901119)]
    st = deck_station(_points(), far)
    assert len(np.unique(np.round(st, 3))) > 5


def test_데크선이_없으면_주곡선으로_사유를_남긴다():
    st, meta = deck_station_checked(_points(), None)
    assert meta["source"] == "principal_curve"
    assert "없다" in meta["reason"]
    assert np.ptp(st) > 50.0


def test_판정_기준을_meta_로_확인할_수_있다():
    """왜 통과/차단했는지 산출물만 보고 재확인할 수 있어야 한다."""
    _, meta = deck_station_checked(_points(n=60, seed=3), DECK)
    assert meta["station_span_m"] >= meta["point_span_m"] * MIN_STATION_SPREAD_FRAC
    assert not meta["degenerate"]
    assert meta["offset_median_m"] < 30.0        # 점이 데크선 근처에 있다


def test_점이_적어도_죽지_않는다():
    pts = np.array([[126.8005, 36.4500], [126.8006, 36.4501]])
    st, meta = deck_station_checked(pts, DECK)
    assert len(st) == 2
    assert "source" in meta
