"""엔진(bridge-insar-monitor) 산출물 판독 — Streamlit 무관(테스트 가능).

산출물 위치 ``<data>/<name>/`` :
  state.json      교량별 판정(대장 정보·허용변위 비율·궤도별 결과, 시계열 제외)
  alerts.json     이번 주기 교량 알람(신규 경보·등급 상승·확인 필요)
  health.json     처리 오류 알람(자료 지연·다운로드·정합·구역 실패·저장공간)
  history.sqlite  주기별 판정(judgements) · 변위 시계열(series: kind=los|vert|vert_tc) · 처리 이벤트(events)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

LEVELS = ("정상", "관심", "주의", "경고")
KINDS = {"los": "LOS", "vert": "수직 (LOS ÷ cos 입사각)", "vert_tc": "수직 (열 신축 보정)"}


def local_path(p) -> str:
    """WSL 경로(/mnt/e/...) ↔ Windows(E:/...) — 엔진은 WSL, 대시보드는 Windows 에서 돌 수 있다."""
    import os
    p = str(p or "")
    if os.name == "nt" and p.startswith("/mnt/") and len(p) > 6 and p[6] == "/":
        return p[5].upper() + ":" + p[6:]
    return p


def status(level) -> str:
    """판정 코드 → 한글. None=미처리, <0=판정 불가."""
    if level is None:
        return "미처리"
    return "판정 불가" if level < 0 else LEVELS[int(level)]


def _load(root: Path, name: str, default):
    p = Path(root) / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def load_state(root) -> dict:
    return _load(root, "state.json", {"bridges": []})


def load_alerts(root) -> dict:
    return _load(root, "alerts.json", {"alerts": []})


def load_health(root) -> dict:
    return _load(root, "health.json", {"events": []})


def bridge_rows(state: dict) -> list[dict]:
    """목록·지도용 평탄화 행. 판정이 없는 교량도 '미처리'로 포함한다."""
    out = []
    for b in state.get("bridges", []):
        r = b.get("r") or {}
        lv = r.get("lv") if b.get("r") else None
        out.append(dict(id=b["id"], name=b["n"], city=b.get("c"), lat=b["lat"], lon=b["lon"],
                        cls=b.get("cls"), type=b.get("t"), length_m=b.get("len"), year=b.get("yr"),
                        inspect=b.get("gr"), level=lv, status=status(lv), ratio_now=r.get("rn"),
                        ratio_10y=r.get("r10"), d_now_mm=r.get("dn"), beta_now=r.get("bn"),
                        span_m=r.get("sp"), deck_ps=r.get("nps"), review=bool(r.get("rv")),
                        track=r.get("trk")))
    return out


def counts(rows: list[dict]) -> dict:
    c: dict[str, int] = {}
    for r in rows:
        c[r["status"]] = c.get(r["status"], 0) + 1
    return c


def series(root, bridge_id: str) -> dict:
    """교량 시계열 → {track: {kind: [(YYYYMMDD, mm), ...]}} (모두 주변 지반 대비)."""
    db = Path(root) / "history.sqlite"
    if not db.exists():
        return {}
    con = sqlite3.connect(str(db))
    try:
        cols = [c[1] for c in con.execute("pragma table_info(series)")]
        if "kind" not in cols:          # 이전 형식: kind 없이 보정 수직만
            q = "select track, 'vert_tc', date, disp_mm from series where bridge=? order by date"
        else:
            q = "select track, kind, date, disp_mm from series where bridge=? order by date"
        out: dict = {}
        for trk, kind, d, v in con.execute(q, (bridge_id,)):
            out.setdefault(trk, {}).setdefault(kind, []).append((d, v))
        return out
    finally:
        con.close()


def last_dates(root) -> dict:
    """가장 최근 주기의 {(교량 ID, 궤도): 마지막 영상일 YYYYMMDD} — 판정이 어느 날짜 기준인지."""
    db = Path(root) / "history.sqlite"
    if not db.exists():
        return {}
    con = sqlite3.connect(str(db))
    try:
        q = ("select bridge, track, last_date from judgements "
             "where cycle = (select max(id) from cycles) and last_date is not null")
        return {(b, t): str(d) for b, t, d in con.execute(q)}
    finally:
        con.close()


def judgement_history(root, bridge_id: str) -> list[dict]:
    """주기별 판정 이력(등급 변화 추적)."""
    db = Path(root) / "history.sqlite"
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        q = ("select c.run_time, j.track, j.level, j.r_now, j.d_now_mm, j.last_date from judgements j "
             "join cycles c on c.id = j.cycle where j.bridge=? order by c.id")
        return [dict(run=a, track=b, level=c, status=status(c), r_now=d, d_now_mm=e, last_date=f)
                for a, b, c, d, e, f in con.execute(q, (bridge_id,))]
    finally:
        con.close()
