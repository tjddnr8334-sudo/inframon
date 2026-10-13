"""보수 CRI — 스케일이 교량 제원에서 나오고, 모르는 것은 불리한 쪽으로 잡는다."""

from __future__ import annotations

import numpy as np

from inframon.fram import conservative as cc
from inframon.region import cri as rcri


def test_allowable_takes_the_smallest_limit_by_type_and_material():
    slab = cc.allowables("RC슬래브교", "2종", span_m=10)
    assert slab["structure"] == "simple" and slab["beta_material"] is None      # 정정 구조 → 침하 응력 없음
    assert slab["governs"] == "관리 기준" and slab["d_allow_mm"] == 20.0         # 1/500 × 10 m
    box = cc.allowables("강박스거더교", "2종", span_m=45)
    assert box["governs"] == "재료 한계" and box["beta_allow"] < 0.002 < box["beta_struct"]
    assert box["d_allow_mm"] == round(box["beta_allow"] * 45000, 2)
    long_psc = cc.allowables("PSC박스거더교", "1종", span_m=60)
    assert long_psc["d_allow_mm"] == 25.0 and long_psc["d_governs"] == "침하 25 mm"


def test_unknown_type_is_treated_as_the_worst_case():
    unk = cc.allowables("기타", None)
    assert unk["structure"] == "continuous" and not unk["type_known"]
    assert unk["beta_allow"] == min(cc.allowables(t, None)["beta_allow"]
                                    for t in ("강박스거더교", "PSC박스거더교", "라멘교"))


def test_importance_only_lowers_thresholds():
    assert cc.allowables("RC슬래브교", "3종")["thresholds"] == (0.3, 0.6, 0.85)   # 3종이라고 올리지 않는다
    old = cc.allowables("RC슬래브교", "1종", inspect_grade="D", build_year=1970, as_of_year=2026)
    assert old["thresholds"][0] < 0.3 * 0.85 * 0.80 + 1e-9


def test_rate_upper_bound_and_conservative_is_never_below_baseline():
    t = np.linspace(2018.5, 2026.3, 140)
    rng = np.random.default_rng(0)
    y = -1.0 * (t - t[0]) + 3.0 * np.sin(2 * np.pi * t) + rng.normal(0, 2.0, t.size)
    fit = cc.fit_series(t, y)
    assert abs(fit["rate"] + 1.0) < 0.3 and fit["rate_upper"] > abs(fit["rate"])   # 계절을 빼고, 상한을 쓴다
    allow = cc.allowables("라멘교", "2종", span_m=15)
    hi = cc.cri_bridge(fit, allow)
    lo = cc.cri_bridge(fit, {**rcri._BASE, "thresholds": (0.3, 0.6, 0.85)}, conservative=False)
    assert hi["cri"] > lo["cri"] and hi["idx"] >= lo["idx"]
    assert cc.fit_series(t[:5], y[:5]) is None
    assert cc.cri_bridge(None, allow)["cri"] == 0.0


def test_region_summary_uses_stored_series(tmp_path):
    import sqlite3
    con = sqlite3.connect(tmp_path / "history.sqlite")
    con.execute("create table series(bridge text, track text, kind text, date text, disp_mm real)")
    for i in range(100):
        d = f"{2018 + i // 12}{i % 12 + 1:02d}15"
        con.execute("insert into series values('b1','a54','vert_tc',?,?)", (d, -0.4 * i))   # −4.8 mm/yr
    con.commit(); con.close()
    st = {"bridges": [
        {"id": "b1", "n": "가", "c": "시", "yr": 1990, "gr": "C",
         "r": {"lv": 1, "typ": "라멘교", "cls": "1종", "sp": 15, "len": 30, "w": 12, "trk": "a54", "d10": 0, "bn": 0, "b10": 0}},
        {"id": "b2", "n": "나", "r": None}]}
    s = rcri.summarize(st, tmp_path, as_of_year=2026)
    assert len(s["rows"]) == 1 and s["rows"][0]["보수등급"] in ("경고", "위험")
    assert s["rows"][0]["보수CRI"] > s["rows"][0]["기본스케일CRI"]
