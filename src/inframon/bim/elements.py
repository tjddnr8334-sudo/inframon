"""BIM 부재 테이블과 점↔부재 연결(association).

좌표 정합이 끝나면 다음 질문은 "이 InSAR 점은 **어느 부재**인가"다. IFC 부재는
GUID 로 식별되고 로컬 좌표계의 축정렬 경계상자(AABB)로 근사할 수 있다. 여기서는
IFC 파싱과 무관하게 **부재 테이블**(GUID·타입·AABB)만 받아 연결한다 —
`ifc_io` 가 실제 IFC 에서 이 테이블을 뽑아주고, 없으면 손으로 작성해도 된다.

연결 규칙:
  ① 점이 부재 AABB 안(허용오차 포함) → 그 부재, 거리 0
  ② 아니면 AABB 표면까지 최단거리가 `max_dist_m` 이내인 최근접 부재
  ③ 둘 다 아니면 미연결(강제로 붙이지 않는다 — 틀린 부재에 값을 넣는 것보다 낫다)

InSAR 부재 라벨(deck/pier/abutment/bearing)과 BIM 부재 타입이 어긋나면 값을 버리지
않고 **불일치 플래그**를 남긴다. 실제로 어긋나는 이유는 대개 정합 오차이거나 CV
라벨 오류이고, 어느 쪽인지는 사람이 판단해야 한다.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..contracts.schema import MEMBER_TYPES

# IFC 엔티티 타입 → inframon 표준 부재 라벨. 부분일치(소문자)로 본다.
_IFC_TO_MEMBER = [
    ("ifcbearing", "bearing"),
    ("ifcslab", "deck"), ("ifcplate", "deck"), ("ifcbeam", "deck"), ("ifcgirder", "deck"),
    ("ifcdeck", "deck"), ("ifcbridgepart", "deck"),
    ("ifccolumn", "pier"), ("ifcpier", "pier"), ("ifcpile", "pier"),
    ("ifcfooting", "abutment"), ("ifcabutment", "abutment"), ("ifcwall", "abutment"),
]


def member_from_ifc_type(ifc_type: str | None, name: str | None = None) -> str | None:
    """IFC 타입(+이름)에서 inframon 부재 라벨을 추론. 못 하면 None."""
    hay = f"{ifc_type or ''} {name or ''}".lower()
    for key, member in _IFC_TO_MEMBER:
        if key in hay:
            return member
    for member in MEMBER_TYPES:                     # 이름에 한/영 부재명이 직접 있으면
        if member in hay:
            return member
    for ko, member in (("바닥판", "deck"), ("상판", "deck"), ("거더", "deck"),
                       ("교각", "pier"), ("교대", "abutment"), ("받침", "bearing")):
        if ko in hay:
            return member
    return None


@dataclass
class Element:
    """BIM 부재 — IFC 로컬 좌표계의 AABB 로 근사."""
    guid: str
    name: str = ""
    ifc_type: str = ""
    member: str | None = None            # inframon 표준 라벨(없으면 타입에서 추론)
    bbox_min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bbox_max: tuple[float, float, float] = (0.0, 0.0, 0.0)
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.member is None:
            self.member = member_from_ifc_type(self.ifc_type, self.name)
        lo = np.minimum(np.asarray(self.bbox_min, float), np.asarray(self.bbox_max, float))
        hi = np.maximum(np.asarray(self.bbox_min, float), np.asarray(self.bbox_max, float))
        # 파이썬 float 로 되돌린다 — np.float64 가 남으면 json.dumps 가 못 쓴다.
        self.bbox_min = tuple(float(v) for v in lo)
        self.bbox_max = tuple(float(v) for v in hi)

    @property
    def center(self) -> np.ndarray:
        return (np.asarray(self.bbox_min, float) + np.asarray(self.bbox_max, float)) / 2.0


def _read_text(p: Path) -> str:
    """UTF-8 우선, 실패하면 cp949 로 읽는다.

    국내 BIM 도구가 뽑아주는 부재 목록은 cp949(euc-kr)인 경우가 흔하다. 여기서
    막히면 사용자는 원인을 알기 어려우므로 조용히 폴백하되, 둘 다 실패하면 알린다.
    """
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", b"", 0, 1,
                             f"{p} 를 UTF-8/cp949 어느 쪽으로도 읽지 못했습니다")


def load_elements(path: str | Path) -> list[Element]:
    """부재 테이블을 JSON 또는 CSV 에서 읽는다(UTF-8/cp949 자동).

    JSON: `{"elements": [...]}` 또는 리스트. 각 항목은 Element 필드.
    CSV : 헤더 `guid,name,ifc_type,member,xmin,ymin,zmin,xmax,ymax,zmax`
          (member 는 생략 가능 — ifc_type/name 에서 추론).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"부재 테이블이 없습니다: {p}")
    if p.suffix.lower() == ".json":
        raw = json.loads(_read_text(p))
        items = raw.get("elements", raw) if isinstance(raw, dict) else raw
        out = []
        for it in items:
            out.append(Element(
                guid=str(it["guid"]), name=str(it.get("name", "")),
                ifc_type=str(it.get("ifc_type", "")), member=it.get("member"),
                bbox_min=tuple(float(v) for v in it["bbox_min"]),
                bbox_max=tuple(float(v) for v in it["bbox_max"]),
                extra=it.get("extra", {}) or {}))
        return out
    out = []
    with io.StringIO(_read_text(p), newline="") as fh:
        for row in csv.DictReader(fh):
            out.append(Element(
                guid=str(row["guid"]), name=str(row.get("name", "") or ""),
                ifc_type=str(row.get("ifc_type", "") or ""),
                member=(row.get("member") or None),
                bbox_min=(float(row["xmin"]), float(row["ymin"]), float(row.get("zmin", 0) or 0)),
                bbox_max=(float(row["xmax"]), float(row["ymax"]), float(row.get("zmax", 0) or 0))))
    return out


def _aabb_distance(pts: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """점 [N,D] 에서 AABB 표면까지 최단거리 [N]. 내부면 0."""
    d = np.maximum(np.maximum(lo[None, :] - pts, pts - hi[None, :]), 0.0)
    return np.linalg.norm(d, axis=1)


def associate(
    points_local: np.ndarray,
    elements: list[Element],
    *,
    member: np.ndarray | None = None,
    max_dist_m: float = 5.0,
    tol_m: float = 0.5,
    use_z: bool = False,
) -> dict:
    """점 [N,2|3] → 부재 연결. 각 점에 GUID·거리·근거를 준다.

    Args:
        member:     [N] InSAR 부재 라벨 정수(MEMBER_TYPES 인덱스). 주면 BIM 타입과의
                    일치 여부를 검사해 `member_mismatch` 로 남긴다(값을 버리진 않는다).
        max_dist_m: 이 거리를 넘으면 미연결. 억지로 붙이지 않는다.
        tol_m:      AABB 를 이 만큼 부풀려 "내부" 판정(정합 오차·모델 단순화 흡수).
        use_z:      3D 로 연결할지. 수직기준면이 검증된 경우에만 켜야 한다
                    (georef.to_ifc_local 참조).

    Returns:
        guid [N] (미연결은 ""), distance_m [N], inside [N] bool,
        member_mismatch [N] bool, summary dict
    """
    P = np.atleast_2d(np.asarray(points_local, dtype=np.float64))
    dim = 3 if (use_z and P.shape[1] >= 3) else 2
    P = P[:, :dim]
    n = P.shape[0]

    if not elements:
        return {"guid": np.array([""] * n, dtype=object),
                "distance_m": np.full(n, np.inf), "inside": np.zeros(n, dtype=bool),
                "member_mismatch": np.zeros(n, dtype=bool),
                "ambiguous": np.zeros(n, dtype=bool),
                "summary": {"n_points": n, "n_elements": 0, "n_assigned": 0,
                            "reason": "부재 테이블이 비어 있습니다"}}

    J = len(elements)
    dists = np.empty((n, J))       # 허용오차 포함 거리 — 연결 판정에 쓴다
    strict = np.empty((n, J))      # 허용오차 없는 거리 — "진짜 안에 있는가"
    sizes = np.empty(J)
    for j, el in enumerate(elements):
        lo0 = np.asarray(el.bbox_min, float)[:dim]
        hi0 = np.asarray(el.bbox_max, float)[:dim]
        dists[:, j] = _aabb_distance(P, lo0 - tol_m, hi0 + tol_m)
        strict[:, j] = _aabb_distance(P, lo0, hi0)
        sizes[j] = float(np.prod(np.maximum(hi0 - lo0 + 2 * tol_m, 1e-9)))

    # 동률 깨기 — 부재는 서로 맞닿고(상판 바닥 = 교각 상단) 2D 로 보면 상판 AABB 가 교각을
    # 통째로 포함하므로 거리 0 인 후보가 여럿 나온다. 배열 순서로 고르면 부재 배치 순서에
    # 결과가 달라지므로, 증거가 강한 순으로 얹는다(전부 1e-6 반올림 거리 차보다 작아 순서 불변).
    #   ① **허용오차 없이도 안에 있는가** — 허용오차는 아무 부재에도 안 걸리는 점을 구제하려는
    #      것이지, 이미 명확히 어느 부재 안에 있는 점을 뺏어가라는 게 아니다. 상판 바닥과
    #      교각 상단이 맞닿은 지점에서 이 규칙이 없으면 상판 점이 교각으로 넘어간다.
    #   ② InSAR 부재 라벨(CV 가 준 독립 증거) — 다만 CV 분할은 완벽하지 않으므로 ①보다 약하다.
    #   ③ 그래도 동률이면 더 작은(구체적인) 부재 — 교각이 상판보다 특정적이다.
    d = np.round(dists, 6)
    outside = (strict > 1e-9).astype(float) * 1e-7              # ① 엄밀 포함이 아니면 감점
    pen = np.zeros((n, J))
    if member is not None:
        mem = np.asarray(member).ravel().astype(int)
        for j, em in enumerate(e.member for e in elements):
            if em is None:
                continue
            match = np.array([0 <= m < len(MEMBER_TYPES) and MEMBER_TYPES[m] == em for m in mem])
            pen[:, j] = np.where(match, 0.0, 1.0)
        pen *= 1e-8                                             # ②
    rank = np.argsort(np.argsort(sizes)).astype(float)          # ③ 작을수록 우선
    d_tb = d + outside + pen + (rank / max(J, 1)) * 1e-9

    best = np.argmin(d_tb, axis=1)
    best_d = dists[np.arange(n), best]
    ok = best_d <= max_dist_m
    # `inside` 는 **허용오차 없이** 부재 안에 있다는 뜻이다. 확장 박스 기준으로 세면
    # 허용오차를 키울수록 "내부"가 늘어나 신뢰도 지표로 못 쓴다.
    strictly_in = strict[np.arange(n), best] <= 1e-9
    # 모호성은 **동등한 증거**의 후보가 여럿일 때다. 엄밀 포함 여부까지 같아야 진짜 동률이므로
    # ①까지 반영한 키로 센다(안 그러면 허용오차로만 걸린 후보가 모호성을 부풀린다).
    key = d + outside
    ambiguous = ((key <= key.min(axis=1, keepdims=True) + 1e-9).sum(axis=1) > 1) & ok

    guid = np.array([""] * n, dtype=object)
    guid[ok] = [elements[j].guid for j in best[ok]]
    inside = ok & strictly_in

    mismatch = np.zeros(n, dtype=bool)
    if member is not None:
        mem = np.asarray(member).ravel().astype(int)
        for i in np.nonzero(ok)[0]:
            em = elements[int(best[i])].member
            if em is None or not (0 <= mem[i] < len(MEMBER_TYPES)):
                continue
            mismatch[i] = MEMBER_TYPES[mem[i]] != em

    assigned = int(ok.sum())
    per_el: dict[str, int] = {}
    for i in np.nonzero(ok)[0]:
        g = elements[int(best[i])].guid
        per_el[g] = per_el.get(g, 0) + 1

    return {
        "guid": guid, "distance_m": best_d, "inside": inside, "member_mismatch": mismatch,
        "ambiguous": ambiguous,
        "summary": {
            "n_points": n, "n_elements": len(elements),
            "n_assigned": assigned,
            "assigned_fraction": round(assigned / n, 4) if n else 0.0,
            "n_inside": int(inside.sum()),
            "n_ambiguous": int(ambiguous.sum()),
            "n_member_mismatch": int(mismatch.sum()),
            "n_elements_covered": len(per_el),
            "elements_without_points": [e.guid for e in elements if e.guid not in per_el],
            "dim": dim, "max_dist_m": float(max_dist_m), "tol_m": float(tol_m),
            "median_distance_m": (round(float(np.median(best_d[ok])), 3) if assigned else None),
        },
    }
