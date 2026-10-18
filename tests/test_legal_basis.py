"""법정 근거표 — 시행령 별표 1(종별)·별표 3(점검 주기)을 규칙 파일대로 적용하는지."""
from datetime import date

import pytest

pytest.importorskip("yaml")

from inframon.legal import build_legal_table, classify_facility, inspection_schedule, load_rules


def test_rules_file_has_sources_for_every_grade():
    r = load_rules()
    assert [g["등급"] for g in r["안전등급"]] == ["A", "B", "C", "D", "E"]
    assert all(g["출처"] and g["확인"] for g in r["안전등급"])


@pytest.mark.parametrize("kw,expect", [
    (dict(length_m=1160, max_span_m=120, n_spans=21, bridge_type="truss"), 1),   # 형식·연장·경간
    (dict(length_m=300, max_span_m=60, n_spans=5, bridge_type="girder"), 1),     # 다경간 50m↑
    (dict(length_m=60, max_span_m=60, n_spans=1, bridge_type="girder"), 2),      # 한 경간 50m↑ 는 2종
    (dict(length_m=108, max_span_m=27, n_spans=4, bridge_type="girder"), 2),     # 연장 100m↑
    (dict(length_m=40, max_span_m=20, n_spans=2, bridge_type="girder"), None),   # 3종은 지정·고시
])
def test_classify_facility(kw, expect):
    assert classify_facility(**kw)["class"] == expect


def test_official_class_wins_and_mismatch_flagged():
    fc = classify_facility(length_m=108, max_span_m=27, n_spans=4, official="1종")
    assert fc["class"] == 1 and fc["computed"] == 2 and fc["mismatch"]


def test_schedule_month_end_rule_and_overdue():
    s = {i["kind"]: i for i in inspection_schedule(
        facility_class=1, grade="C", today=date(2026, 10, 16),
        last={"정밀안전점검": "2024-11-20", "정밀안전진단": "2021-06-30"})}
    # 비고 8: 완료한 달의 말일 + 주기
    assert s["정밀안전점검"]["next_due"] == "2026-11-30" and not s["정밀안전점검"]["overdue"]
    assert s["정밀안전진단"]["next_due"] == "2026-06-30" and s["정밀안전진단"]["overdue"]


def test_de_grade_regular_inspection_uses_season_windows():
    s = {i["kind"]: i for i in inspection_schedule(
        facility_class=2, grade="D", today=date(2026, 4, 1), last={"정기안전점검": "2026-03-10"})}
    assert s["정기안전점검"]["next_due"] == "2026-06-30"          # 우기 전(5·6월)
    assert s["정밀안전진단"]["applicable"] is True                 # D등급 2종은 진단 대상


def test_no_dates_no_guess():
    s = inspection_schedule(facility_class=2, grade=None, today=date(2026, 10, 16))
    assert all(i["next_due"] is None for i in s)


def test_table_never_claims_legal_threshold_for_insar_values():
    t = build_legal_table({"length_m": 500, "n_spans": 10, "max_span_m": 50},
                          {"cri_max": 0.9, "warning_level": 3, "velocity_maxabs_mm_yr": 10.0})
    insar_rows = [f for f in t["findings"] if "CRI" in f["item"] or "변위속도" in f["item"]]
    assert insar_rows and not any(f["has_legal_threshold"] for f in insar_rows)
    assert "대체하지 않는다" in t["notice"]
    import json
    json.dumps(t, ensure_ascii=False)          # 플랫폼으로 보낼 수 있어야 한다
