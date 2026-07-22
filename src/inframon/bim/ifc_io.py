"""IFC 입출력 어댑터 — `ifcopenshell` 선택 의존.

정합 코어(`georef`·`elements`·`psets`)는 IFC 파일 없이도 완전히 동작하고 검증된다.
이 모듈만 실제 IFC 를 읽고 쓰며, `ifcopenshell` 이 없으면 **명확한 안내와 함께 실패**한다
(조용히 폴백하지 않는다 — IFC 를 못 읽었는데 진행하면 빈 결과가 정상처럼 보인다).

⚠️ 현 개발 환경에는 ifcopenshell 이 설치돼 있지 않아 **이 경로는 실 IFC 로 검증되지
않았다**. 코어 정합·연결·Pset 생성은 검증됐다. 실 IFC 투입 시 먼저
`--bim-inspect <ifc>` 로 IfcMapConversion·부재 수를 확인할 것.
"""

from __future__ import annotations

from pathlib import Path

from .elements import Element, member_from_ifc_type
from .georef import AlignmentError, MapConversion

_INSTALL_HINT = (
    "ifcopenshell 이 필요합니다 — `pip install ifcopenshell`. "
    "설치 없이도 부재 테이블(JSON/CSV)을 직접 주면 정합·연결·Pset 생성은 동작합니다."
)


def available() -> bool:
    try:
        import ifcopenshell  # noqa: F401
        return True
    except ImportError:
        return False


def _require():
    try:
        import ifcopenshell
        return ifcopenshell
    except ImportError as exc:
        raise AlignmentError(_INSTALL_HINT) from exc


def read_map_conversion(ifc_path: str | Path) -> MapConversion | None:
    """IFC 에서 `IfcMapConversion` 을 읽는다. 없으면 None(→ 기준점 정합으로).

    IFC4 에서 지오레퍼런싱은 선택 사항이라 국내 실무 모델에는 없는 경우가 흔하다.
    None 을 돌려주는 것은 오류가 아니라 "기준점 정합이 필요하다"는 뜻이다.
    """
    ios = _require()
    f = ios.open(str(ifc_path))
    convs = f.by_type("IfcMapConversion")
    if not convs:
        return None
    mc = convs[0]
    crs = None
    target = getattr(mc, "TargetCRS", None)
    if target is not None:
        crs = getattr(target, "Name", None)
    return MapConversion(
        eastings=float(getattr(mc, "Eastings", 0.0) or 0.0),
        northings=float(getattr(mc, "Northings", 0.0) or 0.0),
        orthogonal_height=float(getattr(mc, "OrthogonalHeight", 0.0) or 0.0),
        x_axis_abscissa=float(getattr(mc, "XAxisAbscissa", None) or 1.0),
        x_axis_ordinate=float(getattr(mc, "XAxisOrdinate", None) or 0.0),
        scale=float(getattr(mc, "Scale", None) or 1.0),
        target_crs=(str(crs) if crs else None),
        source="ifc",
    )


def read_elements(ifc_path: str | Path, *, types: tuple[str, ...] = ("IfcElement",),
                  max_elements: int = 20000) -> list[Element]:
    """IFC 부재 → `Element` 테이블(로컬 좌표 AABB).

    형상 AABB 는 `ifcopenshell.geom` 으로 계산한다. 형상 처리가 실패하는 부재는
    배치 원점만 아는 **영(0)크기 AABB** 로 넣고 `extra["bbox_source"]="placement"`
    로 표시한다 — 그런 부재는 연결 시 `max_dist_m` 안에서만 잡힌다.
    """
    ios = _require()
    import numpy as np

    f = ios.open(str(ifc_path))
    try:
        from ifcopenshell import geom
        settings = geom.settings()
        settings.set(settings.USE_WORLD_COORDS, True)
    except Exception:  # noqa: BLE001 — geom 확장이 없으면 배치 원점만 사용
        geom = settings = None

    out: list[Element] = []
    seen: set[str] = set()
    for t in types:
        for el in f.by_type(t):
            guid = getattr(el, "GlobalId", None)
            if not guid or guid in seen:
                continue
            seen.add(guid)
            ifc_type = el.is_a()
            name = getattr(el, "Name", "") or ""
            lo = hi = None
            src = "placement"
            if geom is not None and getattr(el, "Representation", None) is not None:
                try:
                    shape = geom.create_shape(settings, el)
                    v = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
                    if v.size:
                        lo, hi, src = v.min(axis=0), v.max(axis=0), "geometry"
                except Exception:  # noqa: BLE001 — 형상 실패 부재는 건너뛰고 배치로
                    lo = hi = None
            if lo is None:
                pl = getattr(el, "ObjectPlacement", None)
                o = (0.0, 0.0, 0.0)
                try:
                    rel = pl.RelativePlacement
                    o = tuple(float(c) for c in rel.Location.Coordinates)
                except Exception:  # noqa: BLE001 — 배치도 못 읽으면 원점
                    pass
                lo = hi = np.asarray(o, dtype=float)
            out.append(Element(guid=str(guid), name=str(name), ifc_type=ifc_type,
                               member=member_from_ifc_type(ifc_type, name),
                               bbox_min=tuple(float(x) for x in lo),
                               bbox_max=tuple(float(x) for x in hi),
                               extra={"bbox_source": src}))
            if len(out) >= max_elements:
                return out
    return out


def _drop_pset(f, element, pset_name: str) -> int:
    """부재에 붙은 동명 PropertySet 과 그 관계를 제거한다. 제거한 개수 반환."""
    dropped = 0
    for rel in list(getattr(element, "IsDefinedBy", ()) or ()):
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        ps = rel.RelatingPropertyDefinition
        if not (ps.is_a("IfcPropertySet") and ps.Name == pset_name):
            continue
        for prop in list(ps.HasProperties or ()):
            f.remove(prop)
        f.remove(ps)
        f.remove(rel)
        dropped += 1
    return dropped


def write_psets(ifc_in: str | Path, payload: dict, ifc_out: str | Path) -> dict:
    """페이로드의 속성을 부재별 IfcPropertySet 으로 주입해 새 IFC 로 저장한다.

    원본을 덮어쓰지 않는다 — BIM 원본은 다른 팀의 산출물이고, 모니터링 결과 주입은
    파생본에서 해야 한다.
    """
    ios = _require()
    if Path(ifc_in).resolve() == Path(ifc_out).resolve():
        raise AlignmentError("입력 IFC 를 덮어쓸 수 없습니다 — 출력 경로를 따로 지정하세요")
    f = ios.open(str(ifc_in))
    owner = (f.by_type("IfcOwnerHistory") or [None])[0]
    pset_name = payload.get("pset_name", "Inframon_Monitoring")

    injected, missing, replaced = 0, [], 0
    for guid, psets in payload.get("elements", {}).items():
        try:
            el = f.by_guid(guid)
        except Exception:  # noqa: BLE001 — GUID 가 이 IFC 에 없음
            missing.append(guid)
            continue
        # 같은 이름의 기존 Pset 은 지우고 새로 넣는다. 모니터링은 주기적으로 다시 도는데
        # 덧붙이기만 하면 실행할 때마다 동명 Pset 이 쌓여 뷰어에서 어느 게 최신인지 알 수 없다.
        replaced += _drop_pset(f, el, pset_name)
        props = []
        for k, v in psets.get(pset_name, {}).items():
            if v is None:
                continue
            if isinstance(v, bool):          # bool 이 int 의 하위형이라 반드시 먼저 본다
                val = f.create_entity("IfcBoolean", bool(v))
            elif isinstance(v, int):         # 개수는 정수다 — IfcReal 로 넣으면 13.0 으로 보인다
                val = f.create_entity("IfcInteger", int(v))
            elif isinstance(v, float):
                val = f.create_entity("IfcReal", float(v))
            else:
                val = f.create_entity("IfcText", str(v))
            props.append(f.create_entity("IfcPropertySingleValue", Name=str(k),
                                         NominalValue=val))
        if not props:
            continue
        ps = f.create_entity("IfcPropertySet", GlobalId=ios.guid.new(),
                             OwnerHistory=owner, Name=pset_name,
                             Description="inframon 위성 모니터링 결과(현재 상태)",
                             HasProperties=props)
        f.create_entity("IfcRelDefinesByProperties", GlobalId=ios.guid.new(),
                        OwnerHistory=owner, RelatedObjects=[el], RelatingPropertyDefinition=ps)
        injected += 1

    Path(ifc_out).parent.mkdir(parents=True, exist_ok=True)
    f.write(str(ifc_out))
    return {"ifc_out": str(ifc_out), "n_injected": injected, "n_replaced": replaced,
            "n_guid_not_found": len(missing), "guid_not_found": missing[:20],
            "pset_name": pset_name}


def inspect(ifc_path: str | Path) -> dict:
    """실 IFC 투입 전 사전점검 — 지오레퍼런싱 유무·부재 수·타입 분포."""
    ios = _require()
    f = ios.open(str(ifc_path))
    mc = read_map_conversion(ifc_path)
    types: dict[str, int] = {}
    for el in f.by_type("IfcElement"):
        types[el.is_a()] = types.get(el.is_a(), 0) + 1
    crs = [getattr(c, "Name", None) for c in f.by_type("IfcProjectedCRS")]
    return {
        "schema": f.schema,
        "has_map_conversion": mc is not None,
        "map_conversion": (mc.to_dict() if mc else None),
        "projected_crs": [c for c in crs if c],
        "n_elements": sum(types.values()),
        "element_types": dict(sorted(types.items(), key=lambda kv: -kv[1])[:20]),
        "advice": ("IfcMapConversion 이 없습니다 — 측량 기준점 쌍(--bim-control-points)으로 "
                   "정합해야 합니다." if mc is None else "IfcMapConversion 사용 가능."),
    }
