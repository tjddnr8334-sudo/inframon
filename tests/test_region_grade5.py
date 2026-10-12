"""변위 5단계 등급 — 허용값이 형식·경간·종별로 달라지고, 법이 주지 않는 수치를 법이라 하지 않는다."""

from __future__ import annotations

from inframon.region import grade5 as g5


def test_allowables_depend_on_type_span_and_class():
    simple = g5.allowables("PSCI거더교", "1종", span_m=30)
    cont = g5.allowables("강박스거더교", "3종", span_m=45)
    assert (simple["structure"], simple["struct_beta"], simple["struct_diff_mm"]) == ("simple", 0.008, 240.0)
    assert (cont["structure"], cont["struct_beta"], cont["struct_diff_mm"]) == ("continuous", 0.004, 180.0)
    assert simple["cuts"] == (0.3, 0.5, 0.7) and cont["cuts"] == (0.5, 0.7, 0.9)   # 1종이 더 일찍 오른다
    # 경간을 모르면 형식별 대표값, 단 교량 연장보다 길 수는 없다
    assert g5.allowables("PSC박스거더교", "2종", length_m=32)["span_m"] == 32
    assert g5.allowables(None, None)["class"] == "기타"


def test_five_grades_in_order():
    kw = dict(typ="PSCI거더교", cls="2종", span_m=30)          # 2종 경계 40 · 60 · 80 %
    got = [g5.grade(d, 0.0, **kw)["grade"] for d in (5.0, 12.0, 17.0, 22.0)]
    assert got == ["우수", "양호", "정상", "주의"]
    assert g5.grade(5.0, 0.0085, **kw)["grade"] == "위험"       # 단순교 구조 한계 0.008 초과
    assert g5.grade(5.0, 0.005, typ="라멘교", cls="2종", span_m=15)["grade"] == "위험"   # 연속교는 0.004
    assert g5.grade(5.0, 0.005, **kw)["grade"] == "주의"        # 같은 각변위라도 단순교는 위험이 아니다


def test_same_displacement_grades_differently_by_class():
    assert g5.grade(9.0, 0.0, typ="RC슬래브교", cls="1종")["grade"] == "양호"
    assert g5.grade(9.0, 0.0, typ="RC슬래브교", cls="3종")["grade"] == "우수"


def test_projection_raises_one_step_but_never_to_danger():
    kw = dict(typ="PSCI거더교", cls="2종", span_m=30)
    g = g5.grade(5.0, 0.0, d_proj_mm=40.0, **kw)
    assert g["grade"] == "양호" and g["raised_by_projection"]            # 우수 → 한 단계만
    g = g5.grade(22.0, 0.001, d_proj_mm=60.0, beta_proj=0.02, **kw)
    assert g["grade"] == "주의" and not g["raised_by_projection"]        # 예측만으로 위험을 만들지 않는다
    assert g5.grade(None, None, **kw)["idx"] == -1


def test_sources_do_not_pass_engineering_numbers_off_as_law():
    s = g5.allowables("PSCI거더교", "1종")["sources"]
    assert "AASHTO" in s["struct_beta"] and "수치 없음" in s["qualitative"]
    assert all("법정 수치 아님" in s[k] for k in ("mgmt_beta", "mgmt_settlement", "class_cuts"))
    o = g5.allowables("PSCI거더교", "1종", overrides={"struct_beta": {"simple": 0.005}})
    assert o["struct_beta"] == 0.005 and o["sources"]["struct_beta"] == "사용자 지정"


def test_matches_engine_levels_and_adds_only_danger():
    """기존 4단계와 어긋나지 않는다 — 한 칸씩 옮겨 읽히고, 구조 한계 초과만 새로 '위험'."""
    def b(lv, dn, bn=0.0, typ="RC슬래브교", cls="3종"):
        return {"id": "x", "n": "x", "len": 60, "r": {"lv": lv, "dn": dn, "bn": bn, "typ": typ, "cls": cls, "sp": 10}}
    st = {"bridges": [b(0, 2.0), b(1, 13.0), b(2, 18.0), b(3, 23.0), b(3, 23.0, 0.009), b(-1, None),
                      {"id": "y", "n": "y", "r": None}]}
    s = g5.summarize(st)
    assert s["counts"] == {"우수": 1, "양호": 1, "정상": 1, "주의": 1, "위험": 1}
    assert [r["등급"] for r in s["rows"]] == ["우수", "양호", "정상", "주의", "위험"]
