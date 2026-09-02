"""B-Maps 연속 반영 — 산출 → 감사 → **통과한 것만** 플랫폼에 올린다.

교량 모니터링은 한 번 올리고 끝나는 일이 아니다. 새 SLC 가 쌓이면 다시 처리해 올려야
하고, 그때마다 **무효 산출물이 섞여 들어가면 안 된다**. 이 모듈은 그 반복을 한 줄로 만든다:

    project.h5 … → 감사(audit) → ✅/🟡 만 전송 → 결과 기록(sync_state.json)

원칙 셋:
  1. **게이트가 먼저다.** 감사 '보고 불가'는 올리지 않는다(래핑 위상·교량 밖·퇴화 PINN).
     남의 플랫폼에 '측정값'으로 남으면 되돌리기 어렵다.
  2. **바뀐 것만 올린다.** 같은 내용을 반복 전송하지 않도록 산출물 지문(내용 해시)을
     기록해 두고 달라졌을 때만 보낸다.
  3. **결과를 남긴다.** 무엇을 언제 왜 올렸는지/걸렀는지 `sync_state.json` 에 적는다 —
     이게 없으면 며칠 뒤 "왜 이 교량만 갱신이 안 됐지" 를 아무도 답할 수 없다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_NAME = "sync_state.json"


@dataclass
class SyncItem:
    """교량 하나의 반영 결과."""

    project_h5: str
    bridge_id: int | None = None
    name: str | None = None
    verdict: str = ""
    action: str = ""            # pushed | skipped_unchanged | blocked | failed | dry-run
    summary_n: int = 0
    member_n: int = 0
    reasons: list[str] = field(default_factory=list)
    fingerprint: str | None = None
    error: str | None = None

    def line(self) -> str:
        mark = {"pushed": "✅", "dry-run": "▷", "skipped_unchanged": "–",
                "blocked": "⛔", "failed": "✗"}.get(self.action, "?")
        tail = (f"summary {self.summary_n}" if self.action in ("pushed", "dry-run")
                else (self.error or (self.reasons[0] if self.reasons else "")))
        return f"  {mark} {self.name or Path(self.project_h5).parent.name} — {self.action} · {tail}"


def fingerprint(project_h5: str | Path) -> str:
    """산출물 지문 — 내용이 바뀌었는지만 알면 되므로 핵심 배열만 해싱한다."""
    import h5py
    import numpy as np

    h = hashlib.sha256()
    try:
        with h5py.File(project_h5, "r") as f:
            for key in ("insar/los", "insar/xyz", "fram/CRI", "insar/date_labels"):
                if key in f:
                    a = np.asarray(f[key][()])
                    h.update(key.encode())
                    h.update(str(a.shape).encode())
                    h.update(np.ascontiguousarray(a).view(np.uint8)[:1 << 20].tobytes())
    except OSError as e:
        return f"unreadable:{e}"
    return h.hexdigest()[:16]


def load_state(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"items": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"items": {}}


def save_state(path: str | Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def register_missing(item: SyncItem, target: dict, *, base: str, token: str | None,
                     dry_run: bool) -> int | None:
    """플랫폼에 교량을 등록하고 받은 id 를 **산출물 곁에 적어** 다음 실행이 재사용하게.

    id 는 사람이 정하는 값이라 지어낼 수 없지만, 플랫폼이 부여해 주는 값은 받아서 쓰면
    된다. 받은 뒤 bridge_target.json 에 적어두지 않으면 매번 새 교량이 만들어진다.
    """
    from .pontifex import PontifexError, register_bridge

    name = target.get("name")
    lat, lon = target.get("lat"), target.get("lon")
    if not name or lat is None or lon is None:
        return None
    if dry_run:
        item.reasons.append(f"등록 예정: {name}({lat},{lon})")
        return None
    try:
        got = register_bridge(str(name), float(lat), float(lon), base=base, token=token)
    except PontifexError as e:
        item.error = f"등록 실패: {str(e)[:200]}"
        return None
    bid = got.get("id")
    if bid is None:
        return None
    side = Path(target["project_h5"]).parent / "bridge_target.json"
    try:
        d = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}
        d["pontifex_id"] = int(bid)
        d.setdefault("name", name)
        side.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass                        # 기록 실패가 전송을 막지는 않는다
    item.reasons.append(f"플랫폼 등록: id={bid}"
                        + (f" · {(got.get('region') or {}).get('name')}"
                           if got.get("region") else ""))
    return int(bid)


def sync(targets: list[dict], *, base: str, token: str | None = None,
         state_path: str | Path = "data/" + STATE_NAME, dry_run: bool = False,
         allow_conditional: bool = True, force: bool = False,
         register: bool = False) -> list[SyncItem]:
    """대상들을 감사한 뒤 통과한 것만 올린다.

    targets: [{"project_h5": ..., "bridge_id": 40001, "name": "청양교",
               "lat": .., "lon": ..(선택 — 감사 ②열용)}, ...]
    allow_conditional: 🟡 조건부도 올릴지(기본 True — 사유는 함께 기록된다).
                       ❌ 보고 불가는 `force` 없이는 절대 올리지 않는다.
    """
    from .audit import NO, OK, audit_artifact
    from .pontifex import PontifexError, push

    state = load_state(state_path)
    items: list[SyncItem] = []
    for t in targets:
        h5 = str(t["project_h5"])
        it = SyncItem(project_h5=h5, bridge_id=t.get("bridge_id"), name=t.get("name"))
        tgt = ((float(t["lat"]), float(t["lon"]))
               if t.get("lat") is not None and t.get("lon") is not None else None)
        a = audit_artifact(h5, target=tgt)
        it.verdict, it.reasons = a.verdict, list(a.reasons)
        it.fingerprint = fingerprint(h5)

        if a.verdict == NO and not force:
            it.action = "blocked"
            items.append(it)
            continue
        if a.verdict != OK and not allow_conditional and not force:
            it.action = "blocked"
            items.append(it)
            continue
        prev = state["items"].get(h5, {})
        if not force and prev.get("fingerprint") == it.fingerprint \
                and prev.get("action") == "pushed":
            it.action = "skipped_unchanged"      # 같은 내용을 반복 전송하지 않는다
            items.append(it)
            continue
        if it.bridge_id is None and register:
            it.bridge_id = register_missing(it, t, base=base, token=token, dry_run=dry_run)
        if it.bridge_id is None:
            it.action = "failed"
            it.error = it.error or (
                "bridge_id 가 없다 — --bmap-register 로 플랫폼에 등록하거나 "
                "bridge_target.json 에 pontifex_id 를 적으세요")
            items.append(it)
            continue
        try:
            res = push(h5, int(it.bridge_id), base=base, token=token, dry_run=dry_run,
                       allow_unreportable=force, target=tgt)
            it.summary_n, it.member_n = res.summary_n, res.member_n
            it.action = "dry-run" if dry_run else "pushed"
        except PontifexError as e:
            it.action, it.error = "failed", str(e)[:300]
        items.append(it)

    state["updated_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["base"] = base
    for it in items:
        if it.action != "skipped_unchanged":
            state["items"][it.project_h5] = {
                k: v for k, v in asdict(it).items() if k != "project_h5"}
    if not dry_run:
        save_state(state_path, state)
    return items


def format_report(items: list[SyncItem], *, dry_run: bool = False) -> str:
    head = "  B-Maps 반영" + (" (예행)" if dry_run else "")
    lines = ["=" * 60, head, "=" * 60]
    lines += [it.line() for it in items]
    n = {k: sum(1 for i in items if i.action == k)
         for k in ("pushed", "dry-run", "skipped_unchanged", "blocked", "failed")}
    lines.append("-" * 60)
    lines.append(f"  전송 {n['pushed'] + n['dry-run']} · 변경없음 {n['skipped_unchanged']} · "
                 f"차단 {n['blocked']} · 실패 {n['failed']}")
    blocked = [i for i in items if i.action == "blocked"]
    if blocked:
        lines.append("  ⛔ 차단 사유(재처리 후 다시):")
        for i in blocked:
            lines.append(f"     · {i.name or Path(i.project_h5).name}: "
                         + " / ".join(i.reasons[:2]))
    lines.append("=" * 60)
    return "\n".join(lines)


def discover(root: str | Path = "data", registry: str | Path | None = None) -> list[dict]:
    """반영 대상 자동 수집 — 산출물 곁의 bridge_target.json·레지스트리에서 좌표·이름을.

    `bridge_id`(플랫폼 id)는 사람이 정해야 하는 값이라 지어내지 않는다. 레지스트리나
    target 파일에 있으면 쓰고, 없으면 그 항목은 '실패(id 없음)'로 보고한다.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for p in sorted(Path(root).rglob("*project*.h5")):
        if str(p) in seen:
            continue
        seen.add(str(p))
        rec: dict[str, Any] = {"project_h5": str(p)}
        for cand in (p.parent / "bridge_target.json", p.parent / "bridge_registry.json"):
            if not cand.exists():
                continue
            try:
                d = json.loads(cand.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rec.setdefault("name", _find(d, ("name", "bridge_name")))
            lat = _find(d, ("selected_lat", "lat"))
            lon = _find(d, ("selected_lon", "lon"))
            if lat is not None and lon is not None:
                rec.setdefault("lat", lat)
                rec.setdefault("lon", lon)
            bid = _find(d, ("pontifex_id", "bridge_id"))
            if isinstance(bid, (int, float)):
                rec.setdefault("bridge_id", int(bid))
        out.append(rec)
    if registry and Path(registry).exists():
        try:
            reg = json.loads(Path(registry).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reg = {}
        ids = {}
        for b in (reg.get("bridges") or []):
            if b.get("project_h5") and b.get("pontifex_id"):
                ids[str(Path(b["project_h5"]))] = int(b["pontifex_id"])
        for rec in out:
            k = str(Path(rec["project_h5"]))
            if k in ids:
                rec["bridge_id"] = ids[k]
    return out


def _find(node, keys):
    if isinstance(node, dict):
        for k in keys:
            if node.get(k) is not None:
                return node[k]
        for v in node.values():
            got = _find(v, keys)
            if got is not None:
                return got
    elif isinstance(node, list):
        for v in node:
            got = _find(v, keys)
            if got is not None:
                return got
    return None
