"""산출물 감사 — "이 project.h5 를 보고에 써도 되는가" 를 파일이 스스로 답하게.

리포에 쌓인 project.h5 는 겉으로는 다 똑같이 '성공한 산출물'로 보인다. 그러나 실제로는
(1) 위상 언래핑을 안 해 LOS 가 ±λ/4 에 갇혔거나, (2) 6km 광역 필드라 대상 교량 위에는
점이 몇 개 없거나, (3) PINN 경간이 실연장의 수백 배로 잡혀 있거나, (4) 어떤 명령으로
만들었는지 기록이 없어 재현이 불가능한 것들이 섞여 있다. 그걸 눈으로 구분할 수 없으면
잘못된 수치가 과제 보고·논문으로 새어 나간다.

여기서는 다섯 가지를 **파일에서 직접 읽어** 표로 만들고 한 줄 판정을 붙인다:
  ① 언래핑    — |LOS| > λ/4 인 점이 있는가(+ 파일이 스스로 적어둔 표기)
  ② 대상 포함 — 교량 30m 내 점수 / 전체 점수
  ③ 경간 정합 — PINN span_m vs 전국교량표준데이터 실연장
  ④ CRI       — 최악값(참고 지표)
  ⑤ 재현      — 실행 기록(pipeline_report.json·prov_* attrs·track_source)

판정은 세 단계다 — **보고 가능 / 조건부 / 보고 불가**. 애매하면 낮춘다.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .insar.track_preflight import LOS_WRAP_LIMIT_MM

DECK_RADIUS_M = 30.0
WIDE_FIELD_FRAC = 0.01          # 교량 30m 내 비율이 이보다 낮으면 광역 필드
SPAN_RATIO_MAX = 2.0            # PINN 경간이 실연장의 2배를 넘으면 퇴화 입력

OK, COND, NO = "보고 가능", "조건부", "보고 불가"


@dataclass
class ArtifactAudit:
    path: str
    exists: bool = True
    n_points: int | None = None
    n_dates: int | None = None
    # ① 언래핑
    unwrapped: bool | None = None
    los_abs_max: float | None = None
    unwrap_note: str = ""
    # ② 대상 포함
    target: tuple[float, float] | None = None
    n_within_deck: int | None = None
    deck_frac: float | None = None
    dist_min_m: float | None = None
    # ③ 경간 정합
    pinn_span_m: float | None = None
    official_length_m: float | None = None
    span_ratio: float | None = None
    official_name: str | None = None
    # ④ CRI
    cri_worst: float | None = None
    # ⑤ 재현
    has_run_record: bool = False
    record_note: str = ""
    # 판정
    verdict: str = COND
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit_artifact(path: str | Path, *, target: tuple[float, float] | None = None,
                   bridge_csv: str | Path | None = None) -> ArtifactAudit:
    """project.h5 하나를 감사한다. 예외를 내지 않고 리포트로 돌려준다."""
    import h5py
    import numpy as np

    p = Path(path)
    a = ArtifactAudit(path=str(p))
    if not p.exists():
        a.exists = False
        a.verdict, a.reasons = NO, ["파일이 없습니다"]
        return a

    try:
        with h5py.File(p, "r") as f:
            if "insar/los" not in f:
                a.verdict, a.reasons = NO, ["/insar/los 가 없습니다 — 산출물이 아닙니다"]
                return a
            los = np.asarray(f["insar/los"][()], dtype=np.float64)
            xyz = np.asarray(f["insar/xyz"][()]) if "insar/xyz" in f else None
            a.n_points, a.n_dates = int(los.shape[0]), int(los.shape[1])
            src = _json_attr(f["insar"], "track_source") if "insar" in f else {}
            a.unwrapped, a.unwrap_note = _unwrap_state(los, src)
            v = los[np.isfinite(los)]
            a.los_abs_max = float(np.abs(v).max()) if v.size else None
            if "fram" in f:
                a.cri_worst = _worst_cri(f)
            if "pinn" in f:
                inp = _json_attr(f["pinn"], "inputs")
                a.pinn_span_m = _num(inp.get("total_length_m") or inp.get("span_m"))
            a.target = target or _target_from(src, p)
            if a.target and xyz is not None and xyz.shape[1] >= 2:
                d = _dist_m(xyz[:, 0], xyz[:, 1], *a.target)
                a.n_within_deck = int((d <= DECK_RADIUS_M).sum())
                a.deck_frac = a.n_within_deck / max(a.n_points, 1)
                a.dist_min_m = round(float(d.min()), 1)
            a.has_run_record, a.record_note = _run_record(p, f, src)
    except OSError as e:
        a.verdict, a.reasons = NO, [f"열 수 없습니다: {e}"]
        return a

    if a.target:
        _official_span(a, bridge_csv)
    _judge(a)
    return a


# ── ① 언래핑 ────────────────────────────────────────────────────────────
def _unwrap_state(los, src: dict) -> tuple[bool | None, str]:
    """파일이 적어둔 표기를 먼저 믿고, 없으면 값으로 판단한다."""
    import numpy as np

    attrs = (src or {}).get("attrs") or {}
    declared = attrs.get("unwrapped")
    if declared is not None:
        val = str(declared).lower() not in ("false", "0", "")
        return val, "파일 표기"
    text = str(attrs.get("source", ""))
    if "wrapped-phase" in text:
        return False, "track source 문구('wrapped-phase')"
    if "unwrap" in text.lower():
        return True, "track source 문구"
    v = np.abs(los[np.isfinite(los)])
    if v.size == 0:
        return None, "LOS 없음"
    if v.max() > LOS_WRAP_LIMIT_MM:
        return True, f"|LOS|max {v.max():.1f}mm > λ/4"
    outer = float((v > LOS_WRAP_LIMIT_MM / 2).mean())
    if outer >= 0.40:
        return False, f"λ/4 안에 균일 분포(바깥절반 {outer * 100:.0f}%)"
    return None, "변위가 작아 판단 보류"


# ── ③ 경간 정합 ─────────────────────────────────────────────────────────
def _official_span(a: ArtifactAudit, bridge_csv: str | Path | None) -> None:
    try:
        from .public_data import find_bridge_csv, nearest_bridge_profile
        csv = str(bridge_csv) if bridge_csv else find_bridge_csv("data")
        if not csv:
            return
        prof = nearest_bridge_profile(csv, a.target[0], a.target[1], max_km=1.0)
    except Exception:  # noqa: BLE001 — 표준데이터가 없어도 감사는 계속한다
        return
    if prof is None or not getattr(prof, "length_m", None):
        return
    a.official_name = getattr(prof, "name", None)
    a.official_length_m = float(prof.length_m)
    if a.pinn_span_m:
        a.span_ratio = round(a.pinn_span_m / a.official_length_m, 2)


# ── ④ CRI ───────────────────────────────────────────────────────────────
def _worst_cri(f) -> float | None:
    import numpy as np

    ref = _json_attr(f["fram"], "reference_range")
    if ref.get("worst_cri") is not None:
        return float(ref["worst_cri"])
    if "fram/CRI" in f:
        cri = np.asarray(f["fram/CRI"][()], dtype=np.float64)
        return float(np.nanmax(cri)) if cri.size else None
    return None


# ── ⑤ 재현 기록 ─────────────────────────────────────────────────────────
def _run_record(p: Path, f, src: dict) -> tuple[bool, str]:
    """어떤 명령·어떤 입력으로 나왔는지 되짚을 수 있는가."""
    marks = []
    if (p.parent / "pipeline_report.json").exists():
        marks.append("pipeline_report.json")
    prov = [k for k in f.attrs if str(k).startswith("prov_")]
    if prov:
        marks.append("prov_attrs")
    if (src or {}).get("path"):
        marks.append("track_source")
    return bool(marks), "·".join(marks) if marks else "없음"


# ── 판정 ────────────────────────────────────────────────────────────────
def _judge(a: ArtifactAudit) -> None:
    """애매하면 낮춘다 — 잘못된 수치가 보고로 새는 것보다 보수적인 판정이 낫다."""
    hard, soft = [], []
    if a.unwrapped is False:
        hard.append(f"위상 언래핑 안 됨({a.unwrap_note}) → 변위·CRI 무의미")
    elif a.unwrapped is None:
        soft.append(f"언래핑 여부 불명({a.unwrap_note})")
    if a.n_within_deck == 0:
        hard.append(f"대상 교량 30m 내 0점(최근접 {a.dist_min_m}m)")
    elif a.deck_frac is not None and a.deck_frac < WIDE_FIELD_FRAC:
        soft.append(f"광역 필드 — 30m 내 {a.n_within_deck}/{a.n_points}"
                    f"({a.deck_frac * 100:.2f}%)")
    if a.span_ratio is not None and a.span_ratio > SPAN_RATIO_MAX:
        hard.append(f"PINN 경간 {a.pinn_span_m:.0f}m 가 실연장 "
                    f"{a.official_length_m:.0f}m 의 {a.span_ratio:.1f}배")
    if a.target is None:
        soft.append("대상 좌표 미지정 — 교량 포함 여부 미확인")
    if not a.has_run_record:
        soft.append("실행 기록 없음 — 재현 불가")
    a.reasons = hard + soft
    a.verdict = NO if hard else (COND if soft else OK)


# ── 표 ──────────────────────────────────────────────────────────────────
def format_table(rows: list[ArtifactAudit]) -> str:
    """마크다운 표 — 문서에 그대로 붙일 수 있게."""
    head = ("| 산출물 | ①언래핑 | ②교량30m내/전체 | ③PINN경간 vs 실연장 | ④CRI | "
            "⑤재현기록 | 판정 |\n|---|---|---|---|---|---|---|")
    lines = [head]
    for r in rows:
        unw = {True: "✅", False: "❌ 래핑", None: "⚠️ 불명"}[r.unwrapped]
        if r.los_abs_max is not None:
            unw += f" ({r.los_abs_max:.1f}mm)"
        deck = ("—" if r.n_within_deck is None
                else f"{r.n_within_deck}/{r.n_points} ({r.deck_frac * 100:.2f}%)")
        if r.pinn_span_m and r.official_length_m:
            span = f"{r.pinn_span_m:.0f}m vs {r.official_length_m:.0f}m (×{r.span_ratio:g})"
        elif r.pinn_span_m:
            span = f"{r.pinn_span_m:.0f}m (실연장 미확인)"
        else:
            span = "—"
        cri = f"{r.cri_worst:.3f}" if r.cri_worst is not None else "—"
        rec = r.record_note if r.has_run_record else "❌ 없음"
        mark = {OK: "✅", COND: "🟡", NO: "❌"}[r.verdict]
        lines.append(f"| `{Path(r.path).parent.name}/{Path(r.path).name}` | {unw} | {deck} | "
                     f"{span} | {cri} | {rec} | {mark} {r.verdict} |")
    return "\n".join(lines)


def format_report(rows: list[ArtifactAudit]) -> str:
    """표 + 행별 사유(왜 그 판정인가)."""
    out = [format_table(rows), ""]
    for r in rows:
        if r.reasons:
            out.append(f"- `{Path(r.path).name}` — " + " · ".join(r.reasons))
    n = {v: sum(1 for r in rows if r.verdict == v) for v in (OK, COND, NO)}
    out += ["", f"합계: {OK} {n[OK]} · {COND} {n[COND]} · {NO} {n[NO]} (총 {len(rows)})"]
    return "\n".join(out)


# ── 보조 ────────────────────────────────────────────────────────────────
def _json_attr(grp, key: str) -> dict:
    v = grp.attrs.get(key)
    if v is None:
        return {}
    try:
        return json.loads(v.decode() if isinstance(v, bytes) else str(v))
    except (ValueError, TypeError):
        return {}


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dist_m(lon, lat, tlat: float, tlon: float):
    import numpy as np

    k = math.cos(math.radians(tlat))
    return np.hypot((np.asarray(lon) - tlon) * 111_320.0 * k,
                    (np.asarray(lat) - tlat) * 111_320.0)


def _target_from(src: dict, p: Path) -> tuple[float, float] | None:
    """대상 좌표를 곁의 기록에서 찾는다.

    산출물마다 좌표를 어디에 남겼는지가 다르다 — ⑭ 레지스트리(같은 폴더), 레시피 폴더의
    bridge_target.json(예: data/honam_project.h5 ↔ data/recipe_honam_asc/), 그리고 원본
    트랙이 있던 폴더. 못 찾으면 None 이고, 그때는 감사표가 '대상 미지정'으로 낮춰 잡는다.
    """
    for cand in (p.parent / "bridge_registry.json", p.parent / "bridge_target.json",
                 p.with_suffix(".json")):
        t = _target_from_json(cand)
        if t:
            return t
    # 교량 이름 토큰으로 레시피 폴더를 찾는다. 산출물 이름 규칙이 제각각이라
    # (honam_project.h5 · jeongjagyo_f120/project_f120.h5 · jeongja_snap/project.h5)
    # 파일명 → 상위 폴더명 → 그 앞부분 순으로 좁은 것부터 넓은 것까지 시도한다.
    for token in _name_tokens(p):
        for root in (p.parent, p.parent.parent):
            if not root.is_dir():
                continue
            for d in sorted(root.glob(f"*{token}*")):
                if d.is_dir():
                    t = (_target_from_json(d / "bridge_target.json")
                         or _target_from_json(d / "bridge_registry.json"))
                    if t:
                        return t
    tp = (src or {}).get("path")
    if tp:                                   # 원본 트랙이 있던 폴더의 기록
        d = Path(tp).parent
        for cand in (d / "bridge_target.json", d.parent / "bridge_target.json"):
            t = _target_from_json(cand)
            if t:
                return t
    return None


def _name_tokens(p: Path) -> list[str]:
    """산출물에서 뽑을 수 있는 교량 이름 후보 — 좁은 것부터."""
    out = []
    for raw in (p.stem.replace("project", "").replace("_exe", ""), p.parent.name):
        tok = raw.strip("_ ")
        if tok and tok not in out and tok not in ("data", ""):
            out.append(tok)
        head = tok.split("_")[0]                # jeongja_snap → jeongja
        if head and head != tok and head not in out:
            out.append(head)
    return out


def _target_from_json(path: Path) -> tuple[float, float] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _find_latlon(data)


def _find_latlon(node) -> tuple[float, float] | None:
    """중첩 구조 어디에 있든 좌표 한 쌍을 찾는다.

    기록 형식이 여럿이다 — 레시피는 selected_lat/lon, ⑭ 레지스트리는
    bridges[].wgs84_center=[lat, lon]. 형식마다 파서를 두면 하나 빠뜨린다.
    """
    if isinstance(node, dict):
        for latk, lonk in (("selected_lat", "selected_lon"), ("lat", "lon")):
            if node.get(latk) is not None and node.get(lonk) is not None:
                return (float(node[latk]), float(node[lonk]))
        c = node.get("wgs84_center")
        if isinstance(c, (list, tuple)) and len(c) == 2:
            return (float(c[0]), float(c[1]))
        for v in node.values():
            got = _find_latlon(v)
            if got:
                return got
    elif isinstance(node, list):
        for v in node:
            got = _find_latlon(v)
            if got:
                return got
    return None


def audit_many(paths, *, targets: dict | None = None,
               bridge_csv: str | Path | None = None) -> list[ArtifactAudit]:
    """여러 산출물을 감사한다. targets 는 {경로: (lat, lon)} 로 개별 지정."""
    targets = targets or {}
    return [audit_artifact(p, target=targets.get(str(p)), bridge_csv=bridge_csv)
            for p in paths]
