"""지역 전수 대장 — 공공데이터 한 행 → 종별·기한·관측 가능성."""
from datetime import date

from inframon.legal.registry import bridge_key, build_registry, registry_row


def _row(**kw):
    base = {"교량명": "시험교", "시설물종별등급구분": "2", "도로종류": "일반국도", "시도명": "강원특별자치도",
            "시군구명": "강릉시", "시군구코드": "51150", "상부구조형식": "PSCI거더교",
            "최종안전점검일자": "2025-03-15", "최종안전점검결과": "B", "최종안전점검유형": "정기점검",
            "데이터기준일자": "2025-12-31", "lat": "37.75", "lon": "128.90", "length_m": "160",
            "asc_bursts": "054_114061_IW2", "desc_bursts": "061_129727_IW3"}
    base.update(kw)
    return base


def test_overdue_is_judged_at_data_date_not_today():
    r = registry_row(_row(), today=date(2026, 10, 16))
    # 2025-03 완료 → 반기 1회 → 2025-09-30. 자료 기준일(2025-12-31)에 이미 지났다
    assert r["next_due"] == "2025-09-30" and r["next_due_kind"] == "정기안전점검"
    assert r["overdue_at_data_date"] == ["정기안전점검"]
    r2 = registry_row(_row(최종안전점검일자="2025-10-20"))
    assert r2["overdue_at_data_date"] == [] and r2["due_after_data_date"] == ["정기안전점검"]


def test_official_class_wins_and_mismatch_only_when_lower():
    hi = registry_row(_row(시설물종별등급구분="1"))          # 공식 1종, 계산 2종(경간장 없음) → 모순 아님
    assert hi["facility_class"] == 1 and not hi["facility_mismatch"]
    lo = registry_row(_row(시설물종별등급구분="3"))          # 160 m 인데 3종 → 자료 확인
    assert lo["facility_class"] == 3 and lo["facility_mismatch"]


def test_code_99_has_no_statutory_schedule():
    r = registry_row(_row(시설물종별등급구분="99", length_m="30"))
    assert r["facility_class"] is None and r["legal"]["inspections"] == []
    assert r["next_due"] is None and not r["facility_mismatch"]


def test_observability_tiers_and_compute_state():
    assert registry_row(_row(length_m="160"))["observability"]["tier"] == "expected"
    assert registry_row(_row(length_m="70"))["observability"]["tier"] == "limited"
    assert registry_row(_row(length_m="20"))["observability"]["tier"] == "unlikely"
    r = registry_row(_row(), computed_bursts={"054_114061_IW2"})
    assert r["compute_state"] == "computed" and r["computed_bursts"] == ["054_114061_IW2"]


def test_key_is_stable_and_summary_counts():
    assert bridge_key(_row()) == bridge_key(_row(교량연장="999"))
    assert bridge_key(_row()) != bridge_key(_row(교량명="다른교"))
    reg = build_registry([_row(), _row(교량명="둘", 최종안전점검결과="D")])
    assert reg["n"] == 2 and reg["summary"]["by_grade"] == {"B": 1, "D": 1}
