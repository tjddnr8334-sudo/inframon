"""지역 교량 전수 대장 — 공공데이터 한 행 → 법정 종별·점검 기한·위성 관측 가능성.

입력은 `scripts/region_inventory.py` 가 만든 `bridges.csv`
(전국교량표준데이터 열 + lat/lon/length_m + asc_bursts/desc_bursts).

판단 원칙:
- **공식 종별이 우선이다.** '시설물종별등급구분' 1·2·3 은 그대로 쓰고, 연장으로 계산한 종별과
  공식이 계산보다 낮을 때만 표시한다(최대경간장이 없어 계산값은 하한이다). 코드 99 는 1·2·3종이 아니라는 뜻이라 시설물안전법 점검 주기를 매기지 않는다.
- **기한은 '자료 기준일' 로 판단한다.** 공공데이터의 최종점검일은 자료 기준일(예: 2025-12-31)
  까지의 이력이다. 오늘 날짜로 비교하면 그 뒤에 한 점검이 빠져 거의 모든 교량이 '초과'로 보인다.
  그래서 `overdue_at_data_date` (자료 기준일에 이미 지난 기한)와 `due_after_data_date`
  (자료 기준일 뒤에 도래 — 최신 이력 확인 필요)를 나눈다.
- **관측 가능성은 원리 판단이다.** Sentinel-1 화소(약 5 × 20 m)에 비해 짧은 교량은 데크 점이
  잡히기 어렵다. 실제로 점이 잡혔는지는 스택을 돌린 뒤에야 안다(`compute_state`).
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from .basis import _as_date, classify_facility, inspection_schedule, load_rules

SCHEMA = "inframon.registry/1.0"

# 관측 가능성 구간 — docs/강원_전수감시_계획.md §5 와 같다
OBS_TIERS = (
    (100.0, "expected", "관측 기대", "연장 100 m 이상 — 데크에 여러 화소가 걸린다"),
    (50.0, "limited", "제한적", "연장 50–100 m — 데크 화소가 적어 점이 안 잡힐 수 있다"),
    (0.0, "unlikely", "원리상 어려움", "연장 50 m 미만 — 화소(약 5 × 20 m) 몇 개뿐이라 데크 점이 거의 안 잡힌다"),
)

_SUPER_EN = (("현수", "suspension"), ("사장", "cable_stayed"), ("아치", "arch"), ("트러스", "truss"))


def _f(v) -> float | None:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _split(v) -> list[str]:
    return [s for s in str(v or "").split(";") if s.strip()]


def bridge_key(row: dict) -> str:
    """공공데이터 교량 한 행의 안정 키 — 시군구코드 + 교량명 + 시점 좌표(1e-5°)."""
    lat, lon = _f(row.get("lat")), _f(row.get("lon"))
    raw = f"{row.get('시군구코드', '')}|{row.get('교량명', '').strip()}|{lat:.5f}|{lon:.5f}" \
        if lat is not None and lon is not None else f"{row.get('시군구코드', '')}|{row.get('교량명', '')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def observability(length_m: float | None, asc: list[str], desc: list[str]) -> dict:
    tier = OBS_TIERS[-1]
    if length_m is not None:
        tier = next(t for t in OBS_TIERS if length_m >= t[0])
    else:
        tier = (None, "unknown", "연장 미상", "연장이 없어 판단하지 않는다")
    geom = "상승+하강" if asc and desc else ("상승만" if asc else ("하강만" if desc else "영상 없음"))
    return {"tier": tier[1], "label": tier[2], "why": tier[3], "geometry": geom,
            "vertical_separation": bool(asc and desc)}


def registry_row(row: dict, *, rules: dict | None = None, computed_bursts: set[str] | None = None,
                 today: date | None = None) -> dict:
    rules = rules or load_rules()
    length = _f(row.get("length_m") or row.get("교량연장"))
    stype = str(row.get("상부구조형식") or "")
    stype_en = next((en for ko, en in _SUPER_EN if ko in stype), None)
    code = str(row.get("시설물종별등급구분") or "").strip()
    data_date = _as_date(row.get("데이터기준일자")) or today or date.today()

    fc = classify_facility(length_m=length, bridge_type=stype_en,
                           official=code if code in ("1", "2", "3") else None, rules=rules)
    # 공공데이터엔 최대경간장이 없어 계산 종별은 '최소한 이 종별' 이라는 하한이다. 공식이 더 높은
    # 종별(1종 ← 계산 2종)이면 경간장 등 빠진 입력 때문이라 모순이 아니다. 공식이 계산보다 낮을 때만
    # (예: 연장 100 m 이상인데 3종) 자료 확인 대상으로 표시한다.
    fc["mismatch"] = bool(fc["official"] and fc["computed"] and fc["official"] > fc["computed"])
    outside = code == "99"
    if outside:
        fc = {**fc, "class": None, "label": "종외 (공공데이터 코드 99)", "official": 99,
              "mismatch": fc["computed"] is not None,
              "reasons": fc["reasons"] + ["공공데이터가 1·2·3종이 아니라고 표시(99) — 시설물안전법 점검 주기를 매기지 않는다"]}

    grade = (str(row.get("최종안전점검결과") or "").strip().upper()[:1]) or None
    last_kind = str(row.get("최종안전점검유형") or "").strip()
    last_date = _as_date(row.get("최종안전점검일자"))
    kind_map = {"정기점검": "정기안전점검", "정밀점검": "정밀안전점검", "정밀안전진단": "정밀안전진단"}
    last = {kind_map.get(last_kind, last_kind): last_date} if last_date and last_kind else {}

    if outside:
        sched = []
    else:
        sched = inspection_schedule(facility_class=fc["class"], grade=grade, last=last,
                                    today=data_date, road_kind=row.get("도로종류"), rules=rules)
    dues = [s for s in sched if s["applicable"] and s["next_due"]]
    nearest = min(dues, key=lambda s: s["next_due"]) if dues else None
    overdue = [s["kind"] for s in dues if s["next_due"] < data_date.isoformat()]
    after = [s["kind"] for s in dues if s["next_due"] >= data_date.isoformat()]
    no_hist = [s["kind"] for s in sched if s["applicable"] and not s["last"]]

    asc, desc = _split(row.get("asc_bursts")), _split(row.get("desc_bursts"))
    obs = observability(length, asc, desc)
    done = sorted(b for b in asc + desc if computed_bursts and b in computed_bursts)

    return {
        "key": bridge_key(row),
        "name": str(row.get("교량명") or "").strip(),
        "sido": row.get("시도명") or "", "sigungu": row.get("시군구명") or "",
        "sigungu_code": row.get("시군구코드") or "",
        "address": row.get("소재지지번주소") or "",
        "lat": _f(row.get("lat")), "lon": _f(row.get("lon")),
        "length_m": length, "road_kind": row.get("도로종류") or "",
        "superstructure": stype, "built_year": row.get("교량준공연도") or "",
        "manager": row.get("관리기관명") or "",
        "facility_class": fc["class"], "facility_label": fc["label"],
        "facility_computed": fc["computed"], "facility_mismatch": bool(fc["mismatch"]),
        "safety_grade": grade,
        "last_inspection": {"kind": last_kind or None, "date": last_date.isoformat() if last_date else None},
        "data_date": data_date.isoformat(),
        "next_due": nearest["next_due"] if nearest else None,
        "next_due_kind": nearest["kind"] if nearest else None,
        "overdue_at_data_date": overdue,
        "due_after_data_date": after,
        "no_history": no_hist,
        "observability": obs,
        "asc_bursts": asc, "desc_bursts": desc,
        "compute_state": "computed" if done else "pending",
        "computed_bursts": done,
        "legal": {"facility_class": fc, "inspections": sched},
    }


def build_registry(rows: list[dict], *, computed_bursts: set[str] | None = None,
                   today: date | None = None) -> dict:
    rules = load_rules()
    items = [registry_row(r, rules=rules, computed_bursts=computed_bursts, today=today) for r in rows]
    return {"schema": SCHEMA, "generated": (today or date.today()).isoformat(),
            "n": len(items), "summary": summarize(items), "bridges": items}


def summarize(items: list[dict]) -> dict[str, Any]:
    from collections import Counter

    def c(f):
        return dict(Counter(f(i) for i in items).most_common())

    return {
        "by_sigungu": c(lambda i: i["sigungu"]),
        "by_class": c(lambda i: i["facility_label"]),
        "by_grade": c(lambda i: i["safety_grade"] or "미상"),
        "by_observability": c(lambda i: i["observability"]["label"]),
        "by_geometry": c(lambda i: i["observability"]["geometry"]),
        "class_mismatch": sum(i["facility_mismatch"] for i in items),
        "overdue_at_data_date": sum(bool(i["overdue_at_data_date"]) for i in items),
        "computed": sum(i["compute_state"] == "computed" for i in items),
    }
