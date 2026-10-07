"""⑭ Pontifex 연동 — project.h5 를 교량 모니터링 플랫폼에 올린다.

Pontifex(스마트인사이드에이아이)는 전국 교량 33,120개를 담은 GeoDjango 플랫폼이고,
inframon 산출을 JSON API 로 받는다. 목표 체인의 마지막 고리(⑭)가 실제 플랫폼에 닿는
지점이다.

계약(플랫폼 README 4-A 기준):
  · `POST /api/ingest/bridge/`  {name, lon, lat, ...} → {id, seq_no, region, detail_url}
  · `POST /api/ingest/sensing/` {summary_records[], member_records[]} → {summary_n, ...}
  · 헤더 `X-Pontifex-Token` (dev 는 토큰 미설정 시 인증 비활성)
  · warning_level 은 FRAM 등급 0~3(정상/주의/경고/위험)을 그대로 쓴다.

**감사 게이트**: 올리기 전에 `audit.audit_artifact` 를 돌려 '보고 불가' 산출물은 막는다.
래핑 위상(±λ/4 에 갇힌 LOS)이나 교량 위에 점이 없는 광역 필드에서 나온 CRI 를 플랫폼에
올리면, 물리적 의미가 없는 수치가 남의 시스템에서 '측정값'으로 보인다. 되돌리기 어렵다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BASE = "http://localhost:38000"
TOKEN_HEADER = "X-Pontifex-Token"
SOURCE = "inframon"
# FRAM 경보 문자열 → 플랫폼 등급(0~3). 플랫폼이 우리 등급 체계를 그대로 쓴다.
LEVELS = {"정상": 0, "주의": 1, "경고": 2, "위험": 3}
MEMBER_TYPES = ("deck", "pier", "abutment", "bearing")


class PontifexError(RuntimeError):
    """연동 실패 — 감사 차단·인증·네트워크·플랫폼 오류."""


@dataclass
class PushResult:
    bridge_id: int
    summary_n: int = 0
    member_n: int = 0
    dry_run: bool = False
    audit_verdict: str = ""
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        head = "예행(dry-run)" if self.dry_run else "전송"
        return (f"{head} — bridge_id={self.bridge_id} · summary {self.summary_n}건 · "
                f"member {self.member_n}건 · 감사 '{self.audit_verdict}'")


# ── project.h5 → 레코드 ──────────────────────────────────────────────────
def build_records(project_h5: str | Path, bridge_id: int, *,
                  source: str = SOURCE) -> dict[str, list[dict[str, Any]]]:
    """project.h5 → {summary_records, member_records}.

    시점마다 한 건씩 만든다(플랫폼이 CRI 시계열을 그린다). 날짜는 `/insar/date_labels`
    가 있으면 절대일자, 없으면 만들지 않는다 — 합성 데모의 상대일수를 오늘 기준으로
    앵커링해 올리면 플랫폼에 **가짜 관측일**이 박힌다.
    """
    import h5py
    import numpy as np

    p = Path(project_h5)
    with h5py.File(p, "r") as f:
        if "fram/CRI" not in f:
            raise PontifexError(f"{p} 에 /fram/CRI 가 없습니다 — FRAM 까지 돈 산출물이어야 합니다.")
        cri = np.asarray(f["fram/CRI"][()], dtype=np.float64)      # [N, M]
        dates = _iso_dates(f, cri.shape[1])
        thresholds = _thresholds(f)
        member = (np.asarray(f["insar/member"][()]).ravel()
                  if "insar/member" in f else None)
        warning = _fram_meta(f).get("warning") or {}
        cal_max = (round(float(np.nanmax(f["fram/calibrated_risk"][()])), 4)
                   if "fram/calibrated_risk" in f else None)
    if member is not None and member.size != cri.shape[0]:
        member = None

    per_date = np.nanmax(cri, axis=0)                              # [M] 시점별 최대 CRI
    first, last = float(per_date[0]), float(per_date[-1])
    # 플랫폼 4-B 스크립트(scripts/ingest_inframon.py)와 같은 항목을 채운다 — 어느 경로로
    # 올리든 화면에 같은 내용이 나오게.
    detail = {
        "n_points": int(cri.shape[0]), "n_dates": int(cri.shape[1]),
        "date_range": [dates[0], dates[-1]],
        "date_basis": "absolute",
        "cri_first": round(first, 4), "cri_last": round(last, 4),
        "trend": "상승" if last > first * 1.2 else ("하락" if last < first * 0.8 else "안정"),
        "lead_time_days": warning.get("lead_time_days"),
        "basis": warning.get("basis"),
        "function_states": warning.get("function_states") or {},
        "produced_by": "inframon",
    }
    summary = []
    for i, d in enumerate(dates):
        crit = []
        if member is not None:
            hot = np.where(cri[:, i] >= thresholds[1])[0]          # 경고 이상인 점의 부재
            crit = sorted({MEMBER_TYPES[int(member[j])] for j in hot
                           if 0 <= int(member[j]) < len(MEMBER_TYPES)})
        summary.append({
            "bridge_id": bridge_id,
            "source": source,
            "observed_at": d,
            "warning_level": _level(float(per_date[i]), thresholds),
            "cri_global_max": round(float(per_date[i]), 4),
            "calibrated_risk": cal_max,
            "critical_members": crit,
            "summary_json": dict(detail),
        })

    members = []
    if member is not None:
        last_col = cri[:, -1]
        for idx, name in enumerate(MEMBER_TYPES):
            sel = member == idx
            if not sel.any():
                continue
            v = float(np.nanmax(last_col[sel]))
            members.append({"bridge_id": bridge_id, "source": source, "member_type": name,
                            "warning_level": _level(v, thresholds),
                            "cri_value": round(v, 4)})
    return {"summary_records": summary, "member_records": members}


def _fram_meta(f) -> dict:
    meta = f["fram"].attrs.get("meta") if "fram" in f else None
    if meta is None:
        return {}
    try:
        d = json.loads(meta.decode() if isinstance(meta, bytes) else str(meta))
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def _iso_dates(f, n_dates: int) -> list[str]:
    """`/insar/date_labels`(YYYYMMDD) → ISO. 없으면 올리지 않는다."""
    if "insar/date_labels" not in f:
        raise PontifexError(
            "관측일(/insar/date_labels)이 없습니다 — 합성 데모 산출물로 보입니다. "
            "가짜 날짜를 붙여 플랫폼에 올리지 않습니다.")
    out = []
    for x in f["insar/date_labels"][:]:
        s = x.decode() if isinstance(x, bytes) else str(x)
        digits = "".join(ch for ch in s if ch.isdigit())[:8]
        if len(digits) != 8:
            raise PontifexError(f"관측일을 YYYYMMDD 로 해석하지 못했습니다: {s!r}")
        out.append(f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}")
    if len(out) != n_dates:
        raise PontifexError(f"관측일 {len(out)}개 ≠ CRI 시점 {n_dates}개")
    return out


def _thresholds(f) -> list[float]:
    """FRAM 경보 임계 — 산출물이 적어뒀으면 그것을, 없으면 기본값."""
    th = _fram_meta(f).get("cri_thresholds")
    if isinstance(th, (list, tuple)) and len(th) == 3:
        try:
            return [float(x) for x in th]
        except (ValueError, TypeError):
            pass
    # 기본값은 FRAM 엔진과 플랫폼 4-B 스크립트가 쓰는 것과 같아야 한다(한 곳에서 가져온다).
    from .config import PipelineConfig
    return [float(x) for x in PipelineConfig().cri_thresholds]


def _level(cri: float, thresholds: list[float]) -> int:
    lo, mid, hi = thresholds
    if cri >= hi:
        return 3
    if cri >= mid:
        return 2
    if cri >= lo:
        return 1
    return 0


# ── HTTP ────────────────────────────────────────────────────────────────
def _get(url: str, token: str | None = None, *, timeout: float = 60.0):
    return _post(url, None, token, method="GET", timeout=timeout)


def _post(url: str, payload: dict | None, token: str | None, *, method: str = "POST",
          timeout: float = 30.0):
    data = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None else None)
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    if token:
        req.add_header(TOKEN_HEADER, token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        hint = ""
        if e.code == 401:
            hint = " — 토큰(X-Pontifex-Token)을 확인하세요(dev 는 토큰 없이 동작)."
        raise PontifexError(f"{url} → HTTP {e.code}{hint}\n{detail}") from e
    except urllib.error.URLError as e:
        raise PontifexError(
            f"{url} 에 연결하지 못했습니다 — Pontifex 스택이 떠 있는지 확인하세요"
            f"(cd backend && docker compose ps). 원인: {e.reason}") from e
    try:
        return json.loads(body) if body else {}
    except ValueError:
        return {"raw": body[:400]}


def register_bridge(name: str, lat: float, lon: float, *, base: str = DEFAULT_BASE,
                    token: str | None = None, structure_type: str | None = None,
                    material: str | None = None, addr1: str = "", addr2: str = "") -> dict:
    """교량을 플랫폼에 등록하고 부여된 id 를 받는다(WGS84 십진도)."""
    payload = {"name": name, "lat": float(lat), "lon": float(lon),
               "addr1": addr1, "addr2": addr2}
    if structure_type:
        payload["structure_type"] = structure_type
    if material:
        payload["material"] = material
    return _post(f"{base.rstrip('/')}/api/ingest/bridge/", payload, token)


# ── 플랫폼에 이미 있는 교량 찾기 ──────────────────────────────────────────
# 플랫폼은 전국 교량 33,120개를 이미 갖고 있다. 그런데 등록 API 는 같은 교량이 있는지 보지
# 않고 새 행을 만든다 — 찾지 않고 등록하면 지도에 같은 교량이 둘 생기고, 우리 결과는 대장
# 제원·BIM 이 없는 새 행에 붙는다. 그래서 **먼저 찾고, 없을 때만 등록한다.**
MATCH_MAX_M = 300.0     # 이름이 같을 때 받아들이는 거리(대장마다 기준점이 시점·중앙으로 다르다)
MATCH_TIE_M = 20.0      # 같은 이름이 이 차이 안에 둘이면 어느 쪽인지 알 수 없다(상·하행 분리교)


def norm_name(s) -> str:
    """교량명 비교용 — 공백·가운뎃점·전각 괄호만 정리한다((상)/(하) 구분은 남긴다)."""
    out = str(s or "").replace("（", "(").replace("）", ")")
    for ch in " \t·.-_":
        out = out.replace(ch, "")
    return out


def _dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    k = math.cos(math.radians(lat1))
    return math.hypot(lat1 - lat2, (lon1 - lon2) * k) * 111_320.0


def match_bridge(name: str, lat: float, lon: float, candidates: list[dict], *,
                 max_m: float = MATCH_MAX_M) -> tuple[dict | None, str]:
    """후보 [{id,name,lat,lon}] 에서 **이름이 같고 가까운** 하나 → (후보|None, 사유).

    이름이 다르면 가까워도 받지 않는다 — 대장이 달라 30 m 안에 다른 교량(윗샘밭교/세월교)이
    흔하다. 같은 이름이 같은 자리에 둘이면(상·하행) 고르지 않고 '모호'로 돌려준다.
    """
    want = norm_name(name)
    near = sorted(((_dist_m(lat, lon, c["lat"], c["lon"]), c) for c in candidates
                   if norm_name(c.get("name")) == want), key=lambda x: x[0])
    near = [(d, c) for d, c in near if d <= max_m]
    if not near:
        return None, "이름이 같은 교량이 가까이 없음"
    if len(near) > 1 and near[1][0] - near[0][0] < MATCH_TIE_M:
        return None, f"같은 이름이 {len(near)}개 겹침(상·하행 분리교 등) — 사람이 골라야 함"
    return near[0][1], f"{near[0][0]:.0f} m"


def region_codes(name: str, *, base: str = DEFAULT_BASE, token: str | None = None) -> list[str]:
    """시·군·구 이름 → 플랫폼 지역 코드(같은 이름이 여러 도에 있으면 여럿: 고성군·중구)."""
    from urllib.parse import quote
    rows = _get(f"{base.rstrip('/')}/api/regions/search?q={quote(name)}&limit=20", token)
    return [r["code"] for r in rows or [] if r.get("level") == 2 and r.get("name") == name]


def region_bridges(code: str, *, base: str = DEFAULT_BASE,
                   token: str | None = None) -> list[dict]:
    """지역 코드의 교량 [{id,name,lat,lon}].

    좌표로 직접 찾는 `bbox` 조회는 플랫폼 1.0 에서 500(PointField 'bbox' 미지원)이라 쓰지
    못한다 — 시·군·구 단위로 받아 여기서 거리를 잰다.
    """
    g = _get(f"{base.rstrip('/')}/api/bridges.geojson?region={code}&limit=10000", token)
    out = []
    for ft in (g or {}).get("features") or []:
        pr, xy = ft.get("properties") or {}, (ft.get("geometry") or {}).get("coordinates") or []
        if pr.get("id") is not None and len(xy) >= 2:
            out.append({"id": int(pr["id"]), "name": pr.get("name"),
                        "lat": float(xy[1]), "lon": float(xy[0]), "region": code})
    return out


def find_bridge(name: str, lat: float, lon: float, *, base: str = DEFAULT_BASE,
                token: str | None = None) -> tuple[dict | None, str]:
    """이름·좌표로 플랫폼의 기존 교량을 찾는다 → ({id,name,lat,lon}|None, 사유)."""
    from urllib.parse import quote
    hits = _get(f"{base.rstrip('/')}/api/bridges/search?q={quote(name)}&limit=50", token) or []
    hits = [h for h in hits if norm_name(h.get("name")) == norm_name(name)]
    if not hits:
        return None, "플랫폼에 같은 이름의 교량이 없음"
    ids = {int(h["id"]) for h in hits}
    # 검색 결과에는 좌표가 없다 — 후보가 속한 시·군·구의 교량 목록에서 좌표를 얻는다.
    sigungu = {" ".join(str(h.get("region_name") or "").split()[1:]) for h in hits} - {""}
    cands: list[dict] = []
    for sg in sorted(sigungu):
        for code in region_codes(sg, base=base, token=token):
            cands += [c for c in region_bridges(code, base=base, token=token) if c["id"] in ids]
    return match_bridge(name, lat, lon, cands)


def ensure_bridge(name: str, lat: float, lon: float, *, base: str = DEFAULT_BASE,
                  token: str | None = None, **reg_kw) -> dict:
    """찾으면 그 id, 없으면 등록 → {id, how: 'matched'|'registered', note, ...}."""
    got, why = find_bridge(name, lat, lon, base=base, token=token)
    if got is not None:
        return {"id": got["id"], "name": got.get("name"), "how": "matched",
                "note": f"기존 교량과 일치({why})", "detail_url": f"/bridge/{got['id']}/"}
    new = register_bridge(name, lat, lon, base=base, token=token, **reg_kw)
    return {**new, "how": "registered", "note": f"새로 등록({why})"}


# ── 이미 올라가 있는 것 ──────────────────────────────────────────────────
def platform_dates(bridge_id: int, *, source: str = SOURCE, base: str = DEFAULT_BASE,
                   token: str | None = None) -> list[str]:
    """플랫폼에 있는 이 교량·소스의 관측일(YYYY-MM-DD, 플랫폼 표시 기준 KST)."""
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))
    rows = _get(f"{base.rstrip('/')}/api/bridge/{int(bridge_id)}/timeseries", token) or []
    out = []
    for r in rows:
        if r.get("source") != source:
            continue
        s = str(r.get("observed_at") or "")
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            out.append((dt.astimezone(kst) if dt.tzinfo else dt).strftime("%Y-%m-%d"))
        except ValueError:
            out.append(s[:10])
    return out


def delete_sensing(bridge_id: int, *, source: str = SOURCE, base: str = DEFAULT_BASE,
                   token: str | None = None) -> dict:
    """이 교량·소스의 센싱 레코드를 플랫폼에서 지운다(다른 소스·다른 교량은 건드리지 않는다)."""
    from urllib.parse import quote
    return _post(f"{base.rstrip('/')}/api/ingest/sensing/?bridge_id={int(bridge_id)}"
                 f"&source={quote(source)}", None, token, method="DELETE")


def push(project_h5: str | Path, bridge_id: int, *, base: str = DEFAULT_BASE,
         token: str | None = None, dry_run: bool = False,
         allow_unreportable: bool = False, target: tuple[float, float] | None = None,
         replace: bool = False) -> PushResult:
    """감사 → 레코드 생성 → 전송. '보고 불가' 산출물은 기본적으로 막는다.

    replace: 올리기 전에 이 교량의 `inframon` 소스 레코드를 지운다. 플랫폼은 관측일별로
    덮어쓰기만 하므로, 재처리로 시점이 바뀌었거나 플랫폼의 'inframon 연동 실행'(합성 데모)
    결과가 같은 소스로 남아 있으면 옛 값이 섞인 채 최고 등급으로 표시된다.
    """
    from .audit import NO, audit_artifact

    a = audit_artifact(project_h5, target=target)
    if a.verdict == NO and not allow_unreportable:
        raise PontifexError(
            "감사 결과 '보고 불가' 산출물이라 올리지 않습니다:\n  · "
            + "\n  · ".join(a.reasons)
            + "\n물리적 의미가 없는 수치가 남의 플랫폼에 '측정값'으로 남으면 되돌리기 "
              "어렵습니다. 재처리 후 다시 시도하거나, 사유를 알고도 올리려면 "
              "--pontifex-force 를 쓰세요.")

    recs = build_records(project_h5, bridge_id)
    res = PushResult(bridge_id=bridge_id, dry_run=dry_run, audit_verdict=a.verdict,
                     warnings=list(a.reasons))
    if dry_run:
        res.summary_n = len(recs["summary_records"])
        res.member_n = len(recs["member_records"])
        return res
    if replace:
        gone = delete_sensing(bridge_id, base=base, token=token)
        if gone.get("summary_n"):
            res.warnings.append(f"기존 inframon 레코드 {gone['summary_n']}건을 지우고 올렸습니다")
    else:
        try:
            ours = {r["observed_at"] for r in recs["summary_records"]}
            stale = [d for d in platform_dates(bridge_id, base=base, token=token)
                     if d not in ours]
        except (PontifexError, AttributeError, TypeError, KeyError):
            stale = []                  # 조회를 못 해도 전송은 한다
        if stale:
            res.warnings.append(
                f"플랫폼에 이 산출물에 없는 관측일 {len(stale)}건이 같은 소스로 남아 있습니다"
                f"({min(stale)} ~ {max(stale)}) — 옛 처리 결과나 플랫폼 합성 실행분일 수 "
                "있습니다. 지우고 올리려면 --pontifex-replace")
    got = _post(f"{base.rstrip('/')}/api/ingest/sensing/", recs, token)
    res.summary_n = int(got.get("summary_n", 0))
    res.member_n = int(got.get("member_n", 0))
    for e in got.get("errors", []) or []:
        res.warnings.append(f"플랫폼 오류: {e}")
    return res
