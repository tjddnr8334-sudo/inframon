"""형상엔진이 실패해도 **압출 단면에서 AABB 를 직접 구한다**.

실 IFC 두 종을 물려 확인한 것:
  · Pontifex 프록시 12종(314부재)은 `IfcProject` 의 Description 이 **따옴표 없는
    문자열**이다(`...,'P1',Pontifex_girder_proxy,$,...`). STEP 파서가 그 인자를 버려
    뒤 5개 속성이 한 칸씩 밀리고, UnitsInContext 가 사라진다. 단위를 못 읽으니 **모든
    부재**의 형상 생성이 죽어 314부재 전부 영(0)크기 AABB 로 떨어졌다. 따옴표 한 쌍이
    파일 전체를 못 쓰게 만든다.
  · 형상엔진이 되는 MIDAS 실설계 IFC(691부재)에서 두 방식 AABB 는 **완전히 일치**했다
    (최대 차이 0.0000 m). 그래서 이 폴백값을 믿어도 된다.
"""

from __future__ import annotations

import numpy as np
import pytest

ios = pytest.importorskip("ifcopenshell")

from inframon.bim.ifc_io import (_axis_matrix, _profile_points_2d,  # noqa: E402
                                 extruded_aabb, read_elements)


def _file_with_beam(tmp_path, *, broken_project: bool = False, origin=(0.0, 0.0, 0.0),
                    poly=((0., 0.), (90., 0.), (90., .8), (0., .8)), depth=1.5):
    """압출 보 하나짜리 IFC2X3.

    `broken_project=True` 면 실제로 겪은 결함을 재현한다 — IfcProject 의 Description
    에서 따옴표를 벗긴다. 그러면 STEP 파서가 그 인자를 버려 뒤 속성이 밀리고,
    UnitsInContext 가 사라져 형상 생성이 전부 실패한다.
    """
    f = ios.file(schema="IFC2X3")
    pts = [f.create_entity("IfcCartesianPoint", Coordinates=p) for p in poly]
    curve = f.create_entity("IfcPolyline", Points=[*pts, pts[0]])
    prof = f.create_entity("IfcArbitraryClosedProfileDef", ProfileType="AREA",
                           OuterCurve=curve)
    zdir = f.create_entity("IfcDirection", DirectionRatios=(0., 0., 1.))
    xdir = f.create_entity("IfcDirection", DirectionRatios=(1., 0., 0.))
    o = f.create_entity("IfcCartesianPoint", Coordinates=(0., 0., 0.))
    solid = f.create_entity(
        "IfcExtrudedAreaSolid", SweptArea=prof,
        Position=f.create_entity("IfcAxis2Placement3D", Location=o, Axis=zdir,
                                 RefDirection=xdir),
        ExtrudedDirection=zdir, Depth=depth)
    rep = f.create_entity("IfcShapeRepresentation", RepresentationIdentifier="Body",
                          RepresentationType="SweptSolid", Items=[solid])
    po = f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(v) for v in origin))
    place = f.create_entity(
        "IfcLocalPlacement",
        RelativePlacement=f.create_entity("IfcAxis2Placement3D", Location=po, Axis=zdir))
    beam = f.create_entity("IfcBeam", GlobalId=ios.guid.new(), Name="Girder#1",
                           ObjectPlacement=place,
                           Representation=f.create_entity("IfcProductRepresentation",
                                                          Representations=[rep]))
    ctx = f.create_entity("IfcGeometricRepresentationContext", ContextType="Model",
                          CoordinateSpaceDimension=3, Precision=1e-5,
                          WorldCoordinateSystem=f.create_entity(
                              "IfcAxis2Placement3D", Location=o))
    units = f.create_entity("IfcUnitAssignment", Units=[
        f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")])
    f.create_entity("IfcProject", GlobalId=ios.guid.new(), Name="P1",
                    Description="Pontifex_girder_proxy",
                    RepresentationContexts=[ctx], UnitsInContext=units)
    p = tmp_path / ("bad.ifc" if broken_project else "ok.ifc")
    f.write(str(p))
    if broken_project:
        # Description 의 따옴표를 벗긴다 — 실 파일이 정확히 이 상태였다.
        txt = p.read_text(encoding="utf-8")
        txt = txt.replace("'Pontifex_girder_proxy'", "Pontifex_girder_proxy")
        p.write_text(txt, encoding="utf-8")
    return p, beam


def test_단위를_못_읽어도_AABB_를_복구한다(tmp_path):
    p, _ = _file_with_beam(tmp_path, broken_project=True)
    els = read_elements(p)
    assert len(els) == 1
    e = els[0]
    assert e.extra["bbox_source"] == "extrusion", "형상엔진이 죽으면 압출로 구제해야 한다"
    size = np.asarray(e.bbox_max) - np.asarray(e.bbox_min)
    assert np.allclose(size, [90.0, 0.8, 1.5], atol=1e-6)


def test_영크기_AABB_로_떨어지지_않는다(tmp_path):
    """구제 전에는 배치 원점만 아는 점(부피 0)이 됐다 — 그러면 부재 결합이 망가진다."""
    p, _ = _file_with_beam(tmp_path, broken_project=True)
    e = read_elements(p)[0]
    assert not np.allclose(e.bbox_min, e.bbox_max)


def test_부재_배치가_AABB_에_반영된다(tmp_path):
    p, _ = _file_with_beam(tmp_path, broken_project=True, origin=(10.0, -5.0, 3.0))
    e = read_elements(p)[0]
    assert np.allclose(e.bbox_min, [10.0, -5.0, 3.0], atol=1e-6)
    assert np.allclose(e.bbox_max, [100.0, -4.2, 4.5], atol=1e-6)


def test_정상_파일은_형상엔진_결과를_쓴다(tmp_path):
    """폴백이 정상 경로를 가로채면 안 된다."""
    p, _ = _file_with_beam(tmp_path, broken_project=False)
    e = read_elements(p)[0]
    assert e.extra["bbox_source"] == "geometry"


def test_두_방식이_같은_AABB_를_준다(tmp_path):
    """형상엔진이 되는 파일에서 압출 계산이 같은 값을 내야 폴백을 믿을 수 있다."""
    p, _ = _file_with_beam(tmp_path, broken_project=False, origin=(4.0, 2.0, -1.0))
    e = read_elements(p)[0]                       # geometry 경로
    f = ios.open(str(p))
    got = extruded_aabb(f.by_type("IfcBeam")[0])  # extrusion 경로
    assert got is not None
    assert np.allclose(got[0], e.bbox_min, atol=1e-6)
    assert np.allclose(got[1], e.bbox_max, atol=1e-6)


def test_axis_matrix_는_RefDirection_이_없으면_기본축을_만든다():
    f = ios.file(schema="IFC2X3")
    o = f.create_entity("IfcCartesianPoint", Coordinates=(1., 2., 3.))
    z = f.create_entity("IfcDirection", DirectionRatios=(0., 0., 1.))
    m = _axis_matrix(f.create_entity("IfcAxis2Placement3D", Location=o, Axis=z))
    assert np.allclose(m[:3, 3], [1., 2., 3.])
    assert np.allclose(m[:3, :3], np.eye(3)), "Z=(0,0,1) 이면 항등이어야 한다"


def test_axis_matrix_는_기울어진_축도_정규직교로_만든다():
    f = ios.file(schema="IFC2X3")
    o = f.create_entity("IfcCartesianPoint", Coordinates=(0., 0., 0.))
    z = f.create_entity("IfcDirection", DirectionRatios=(1., 0., 0.))   # X 를 Z 축으로
    m = _axis_matrix(f.create_entity("IfcAxis2Placement3D", Location=o, Axis=z))
    R = m[:3, :3]
    assert np.allclose(R.T @ R, np.eye(3), atol=1e-9)
    assert np.allclose(R[:, 2], [1., 0., 0.])


def test_사각형_단면도_읽는다():
    f = ios.file(schema="IFC4")
    prof = f.create_entity("IfcRectangleProfileDef", ProfileType="AREA",
                           XDim=3.0, YDim=2.0)
    pts = _profile_points_2d(prof)
    assert pts is not None
    assert np.isclose(pts[:, 0].max() - pts[:, 0].min(), 3.0)
    assert np.isclose(pts[:, 1].max() - pts[:, 1].min(), 2.0)


def test_모르는_단면은_None_을_준다():
    f = ios.file(schema="IFC4")
    assert _profile_points_2d(f.create_entity("IfcIShapeProfileDef", ProfileType="AREA",
                                              OverallWidth=1.0, OverallDepth=1.0,
                                              WebThickness=.1, FlangeThickness=.1)) is None
    assert _profile_points_2d(None) is None


def test_압출_형상이_없으면_None(tmp_path):
    f = ios.file(schema="IFC2X3")
    beam = f.create_entity("IfcBeam", GlobalId=ios.guid.new(), Name="bare")
    assert extruded_aabb(beam) is None
