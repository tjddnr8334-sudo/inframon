"""법정 근거표 만들기.

설계 결정 셋:

- **수치 기준이 없는 곳에 수치를 만들지 않는다.** 세부지침 교량 편은 교대·교각·기초의
  기울기·침하와 거더 처짐에 mm·각도 임계값이 없다(표 1.19~1.22, 2021.12판). 그래서 관측
  해석표의 '해석'은 법정 판정이 아니라 "관련 조항은 이것이고, 법정 수치 기준은 없으며,
  inframon 자체 등급은 이렇다"를 나란히 적는다.
- **확인 안 된 규칙은 쓰지 않는다.** YAML 의 `확인: false` 항목은 표에 '미확인' 으로만 남긴다.
- **입력이 없으면 비워 둔다.** 안전등급·최근 점검일을 모르면 기한을 추정하지 않는다
  (공공데이터 '최종안전점검일자' 나 bridge.json 의 `inspections` 로 채운다).
"""
from __future__ import annotations

import calendar
import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

RULES_PATH = Path(__file__).with_name("facility_safety_law.yaml")
NOTICE = ("InSAR 산출은 시설물안전법에 따른 법정 안전점검·정밀안전점검·정밀안전진단·성능평가를 "
          "대체하지 않는다(법 제2조제5호 — 안전점검은 '경험과 기술을 갖춘 자가 육안이나 점검기구 "
          "등으로 검사'). 이 표는 관리주체의 현장 확인 우선순위를 정하는 참고 자료다.")

GRADES = ("A", "B", "C", "D", "E")
LEVEL_KO = ("정상", "주의", "경고", "위험")
# 상부구조형식 영문 키(inframon bridge_type) → 별표 1 표현
_SUPER_KO = {"suspension": "현수교", "cable_stayed": "사장교", "arch": "아치교", "truss": "트러스교"}


@lru_cache(maxsize=4)
def load_rules(path: str | None = None) -> dict:
    import yaml

    p = Path(path) if path else RULES_PATH
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _src(rule: dict) -> str:
    s = str(rule.get("출처", ""))
    return s if rule.get("확인", True) else f"[미확인] {s}"


# ── 1. 종별 ────────────────────────────────────────────────────────────
def classify_facility(*, length_m: float | None, max_span_m: float | None = None,
                      n_spans: int | None = None, bridge_type: str | None = None,
                      kind: str = "도로교량", official: str | None = None,
                      rules: dict | None = None) -> dict:
    """시행령 별표 1 로 도로교량 종별 판정.

    official(공공데이터 '시설물종별') 이 있으면 그것을 우선하고 계산값과 다르면 표시한다.
    3종은 수치 기준이 아니라 지정·고시(법 제8조)라 계산으로 3종을 만들지 않는다.
    """
    rules = rules or load_rules()
    road = [r for r in rules["종별_교량"] if r.get("대상") == kind]
    c1 = next((r for r in road if r["종별"] == 1), None)
    c2 = next((r for r in road if r["종별"] == 2), None)
    reasons: list[str] = []
    computed: int | None = None
    single = (n_spans == 1)
    stype = _SUPER_KO.get((bridge_type or "").lower())

    if c1 and c1.get("기계판정"):
        m = c1["기계판정"]
        if stype and stype in m["상부형식"]:
            reasons.append(f"상부구조형식 {stype} → 1종")
            computed = 1
        if length_m is not None and length_m >= m["연장_m_이상"]:
            reasons.append(f"연장 {length_m:g}m ≥ {m['연장_m_이상']}m → 1종")
            computed = 1
        if (max_span_m is not None and max_span_m >= m["최대경간장_m_이상"] and not single):
            reasons.append(f"최대경간장 {max_span_m:g}m ≥ {m['최대경간장_m_이상']}m(다경간) → 1종")
            computed = 1
    if computed is None and c2 and c2.get("기계판정"):
        m = c2["기계판정"]
        if single and max_span_m is not None and max_span_m >= m["한경간_경간장_m_이상"]:
            reasons.append(f"한 경간 경간장 {max_span_m:g}m ≥ {m['한경간_경간장_m_이상']}m → 2종")
            computed = 2
        if length_m is not None and length_m >= m["연장_m_이상"]:
            reasons.append(f"연장 {length_m:g}m ≥ {m['연장_m_이상']}m → 2종")
            computed = 2
    if computed is None:
        reasons.append("1·2종 수치 기준에 해당하지 않음 — 3종은 지자체 등의 지정·고시로 정해진다(법 제8조)")

    off = _parse_class(official)
    final = off if off is not None else computed
    label = f"{final}종" if final else ("3종 여부 지정·고시 확인 필요" if computed is None else "미상")
    return {
        "class": final, "label": label, "computed": computed, "official": off,
        "mismatch": off is not None and computed is not None and off != computed,
        "reasons": reasons,
        "basis": _src(c1) if c1 else "시행령 별표 1",
        "inputs": {"length_m": length_m, "max_span_m": max_span_m, "n_spans": n_spans,
                   "bridge_type": bridge_type},
    }


def _parse_class(v) -> int | None:
    if v is None:
        return None
    s = str(v)
    for k in (1, 2, 3):
        if f"{k}종" in s or s.strip() == str(k):
            return k
    return None


# ── 2. 법정 점검 기한 ─────────────────────────────────────────────────────
def _as_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v if not isinstance(v, datetime) else v.date()
    s = "".join(ch for ch in str(v) if ch.isdigit())
    try:
        if len(s) >= 8:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        if len(s) == 6:
            return date(int(s[:4]), int(s[4:6]), 1)
    except ValueError:
        return None
    return None


def _month_end_plus(d: date, months: int) -> date:
    """비고 8·11: 주기 기준점 = 이전 점검을 완료한 달의 마지막 날. 거기에 개월 수를 더한다."""
    y, m = d.year, d.month + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, calendar.monthrange(y, m)[1])


# 등급 D·E 정기안전점검: 해빙기 전(2·3월)·우기 전(5·6월)·동절기 전(11·12월)
_DE_WINDOWS = ((2, 3, "해빙기 전"), (5, 6, "우기 전"), (11, 12, "동절기 전"))


def _next_de_window(after: date) -> tuple[date, str]:
    for y in (after.year, after.year + 1):
        for m0, m1, label in _DE_WINDOWS:
            end = date(y, m1, calendar.monthrange(y, m1)[1])
            start = date(y, m0, 1)
            if start > after:
                return end, label
    return _month_end_plus(after, 4), "다음 점검기"


def inspection_schedule(*, facility_class: int | None, grade: str | None,
                        last: dict[str, Any] | None = None, today: date | None = None,
                        road_kind: str | None = None, rules: dict | None = None) -> list[dict]:
    """별표 3 주기로 법정 점검·진단·성능평가의 다음 기한."""
    rules = rules or load_rules()
    cyc = rules["점검주기"]
    today = today or date.today()
    last = {k: _as_date(v) for k, v in (last or {}).items()}
    g = (grade or "").strip().upper()[:1] or None
    if g and g not in GRADES:
        g = None
    out = []

    def row(kind: str, applicable: bool | None, why: str, months: int | None, text: str,
            *, window: str | None = None):
        lt = last.get(kind)
        nxt = overdue = None
        if applicable and lt and (months or window):
            if window:
                nd, wl = _next_de_window(lt)
                nxt, text = nd.isoformat(), f"{text} — 다음: {wl}"
            else:
                nxt = _month_end_plus(lt, months).isoformat()
            overdue = date.fromisoformat(nxt) < today
        out.append({"kind": kind, "applicable": applicable, "why": why,
                    "interval_text": text if applicable is not False else "대상 아님",
                    "months": months, "last": lt.isoformat() if lt else None,
                    "next_due": nxt, "overdue": overdue,
                    "basis": cyc.get(kind, {}).get("출처") or cyc.get("출처_공통", "")})

    fc = facility_class
    # 정기안전점검 — 1·2·3종 전부
    reg = cyc["정기안전점검"]
    if g:
        r = reg[g]
        row("정기안전점검", True, "1·2·3종 전부 (시행령 제8조제1항)",
            r.get("개월"), r["원문"], window="DE" if g in ("D", "E") else None)
    else:
        row("정기안전점검", True, "1·2·3종 전부", 6, reg["최초등급_지정전"] + " — 안전등급 미상")

    # 정밀안전점검 — 1·2종, 3종은 D·E 일 때만
    pr = cyc["정밀안전점검"]
    app = (fc in (1, 2)) or (fc == 3 and g in ("D", "E"))
    if fc is None:
        app = None
    row("정밀안전점검", app, "1·2종 (3종은 D·E 등급일 때)",
        pr[g]["개월"] if g else None, pr[g]["원문"] if g else "안전등급을 알아야 주기가 정해진다")

    # 정밀안전진단 — 1종, 그리고 D·E 2종
    dg = cyc["정밀안전진단"]
    app = (fc == 1) or (fc == 2 and g in ("D", "E"))
    if fc is None:
        app = None
    row("정밀안전진단", app, "1종, D·E 등급 2종 (법 제12조제1항·시행령 제10조제1항)",
        dg[g]["개월"] if g else None, dg[g]["원문"] if g else "안전등급을 알아야 주기가 정해진다")

    # 성능평가 — 1·2종 고속국도·일반국도 교량, 등급 무관 5년
    pe = cyc["성능평가"]
    rk = road_kind or ""
    if fc in (1, 2) and rk:
        app = any(k in rk for k in ("고속국도", "고속도로", "일반국도", "국도"))
    elif fc in (1, 2):
        app = None                       # 도로종류 모름
    else:
        app = False if fc else None
    row("성능평가", app, "1·2종 고속국도·일반국도 교량 (별표 13)" + ("" if rk else " — 도로종류 미상"),
        pe["주기"]["개월"], pe["주기"]["원문"])
    return out


# ── 3. 관측 해석표 ──────────────────────────────────────────────────────
def _guideline(rules: dict, prefix: str) -> dict | None:
    return next((r for r in rules["상태평가_변위기준"] if str(r["항목"]).startswith(prefix)), None)


def observation_findings(obs: dict, *, rules: dict | None = None) -> list[dict]:
    """위성 관측 요약 → 세부지침 조항 대응표.

    obs 키(있는 것만): cri_max, warning_level, velocity_median_mm_yr, velocity_maxabs_mm_yr,
    cumulative_maxabs_mm, n_points, n_dates, date_range, deck_vs_ground_mm_per_c
    """
    rules = rules or load_rules()
    rows = []
    lv = obs.get("warning_level")
    lvp = obs.get("warning_level_period")
    if lvp is not None and (lv is None or lvp > lv):
        lv = lvp                                   # 판단은 더 나쁜 쪽으로
    if obs.get("cri_max") is not None:
        val = f"최근 {obs['cri_max']:.3f}"
        if obs.get("cri_period_max") is not None:
            val += f" · 전 기간 최대 {obs['cri_period_max']:.3f}({obs.get('cri_period_max_at', '')})"
        rows.append({
            "item": "위성 위험지수 CRI (inframon)",
            "value": val + (f" · {LEVEL_KO[lv]}" if lv is not None else ""),
            "basis": "법정 기준 없음 — inframon FRAM 자체 등급(임계값 잠정)",
            "reading": ("법정 안전등급(A~E)과 다른 척도다. '경고·위험' 이면 다음 정기안전점검에서 "
                        "해당 부재를 우선 육안 확인하도록 관리주체에 알린다."
                        if lv is not None and lv >= 2 else
                        "법정 안전등급과 다른 척도다. 현재 우선 확인 신호 없음."),
            "has_legal_threshold": False,
        })
    vm = obs.get("velocity_maxabs_mm_yr")
    if vm is not None or obs.get("velocity_median_mm_yr") is not None:
        g = _guideline(rules, "기초")
        rows.append({
            "item": "교면·하부 연간 변위속도 (LOS)",
            "value": " / ".join(x for x in (
                f"중앙 {obs['velocity_median_mm_yr']:+.2f}" if obs.get("velocity_median_mm_yr") is not None else "",
                f"최대 |{vm:.2f}|" if vm is not None else "") if x) + " mm/년",
            "basis": (g["출처"] if g else "세부지침 표 1.21") + " — 부등침하·기울음은 서술 기준(d·e)",
            "reading": ("세부지침은 기초 부등침하·교대/교각 기울음을 mm 수치 없이 서술로 d(기울음)·"
                        "e(상부구조 단차·파손) 로 평가한다. 위성 변위는 이 판단의 현장 확인 대상을 "
                        "고르는 근거로만 쓴다."),
            "has_legal_threshold": False,
        })
    if obs.get("cumulative_maxabs_mm") is not None:
        g = _guideline(rules, "거더 처짐")
        rows.append({
            "item": "누적 변위 최대",
            "value": f"{obs['cumulative_maxabs_mm']:.1f} mm",
            "basis": (g["출처"] if g else "세부지침 표 1.14·1.15") + " — 처짐 수치 기준 없음",
            "reading": ("상태평가표의 처짐은 '과대처짐으로 안전성 저하'(e) 서술뿐이다. inframon 잔존수명의 "
                        "L/800 은 설계 관례에서 빌린 가정이며 법정 한계가 아니다."),
            "has_legal_threshold": False,
        })
    if obs.get("deck_vs_ground_mm_per_c") is not None:
        rows.append({
            "item": "온도 민감도 (교면/지반)",
            "value": f"{obs['deck_vs_ground_mm_per_c']}",
            "basis": "세부지침 표 1.22 교량받침 — 수치 기준은 탄성받침 전단변형(고무두께의 0.7·1.5배)뿐",
            "reading": ("받침 신축기능 불량은 육안·계측으로 판정한다. 위성은 고무두께 대비 전단변형을 "
                        "직접 재지 못한다 — 교면과 지반의 열거동이 크게 다르면 받침 점검을 권한다."),
            "has_legal_threshold": True,
        })
    rows.append({
        "item": "교량받침 이동량·균열폭",
        "value": "위성으로 관측 불가",
        "basis": "세부지침 표 1.19~1.22 (균열폭 0.1/0.3/0.5/1.0mm, 받침 전단변형)",
        "reading": "법정 수치 기준이 있는 항목은 육안·접촉 계측 대상이다. InSAR 해상도(5×20m)로는 잴 수 없다.",
        "has_legal_threshold": True,
    })
    return rows


# ── 묶음 ────────────────────────────────────────────────────────────────
def build_legal_table(meta: dict, obs: dict | None = None, *, today: date | None = None,
                      rules: dict | None = None) -> dict:
    """bridge.json 같은 교량 메타 + 관측 요약 → 법정 근거표(JSON 직렬화 가능).

    meta 키: length_m, max_span_m, n_spans, bridge_type, facility_class(공식 종별),
             safety_grade(A~E), inspections{정기안전점검: 날짜, ...}, road_kind
    """
    rules = rules or load_rules()
    fc = classify_facility(length_m=meta.get("length_m"), max_span_m=meta.get("max_span_m"),
                           n_spans=meta.get("n_spans"), bridge_type=meta.get("bridge_type"),
                           official=meta.get("facility_class"), rules=rules)
    g = (str(meta.get("safety_grade") or "").strip().upper()[:1]) or None
    grade_rule = next((r for r in rules["안전등급"] if r["등급"] == g), None) if g else None
    sched = inspection_schedule(facility_class=fc["class"], grade=g,
                                last=meta.get("inspections") or {}, today=today,
                                road_kind=meta.get("road_kind"), rules=rules)
    laws = [{"name": r["명칭"], "no": r.get("번호") or r.get("판"), "effective": str(r["시행일"]) if r.get("시행일") else None,
             "verified": bool(r.get("확인", True))} for r in rules["법령"]]
    return {
        "schema": "inframon.legal/1.0",
        "generated": (today or date.today()).isoformat(),
        "facility_class": fc,
        "safety_grade": {"grade": g, "name": grade_rule["명칭"] if grade_rule else None,
                         "state": grade_rule["상태"] if grade_rule else None,
                         "basis": _src(grade_rule) if grade_rule else "시행령 별표 8",
                         "source": meta.get("safety_grade_source")},
        "inspections": sched,
        "findings": observation_findings(obs or {}, rules=rules),
        "laws": laws,
        "remote_sensing": [r["내용"].strip() for r in rules["원격탐사_관련"] if r.get("확인")],
        "notice": NOTICE,
    }


def to_json(table: dict) -> str:
    return json.dumps(table, ensure_ascii=False, indent=1)
