"""실측 제원으로 **그 교량에 맞는** 프록시 부재 모델을 만든다.

일반 프록시 IFC(30m 3경간·폭 12m 같은 표준 모형)를 실 교량에 얹으면 결합은 되지만
**부재가 실제와 다르다**. 청양교(90m·2경간·폭 22m) 데이터를 일반 거더 프록시에 붙였더니
데크 관측점이 상판이 아니라 **교각 두부(PierCap)** 에 붙었다 — 프록시의 데크가 8m 인데
청양교 데크는 10m 라서다. 그렇게 결합한 부재별 통계는 정상처럼 보이면서 전부 틀린다.

여기서는 **표준데이터 실측**(연장·경간수·폭·교량높이)으로 부재를 세운다:

    상판 S1 · 교각 P1…P(n−1) · 교각 코핑 P1C… · 교대 A1·A2

부재 이름은 국내 교량 도면 관례를 따른다 — 교대 A1/A2(시점/종점), 교각 P1부터 시점 쪽에서
순번, 상부구조 S1. 트윈·프로파일·보고서에서 같은 이름으로 부른다.

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


SPAN_LAYOUTS = ("auto", "equal", "measured")


def span_edges(length_m: float, n_spans: int, *, max_span_m: float | None = None,
               layout: str = "auto") -> tuple[list[float], str, str]:
    """교각이 설 x 위치(양 끝 제외) · 실제로 쓴 배치 · 그 이유.

    **등간격이 기본값이었던 것이 문제였다.** `length/n_spans` 로 교각을 죽 세우면
    1,160 m·21경간 성수대교가 55 m 씩 균일한 빗처럼 나온다 — 실제로는 하천을 넘는
    주경간 120 m 가 가운데 있고 나머지가 52 m 다. 부재 위치가 틀리면 그 부재에 결합된
    측점 통계도 같이 틀린다.

    `layout`:
      · ``equal``    연장 ÷ 경간수 로 균등. 근거가 없을 때의 솔직한 기본형.
      · ``measured`` **최대경간장 실측**을 가운데 주경간으로 두고 나머지를 균등 분배.
                     하천 교량은 주운 구간을 한 경간으로 넘기므로 이쪽이 실제에 가깝다.
      · ``auto``     최대경간장이 있으면 measured, 없으면 equal(기본값).

    반환하는 사유 문자열은 산출물에 그대로 남긴다 — 어느 배치를 왜 썼는지 보이게.
    """
    if layout not in SPAN_LAYOUTS:
        raise ValueError(f"span_layout 은 {SPAN_LAYOUTS} 중 하나여야 합니다: {layout}")
    n_spans = max(1, int(n_spans))
    x0 = -length_m / 2

    def _equal(why: str) -> tuple[list[float], str, str]:
        w = length_m / n_spans
        return [x0 + w * i for i in range(1, n_spans)], "equal", why

    if layout == "equal":
        return _equal(f"등간격 지정 — {length_m / n_spans:.0f} m × {n_spans}경간")
    ms = float(max_span_m) if max_span_m else 0.0
    if ms <= 0:
        why = "최대경간장 실측이 없어 등간격"
        if layout == "measured":
            why = "최대경간장 실측이 없어 등간격으로 되돌림(measured 요청)"
        return _equal(why)
    if n_spans < 3 or ms >= length_m:
        return _equal(f"경간수 {n_spans}·최대경간 {ms:.0f} m 로는 주경간을 못 세워 등간격")
    rest = (length_m - ms) / (n_spans - 1)
    if rest <= 0 or rest > ms:
        return _equal(f"최대경간 {ms:.0f} m 가 나머지 경간 {rest:.0f} m 보다 크지 않아 등간격")

    k = (n_spans - 1) // 2                      # 주경간을 가운데에
    edges, x = [], x0
    for i in range(n_spans - 1):
        x += ms if i == k else rest
        edges.append(x)
    return (edges, "measured",
            f"주경간 {ms:.0f} m(실측)를 가운데, 나머지 {n_spans - 1}경간 {rest:.0f} m 균등")


def bridge_elements(*, length_m: float, width_m: float, n_spans: int = 1,
                    clearance_m: float = 5.0, deck_depth_m: float | None = None,
                    deck_thickness_m: float = DEFAULT_DECK_THICKNESS_M,
                    max_span_m: float | None = None, span_layout: str = "auto",
                    bridge_type: str = "girder", superstructure: bool = True,
                    name: str = "bridge") -> list[Element]:
    """실측 제원 → 부재 목록(IFC 로컬 좌표).

    length_m·width_m·n_spans·clearance_m(형하고)는 전국교량표준데이터에서 온다.
    deck_depth_m(형고)는 모르면 경간의 1/20 로 둔다.
    `span_layout` 은 교각 배치 — `span_edges()` 참고(기본 auto: 실측이 있으면 비등간격).
    `bridge_type` 이 사장·현수·아치·트러스면 그 형식의 상부구조까지 세운다
    (`superstructure_elements()`). `superstructure=False` 로 끄면 상판+교각만 나온다.
    """
    if length_m <= 0 or width_m <= 0:
        raise ValueError("연장·폭이 있어야 부재를 세울 수 있습니다.")
    n_spans = max(1, int(n_spans))
    edges, used, why = span_edges(length_m, n_spans, max_span_m=max_span_m,
                                  layout=span_layout)
    span = (max(max_span_m or 0.0, (length_m - (max_span_m or 0.0)) / max(n_spans - 1, 1))
            if used == "measured" else length_m / n_spans)
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
    add("IfcSlab", "S1", "deck", (x0, y0, z_deck_bot), (x1, y1, z_deck_top))

    # 교각 + 두부 (경간 경계마다 — 배치는 span_edges 가 정한다)
    for i, xc in enumerate(edges, start=1):
        add("IfcColumn", f"P{i}", "pier",
            (xc - PIER_WIDTH_M / 2, y0, 0.0), (xc + PIER_WIDTH_M / 2, y1, z_deck_bot))
        add("IfcBuildingElementProxy", f"P{i}C", "pier",
            (xc - PIER_WIDTH_M / 2 - CAP_OVERHANG_M, y0, z_deck_bot - 0.6),
            (xc + PIER_WIDTH_M / 2 + CAP_OVERHANG_M, y1, z_deck_bot))

    # 교대 2 (양 끝)
    for i, xe in enumerate((x0 - ABUTMENT_LEN_M, x1), start=1):
        add("IfcBuildingElementProxy", f"A{i}", "abutment",
            (xe, y0, 0.0), (xe + ABUTMENT_LEN_M, y1, z_deck_bot))

    # 형식별 상부구조 — 사장교는 주탑·케이블, 아치교는 아치리브, 트러스교는 상현재
    if superstructure:
        superstructure_elements(add, bridge_type=(bridge_type or "girder"),
                                length_m=length_m, width_m=width_m,
                                main_span_m=float(max_span_m or 0.0),
                                z_deck_bot=z_deck_bot, z_deck_top=z_deck_top)
    return els


# 상부구조를 따로 세우는 형식 — 나머지(거더·박스·슬래브·라멘)는 상판+교각으로 충분하다.
SUPERSTRUCTURE_TYPES = ("cable_stayed", "suspension", "arch", "truss")
# 결합에 쓰는 부재 — 상부구조(주탑·케이블·아치·트러스)는 평면에서 데크와 겹쳐
# 측점을 뺏으므로 여기서 뺀다(associate 는 기본이 2D 최근접이다).
BIND_MEMBERS = ("deck", "pier", "abutment", "bearing")


CABLE_SEGMENTS = 12          # 케이블 한 본을 몇 토막으로 — 토막이 굵으면 계단으로 보인다


def _seg_boxes(add, base: str, member: str, ifc_type: str,
               pts: list[tuple[float, float, float]], half: float) -> None:
    """꺾은선을 짧은 AABB 사슬로 — 경사재(케이블·아치리브)는 AABB 하나로 못 그린다.

    토막 하나의 AABB 는 그 토막의 **가로×세로를 모두 덮는 상자**다. 그래서 토막이
    길면 케이블이 굵은 계단처럼 보인다 — 토막 수로 두께가 정해지는 셈이라 넉넉히 쪼갠다.
    """
    for k, (p, q) in enumerate(zip(pts[:-1], pts[1:]), start=1):
        lo = [min(p[i], q[i]) - half for i in range(3)]
        hi = [max(p[i], q[i]) + half for i in range(3)]
        add(ifc_type, f"{base}-{k}", member, lo, hi)


def superstructure_elements(add, *, bridge_type: str, length_m: float, width_m: float,
                            main_span_m: float, z_deck_bot: float,
                            z_deck_top: float) -> str:
    """형식에 맞는 상부구조 부재를 세운다 — 사장교는 주탑·케이블, 아치교는 아치리브.

    지금까지 모든 교량을 **상판+교각 상자**로만 세웠다. 그러면 사장교(월드컵·올림픽)도
    트러스교(성수)도 아치교(한강·암사·서강)도 전부 같은 모양이 나온다 — 트윈이라고
    부르기 어렵다. 형식은 이미 CSV 상부구조형식에서 읽고 있으니(`parse_structure_ko`),
    그 형식대로 세운다.

    형상은 제원(연장·폭·주경간)에서 유도한 **프록시**다. 실 도면의 케이블 배치·아치
    라이즈가 아니라 그 형식이 어떤 부재를 갖는지를 보이는 것이고, 산출물에 그렇게 적는다.
    반환: 무엇을 세웠는지 한 줄.
    """
    if bridge_type not in SUPERSTRUCTURE_TYPES:
        return ""
    ms = float(main_span_m) if main_span_m and main_span_m > 0 else 0.0
    if ms <= 0:
        if bridge_type == "truss":
            ms = length_m / 3.0          # 트러스는 전 연장을 덮으므로 주경간이 형상을 안 정한다
        else:
            # 사장·현수·아치는 **주경간이 형상 그 자체**다. 실측이 없다고 연장÷3 으로
            # 지어내면 암사대교 아치 라이즈가 68 m(실제의 두 배 이상)로 나온다.
            # 모르는 것은 세우지 않는다.
            return (f"{bridge_type} — 최대경간장 실측이 없어 형식 상부구조를 세우지 않는다"
                    "(주경간이 형상을 정하는 형식이라 추정으로 만들지 않는다)")
    ms = min(ms, length_m * 0.95)
    hw = width_m / 2
    y_leg = max(hw - 2.0, hw * 0.55)             # 주탑 다리·아치 리브의 횡방향 위치

    if bridge_type in ("cable_stayed", "suspension"):
        h_p = max(0.20 * ms, 14.0)               # 데크 위 주탑 높이
        z_top = z_deck_top + h_p
        n_c = 6                                  # 한 주탑 한 쪽 케이블 수
        for s, xp in ((1, -ms / 2), (2, ms / 2)):
            for j, yl in ((1, -y_leg), (2, y_leg)):
                add("IfcColumn", f"PY{s}-{j}", "pylon",
                    (xp - 2.0, yl - 2.0, 0.0), (xp + 2.0, yl + 2.0, z_top))
            add("IfcBeam", f"PY{s}X", "pylon",           # 주탑 가로보
                (xp - 1.2, -y_leg - 2.0, z_top - 3.0), (xp + 1.2, y_leg + 2.0, z_top))
            for d in (-1, 1):                            # 양 방향 케이블
                reach = (ms / 2) if (xp * d < 0) else (length_m / 2 - abs(xp))
                if reach <= 5:
                    continue
                for i in range(1, n_c + 1):
                    xa = xp + d * reach * i / (n_c + 0.5)
                    if abs(xa) > length_m / 2:
                        continue
                    for j, yl in ((1, -y_leg), (2, y_leg)):
                        n_s = CABLE_SEGMENTS
                        pts = [(xp + (xa - xp) * t / n_s, yl,
                                z_top + (z_deck_top - z_top) * t / n_s)
                               for t in range(n_s + 1)]
                        _seg_boxes(add, f"CB{s}{'AB'[d > 0]}{i}-{j}", "cable",
                                   "IfcMember", pts, 0.25)
        kind = "사장교" if bridge_type == "cable_stayed" else "현수교"
        return (f"{kind} — 주탑 2기(데크 위 {h_p:.0f} m) · 케이블 {8 * n_c}본 "
                f"(주경간 {ms:.0f} m 기준)")

    if bridge_type == "arch":
        rise = max(0.18 * ms, 8.0)
        n_seg, n_h = 24, 9
        for j, yl in ((1, -y_leg), (2, y_leg)):
            pts = []
            for k in range(n_seg + 1):
                x = -ms / 2 + ms * k / n_seg
                z = z_deck_top + rise * (1 - (2 * x / ms) ** 2)
                pts.append((x, yl, z))
            _seg_boxes(add, f"AR{j}", "arch", "IfcBuildingElementProxy", pts, 0.8)
            for i in range(1, n_h + 1):          # 행어(수직재)
                x = -ms / 2 + ms * i / (n_h + 1)
                z = z_deck_top + rise * (1 - (2 * x / ms) ** 2)
                add("IfcMember", f"HG{j}-{i}", "arch",
                    (x - 0.3, yl - 0.3, z_deck_top), (x + 0.3, yl + 0.3, z))
        return f"아치교 — 아치리브 2련(라이즈 {rise:.0f} m) · 행어 {2 * n_h}본"

    # truss — 상현재 + 수직재(사재는 프록시에서 생략)
    h_t = max(0.10 * ms, 6.0)
    z_top = z_deck_top + h_t
    n_v = max(4, int(length_m / max(ms / 4, 20.0)))
    for j, yl in ((1, -y_leg), (2, y_leg)):
        add("IfcMember", f"TC{j}", "truss",
            (-length_m / 2, yl - 0.5, z_top - 1.0), (length_m / 2, yl + 0.5, z_top))
        for i in range(n_v + 1):
            x = -length_m / 2 + length_m * i / n_v
            add("IfcMember", f"TV{j}-{i}", "truss",
                (x - 0.4, yl - 0.4, z_deck_top), (x + 0.4, yl + 0.4, z_top))
    return f"트러스교 — 상현재 2련(데크 위 {h_t:.0f} m) · 수직재 {2 * (n_v + 1)}본"


def elements_from_profile(profile, *, name: str = "bridge",
                          span_layout: str = "auto") -> list[Element]:
    """교량 제원 객체(BridgeProfile 등) → 부재 목록. 실측이 없으면 만들지 않는다.

    최대경간장(`extra['max_span_m']`)이 **실측**이면 교각을 비등간격으로 세운다
    (`span_edges` 참고). 추정값(`max_span_source == 'estimate'`)은 쓰지 않는다 —
    추정으로 부재 위치를 바꾸면 근거 없이 그럴듯해 보이기만 한다.
    """
    ex = getattr(profile, "extra", None) or {}
    length = getattr(profile, "length_m", None)
    width = getattr(profile, "width_m", None)
    if not length or not width:
        raise ValueError("연장·폭 실측이 없어 프록시 부재를 세울 수 없습니다 "
                         "— 표준데이터 CSV 를 넣거나 --bridge-profile 로 지정하세요.")
    n_spans = int(ex.get("n_spans") or 1)
    clearance = ex.get("height_m") or ex.get("clearance_m") or 5.0
    ms = ex.get("max_span_m") if ex.get("max_span_source") != "estimate" else None
    return bridge_elements(length_m=float(length), width_m=float(width), n_spans=n_spans,
                           clearance_m=float(clearance),
                           deck_depth_m=getattr(profile, "section_depth_m", None),
                           max_span_m=float(ms) if ms else None,
                           span_layout=span_layout,
                           bridge_type=str(getattr(profile, "bridge_type", "") or "girder"),
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
