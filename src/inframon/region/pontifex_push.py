"""광역 감시 판정 → Pontifex(교량 모니터링 플랫폼).

단일 교량 경로(``pontifex.push``)는 ``project.h5`` 의 CRI 시계열을 올린다. 광역 감시는 CRI 가
아니라 **허용변위 비율 판정**(정상·관심·주의·경고)을 내므로 같은 레코드에 섞지 않는다 —
소스를 ``inframon_region`` 으로 따로 두고 ``cri_global_max`` 는 비운다(플랫폼의 CRI 순위·평균에
비율이 CRI 인 것처럼 들어가지 않게). 플랫폼은 소스별 최고 등급을 합쳐 지도 색을 정한다.

올리지 않는 것(사유를 세어 돌려준다):
  · 미처리·판정 불가 — 값이 없다
  · 확인 필요(교량 위 PS 부족·교대부 대체·범위가 등급 경계를 넘음·자료 공백) — 기본 제외.
    남의 플랫폼에 '경고'로 남으면 되돌리기 어렵다. ``include_review`` 로 포함할 수 있고
    그때는 레코드에 ``review`` 와 사유를 같이 적는다.
  · 플랫폼에서 같은 교량을 찾지 못한 것 — 새로 등록하지 않는다(대장이 달라 이름이 다른
    같은 교량이 많다. 자동 등록하면 지도에 교량이 둘씩 생긴다).
"""

from __future__ import annotations

from pathlib import Path

from .. import pontifex as px
from . import results as rr

SOURCE = "inframon_region"
# 광역 판정(정상·관심·주의·경고) → 플랫폼 등급(0 정상 · 1 주의 · 2 경고 · 3 위험).
# **말이 같은 등급끼리** 맞춘다 — 순서대로 0~3 을 주면 우리 '경고'가 플랫폼에 '위험'으로 뜬다.
# '관심'은 플랫폼에 없어 가장 낮은 비정상 등급(주의)으로 보낸다. region.json 의
# ``pontifex_levels`` 로 바꿀 수 있다.
LEVEL_MAP = (0, 1, 1, 2)
BATCH = 500


def _iso(yyyymmdd: str) -> str | None:
    d = "".join(ch for ch in str(yyyymmdd or "") if ch.isdigit())[:8]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else None


def _pct(x) -> str:
    return f"{round(100 * float(x or 0))}%"


def headline(r: dict, status: str) -> str:
    """판정 이유 한 문장 — 플랫폼 교량 화면의 '결론' 칸에 그대로 쓴다(숫자는 엔진 값 그대로)."""
    now, proj = r.get("rn"), r.get("r10")
    if now is None:
        return f"위성 판정: {status}"
    s = f"허용변위의 {_pct(now)}"
    if status == "정상":
        s += " — 허용 범위 안입니다."
    elif proj is not None and proj > (now or 0):
        s += f" — 같은 속도가 이어지면 10년 뒤 {_pct(proj)}로 예측됩니다."
    else:
        s += f" — 판정 {status}."
    if r.get("conf") == "낮음":
        s += f" 교량 위 측정점이 {r.get('nps') or 0}개뿐이라 신뢰도는 낮습니다."
    return s


def action(r: dict, status: str) -> str:
    """관리자가 할 일 한 줄. 법정 점검을 대신하지 않는다 — 어디부터 볼지만 말한다."""
    where = next((w for w in (r.get("wh") or [])[:3] if w), "")
    spot = f" 변위가 큰 곳: {where}." if where and status != "정상" else ""
    if r.get("rv"):
        return "판정을 확정하기 전에 자료 확인이 필요합니다." + (f" ({r['nt']})" if r.get("nt") else "")
    return {"정상": "별도 조치 없음 — 정기점검 일정대로 진행합니다.",
            "관심": "다음 정기점검 때 먼저 확인합니다.",
            "주의": "가까운 시일 안에 현장 확인을 권합니다.",
            "경고": "현장 확인이 필요합니다."}.get(status, "") + spot


def evidence_series(root, bridge_id: str, track) -> dict | None:
    """근거 그래프용 변위 시계열(주변 지반 대비). 열 신축 보정 수직 > 수직 > LOS 순으로 하나."""
    import sqlite3
    try:
        by_kind = (rr.series(root, bridge_id) or {}).get(track) or {}
    except sqlite3.Error:               # 시계열 표가 없는 이력 DB — 근거 그래프 없이 판정만 보낸다
        return None
    for kind in ("vert_tc", "vert", "los"):
        pts = [(_iso(d), v) for d, v in by_kind.get(kind) or [] if v is not None and _iso(d)]
        if pts:
            return {"kind": kind, "label": rr.KINDS[kind], "unit": "mm",
                    "dates": [d for d, _ in pts], "mm": [round(float(v), 1) for _, v in pts]}
    return None


def platform_candidates(cities: dict, *, base: str, token: str | None = None) -> dict:
    """시·군 이름 → 플랫폼 교량 [{id,name,lat,lon}].

    ``cities``: {시·군 이름: (위도, 경도) 우리 교량 중심}. 같은 이름이 여러 도에 있으면(고성군)
    우리 교량과 가까운 쪽을 고른다.
    """
    out: dict[str, list[dict]] = {}
    for city, (clat, clon) in cities.items():
        best, best_d = [], float("inf")
        for code in px.region_codes(city, base=base, token=token):
            cands = px.region_bridges(code, base=base, token=token)
            if not cands:
                continue
            d = px._dist_m(clat, clon, sum(c["lat"] for c in cands) / len(cands),
                           sum(c["lon"] for c in cands) / len(cands))
            if d < best_d:
                best, best_d = cands, d
        out[city] = best
    return out


def plan(root, *, base: str = px.DEFAULT_BASE, token: str | None = None,
         include_review: bool = False, levels=LEVEL_MAP, candidates: dict | None = None,
         evidence: bool = True) -> dict:
    """무엇을 올릴지 정한다(전송 없음) → {records, matched, skipped{사유: n}, unmatched[...]}.

    ``candidates`` 를 주면 플랫폼을 조회하지 않는다(테스트·재사용).
    ``evidence`` 면 레코드에 결론 문장(headline·action)과 근거(변위 시계열)를 같이 싣는다 —
    플랫폼이 등급만이 아니라 '왜'를 같은 화면에서 펼쳐 보일 수 있게.
    """
    state = rr.load_state(root)
    dates = rr.last_dates(root)
    skipped: dict[str, int] = {}
    todo = []
    for b in state.get("bridges", []):
        r = b.get("r") or {}
        lv = r.get("lv") if b.get("r") else None
        why = None
        if lv is None:
            why = "미처리"
        elif lv < 0:
            why = "판정 불가"
        elif r.get("rv") and not include_review:
            why = "확인 필요(제외)"
        elif _iso(dates.get((b["id"], r.get("trk")))) is None:
            why = "판정 기준일 없음"
        if why:
            skipped[why] = skipped.get(why, 0) + 1
        else:
            todo.append(b)

    if candidates is None:
        cen: dict[str, list] = {}
        for b in todo:
            cen.setdefault(b.get("c") or "", []).append((b["lat"], b["lon"]))
        cities = {c: (sum(p[0] for p in ps) / len(ps), sum(p[1] for p in ps) / len(ps))
                  for c, ps in cen.items() if c}
        candidates = platform_candidates(cities, base=base, token=token)

    records, matched, unmatched, taken = [], [], [], {}
    for b in todo:
        r = b["r"]
        hit, why = px.match_bridge(b["n"], b["lat"], b["lon"], candidates.get(b.get("c") or "", []))
        if hit is None:
            unmatched.append({"id": b["id"], "name": b["n"], "city": b.get("c"), "why": why})
            continue
        if hit["id"] in taken:              # 플랫폼 교량 하나에 우리 교량 둘 — 먼저 온 것만
            unmatched.append({"id": b["id"], "name": b["n"], "city": b.get("c"),
                              "why": f"플랫폼 id={hit['id']} 에 이미 '{taken[hit['id']]}' 대응"})
            continue
        taken[hit["id"]] = b["id"]
        lv = int(r["lv"])
        st_name = rr.status(lv)
        extra = {}
        if evidence:
            extra = {"headline": headline(r, st_name), "action": action(r, st_name),
                     "where": next((w for w in (r.get("wh") or [])[:3] if w), ""),
                     "obs_years": r.get("tob"), "d_10y_mm": r.get("d10"),
                     "series": evidence_series(root, b["id"], r.get("trk"))}
        matched.append({"id": b["id"], "name": b["n"], "city": b.get("c"),
                        "pontifex_id": hit["id"], "match": why, "status": rr.status(lv)})
        records.append({
            "bridge_id": hit["id"],
            "source": SOURCE,
            "observed_at": _iso(dates[(b["id"], r.get("trk"))]),
            "warning_level": int(levels[lv]),
            "critical_members": [],
            "summary_json": {
                "kind": "insar_allowable_displacement",
                "status": rr.status(lv),                 # 광역 판정 원래 등급(정상·관심·주의·경고)
                "review": bool(r.get("rv")),
                "confidence": r.get("conf"),
                "ratio_now": r.get("rn"), "ratio_10y": r.get("r10"),
                "ratio_range": [r.get("rlo"), r.get("rhi")],
                "d_now_mm": r.get("dn"), "d_limit_mm": r.get("sa"),
                "beta_now": r.get("bn"), "beta_limit": r.get("ba"),
                "deck_ps": r.get("nps"), "track": r.get("trk"),
                "note": r.get("nt"),
                "engine_bridge_id": b["id"],
                "produced_by": "inframon.region",
                **extra,
            },
        })
    for u in unmatched:
        k = "플랫폼에서 못 찾음" if "이름" in u["why"] else "플랫폼 대응 모호"
        skipped[k] = skipped.get(k, 0) + 1
    return {"title": state.get("title"), "records": records, "matched": matched,
            "skipped": skipped, "unmatched": unmatched}


def push_region(root, *, base: str = px.DEFAULT_BASE, token: str | None = None,
                dry_run: bool = False, include_review: bool = False, levels=LEVEL_MAP,
                report_path=None, candidates: dict | None = None) -> dict:
    """판정을 플랫폼에 올린다 → {sent, errors, levels{등급: n}, skipped, unmatched, ...}."""
    import json

    p = plan(root, base=base, token=token, include_review=include_review, levels=levels,
             candidates=candidates)
    recs = p["records"]
    by_level: dict[int, int] = {}
    for r in recs:
        by_level[r["warning_level"]] = by_level.get(r["warning_level"], 0) + 1
    out = {"title": p["title"], "base": base, "source": SOURCE, "dry_run": dry_run,
           "planned": len(recs), "sent": 0, "errors": [], "levels": by_level,
           "skipped": p["skipped"], "unmatched": p["unmatched"], "matched": p["matched"]}
    if dry_run:
        return out
    for i in range(0, len(recs), BATCH):
        got = px._post(f"{base.rstrip('/')}/api/ingest/sensing/",
                       {"summary_records": recs[i:i + BATCH], "member_records": []}, token,
                       timeout=120.0)
        out["sent"] += int(got.get("summary_n", 0))
        out["errors"] += [str(e) for e in got.get("errors") or []]
    if report_path:
        rp = Path(report_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def format_report(res: dict) -> str:
    names = {0: "정상", 1: "주의", 2: "경고", 3: "위험"}
    head = "  광역 판정 → Pontifex" + (" (예행 — 전송 없음)" if res["dry_run"] else "")
    lines = ["=" * 60, head, "=" * 60,
             f"  대상            : {res.get('title') or ''} → {res['base']} (소스 {res['source']})",
             f"  올릴 교량       : {res['planned']:,}개 · "
             + " · ".join(f"{names[k]} {v:,}" for k, v in sorted(res["levels"].items()))]
    if not res["dry_run"]:
        lines.append(f"  전송            : {res['sent']:,}건" + (f" · 플랫폼 오류 {len(res['errors'])}건"
                                                            if res["errors"] else ""))
    if res["skipped"]:
        lines.append("  올리지 않음     : " + " · ".join(
            f"{k} {v:,}" for k, v in sorted(res["skipped"].items(), key=lambda x: -x[1])))
    for u in res["unmatched"][:8]:
        lines.append(f"     · {u['city']} {u['name']} — {u['why']}")
    if len(res["unmatched"]) > 8:
        lines.append(f"     … 외 {len(res['unmatched']) - 8:,}개")
    lines.append("=" * 60)
    return "\n".join(lines)
