"""법규 기반 CRI — 위성(InSAR) 변위를 허용 한계로 나눈 비율. 1.0 = 허용 한계 도달.

    CRI = max( 각변위 상한 / 허용 각변위,  침하 상한 / 50 mm,  [연속교·경간 ≤ 18.3 m] 침하 상한 / 25.4 mm )

CRI ≥ 1.0 은 하부구조 위성 평가 d(세부지침 "중대한 결함 해당 가능")와 같은 조건이다.
세부지침의 환산 결함도 점수·상태등급은 보조 지표로 함께 낸다.
근거는 법령·지침·보고서·논문만 쓴다. 이 모듈이 스스로 정한 가중치·임계는 없다.

  허용 한계에 국내 수치가 없는 이유
    도로교설계기준(2005) 5.5.3 (5): "기초의 수평 및 수직 허용 변위량은 구조물의 기능과 형태, 예상 사용수명,
    과대 변위 발생시 구조물에 미치는 영향 등을 고려하여 결정되어야 한다." — 수치를 정하지 않는다.
    그래서 구조물기초설계기준(2008) 4.3.6 에 따라 국제 기준을 준용하고, 그중 더 엄격한 값을 쓴다
    (단순교: AASHTO LRFD 0.008 대신 Moulton 0.005).

  틀(법령·지침)
    「시설물의 안전 및 유지관리 실시 세부지침(안전점검·진단 편)」(2021.12) 교량 편
      [표 1.19] 교대 · [표 1.20] 콘크리트 교각 · [표 1.21] 기초 상태평가 기준
          d  기초의 부등침하로 인한 구체/교각 기울음
          e  부등침하로 인한 교대/교각 안전성 저하 · 상부구조의 단차 및 파손
      1.1.4 중대한 결함: "교량 교각의 부등침하 — [표 1.20] 교각 변위의 상태평가기준이 d 이하"
      [표 1.29] 구조형식에 따른 부재별 가중치 (교대/교각 · 기초의 몫이 형식마다 다르다)
      [표 1.31] 결함도 지수 a 0.1 · b 0.2 · c 0.4 · d 0.7 · e 1.0,
                범위 A <0.13 · B <0.26 · C <0.49 · D <0.79 · E ≥0.79
    구조물기초설계기준(2008) 4.3.6: 허용침하량에 별도 기준이 없으면 국제적으로 통용되는 기준을 준용할 수 있다.

  수치(보고서) — 지침의 표에는 mm·rad 가 없다. 위 4.3.6 에 따라 다음을 준용한다.
    Moulton, GangaRao, Halvorsen (1985) Tolerable Movement Criteria for Highway Bridges, FHWA/RD-85/107
      (미국·캐나다 교량 실측. 각변위 = 부등침하 / 경간)
        각변위 < 0.001          97.7 % 가 허용 가능으로 보고
        허용 한계                연속교 0.004 · 단순교 0.005
        0.004 ~ 0.01            42.9 % 만 허용 가능
        > 0.01                   7.1 % 만 허용 가능
        연속교 · 경간 18.3 m(50 ft) 이하: 부등침하 25.4 mm(1 in) 이상은 응력 증가로 허용 불가(해석)
    Bozozuk (1978) Bridge Foundations Move, TRR 678 — 연직 변위 < 50 mm 허용 · 50~100 mm 유해하나 허용 · > 100 mm 허용 불가

  이 모듈이 한 해석은 하나뿐이다: 위 문헌의 구간을 지침의 a~e 에 대응시킨 것.
  d 는 "허용 한계 초과", e 는 "대부분 허용 불가(> 0.01, > 100 mm)".

보수적으로 잡는 규칙(전부 "더 나쁜 쪽"을 고르는 규칙이고 새 수치를 만들지 않는다)
  · 변위는 추정값이 아니라 95 % 신뢰구간의 먼 쪽 끝.
  · 교량의 침하 전부를 한 경간 안의 부등침하로 본다(이웃 지점은 안 움직였다고 가정).
  · 형식을 모르면 연속교(한계 0.004)와 하부구조 가중치가 가장 큰 형식.
  · 각변위·침하량·단경간 연속교 기준 중 가장 나쁜 등급.
  · 하부구조 지수 = max(기존 점검등급, 위성 평가). 위성이 좋다고 기존 등급을 올리지 않는다.

한계: 위성은 기초·교대·교각의 변위만 본다. 균열·부식·세굴·받침·내하력(철근량)은 보지 못한다.
그 부재들은 기존 점검등급을 그대로 쓴다(부재별 등급이 대장에 없어 전체 등급으로 대신한다).
안전성평가(안전율, [표 1.32])는 철근량·재료강도·구조계산이 있어야 하므로 여기서 계산하지 않는다.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date
from pathlib import Path

import numpy as np

from .grade5 import CONTINUOUS_TYPES

LETTERS = "abcde"
INDEX = {"a": 0.10, "b": 0.20, "c": 0.40, "d": 0.70, "e": 1.00}        # 세부지침 [표 1.31]
RANGES = ((0.13, "A"), (0.26, "B"), (0.49, "C"), (0.79, "D"))            # 그 이상 E
BETA_LIMIT = {"continuous": 0.004, "simple": 0.005}                      # Moulton et al. (1985)
BETA_MINOR, BETA_SEVERE = 0.001, 0.01                                    # 같은 보고서의 구간
SETTLE_HARMFUL_MM, SETTLE_INTOLERABLE_MM = 50.0, 100.0                   # Bozozuk (1978)
SHORT_CONT_SPAN_M, SHORT_CONT_SETTLE_MM = 18.3, 25.4                     # Moulton et al. (1985) 해석

# 세부지침 [표 1.29]·[표 1.30] — 하부구조(교대/교각 + 기초)의 가중치 합(전체 100)
SUB_WEIGHT = {"슬래브교": 27, "일반 거더교": 20, "바닥판·거더 일체형": 20, "아치·트러스": 20,
              "라멘교(거더 없음)": 41, "케이블교": 30}
_WEIGHT_RULES = (("현수", "케이블교"), ("사장", "케이블교"), ("엑스트라도즈드", "케이블교"),
                 ("아치", "아치·트러스"), ("트러스", "아치·트러스"), ("박스", "바닥판·거더 일체형"),
                 ("슬래브", "슬래브교"), ("라멘", "라멘교(거더 없음)"), ("거더", "일반 거더교"))

SOURCES = (
    "세부지침(안전점검·진단 편, 2021.12) 교량 [표 1.19]·[표 1.20]·[표 1.21] — 부등침하의 d·e 기준(정성)",
    "세부지침 교량 1.1.4 — 교각 부등침하가 d 이하이면 중대한 결함",
    "세부지침 교량 [표 1.29]·[표 1.30] 구조형식별 부재 가중치, [표 1.31] 결함도 지수와 범위",
    "도로교설계기준(2005) 5.5.3 (5) — 기초의 허용 변위량은 구조물마다 정한다(국내 수치 없음)",
    "구조물기초설계기준(2008) 4.3.6 — 별도 기준이 없으면 국제적으로 통용되는 기준 준용",
    "Moulton, GangaRao, Halvorsen (1985) FHWA/RD-85/107 — 각변위 0.001 · 0.004(연속)/0.005(단순) · 0.01, 단경간 연속교 25.4 mm",
    "Bozozuk (1978) Transportation Research Record 678 — 연직 변위 50 mm · 100 mm",
)


def weight_class(typ: str | None) -> tuple[str, bool]:
    """형식 이름 → [표 1.29]의 열. 모르는 형식은 하부구조 가중치가 가장 큰 열(불리한 쪽)."""
    t = typ or ""
    for key, cls in _WEIGHT_RULES:
        if key in t:
            return cls, True
    return max(SUB_WEIGHT, key=SUB_WEIGHT.get), False


def fit_series(t_years, mm, *, z: float = 1.96) -> dict | None:
    """시계열 → 속도와 95 % 상한. 직선 + 연주기(sin·cos)를 맞춰 가역 계절 거동을 뺀다.

    표준오차는 잔차의 1차 자기상관 ρ 로 √((1+ρ)/(1−ρ)) 배 키운다(시계열은 서로 독립이 아니다).
    """
    t = np.asarray(t_years, float)
    y = np.asarray(mm, float)
    ok = np.isfinite(t) & np.isfinite(y)
    t, y = t[ok], y[ok]
    if t.size < 8 or float(t.max() - t.min()) < 0.75:
        return None
    tc = t - t.mean()
    ph = 2.0 * np.pi * t
    X = np.column_stack([np.ones_like(tc), tc, np.sin(ph), np.cos(ph)])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ beta
    s2 = float(res @ res) / max(t.size - 4, 1)
    se = math.sqrt(max(s2 * float(np.linalg.pinv(X.T @ X)[1, 1]), 0.0))
    rho = float(np.clip(np.corrcoef(res[:-1], res[1:])[0, 1], 0.0, 0.95)) if res.std() > 0 else 0.0
    se *= math.sqrt((1.0 + rho) / (1.0 - rho))
    rate = float(beta[1])
    return {"rate": rate, "se": se, "rate_upper": abs(rate) + z * se, "significant": abs(rate) > z * se,
            "years": float(t.max() - t.min()), "n": int(t.size)}


def substructure_letter(settle_up_mm: float, span_m: float, *, continuous: bool, significant: bool,
                        beta_measured: float = 0.0) -> dict:
    """위성 변위 → 하부구조(교대·교각·기초) 변위 항목의 a~e 와 그 이유."""
    beta = max(float(settle_up_mm) / (float(span_m) * 1000.0), abs(float(beta_measured or 0.0)))
    kind = "연속" if continuous else "단순"
    limit = BETA_LIMIT["continuous" if continuous else "simple"]
    why = []
    if beta >= BETA_SEVERE:
        k = 4
        why.append(f"각변위 {beta:.4f} ≥ 0.01 — 7.1 % 만 허용 가능(Moulton 1985)")
    elif beta >= limit:
        k = 3
        why.append(f"각변위 {beta:.4f} ≥ 허용 한계 {limit}({kind}교, Moulton 1985)")
    elif beta >= BETA_MINOR:
        k = 2
        why.append(f"각변위 {beta:.4f} — 0.001 이상, 허용 한계 {limit} 미만")
    else:
        k = 1 if significant else 0
        why.append(f"각변위 {beta:.4f} < 0.001" + (" · 변위는 통계적으로 유의" if significant else " · 유의한 변위 없음"))
    if settle_up_mm >= SETTLE_INTOLERABLE_MM:
        k = max(k, 4)
        why.append(f"침하 {settle_up_mm:.0f} mm ≥ 100 mm — 허용 불가(Bozozuk 1978)")
    elif settle_up_mm >= SETTLE_HARMFUL_MM:
        k = max(k, 3)
        why.append(f"침하 {settle_up_mm:.0f} mm ≥ 50 mm — 유해(Bozozuk 1978)")
    if continuous and span_m <= SHORT_CONT_SPAN_M and settle_up_mm >= SHORT_CONT_SETTLE_MM:
        k = max(k, 3)
        why.append(f"연속교 · 경간 {span_m:.0f} m ≤ 18.3 m 에서 부등침하 {settle_up_mm:.0f} mm ≥ 25.4 mm(Moulton 1985)")
    return {"letter": LETTERS[k], "beta": round(beta, 5), "beta_limit": limit, "why": why}


def limit_ratio(settle_up_mm: float, span_m: float, *, continuous: bool, beta: float) -> dict:
    """CRI — 허용 한계 대비 비율. 기준마다 비율을 내고 가장 큰 것을 쓴다(1.0 = 허용 한계)."""
    limit = BETA_LIMIT["continuous" if continuous else "simple"]
    kind = "연속" if continuous else "단순"
    parts = [(beta / limit, f"각변위 {beta:.4f} / 허용 {limit}({kind}교, Moulton 1985)"),
             (settle_up_mm / SETTLE_HARMFUL_MM, f"침하 {settle_up_mm:.1f} mm / 50 mm(Bozozuk 1978)")]
    if continuous and span_m <= SHORT_CONT_SPAN_M:
        parts.append((settle_up_mm / SHORT_CONT_SETTLE_MM,
                      f"침하 {settle_up_mm:.1f} mm / 25.4 mm(연속교·경간 ≤ 18.3 m, Moulton 1985)"))
    x, why = max(parts, key=lambda p: p[0])
    return {"cri": round(x, 3), "governing": why}


def defect_score(inspect_grade: str | None, sub_letter: str, typ: str | None) -> dict:
    """세부지침 [표 1.29]·[표 1.31] — 환산 결함도 점수와 상태평가 등급.

    하부구조(교대/교각·기초)는 max(기존 등급, 위성 평가), 나머지 부재는 기존 등급.
    기존 등급을 모르면 위성이 볼 수 없는 부재를 평가할 근거가 없으므로 점수를 내지 않는다.
    """
    cls, known = weight_class(typ)
    out = {"weight_class": cls, "sub_weight": SUB_WEIGHT[cls], "type_known": known,
           "score": None, "grade": None, "base_score": None}
    g = str(inspect_grade or "").strip().lower()[:1]
    if g not in INDEX:
        return out
    ws = SUB_WEIGHT[cls] / 100.0
    x = INDEX[g] * (1.0 - ws) + max(INDEX[g], INDEX[sub_letter]) * ws
    out.update(score=round(x, 3), grade=next((name for cut, name in RANGES if x < cut), "E"),
               base_score=INDEX[g])
    return out


def _years(d: str) -> float:
    return date(int(d[:4]), int(d[4:6]), int(d[6:8])).toordinal() / 365.25


def load_series(root) -> dict:
    """{bridge: {(track, kind): [(t_years, mm), ...]}}"""
    p = Path(root) / "history.sqlite"
    out: dict = {}
    if not p.exists():
        return out
    con = sqlite3.connect(p)
    try:
        rows = con.execute("select bridge, track, kind, date, disp_mm from series order by bridge, date").fetchall()
    except sqlite3.Error:
        return out
    finally:
        con.close()
    for b, trk, kind, d, mm in rows:
        if mm is not None:
            out.setdefault(b, {}).setdefault((trk, kind), []).append((_years(str(d)), float(mm)))
    return out


def _pick(groups: dict, track):
    for kind in ("vert_tc", "vert", "los"):
        for (trk, k), pts in groups.items():
            if k == kind and (track is None or trk == track):
                return [p[0] for p in pts], [p[1] for p in pts]
    return (None, None) if track is None else _pick(groups, None)


def assess(b: dict, groups: dict | None = None) -> dict | None:
    """교량 한 개(state.json 의 항목). 판정되지 않은 교량은 None."""
    r = b.get("r")
    if not r or (r.get("lv") if r.get("lv") is not None else -1) < 0:
        return None
    typ = r.get("typ") or b.get("t")
    continuous = (typ in CONTINUOUS_TYPES) or not weight_class(typ)[1]
    length = float(r.get("len") or b.get("len") or 0)
    span = float(r.get("sp") or length or 1.0)
    if length:
        span = min(span, length)                       # 경간이 연장보다 길 수는 없다(짧을수록 불리)
    t, mm = _pick(groups or {}, r.get("trk"))
    fit = fit_series(t, mm) if t else None
    dn = abs(float(r.get("dn") or 0.0))
    ups = [dn, abs(float(r.get("dhi") or 0.0)), abs(float(r.get("dlo") or 0.0))]
    if fit:
        ups.append(fit["rate_upper"] * fit["years"])
    settle_up = max(ups)
    significant = dn > 0 or bool(fit and fit["significant"])
    sub = substructure_letter(settle_up, span, continuous=continuous, significant=significant,
                              beta_measured=r.get("bn") or 0.0)
    sc = defect_score(b.get("gr"), sub["letter"], typ)
    lr = limit_ratio(settle_up, span, continuous=continuous, beta=sub["beta"])
    return {**lr, "typ": typ, "continuous": continuous, "span_m": span, "settle_mm": round(dn, 1),
            "settle_up_mm": round(settle_up, 1), "rate": None if fit is None else round(fit["rate"], 3),
            "rate_upper": None if fit is None else round(fit["rate_upper"], 3), "significant": significant,
            "major_defect": sub["letter"] in "de", **sub, **sc}


def summarize(state: dict, root) -> dict:
    """판정된 교량 전체. 반환: {letters, grades, base_grades, over_limit, rows}."""
    series = load_series(root)
    rows = []
    letters = {k: 0 for k in LETTERS}
    grades = {k: 0 for k in "ABCDE"}
    base = {k: 0 for k in "ABCDE"}
    for b in state.get("bridges", []):
        a = assess(b, series.get(b["id"]))
        if a is None:
            continue
        r = b["r"]
        letters[a["letter"]] += 1
        if a["grade"]:
            grades[a["grade"]] += 1
        g0 = str(b.get("gr") or "").strip().upper()[:1]
        if g0 in base:
            base[g0] += 1
        rows.append({
            "id": b["id"], "교량": b.get("n"), "시군": b.get("c"), "형식": a["typ"],
            "종별": r.get("cls") or b.get("cls"), "연장_m": r.get("len") or b.get("len"),
            "경간_m": round(a["span_m"], 1), "구조": "연속" if a["continuous"] else "단순",
            "CRI_허용한계비": a["cri"], "지배기준": a["governing"],
            "기존점검등급": g0 or None, "침하_mm": a["settle_mm"], "침하상한_mm": a["settle_up_mm"],
            "속도_mm_yr": a["rate"], "속도상한_mm_yr": a["rate_upper"], "각변위상한": a["beta"],
            "허용한계": a["beta_limit"], "하부구조_위성평가": a["letter"],
            "중대한결함_해당가능": a["major_defect"], "가중치열": a["weight_class"],
            "하부구조가중치": a["sub_weight"], "기존결함도": a["base_score"],
            "환산결함도_보조": a["score"], "상태등급_보조": a["grade"], "이유": " / ".join(a["why"]),
            "산란체수": r.get("nps"), "확인필요": bool(r.get("rv")),
        })
    rows.sort(key=lambda x: (-x["CRI_허용한계비"], -(x["환산결함도_보조"] or 0)))
    over = sum(1 for x in rows if x["CRI_허용한계비"] >= 1.0)
    return {"letters": letters, "grades": grades, "base_grades": base, "over_limit": over, "rows": rows}
