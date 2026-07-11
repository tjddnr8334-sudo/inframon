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
    assert bm.max_span_estimate("cable_stayed", 400) == 220.0          # 형식비율 0.55
    assert bm.max_span_estimate("girder", None) is None


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
