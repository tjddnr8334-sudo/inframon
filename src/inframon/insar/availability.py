"""데이터 가용성 진단 — 상승/하강 트랙 장면 수·시간겹침으로 처리 모드 자동 추천.

내림 트랙이 없거나 적을 때, 또는 시기가 안 겹칠 때 어떤 모드로 갈지 결정한다:
- asc+desc (연직분해)  : 양 궤도 충분 + 취득시기 겹침
- UNION only          : 양 궤도 있으나 시기 안 겹침(연직분해 불가, 점 증가만)
- single LOS          : 한 궤도만 충분
- accumulate          : 데이터 부족 → 누적 대기
- none                : SLC 없음
"""
from __future__ import annotations

from datetime import datetime


def _ymd(s) -> datetime | None:
    try:
        return datetime.strptime(str(s)[:8], "%Y%m%d")
    except Exception:  # noqa: BLE001
        return None


def _overlap_days(a, b) -> int:
    """두 트랙 취득기간 [first,last] 겹침 일수(음수=안 겹침)."""
    a0, a1 = _ymd(a.first_date), _ymd(a.last_date)
    b0, b1 = _ymd(b.first_date), _ymd(b.last_date)
    if None in (a0, a1, b0, b1):
        return 0
    lo, hi = max(a0, b0), min(a1, b1)
    return (hi - lo).days


def assess_availability(groups, min_scenes: int = 15) -> dict:
    """트랙 그룹(각 .flight_direction/.n_scenes/.first_date/.last_date) → 모드 추천.

    반환: asc/desc 최다트랙 요약 + overlap_days + mode + reason(한국어).
    """
    def pick(direction: str):
        cand = [g for g in groups if getattr(g, "flight_direction", "") == direction]
        return max(cand, key=lambda g: g.n_scenes) if cand else None

    a, d = pick("ASCENDING"), pick("DESCENDING")
    an = a.n_scenes if a else 0
    dn = d.n_scenes if d else 0
    overlap = _overlap_days(a, d) if (a and d) else 0

    if an >= min_scenes and dn >= min_scenes and overlap > 0:
        mode = "asc+desc"
        reason = f"양 궤도 충분(asc {an}·desc {dn}) + 시간겹침 {overlap}일 → 연직/EW 분해 가능"
    elif an and dn and overlap <= 0:
        mode = "union"
        reason = f"양 궤도 있으나 취득시기 안 겹침 → UNION(점 증가)만, 연직분해 불가"
    elif max(an, dn) >= min_scenes:
        who = "asc" if an >= dn else "desc"
        mode = "single"
        reason = f"한 궤도({who} {max(an, dn)}장)만 충분 → 단일 LOS"
    elif max(an, dn) > 0:
        mode = "accumulate"
        reason = f"최대 {max(an, dn)}장 < 권장 {min_scenes} → 데이터 누적 대기(계속 촬영됨)"
    else:
        mode = "none"
        reason = "이 위치 SLC 없음"

    def _sum(g):
        return None if g is None else {"path": getattr(g, "path", None),
                                       "frame": getattr(g, "frame", None), "n_scenes": g.n_scenes,
                                       "first": g.first_date, "last": g.last_date}
    return {"ascending": _sum(a), "descending": _sum(d), "overlap_days": overlap,
            "mode": mode, "reason": reason, "min_scenes": min_scenes}
