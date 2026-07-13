"""공공데이터포털 실데이터 프리셋 — 한글 상부구조형식 파싱·레코드→프로파일·CSV 로더."""
from __future__ import annotations

import csv

from inframon.public_data import (parse_structure_ko, bridge_profile_from_record,
                                  describe_datasets, DATASETS, normalize_grade,
                                  load_bridges_csv, nearest_bridge_profile,
                                  design_load_factor)


def test_parse_structure_ko():
    assert parse_structure_ko("PSC박스거더") == ("box_girder", "prestressed_concrete")
    assert parse_structure_ko("강교 판형교") == ("girder", "steel")
    assert parse_structure_ko("RC라멘교") == ("rahmen", "reinforced_concrete")
    assert parse_structure_ko("PSC I형 거더") == ("girder", "prestressed_concrete")
    assert parse_structure_ko("사장교") == ("cable_stayed", None)
    assert parse_structure_ko("") == ("girder", None)


def test_bridge_profile_from_record_psc_box():
    rec = {"교량명": "테스트대교", "상부구조형식": "PSC박스거더",
           "교량길이": "650.5", "교량폭": "12.5", "차로수": "4",
           "위도": "37.32", "경도": "127.10", "준공일자": "20051130",
           "시설물종류": "1종"}
    prof = bridge_profile_from_record(rec)
    assert prof.bridge_type == "box_girder"
    assert prof.material == "prestressed_concrete"       # 강재 아님(실 제원)
    assert prof.length_m == 650.5 and prof.width_m == 12.5
    assert prof.youngs() < 1e11                          # 콘크리트 E
    assert prof.extra["lanes"] == 4.0
    assert prof.source.startswith("data_go_kr")


def test_bridge_profile_lanes_to_width():
    rec = {"상부구조형식": "강박스거더", "교량길이": "300", "차로수": "6"}   # 폭 없음
    prof = bridge_profile_from_record(rec)
    assert prof.bridge_type == "box_girder" and prof.material == "steel"   # 강박스
    assert prof.width_m == round(6 * 3.5 + 1.0, 1)       # 차로수→폭


def test_datasets_and_describe():
    assert "15081953" in DATASETS["national_bridge_standard"]["id"]   # CSV 파일데이터
    assert "15062049" in DATASETS["korex_traffic"]["id"]              # EX 일자별 전국 교통량
    assert "nationalTrafficVolumn" in DATASETS["korex_traffic"]["endpoint"]
    txt = describe_datasets()
    assert "전국교량표준데이터" in txt and "data.go.kr" in txt


def test_normalize_grade_numeric_codes():
    # 실 데이터셋(15081953) 숫자코드
    assert normalize_grade("01") == "1종"
    assert normalize_grade("02") == "2종"
    assert normalize_grade("03") == "3종"
    assert normalize_grade("99") == "기타"       # 제3종 미만 소규모
    # generic 문자열
    assert normalize_grade("제1종시설물") == "1종"
    assert normalize_grade("3종시설물") == "3종"
    assert normalize_grade("기타") == "기타"
    assert normalize_grade("") is None
    assert normalize_grade(None) is None


def test_design_load_factor():
    assert design_load_factor("DB-24") == 1.0
    assert design_load_factor("DB-18") == 0.75
    assert design_load_factor("DB-13.5") == 0.5625
    assert design_load_factor("KL-510") == 1.10
    assert design_load_factor("미상") is None
    assert design_load_factor("") is None
    assert design_load_factor(None) is None


# 전국교량표준데이터(15081953) 실 컬럼명 기준 합성 CSV
_STD_COLS = ["교량명", "상부구조형식", "교량연장", "교량폭", "차로수", "교량높이",
             "교량준공연도", "교량시작점위도", "교량시작점경도",
             "시설물종별등급구분", "설계활하중", "최종안전점검결과"]
_ROWS = [
    ["가교", "강판형교", "45", "8.5", "2", "2.0", "1998", "37.10", "127.00", "03", "DB-18", "B"],
    ["정자대교", "PSC박스거더교", "650.5", "12.5", "4", "3.5", "2005", "37.3634", "127.1090", "01", "DB-24", "A"],
    ["원교", "RC라멘교", "20", "6", "2", "1.2", "2010", "37.50", "127.30", "99", "미상", "C"],
]


def _write_csv(path, encoding="utf-8-sig"):
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.writer(f)
        w.writerow(_STD_COLS)
        w.writerows(_ROWS)
    return str(path)


def test_load_bridges_csv_roundtrip(tmp_path):
    p = _write_csv(tmp_path / "bridges.csv")
    rows = load_bridges_csv(p)
    assert len(rows) == 3
    assert rows[1]["교량명"] == "정자대교"
    assert rows[1]["시설물종별등급구분"] == "01"


def test_load_bridges_csv_cp949(tmp_path):
    p = _write_csv(tmp_path / "bridges_cp949.csv", encoding="cp949")   # 한글 CSV 흔한 인코딩
    rows = load_bridges_csv(p)
    assert rows[1]["교량명"] == "정자대교"


def test_nearest_bridge_profile(tmp_path):
    p = _write_csv(tmp_path / "bridges.csv")
    prof = nearest_bridge_profile(p, 37.3634, 127.1090, max_km=1.0)
    assert prof is not None
    assert prof.name == "정자대교"
    assert prof.bridge_type == "box_girder" and prof.material == "prestressed_concrete"
    assert prof.length_m == 650.5 and prof.width_m == 12.5
    assert prof.extra["match_dist_m"] < 50.0          # 사실상 정확 일치
    assert prof.extra["grade"] == "1종"               # 코드 01 → 1종
    assert prof.extra["design_load"] == "DB-24"
    assert prof.extra["design_load_factor"] == 1.0
    assert prof.extra["inspect_grade"] == "A"
    assert prof.extra["height_m"] == 3.5              # 교량높이는 참고값(단면높이는 형식추론)


def test_nearest_bridge_profile_out_of_range(tmp_path):
    p = _write_csv(tmp_path / "bridges.csv")
    assert nearest_bridge_profile(p, 35.0, 129.0, max_km=1.0) is None   # 부산 — 근처 없음


def test_bridge_profile_grade_field_present():
    # 확인된 표준데이터 등급 컬럼이 프리셋에 존재
    assert "grade" in DATASETS["national_bridge_standard"]["fields"]
    assert "시설물종별등급구분" in DATASETS["national_bridge_standard"]["fields"]["grade"]
