"""실측 제원으로 **그 교량에 맞는** 프록시 부재 모델을 만든다.

일반 프록시 IFC(30m 3경간·폭 12m 같은 표준 모형)를 실 교량에 얹으면 결합은 되지만
**부재가 실제와 다르다**. 청양교(90m·2경간·폭 22m) 데이터를 일반 거더 프록시에 붙였더니
데크 관측점이 상판이 아니라 **교각 두부(PierCap)** 에 붙었다 — 프록시의 데크가 8m 인데
청양교 데크는 10m 라서다. 그렇게 결합한 부재별 통계는 정상처럼 보이면서 전부 틀린다.

여기서는 **표준데이터 실측**(연장·경간수·폭·교량높이)으로 부재를 세운다:

    상판 1 · 교각 (경간수−1) · 교각 두부 · 교대 2

정확한 BIM 이 있으면 당연히 그쪽이 낫다(`--ifc`). 이건 IFC 가 없는 임의 교량에서
**부재 단위 결합을 근거 있게** 하기 위한 대체물이고, 산출물에 그 사실을 남긴다.

좌표계: IFC 로컬(원점=교량 중심, x=종축, y=횡축, z=지면 0 기준 위쪽).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .elements import Element

# 형고(거더 높이) 기본 비율 — 형식별 대표값이 있으면 그것을 쓴다.
DEFAULT_DEPTH_RATIO = 1 / 20
DEFAULT_DECK_THICKNESS_M = 0.3
PIER_WIDTH_M = 3.0          # 교각 종축 두께
CAP_OVERHANG_M = 1.0        # 교각 두부가 교각보다 종축으로 더 나온 길이
ABUTMENT_LEN_M = 4.0


def _guid(*parts: object) -> str:
    """이름에서 만든 안정적인 22자 식별자.

    실 IFC 의 GlobalId 와 형식만 맞춘 대체값이다. 같은 제원이면 같은 값이 나와야
    재실행해도 부재 결합이 유지된다(무작위 UUID 면 매번 결합이 끊긴다).
    """
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_$"
    n = int(h[:28], 16)
    out = []
    while len(out) < 22:
        n, r = divmod(n, len(alphabet))
        out.append(alphabet[r])
    return "".join(reversed(out))


def bridge_elements(*, length_m: float, width_m: float, n_spans: int = 1,
                    clearance_m: float = 5.0, deck_depth_m: float | None = None,
                    deck_thickness_m: float = DEFAULT_DECK_THICKNESS_M,
                    name: str = "bridge") -> list[Element]:
    """실측 제원 → 부재 목록(IFC 로컬 좌표).

    length_m·width_m·n_spans·clearance_m(형하고)는 전국교량표준데이터에서 온다.
    deck_depth_m(형고)는 모르면 경간의 1/20 로 둔다.
    """
    if length_m <= 0 or width_m <= 0:
        raise ValueError("연장·폭이 있어야 부재를 세울 수 있습니다.")
    n_spans = max(1, int(n_spans))
    span = length_m / n_spans
    depth = float(deck_depth_m) if deck_depth_m else max(0.4, span * DEFAULT_DEPTH_RATIO)
    z_deck_bot = float(clearance_m)                 # 형하고 = 지면~상판 아래
    z_deck_top = z_deck_bot + depth + deck_thickness_m
    x0, x1 = -length_m / 2, length_m / 2
    y0, y1 = -width_m / 2, width_m / 2
    els: list[Element] = []

    def add(kind: str, nm: str, member: str, lo, hi) -> None:
        els.append(Element(guid=_guid(name, nm), name=nm, ifc_type=kind, member=member,
                           bbox_min=tuple(float(v) for v in lo),
                           bbox_max=tuple(float(v) for v in hi),
                           extra={"source": "proxy_from_specs"}))

    # 상판 — 관측점(데크 PS/DS)이 붙어야 할 부재
    add("IfcSlab", "Deck#1", "deck", (x0, y0, z_deck_bot), (x1, y1, z_deck_top))

    # 교각 + 두부 (경간 경계마다)
    for i in range(1, n_spans):
        xc = x0 + span * i
        add("IfcColumn", f"Pier#{i}", "pier",
            (xc - PIER_WIDTH_M / 2, y0, 0.0), (xc + PIER_WIDTH_M / 2, y1, z_deck_bot))
        add("IfcBuildingElementProxy", f"PierCap#{i}", "pier",
            (xc - PIER_WIDTH_M / 2 - CAP_OVERHANG_M, y0, z_deck_bot - 0.6),
            (xc + PIER_WIDTH_M / 2 + CAP_OVERHANG_M, y1, z_deck_bot))

    # 교대 2 (양 끝)
    for i, xe in enumerate((x0 - ABUTMENT_LEN_M, x1), start=1):
        add("IfcBuildingElementProxy", f"Abutment#{i}", "abutment",
            (xe, y0, 0.0), (xe + ABUTMENT_LEN_M, y1, z_deck_bot))
    return els


def elements_from_profile(profile, *, name: str = "bridge") -> list[Element]:
    """교량 제원 객체(BridgeProfile 등) → 부재 목록. 실측이 없으면 만들지 않는다."""
    ex = getattr(profile, "extra", None) or {}
    length = getattr(profile, "length_m", None)
    width = getattr(profile, "width_m", None)
    if not length or not width:
        raise ValueError("연장·폭 실측이 없어 프록시 부재를 세울 수 없습니다 "
                         "— 표준데이터 CSV 를 넣거나 --bridge-profile 로 지정하세요.")
    n_spans = int(ex.get("n_spans") or 1)
    clearance = ex.get("height_m") or ex.get("clearance_m") or 5.0
    return bridge_elements(length_m=float(length), width_m=float(width), n_spans=n_spans,
                           clearance_m=float(clearance),
                           deck_depth_m=getattr(profile, "section_depth_m", None),
                           name=name)


def save_elements_json(elements: list[Element], out_path: str | Path, *,
                       meta: dict | None = None) -> str:
    """부재 목록을 JSON 으로 — `bim.elements.load_elements` 가 그대로 읽는다."""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "inframon.bim.elements/1",
        "note": "실측 제원으로 생성한 프록시 부재 — 실 IFC 가 아니다(출처 proxy_from_specs)",
        "meta": meta or {},
        "elements": [
            {"guid": e.guid, "name": e.name, "ifc_type": e.ifc_type, "member": e.member,
             "bbox_min": list(e.bbox_min), "bbox_max": list(e.bbox_max), "extra": e.extra}
            for e in elements],
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(p)
