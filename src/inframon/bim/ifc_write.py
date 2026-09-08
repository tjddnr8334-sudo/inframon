"""부재 테이블 → **IFC4 파일 생성**.

`proxy_model.bridge_elements()` 는 실측 제원으로 부재(상판·교각·두부·교대)를 세우지만
JSON 까지만 낸다. 그래서 "IFC 로 부재 결합까지 했다"고 말할 근거가 파일로 남지 않았고,
BIM 도구(Revit·BlenderBIM·IfcOpenShell)로 열어 확인할 수도 없었다.

여기서 그 부재를 **실제 IFC4 파일**로 쓴다:

  · 부재마다 bbox 를 `IfcExtrudedAreaSolid`(직사각 단면 × 높이)로 — 형상이 있어야
    `read_elements` 가 배치 원점이 아닌 **형상 AABB** 를 되읽는다.
  · `IfcProjectedCRS` + `IfcMapConversion` 으로 지오참조를 박는다. 이게 없으면 트윈이
    점을 어디에 놓을지 알 수 없어 기준점 정합으로 되돌아간다.
  · GlobalId 는 부재 테이블의 것을 그대로 쓴다 — 재생성해도 결합이 유지되어야 한다.

**이것은 실 BIM 이 아니다.** 표준데이터 제원으로 세운 프록시이고, 파일 헤더와 각 부재의
Description 에 그 사실을 남긴다. 실 IFC 가 있으면 당연히 그쪽을 쓴다.
"""

from __future__ import annotations

from pathlib import Path

from .elements import Element
from .georef import MapConversion
from .ifc_io import _require

# IFC 타입 → (엔티티, PredefinedType). IFC4 에서 PredefinedType 이 필수인 타입이 있다.
_PREDEFINED = {
    "IfcSlab": "FLOOR",
    "IfcColumn": "COLUMN",
    "IfcBeam": "BEAM",
    "IfcFooting": "PAD_FOOTING",
}
_PROXY_NOTE = "proxy from measured specs — not a surveyed BIM"


def _guid_ok(g: str) -> bool:
    """IFC GlobalId 형식(22자 base64 변형)인가."""
    ok = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$")
    return len(g) == 22 and set(g) <= ok


def write_elements(elements: list[Element], out_path: str | Path, *,
                   map_conversion: MapConversion | None = None,
                   project_name: str = "inframon proxy bridge",
                   site_name: str = "bridge site",
                   description: str = _PROXY_NOTE) -> dict:
    """부재 목록 → IFC4 파일. 반환: 요약(경로·부재수·지오참조 여부).

    `map_conversion` 은 IFC 로컬(원점=교량 중심) → 지도 CRS 변환이다. 주면
    `IfcProjectedCRS`/`IfcMapConversion` 으로 기록해 트윈이 좌표를 바로 쓸 수 있게 한다.
    """
    ios = _require()
    if not elements:
        raise ValueError("부재가 비었습니다 — 쓸 IFC 가 없습니다.")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    f = ios.file(schema="IFC4")
    person = f.create_entity("IfcPerson", FamilyName="inframon")
    org = f.create_entity("IfcOrganization", Name="inframon")
    owner = f.create_entity(
        "IfcOwnerHistory",
        OwningUser=f.create_entity("IfcPersonAndOrganization", ThePerson=person,
                                   TheOrganization=org),
        OwningApplication=f.create_entity("IfcApplication", ApplicationDeveloper=org,
                                          Version="0.1.0", ApplicationFullName="inframon",
                                          ApplicationIdentifier="inframon"),
        ChangeAction="ADDED")

    # ── 단위: 미터·라디안. 부재 bbox 가 미터이므로 단위를 섞으면 안 된다.
    units = f.create_entity("IfcUnitAssignment", Units=[
        f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE"),
        f.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE"),
        f.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE"),
        f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN"),
    ])

    origin = f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
    axis3d = f.create_entity("IfcAxis2Placement3D", Location=origin)
    ctx = f.create_entity("IfcGeometricRepresentationContext", ContextType="Model",
                          CoordinateSpaceDimension=3, Precision=1e-5,
                          WorldCoordinateSystem=axis3d)
    body = f.create_entity("IfcGeometricRepresentationSubContext",
                           ContextIdentifier="Body", ContextType="Model",
                           ParentContext=ctx, TargetView="MODEL_VIEW")

    project = f.create_entity("IfcProject", GlobalId=ios.guid.new(), OwnerHistory=owner,
                              Name=project_name, Description=description,
                              UnitsInContext=units, RepresentationContexts=[ctx])

    site_place = f.create_entity("IfcLocalPlacement", RelativePlacement=axis3d)
    site = f.create_entity("IfcSite", GlobalId=ios.guid.new(), OwnerHistory=owner,
                           Name=site_name, ObjectPlacement=site_place,
                           CompositionType="ELEMENT")
    bld_place = f.create_entity("IfcLocalPlacement", PlacementRelTo=site_place,
                                RelativePlacement=axis3d)
    building = f.create_entity("IfcBuilding", GlobalId=ios.guid.new(), OwnerHistory=owner,
                               Name=project_name, ObjectPlacement=bld_place,
                               CompositionType="ELEMENT")
    storey_place = f.create_entity("IfcLocalPlacement", PlacementRelTo=bld_place,
                                   RelativePlacement=axis3d)
    storey = f.create_entity("IfcBuildingStorey", GlobalId=ios.guid.new(),
                             OwnerHistory=owner, Name="deck level",
                             ObjectPlacement=storey_place, CompositionType="ELEMENT")
    for rel_parent, children in ((project, [site]), (site, [building]),
                                 (building, [storey])):
        f.create_entity("IfcRelAggregates", GlobalId=ios.guid.new(), OwnerHistory=owner,
                        RelatingObject=rel_parent, RelatedObjects=children)

    georef = _write_georef(f, ctx, map_conversion) if map_conversion else None

    made = []
    for e in elements:
        made.append(_write_element(f, ios, owner, body, storey_place, e, description))
    f.create_entity("IfcRelContainedInSpatialStructure", GlobalId=ios.guid.new(),
                    OwnerHistory=owner, RelatingStructure=storey, RelatedElements=made)

    f.write(str(out))
    return {"out": str(out), "schema": "IFC4", "n_elements": len(made),
            "has_map_conversion": georef is not None,
            "target_crs": (map_conversion.target_crs if map_conversion else None),
            "note": description}


def _write_georef(f, ctx, mc: MapConversion):
    """`IfcProjectedCRS` + `IfcMapConversion` — IFC 로컬 → 지도 CRS."""
    crs_name = mc.target_crs or "EPSG:0"
    crs = f.create_entity("IfcProjectedCRS", Name=crs_name,
                          Description="inframon 프록시 배치 기준", GeodeticDatum=None,
                          MapUnit=f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT",
                                                  Name="METRE"))
    return f.create_entity("IfcMapConversion", SourceCRS=ctx, TargetCRS=crs,
                           Eastings=float(mc.eastings), Northings=float(mc.northings),
                           OrthogonalHeight=float(mc.orthogonal_height),
                           XAxisAbscissa=float(mc.x_axis_abscissa),
                           XAxisOrdinate=float(mc.x_axis_ordinate),
                           Scale=float(mc.scale))


def _write_element(f, ios, owner, body, parent_place, e: Element, description: str):
    """부재 하나 — bbox 를 직사각 단면 압출로 쓴다(형상이 있어야 AABB 를 되읽는다)."""
    lo, hi = e.bbox_min, e.bbox_max
    dx, dy, dz = (max(float(hi[i]) - float(lo[i]), 1e-3) for i in range(3))
    cx, cy = (float(lo[0]) + dx / 2, float(lo[1]) + dy / 2)

    place = f.create_entity(
        "IfcLocalPlacement", PlacementRelTo=parent_place,
        RelativePlacement=f.create_entity(
            "IfcAxis2Placement3D",
            Location=f.create_entity("IfcCartesianPoint",
                                     Coordinates=(cx, cy, float(lo[2])))))
    profile = f.create_entity("IfcRectangleProfileDef", ProfileType="AREA",
                              ProfileName=e.name or None,
                              Position=f.create_entity(
                                  "IfcAxis2Placement2D",
                                  Location=f.create_entity("IfcCartesianPoint",
                                                           Coordinates=(0.0, 0.0))),
                              XDim=dx, YDim=dy)
    solid = f.create_entity(
        "IfcExtrudedAreaSolid", SweptArea=profile,
        Position=f.create_entity("IfcAxis2Placement3D",
                                 Location=f.create_entity("IfcCartesianPoint",
                                                          Coordinates=(0.0, 0.0, 0.0))),
        ExtrudedDirection=f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)),
        Depth=dz)
    shape = f.create_entity("IfcProductDefinitionShape", Representations=[
        f.create_entity("IfcShapeRepresentation", ContextOfItems=body,
                        RepresentationIdentifier="Body", RepresentationType="SweptSolid",
                        Items=[solid])])

    guid = e.guid if _guid_ok(e.guid) else ios.guid.new()
    kwargs = dict(GlobalId=guid, OwnerHistory=owner, Name=e.name or e.member or "element",
                  Description=description, ObjectPlacement=place, Representation=shape)
    ifc_type = e.ifc_type or "IfcBuildingElementProxy"
    pre = _PREDEFINED.get(ifc_type)
    if pre:
        kwargs["PredefinedType"] = pre
    try:
        return f.create_entity(ifc_type, **kwargs)
    except Exception:                       # noqa: BLE001 — 낯선 타입은 프록시로 떨어뜨린다
        kwargs.pop("PredefinedType", None)
        return f.create_entity("IfcBuildingElementProxy", **kwargs)
