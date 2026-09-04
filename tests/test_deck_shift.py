"""교량마다 다른 쉬프트 기하 — 고정값으로는 안 된다.

InSAR 지오코딩 쉬프트(δh/tanθ)가 문제가 되는 정도는 **교량 방위와 LOS 의 관계**에 달렸다.
직교면 점이 다리를 벗어나고(보정 필요·검증 가능), 나란하면 다리 위에서 미끄러질 뿐이다
(무해·검증 불가). 청양교가 후자였다 — 이 성질을 고정한다.
"""

from __future__ import annotations

import math

import pytest

from inframon.insar.deck_shift import (MAX_BUFFER_M, MIN_BUFFER_M, DeckShift,
                                          deck_azimuth_deg, for_bridge, shift_geometry)

TRACK = {"heading_deg": -13.1, "incidence_deg": 38.94, "dh_m": 10.0, "width_m": 22.0}


def test_azimuth_from_deck_line():
    # 동서로 뻗은 데크 → 방위 ~90°
    az = deck_azimuth_deg([(36.4506, 126.8068), (36.4507, 126.8078)])
    assert 80.0 < az < 100.0


def test_perpendicular_deck_shows_shift_across():
    """데크가 LOS 와 직교하면 쉬프트가 통째로 횡방향 — 점이 다리를 벗어난다."""
    g = shift_geometry(deck_azimuth=0.0, **TRACK)        # 남북 데크 vs 서향 LOS
    assert g.observability > 0.9 and g.shift_observable
    assert abs(g.cross_m) > abs(g.along_m)
    assert g.buffer_m > g.half_width_m + abs(g.cross_m) - 1e-6


def test_parallel_deck_slides_along_and_is_not_verifiable():
    """데크가 LOS 와 나란하면 종방향으로 미끄러진다 — 다리를 벗어나지 않고 검증도 불가."""
    g = shift_geometry(deck_azimuth=83.0, **TRACK)      # 청양교 실측 방위
    assert g.observability < 0.4 and not g.shift_observable
    assert abs(g.along_m) > abs(g.cross_m) * 5
    assert "종방향" in g.describe()


def test_shift_magnitude_matches_physics():
    """쉬프트 크기 = δh/tanθ — 입사각이 클수록 작다."""
    a = shift_geometry(deck_azimuth=0.0, heading_deg=-13.1, incidence_deg=30.0,
                       dh_m=10.0, width_m=20.0)
    b = shift_geometry(deck_azimuth=0.0, heading_deg=-13.1, incidence_deg=45.0,
                       dh_m=10.0, width_m=20.0)
    assert a.shift_m == pytest.approx(10.0 / math.tan(math.radians(30.0)), rel=1e-6)
    assert b.shift_m < a.shift_m


def test_buffer_follows_width_and_shift_not_a_fixed_number():
    """버퍼는 교량마다 달라야 한다 — 넓은 다리는 넓게, 좁은 다리는 좁게."""
    narrow = shift_geometry(deck_azimuth=83.0, heading_deg=-13.1, incidence_deg=38.9,
                            dh_m=5.0, width_m=8.0)
    wide = shift_geometry(deck_azimuth=83.0, heading_deg=-13.1, incidence_deg=38.9,
                          dh_m=5.0, width_m=30.0)
    assert narrow.buffer_m < wide.buffer_m
    assert MIN_BUFFER_M <= narrow.buffer_m <= MAX_BUFFER_M


def test_taller_bridge_needs_bigger_buffer_when_observable():
    """형하고가 높을수록 쉬프트가 크다 — 직교 교량에서는 버퍼도 커진다."""
    low = shift_geometry(deck_azimuth=0.0, heading_deg=-13.1, incidence_deg=38.9,
                         dh_m=3.0, width_m=20.0)
    high = shift_geometry(deck_azimuth=0.0, heading_deg=-13.1, incidence_deg=38.9,
                          dh_m=20.0, width_m=20.0)
    assert high.buffer_m > low.buffer_m


def test_impossible_incidence_is_refused():
    """비물리 입사각은 δh/tanθ 를 폭발시킨다 — 조용히 큰 값을 내지 않는다."""
    with pytest.raises(ValueError, match="입사각"):
        shift_geometry(deck_azimuth=0.0, heading_deg=0.0, incidence_deg=0.0, dh_m=10.0)


def test_for_bridge_end_to_end():
    g = for_bridge([(36.45061, 126.80684), (36.45070, 126.80780)],
                   heading_deg=-13.1, incidence_deg=38.94, dh_m=10.0, width_m=22.0)
    assert isinstance(g, DeckShift)
    assert not g.shift_observable                    # 청양교는 LOS 와 나란
    assert 15.0 < g.buffer_m < 25.0                  # 반폭 11 + |횡 1.4| + 여유 7
    assert g.as_dict()["observability"] < 0.2
