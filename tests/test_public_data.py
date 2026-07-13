"""공공데이터포털 실데이터 프리셋 — 한글 상부구조형식 파싱·레코드→프로파일."""
from __future__ import annotations
from inframon.public_data import (parse_structure_ko, bridge_profile_from_record,
                                  describe_datasets, DATASETS)


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
    assert "15028196" in DATASETS["national_bridge_standard"]["id"]
    assert "15076822" in DATASETS["korex_traffic"]["id"]
    txt = describe_datasets()
    assert "전국교량표준데이터" in txt and "data.go.kr" in txt
