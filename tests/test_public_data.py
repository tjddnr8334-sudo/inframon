"""공공데이터포털 실데이터 프리셋 — 한글 상부구조형식 파싱·레코드→프로파일·CSV 로더."""
from __future__ import annotations

import csv

from inframon.public_data import (parse_structure_ko, bridge_profile_from_record,
                                  describe_datasets, DATASETS, normalize_grade,
                                  load_bridges_csv, nearest_bridge_profile,
                                  design_load_factor, search_bridges_by_name, find_bridge_csv)


def test_parse_structure_ko():
    assert parse_structure_ko("PSC박스거더") == ("box_girder", "prestressed_concrete")
    assert parse_structure_ko("강교 판형교") == ("girder", "steel")
    assert parse_structure_ko("RC라멘교") == ("rahmen", "reinforced_concrete")
    # PSC I 거더는 프리스트레스 축압축이 지배식에 들어가므로 일반 거더와 다른 형식이다
    # (실 데이터 8,548건 — 이전에는 전부 girder 로 뭉뚱그려졌다).
    assert parse_structure_ko("PSC I형 거더") == ("psc_girder", "prestressed_concrete")
    assert parse_structure_ko("PSCI거더교") == ("psc_girder", "prestressed_concrete")
    assert parse_structure_ko("RC슬래브교") == ("slab", "reinforced_concrete")
    assert parse_structure_ko("RC중공슬래브교") == ("slab", "reinforced_concrete")
    assert parse_structure_ko("프리플렉스거더교") == ("psc_girder", None)
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
             "교량준공연도", "교량시작점위도", "교량시작점경도", "교량종료점위도", "교량종료점경도",
             "시설물종별등급구분", "설계활하중", "최종안전점검결과"]
_ROWS = [
    ["가교", "강판형교", "45", "8.5", "2", "2.0", "1998", "37.10", "127.00", "37.101", "127.001", "03", "DB-18", "B"],
    ["정자대교", "PSC박스거더교", "650.5", "12.5", "4", "3.5", "2005", "37.3634", "127.1090", "37.3640", "127.1100", "01", "DB-24", "A"],
    ["원교", "RC라멘교", "20", "6", "2", "1.2", "2010", "37.50", "127.30", "37.501", "127.301", "99", "미상", "C"],
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


def test_search_bridges_by_name(tmp_path):
    p = _write_csv(tmp_path / "bridges.csv")
    hits = search_bridges_by_name(p, "정자")
    assert len(hits) == 1 and hits[0]["name"] == "정자대교"
    h = hits[0]
    assert h["lat"] == 37.3634 and h["structure"] == "PSC박스거더교"
    assert h["grade"] == "1종" and h["length_m"] == 650.5
    # 시점·종점 좌표 → 데크 지오메트리 2점 폴리라인
    assert len(h["geometry"]) == 2
    assert h["geometry"][0] == [37.3634, 127.1090] and h["geometry"][1] == [37.364, 127.11]
    # 공백 무시 부분일치
    assert search_bridges_by_name(p, " 가교 ")[0]["name"] == "가교"
    assert search_bridges_by_name(p, "없는교") == []
    assert search_bridges_by_name(p, "") == []


def test_find_bridge_csv(tmp_path):
    d = tmp_path / "store"; d.mkdir()
    (d / "national_bridge_standard_20251231.csv").write_text("x", encoding="utf-8")
    hit = find_bridge_csv(str(d))                       # 지정 폴더 우선
    assert hit and hit.endswith("national_bridge_standard_20251231.csv")
    assert str(d) in hit
    # 지정 폴더에 없으면 data/ 폴백(없으면 None) — 존재하는 폴더만 통과
    empty = tmp_path / "empty"; empty.mkdir()
    assert find_bridge_csv(str(empty)) is None or "data" in find_bridge_csv(str(empty))


def test_bridge_profile_grade_field_present():
    # 확인된 표준데이터 등급 컬럼이 프리셋에 존재
    assert "grade" in DATASETS["national_bridge_standard"]["fields"]
    assert "시설물종별등급구분" in DATASETS["national_bridge_standard"]["fields"]["grade"]


def test_measured_csv_fields_are_ingested_not_estimated():
    """CSV 에 실측으로 있는 값은 추정으로 대체하지 않고 그대로 들어와야 한다.

    보도폭·허용통행하중·내진·점검일자·관리기관은 파일에 있는데도 쓰이지 않고 있었다.
    """
    rec = {"교량명": "청양교", "상부구조형식": "RC슬래브교", "교량연장": "90",
           "교량폭": "22", "교량보도폭": "8", "차로수": "4", "설계활하중": "DB-24",
           "허용통행하중": "43.2", "상하행선분리여부": "N", "내진설계적용여부": "미적용",
           "내진성능확보여부": "N", "최종안전점검결과": "C", "최종안전점검일자": "2025-11-30",
           "최종안전점검유형": "정기점검", "도로종류": "군도", "관리기관명": "충청남도 청양군",
           "소재지지번주소": "충청남도 청양군 청양읍 교월리", "데이터기준일자": "2025-12-31"}
    prof = bridge_profile_from_record(rec)
    ex = prof.extra
    # 차도폭 = 교량폭 − 보도폭. 보도까지 차로로 세면 활하중이 과대해진다.
    assert ex["sidewalk_width_m"] == 8.0 and ex["carriage_width_m"] == 14.0
    assert ex["allow_load_ton"] == 43.2 and ex["separated"] == "N"
    assert ex["seismic_applied"] == "미적용" and ex["seismic_secured"] == "N"
    assert ex["inspect_grade"] == "C" and ex["inspect_date"] == "2025-11-30"
    assert ex["road_kind"] == "군도" and ex["manager"] == "충청남도 청양군"
    assert ex["data_base_date"] == "2025-12-31"       # 언제 기준 값인지도 남긴다


def test_carriage_width_absent_when_no_sidewalk_column():
    """보도폭이 없으면 차도폭을 지어내지 않는다(None)."""
    prof = bridge_profile_from_record(
        {"교량명": "x", "상부구조형식": "PSCI거더교", "교량연장": "50", "교량폭": "10"})
    assert prof.extra["carriage_width_m"] is None and prof.extra["sidewalk_width_m"] is None
