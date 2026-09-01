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

    per_date = np.nanmax(cri, axis=0)                              # [M] 시점별 최대 CRI
    summary = []
    for i, d in enumerate(dates):
        summary.append({
            "bridge_id": bridge_id,
            "source": source,
            "observed_at": d,
            "warning_level": _level(float(per_date[i]), thresholds),
            "cri_global_max": round(float(per_date[i]), 4),
            "critical_members": [],
            "summary_json": {
                "n_points": int(cri.shape[0]), "n_dates": int(cri.shape[1]),
                "date_range": [dates[0], dates[-1]],
                "cri_first": round(float(per_date[0]), 4),
                "cri_last": round(float(per_date[-1]), 4),
                "produced_by": "inframon",
            },
        })

    members = []
    if member is not None and member.size == cri.shape[0]:
        last = cri[:, -1]
        for idx, name in enumerate(MEMBER_TYPES):
            sel = member == idx
            if not sel.any():
                continue
            v = float(np.nanmax(last[sel]))
            members.append({"bridge_id": bridge_id, "member_type": name,
                            "warning_level": _level(v, thresholds),
                            "cri_value": round(v, 4)})
    return {"summary_records": summary, "member_records": members}


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
    meta = f["fram"].attrs.get("meta") if "fram" in f else None
    if meta is not None:
        try:
            d = json.loads(meta.decode() if isinstance(meta, bytes) else str(meta))
            th = d.get("cri_thresholds")
            if isinstance(th, (list, tuple)) and len(th) == 3:
                return [float(x) for x in th]
        except (ValueError, TypeError):
            pass
    return [0.3, 0.6, 0.8]


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
def _post(url: str, payload: dict, token: str | None, *, method: str = "POST",
          timeout: float = 30.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
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


def push(project_h5: str | Path, bridge_id: int, *, base: str = DEFAULT_BASE,
         token: str | None = None, dry_run: bool = False,
         allow_unreportable: bool = False, target: tuple[float, float] | None = None
         ) -> PushResult:
    """감사 → 레코드 생성 → 전송. '보고 불가' 산출물은 기본적으로 막는다."""
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
    got = _post(f"{base.rstrip('/')}/api/ingest/sensing/", recs, token)
    res.summary_n = int(got.get("summary_n", 0))
    res.member_n = int(got.get("member_n", 0))
    for e in got.get("errors", []) or []:
        res.warnings.append(f"플랫폼 오류: {e}")
    return res
