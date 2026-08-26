"""⑪ 교량 메타 — 등급·PSC box/라멘·폭·산지(표고 네트워크 격리)."""

from __future__ import annotations

from inframon.insar import bridge_meta as bm


def test_bridge_grade():
    assert bm.bridge_grade(650) == "1종"            # 연장≥500
    assert bm.bridge_grade(120, 40) == "2종"        # 연장≥100
    assert bm.bridge_grade(80, 60) == "1종"         # 최대경간≥50
    assert bm.bridge_grade(30) == "3종"             # 연장≥20
    assert bm.bridge_grade(10) == "기타"
    assert bm.bridge_grade(None) == "기타"


def test_max_span_estimate():
    assert bm.max_span_estimate("girder", 650, n_spans=13) == 50.0     # 경간수 우선
    assert bm.max_span_estimate("cable_stayed", 400) == 220.0          # 주경간지배형 0.55
    assert bm.max_span_estimate("girder", None) is None


def test_max_span_estimate_repeated_spans():
    # 반복경간형: 대표경간(girder 35·box 50·rahmen 15) 근처로 균등분할
    assert bm.max_span_estimate("girder", 108) == 36.0        # 3경간(108/3) — 22Hz→~5Hz
    assert bm.max_span_estimate("box_girder", 200) == 50.0    # 4경간
    assert bm.max_span_estimate("rahmen", 30) == 15.0         # 2경간
    # 짧으면 단경간=연장
    assert bm.max_span_estimate("girder", 40) == 40.0
    assert bm.max_span_estimate("box_girder", 60) == 60.0
    # 주경간지배형은 비율 유지
    assert bm.max_span_estimate("arch", 300) == 150.0
    assert bm.max_span_estimate("suspension", 2000) == 1500.0


def test_classify_structure():
    assert bm.classify_structure({"bridge:structure": "box-girder"}, "girder") == bm.BOX_GIRDER
    assert bm.classify_structure({"bridge:structure": "rahmen"}, "girder") == bm.RAHMEN
    assert bm.classify_structure({"note": "rigid frame"}, "girder") == bm.RAHMEN
    assert bm.classify_structure({}, "cable_stayed") == "cable_stayed"   # 유지


def test_bridge_width_m():
    assert bm.bridge_width_m({"width": "12.5"}) == 12.5
    assert bm.bridge_width_m({"width": "20 m"}) == 20.0
    assert bm.bridge_width_m({"lanes": "4"}) == 15.0     # 4×3.5+1
    assert bm.bridge_width_m({}) is None


def test_terrain_class_marine():
    t, r = bm.terrain_class(37.0, 127.0, "marine", elev_fn=lambda la, lo: [0] * 9)
    assert t == "해상" and r is None


def test_terrain_class_mountain_vs_flat():
    mt, mr = bm.terrain_class(37.0, 127.0, "river",
                              elev_fn=lambda la, lo: [10, 300, 20, 250, 15, 280, 30, 260, 12])
    assert mt == "산지" and mr >= 150
    ft, fr = bm.terrain_class(37.0, 127.0, "river",
                              elev_fn=lambda la, lo: [10, 15, 12, 18, 11, 14, 13, 16, 12])
    assert ft == "평지" and fr < 150


def test_build_bridge_meta():
    m = bm.build_bridge_meta(37.32, 127.10, {"width": "24", "bridge:structure": "box-girder"},
                             "girder", 650.0, "river",
                             elev_fn=lambda la, lo: [20] * 9)
    assert m.grade == "1종" and m.structure == bm.BOX_GIRDER
    assert m.structure_ko == "PSC박스교" and m.width_m == 24.0
    assert m.terrain == "평지" and m.max_span_m is not None
    assert m.as_dict()["grade"] == "1종"
    assert m.source == "osm"                      # 공식 제원 없으면 추정임을 밝힌다


# ── 공식 제원(전국교량표준데이터) 우선 — OSM 만 보면 '폭미상·경간 None' 이 된다 ──
class _Official:
    """nearest_bridge_profile 이 주는 BridgeProfile 의 최소 대역(실측: 광안대교)."""

    def __init__(self, **kw):
        self.length_m = kw.get("length_m")
        self.width_m = kw.get("width_m")
        self.bridge_type = kw.get("bridge_type")
        self.extra = kw.get("extra", {})


def test_official_specs_fill_missing_width_and_span():
    """OSM 태그가 비어도 표준데이터가 있으면 폭·경간·등급이 채워진다."""
    m = bm.build_bridge_meta(
        35.1355, 129.1112, {}, "girder", 7.0, "marine",
        elev_fn=lambda la, lo: [20] * 9,
        official=_Official(length_m=7420.0, width_m=25.0, bridge_type="suspension",
                           extra={"max_span_m": 5565.0, "grade": "1종"}))
    assert m.width_m == 25.0 and m.length_m == 7420.0    # ①이 준 7m 를 실측이 덮는다
    assert m.max_span_m == 5565.0 and m.grade == "1종"
    assert m.structure == "suspension" and m.structure_ko == "현수교"
    assert m.source == "csv"                            # 출처가 드러나야 신뢰도가 읽힌다


def test_official_does_not_erase_osm_when_fields_missing():
    """표준데이터에 없는 항목은 OSM/추정값을 지우지 않는다."""
    m = bm.build_bridge_meta(
        37.32, 127.10, {"width": "24"}, "girder", 650.0, "river",
        elev_fn=lambda la, lo: [20] * 9,
        official=_Official(extra={}))                    # 아무 값도 없는 공식 레코드
    assert m.width_m == 24.0 and m.length_m == 650.0     # OSM 값 보존
    assert m.max_span_m is not None                      # 추정으로 채움


def test_official_grade_beats_estimate():
    """공식 등급은 연장·경간 추정 등급보다 우선한다(법정 종별)."""
    m = bm.build_bridge_meta(
        36.0, 127.0, {}, "girder", 30.0, "river",        # 짧아서 추정은 3종/기타
        elev_fn=lambda la, lo: [20] * 9,
        official=_Official(length_m=30.0, extra={"grade": "2종"}))
    assert m.grade == "2종"
