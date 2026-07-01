"""데이터 가용성 advisor — 모드 자동 추천 검증."""
from __future__ import annotations

from dataclasses import dataclass

from inframon.insar.availability import assess_availability


@dataclass
class G:
    flight_direction: str
    n_scenes: int
    first_date: str
    last_date: str
    path: int = 0
    frame: int = 0


def test_asc_desc_when_both_full_and_overlap():
    g = [G("ASCENDING", 200, "20170101", "20250101"),
         G("DESCENDING", 160, "20180101", "20240101")]
    assert assess_availability(g)["mode"] == "asc+desc"


def test_union_when_no_time_overlap():
    g = [G("ASCENDING", 200, "20170101", "20250101"),
         G("DESCENDING", 30, "20150101", "20151231")]     # 2015 만 → 안 겹침
    r = assess_availability(g)
    assert r["mode"] == "union" and r["overlap_days"] <= 0


def test_single_when_only_one_track():
    g = [G("ASCENDING", 200, "20170101", "20250101")]
    assert assess_availability(g)["mode"] == "single"


def test_accumulate_when_too_few():
    g = [G("ASCENDING", 5, "20240101", "20240601")]
    assert assess_availability(g)["mode"] == "accumulate"


def test_none_when_empty():
    assert assess_availability([])["mode"] == "none"
