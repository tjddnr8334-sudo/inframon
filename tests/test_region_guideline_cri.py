"""법규 기반 CRI — 수치는 지침·보고서의 값만 쓰고, 모르는 것은 불리한 쪽으로 잡는다."""

from __future__ import annotations

import sqlite3

from inframon.region import guideline_cri as gc


def test_constants_are_the_published_values():
    assert gc.INDEX == {"a": 0.10, "b": 0.20, "c": 0.40, "d": 0.70, "e": 1.00}          # 세부지침 [표 1.31]
    assert gc.RANGES == ((0.13, "A"), (0.26, "B"), (0.49, "C"), (0.79, "D"))
    assert gc.BETA_LIMIT == {"continuous": 0.004, "simple": 0.005}                      # Moulton 외 (1985)
    assert gc.SUB_WEIGHT["슬래브교"] == 20 + 7 and gc.SUB_WEIGHT["라멘교(거더 없음)"] == 34 + 7   # [표 1.29]


def test_angular_distortion_tiers():
    f = lambda mm, span, cont, sig=True: gc.substructure_letter(mm, span, continuous=cont, significant=sig)["letter"]
    assert f(10, 30, False, sig=False) == "a" and f(10, 30, False) == "b"       # 0.00033
    assert f(45, 30, False) == "c"                                              # 0.0015
    assert f(130 / 1.0, 30, False) == "e" and f(49, 10, False) == "c"           # 100 mm 초과 · 0.0049 < 0.005
    assert f(42, 10, True) == "d" and f(42, 10, False) == "c"                   # 0.0042 — 연속 한계만 넘는다
    assert f(40, 50, True) == "b" and f(60, 50, True) == "d"                    # 침하 50 mm (Bozozuk)
    assert f(26, 15, True) == "d" and f(26, 15, False) == "c"                   # 단경간 연속교 25.4 mm
    assert f(310, 30, False) == "e"                                             # 각변위 0.0103 ≥ 0.01


def test_cri_is_the_ratio_to_the_allowable_limit():
    f = lambda mm, span, cont, beta: gc.limit_ratio(mm, span, continuous=cont, beta=beta)
    assert f(10, 30, False, 0.001)["cri"] == 0.2                          # 0.001 / 0.005
    assert f(42, 10, True, 0.0042)["cri"] == 1.654                       # 42 / 25.4 — 단경간 연속교가 지배
    assert f(60, 50, True, 0.0012)["cri"] == 1.2 and "Bozozuk" in f(60, 50, True, 0.0012)["governing"]
    # CRI ≥ 1.0 ⇔ 하부구조 d 이상
    for mm, span, cont in [(10, 30, False), (45, 30, False), (42, 10, True), (42, 10, False), (26, 15, True),
                           (24, 15, True), (49, 60, True), (60, 50, True), (310, 30, False)]:
        sub = gc.substructure_letter(mm, span, continuous=cont, significant=True)
        assert (f(mm, span, cont, sub["beta"])["cri"] >= 1.0) == (sub["letter"] in "de"), (mm, span, cont)


def test_defect_score_follows_the_guideline_weights():
    s = gc.defect_score("B", "d", "라멘교")
    assert s["sub_weight"] == 41 and s["score"] == round(0.2 * 0.59 + 0.7 * 0.41, 3) and s["grade"] == "C"
    assert gc.defect_score("B", "e", "PSCI거더교")["score"] == round(0.2 * 0.8 + 1.0 * 0.2, 3)
    assert gc.defect_score("C", "a", "RC슬래브교")["score"] == 0.4          # 위성이 좋다고 기존 등급을 올리지 않는다
    unk = gc.defect_score("A", "c", "기타")
    assert not unk["type_known"] and unk["sub_weight"] == 41               # 모르는 형식 → 하부구조 몫이 가장 큰 열
    assert gc.defect_score(None, "d", "라멘교")["score"] is None           # 기존 등급이 없으면 점수를 내지 않는다


def test_assess_uses_the_upper_bound_and_flags_major_defects(tmp_path):
    con = sqlite3.connect(tmp_path / "history.sqlite")
    con.execute("create table series(bridge text, track text, kind text, date text, disp_mm real)")
    for i in range(96):
        d = f"{2018 + i // 12}{i % 12 + 1:02d}15"
        con.execute("insert into series values('b1','a54','vert_tc',?,?)", (d, -0.4 * i))     # 약 −4.8 mm/yr
    con.commit()
    con.close()
    st = {"bridges": [
        {"id": "b1", "n": "가", "c": "시", "gr": "B", "len": 30,
         "r": {"lv": 1, "typ": "라멘교", "cls": "2종", "sp": 15, "len": 30, "trk": "a54", "dn": 20.0, "bn": 0.0}},
        {"id": "b2", "n": "나", "gr": "A", "r": {"lv": 0, "typ": "PSCI거더교", "sp": 30, "len": 90, "dn": 0.0}},
        {"id": "b3", "n": "다", "r": None}]}
    s = gc.summarize(st, tmp_path)
    by = {r["id"]: r for r in s["rows"]}
    assert set(by) == {"b1", "b2"}
    assert by["b1"]["침하상한_mm"] > 20.0 and by["b1"]["하부구조_위성평가"] == "d"       # 시계열 상한 ≈ 38 mm
    assert by["b1"]["중대한결함_해당가능"] and by["b1"]["상태등급_보조"] == "C"
    assert by["b1"]["CRI_허용한계비"] >= 1.0 and s["over_limit"] == 1
    assert by["b2"]["하부구조_위성평가"] == "a" and by["b2"]["환산결함도_보조"] == 0.1
    assert by["b2"]["CRI_허용한계비"] == 0.0
    assert s["letters"]["d"] == 1 and s["base_grades"] == {"A": 1, "B": 1, "C": 0, "D": 0, "E": 0}
