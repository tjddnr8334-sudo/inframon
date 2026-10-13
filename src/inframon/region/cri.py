"""광역 교량의 보수 CRI — 교량마다 제원으로 정한 스케일로, 저장된 변위 시계열에서 계산한다.

광역 엔진은 교량당 시계열 한 줄(연직 환산)을 남긴다. 점별 CRI[N,M](``fram.real_engine``)는
점군과 PINN 분해가 있어야 하므로 여기서는 **교량 단위** CRI 를 낸다. 식·가중치는 같고,
구할 수 없는 기능 결합 항은 상한으로 둔다(``fram.conservative`` 머리말).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from ..fram import conservative as cc
from ..fram.real_engine import age_alert_factor, grade_alert_factor, inspection_alert_factor

_KINDS = ("vert_tc", "vert", "los")
# 비교용 — 기본 엔진과 같은 고정 스케일(속도 10 mm/yr · 게이트 6 · 가속 8). 각변위는 관리 기준 1/500.
_BASE = {"horizon_years": 10.0, "d_allow_mm": 100.0, "gate_scale": 6.0, "accel_scale": 8.0,
         "beta_allow": 0.002, "weights": cc.DEFAULTS["weights"]}


def _years(d: str) -> float:
    return date(int(d[:4]), int(d[4:6]), int(d[6:8])).toordinal() / 365.25


def load_series(root, track: str | None = None) -> dict:
    """{bridge: (t_years[], mm[], kind)} — 종류는 vert_tc > vert > los, 트랙은 판정에 쓰인 것."""
    out: dict = {}
    p = Path(root) / "history.sqlite"
    if not p.exists():
        return out
    con = sqlite3.connect(p)
    try:
        rows = con.execute("select bridge, track, kind, date, disp_mm from series order by bridge, date").fetchall()
    except sqlite3.Error:
        return out
    finally:
        con.close()
    raw: dict = {}
    for b, trk, kind, d, mm in rows:
        if mm is not None:
            raw.setdefault(b, {}).setdefault((trk, kind), []).append((_years(str(d)), float(mm)))
    for b, groups in raw.items():
        out[b] = groups
    return out


def _pick(groups: dict, track: str | None):
    for kind in _KINDS:
        for (trk, k), pts in groups.items():
            if k == kind and (track is None or trk == track):
                return [p[0] for p in pts], [p[1] for p in pts], kind
    return (None, None, None) if track is None else _pick(groups, None)


def summarize(state: dict, root, *, opts: dict | None = None, as_of_year: int | None = None) -> dict:
    """판정된 교량마다 보수 CRI 와 비교용 기본 스케일 CRI. 반환: {counts, base_counts, rows}."""
    series = load_series(root)
    counts = {lv: 0 for lv in cc.LEVELS}
    base_counts = {lv: 0 for lv in cc.LEVELS}
    rows = []
    for b in state.get("bridges", []):
        r = b.get("r")
        if not r or (r.get("lv") if r.get("lv") is not None else -1) < 0:
            continue
        typ, cls = r.get("typ") or b.get("t"), r.get("cls") or b.get("cls")
        allow = cc.allowables(typ, cls, span_m=r.get("sp"), length_m=r.get("len") or b.get("len"),
                              width_m=r.get("w") or b.get("w"), inspect_grade=b.get("gr"),
                              build_year=b.get("yr"), as_of_year=as_of_year, opts=opts)
        t, mm, kind = _pick(series.get(b["id"], {}), r.get("trk"))
        fit = cc.fit_series(t, mm, z=allow["ci_z"]) if t else None
        kw = dict(d_10y_mm=r.get("d10"), beta_now=r.get("bn"), beta_10y=r.get("b10"),
                  zone_rates=[z.get("v") for z in (r.get("zones") or []) if z], length_m=allow_len(r, b))
        c = cc.cri_bridge(fit, allow, **kw)
        ef = (grade_alert_factor(cls) * inspection_alert_factor(b.get("gr"))
              * age_alert_factor(b.get("yr"), as_of_year))
        base = cc.cri_bridge(fit, {**_BASE, "thresholds": tuple(min(x * ef, 1.0) for x in cc.DEFAULTS["thresholds"])},
                             conservative=False, **kw)
        counts[c["level"]] += 1
        base_counts[base["level"]] += 1
        rows.append({
            "id": b["id"], "교량": b.get("n"), "시군": b.get("c"), "형식": allow["type"], "종별": allow["class"],
            "연장_m": r.get("len") or b.get("len"), "폭_m": allow["width_m"], "경간_m": allow["span_m"],
            "준공": b.get("yr"), "점검등급": b.get("gr"),
            "구조": "연속" if allow["structure"] == "continuous" else "단순", "재료": allow["material"],
            "허용각변위": allow["beta_allow"], "지배": allow["governs"], "허용침하_mm": allow["d_allow_mm"],
            "임계배율": allow["alert_factor"],
            "속도_mm_yr": None if fit is None else round(fit["rate"], 3),
            "속도상한_mm_yr": None if fit is None else round(fit["rate_upper"], 3),
            "10년수요_mm": c["demand_mm"], "A": c["A"], "R_couple": c["R_couple"],
            "R_spatial": c["R_spatial"], "R_div": c["R_div"],
            "보수CRI": c["cri"], "보수등급": c["level"],
            "기본스케일CRI": base["cri"], "기본스케일등급": base["level"],
            "산란체수": r.get("nps"), "확인필요": bool(r.get("rv")), "시계열": kind,
        })
    rows.sort(key=lambda x: -x["보수CRI"])
    return {"counts": counts, "base_counts": base_counts, "rows": rows}


def allow_len(r: dict, b: dict):
    return r.get("len") or b.get("len")
