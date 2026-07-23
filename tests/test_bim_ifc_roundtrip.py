"""실 IFC 왕복 — 지오레퍼런싱 읽기 · 부재 AABB · Pset 주입 (ifcopenshell 필요).

`ifc_io` 는 이 파일이 생기기 전까지 유일하게 검증되지 않은 모듈이었다. 실 교량 IFC 가
없어도 **ifcopenshell 로 IFC 를 만들어** 왕복시키면 실제로 겪을 문제(단위, 형상 AABB,
GUID 매칭, 재주입 누적)를 대부분 만난다.

ifcopenshell 이 없으면 파일 전체를 skip 한다 — 정합 코어(georef·elements·psets·align)는
이 의존성 없이 동작하고 별도 파일에서 검증된다.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

ifcopenshell = pytest.importorskip("ifcopenshell", reason="ifcopenshell 없음 — 실 IFC 경로 skip")

import ifcopenshell.api.context  # noqa: E402
import ifcopenshell.api.georeference  # noqa: E402
import ifcopenshell.api.root  # noqa: E402
import ifcopenshell.api.unit  # noqa: E402

from inframon.bim import align_project_to_bim, ifc_io  # noqa: E402
from inframon.bim.georef import AlignmentError  # noqa: E402
from inframon.config import PipelineConfig  # noqa: E402
from inframon.contracts.io import ProjectStore  # noqa: E402
from inframon.contracts.schema import InSAROutput  # noqa: E402
from inframon.orchestrator.pipeline import run_pipeline  # noqa: E402

ROT_DEG, EAST, NORTH, HEIGHT = 20.0, 200000.0, 550000.0, 12.0
BOXES = [                                   # (이름, IFC 타입, bbox_min, bbox_max) — 로컬 미터
    ("상판", "IfcSlab", (0, -5, 8), (100, 5, 9)),
    ("교각1", "IfcColumn", (30, -2, 0), (34, 2, 8)),
    ("교각2", "IfcColumn", (66, -2, 0), (70, 2, 8)),
    ("교대", "IfcFooting", (-4, -5, 0), (0, 5, 8)),
]


def _box(f, ctx, name, ifc_class, lo, hi):
    dx, dy, dz = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
    prof = f.create_entity(
        "IfcRectangleProfileDef", ProfileType="AREA", XDim=dx, YDim=dy,
        Position=f.create_entity("IfcAxis2Placement2D",
                                 Location=f.create_entity(
                                     "IfcCartesianPoint", (lo[0] + dx / 2, lo[1] + dy / 2))))
    solid = f.create_entity(
        "IfcExtrudedAreaSolid", SweptArea=prof, Depth=dz,
        Position=f.create_entity("IfcAxis2Placement3D",
                                 Location=f.create_entity("IfcCartesianPoint",
                                                          (0.0, 0.0, float(lo[2])))),
        ExtrudedDirection=f.create_entity("IfcDirection", (0.0, 0.0, 1.0)))
    el = ifcopenshell.api.root.create_entity(f, ifc_class=ifc_class, name=name)
    el.Representation = f.create_entity("IfcProductDefinitionShape", Representations=[
        f.create_entity("IfcShapeRepresentation", ContextOfItems=ctx,
                        RepresentationIdentifier="Body", RepresentationType="SweptSolid",
                        Items=[solid])])
    el.ObjectPlacement = f.create_entity(
        "IfcLocalPlacement", RelativePlacement=f.create_entity(
            "IfcAxis2Placement3D",
            Location=f.create_entity("IfcCartesianPoint", (0.0, 0.0, 0.0))))
    return el


def _make_ifc(path, *, georeferenced: bool = True, add_shapeless: bool = False) -> str:
    f = ifcopenshell.file(schema="IFC4")
    ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="TestBridge")
    # 길이 단위를 미터로 **명시**한다. 지정하지 않으면 밀리미터가 되어 100 이 0.1m 로 읽힌다
    # — 실 IFC 에서 가장 흔한 함정이라 픽스처에서도 이 축을 고정한다.
    ifcopenshell.api.unit.assign_unit(f, length={"is_metric": True, "raw": "METERS"})
    ctx = ifcopenshell.api.context.add_context(f, context_type="Model")
    body = ifcopenshell.api.context.add_context(
        f, context_type="Model", context_identifier="Body",
        target_view="MODEL_VIEW", parent=ctx)
    if georeferenced:
        ifcopenshell.api.georeference.add_georeferencing(f)
        t = math.radians(ROT_DEG)
        ifcopenshell.api.georeference.edit_georeferencing(
            f, projected_crs={"Name": "EPSG:5186"},
            coordinate_operation={"Eastings": EAST, "Northings": NORTH,
                                  "OrthogonalHeight": HEIGHT,
                                  "XAxisAbscissa": math.cos(t), "XAxisOrdinate": math.sin(t),
                                  "Scale": 1.0})
    for name, cls, lo, hi in BOXES:
        _box(f, body, name, cls, lo, hi)
    if add_shapeless:                          # 형상 없는 부재 → 배치 원점 폴백 경로
        el = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBeam", name="형상없음")
        el.ObjectPlacement = f.create_entity(
            "IfcLocalPlacement", RelativePlacement=f.create_entity(
                "IfcAxis2Placement3D",
                Location=f.create_entity("IfcCartesianPoint", (50.0, 0.0, 8.5))))
    f.write(str(path))
    return str(path)


@pytest.fixture()
def ifc(tmp_path):
    return _make_ifc(tmp_path / "bridge.ifc")


# ── 읽기 ───────────────────────────────────────────────────────────────
def test_available_is_true_here():
    assert ifc_io.available() is True


def test_read_map_conversion_recovers_exact_values(ifc):
    mc = ifc_io.read_map_conversion(ifc)
    assert mc is not None and mc.source == "ifc"
    assert mc.eastings == pytest.approx(EAST)
    assert mc.northings == pytest.approx(NORTH)
    assert mc.orthogonal_height == pytest.approx(HEIGHT)
    assert mc.rotation_deg == pytest.approx(ROT_DEG, abs=1e-9)
    assert mc.target_crs == "EPSG:5186"


def test_missing_georeferencing_returns_none_not_error(tmp_path):
    """지오레퍼런싱은 IFC 에서 선택 사항 — 없는 건 오류가 아니라 '기준점 정합 필요'다."""
    p = _make_ifc(tmp_path / "plain.ifc", georeferenced=False)
    assert ifc_io.read_map_conversion(p) is None
    info = ifc_io.inspect(p)
    assert info["has_map_conversion"] is False
    assert "기준점" in info["advice"]


def test_read_elements_recovers_bboxes_in_metres(ifc):
    els = {e.name: e for e in ifc_io.read_elements(ifc)}
    assert set(els) == {n for n, *_ in BOXES}
    for name, _cls, lo, hi in BOXES:
        e = els[name]
        assert e.bbox_min == pytest.approx(lo, abs=1e-6)
        assert e.bbox_max == pytest.approx(hi, abs=1e-6)
        assert e.extra["bbox_source"] == "geometry"
        assert all(isinstance(v, float) for v in e.bbox_min)   # json 직렬화 가능


def test_read_elements_infers_member_from_ifc_type(ifc):
    m = {e.name: e.member for e in ifc_io.read_elements(ifc)}
    assert m == {"상판": "deck", "교각1": "pier", "교각2": "pier", "교대": "abutment"}


def test_shapeless_element_falls_back_to_placement(tmp_path):
    p = _make_ifc(tmp_path / "mixed.ifc", add_shapeless=True)
    els = {e.name: e for e in ifc_io.read_elements(p)}
    e = els["형상없음"]
    assert e.extra["bbox_source"] == "placement"
    assert e.bbox_min == e.bbox_max == pytest.approx((50.0, 0.0, 8.5))


def test_inspect_reports_types_and_schema(ifc):
    info = ifc_io.inspect(ifc)
    assert info["schema"] == "IFC4"
    assert info["n_elements"] == len(BOXES)
    assert info["element_types"]["IfcColumn"] == 2
    assert info["projected_crs"] == ["EPSG:5186"]


# ── 쓰기 ───────────────────────────────────────────────────────────────
@pytest.fixture()
def project_on_bridge(tmp_path, ifc):
    """부재 위에 점을 둔 project.h5 (IFC 지오레퍼런싱으로 지도 좌표 환산)."""
    p = tmp_path / "project.h5"
    run_pipeline(str(p), PipelineConfig(n_points=60, n_dates=24))
    mc = ifc_io.read_map_conversion(ifc)
    with ProjectStore(str(p)) as s:
        ins = s.read_meta("insar", InSAROutput)
        n = int(ins.n_points)
        x = np.linspace(2.0, 98.0, n)
        local = np.column_stack([x, np.zeros(n), np.full(n, 8.5)])
        s.write_array(ins.xyz_ds, mc.to_map(local))
    return str(p)


def _payload(project_h5, ifc_path, *, use_z=False):
    mc = ifc_io.read_map_conversion(ifc_path)
    els = ifc_io.read_elements(ifc_path)
    return align_project_to_bim(project_h5, els, map_conversion=mc,
                                source_crs="EPSG:5186", max_dist_m=2.0, use_z=use_z)


def test_align_through_real_ifc(project_on_bridge, ifc):
    res = _payload(project_on_bridge, ifc)
    assert res["alignment"]["source"] == "ifc"
    assert res["association"]["assigned_fraction"] == 1.0


def test_real_ifc_height_makes_3d_association_correct(project_on_bridge, ifc):
    """점은 전부 상판 높이(z=8.5)에 있다.

    2D 로는 상판 AABB 가 교각을 포함해 겹치는 점이 교각으로도 간다(모호성). 실 IFC 는
    `OrthogonalHeight` 를 갖고 있어 표고 기준이 정의되므로 3D 연결이 정당하고, 그러면
    전부 상판으로 정확히 갈린다.
    """
    guids = {e.guid: e.name for e in ifc_io.read_elements(ifc)}
    flat = _payload(project_on_bridge, ifc, use_z=False)
    solid = _payload(project_on_bridge, ifc, use_z=True)

    assert {guids[g] for g in flat["per_element"]} > {"상판"}       # 2D 는 교각까지 섞인다
    assert flat["association"]["n_ambiguous"] > 0
    assert {guids[g] for g in solid["per_element"]} == {"상판"}     # 3D 는 정확
    assert solid["association"]["n_ambiguous"] == 0
    assert solid["association"]["dim"] == 3


def test_write_psets_injects_readable_properties(project_on_bridge, ifc, tmp_path):
    res = _payload(project_on_bridge, ifc)
    out = tmp_path / "monitored.ifc"
    r = ifc_io.write_psets(ifc, res["payload"], out)
    assert r["n_injected"] == len(res["per_element"]) and r["n_guid_not_found"] == 0

    f = ifcopenshell.open(str(out))
    psets = [p for p in f.by_type("IfcPropertySet") if p.Name == "Inframon_Monitoring"]
    assert len(psets) == r["n_injected"]
    props = {p.Name: p.NominalValue for p in psets[0].HasProperties}
    assert props["PointCount"].is_a() == "IfcInteger"        # 개수는 정수여야 한다
    assert props["Sparse"].is_a() == "IfcBoolean"
    assert props["CoherenceMedian"].is_a() == "IfcReal"
    assert str(props["SourceProject"].wrappedValue).endswith("project.h5")
    assert props["SourceGroups"].wrappedValue == "/insar,/fram,/life"


def test_reinjection_replaces_instead_of_accumulating(project_on_bridge, ifc, tmp_path):
    """모니터링은 주기적으로 다시 돈다 — 덧붙이기만 하면 동명 Pset 이 쌓인다."""
    res = _payload(project_on_bridge, ifc)
    first, second = tmp_path / "m1.ifc", tmp_path / "m2.ifc"
    r1 = ifc_io.write_psets(ifc, res["payload"], first)
    r2 = ifc_io.write_psets(first, res["payload"], second)
    assert r1["n_replaced"] == 0 and r2["n_replaced"] == r1["n_injected"]
    for path in (first, second):
        f = ifcopenshell.open(str(path))
        n = len([p for p in f.by_type("IfcPropertySet") if p.Name == "Inframon_Monitoring"])
        assert n == r1["n_injected"]


def test_source_ifc_is_never_modified(project_on_bridge, ifc, tmp_path):
    """BIM 원본은 다른 팀의 산출물 — 주입은 사본에만."""
    res = _payload(project_on_bridge, ifc)
    ifc_io.write_psets(ifc, res["payload"], tmp_path / "copy.ifc")
    src = ifcopenshell.open(str(ifc))
    assert [p for p in src.by_type("IfcPropertySet") if p.Name == "Inframon_Monitoring"] == []


def test_write_psets_refuses_to_overwrite_source(ifc):
    with pytest.raises(AlignmentError, match="덮어쓸 수 없습니다"):
        ifc_io.write_psets(ifc, {}, ifc)


def test_unknown_guid_is_reported_not_silently_dropped(ifc, tmp_path):
    payload = {"pset_name": "Inframon_Monitoring",
               "elements": {"0NOTREALGUID0000000000": {"Inframon_Monitoring": {"PointCount": 1}}}}
    r = ifc_io.write_psets(ifc, payload, tmp_path / "o.ifc")
    assert r["n_injected"] == 0 and r["n_guid_not_found"] == 1


# ── 실 BIM 산출물 호환성 ────────────────────────────────────────────────
# 실 IFC 는 스키마·단위·지오레퍼런싱이 제각각이다. 특히 **단위**는 틀려도 오류가 안 나고
# 결과만 1000배 어긋나므로, 여기서 축을 고정한다.

def _unit_ifc(path, unit: str, *, nested: bool = False) -> str:
    f = ifcopenshell.file(schema="IFC4")
    ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="U")
    ifcopenshell.api.unit.assign_unit(f, length={"is_metric": True, "raw": unit})
    ctx = ifcopenshell.api.context.add_context(f, context_type="Model")
    body = ifcopenshell.api.context.add_context(
        f, context_type="Model", context_identifier="Body",
        target_view="MODEL_VIEW", parent=ctx)
    el = _box(f, body, "deck", "IfcSlab", (0, 0, 0), (100, 10, 1))   # 선언값 100
    if nested:                                    # 사이트 배치 위의 상대 배치
        site = f.create_entity("IfcLocalPlacement", RelativePlacement=f.create_entity(
            "IfcAxis2Placement3D",
            Location=f.create_entity("IfcCartesianPoint", (1000.0, 2000.0, 0.0))))
        el.ObjectPlacement = f.create_entity(
            "IfcLocalPlacement", PlacementRelTo=site, RelativePlacement=f.create_entity(
                "IfcAxis2Placement3D",
                Location=f.create_entity("IfcCartesianPoint", (5.0, 0.0, 0.0))))
    f.write(str(path))
    return str(path)


def test_millimetre_model_is_read_in_metres(tmp_path):
    """국내 IFC 는 밀리미터가 흔하다. 100mm 를 100m 로 읽으면 조용히 1000배 틀린다."""
    m = ifc_io.read_elements(_unit_ifc(tmp_path / "m.ifc", "METERS"))[0]
    mm = ifc_io.read_elements(_unit_ifc(tmp_path / "mm.ifc", "MILLIMETERS"))[0]
    assert m.bbox_max[0] == pytest.approx(100.0, abs=1e-6)      # 100 m
    assert mm.bbox_max[0] == pytest.approx(0.1, abs=1e-6)       # 100 mm = 0.1 m


def test_length_scale_is_reported(tmp_path):
    import ifcopenshell as ios
    assert ifc_io.length_scale_to_m(ios.open(_unit_ifc(tmp_path / "a.ifc", "METERS"))) == pytest.approx(1.0)
    assert ifc_io.length_scale_to_m(ios.open(_unit_ifc(tmp_path / "b.ifc", "MILLIMETERS"))) == pytest.approx(0.001)


def test_nested_placement_chain_is_resolved(tmp_path):
    """사이트/빌딩 배치 위에 부재가 놓이는 게 실 모델의 기본이다.

    RelativePlacement 만 읽으면 부모 오프셋이 통째로 빠져 수백~수천 m 어긋난다.
    """
    import ifcopenshell as ios
    p = _unit_ifc(tmp_path / "n.ifc", "METERS", nested=True)
    el = ios.open(p).by_type("IfcSlab")[0]
    xyz = ifc_io._placement_xyz(el, 1.0)
    assert xyz[0] == pytest.approx(1005.0) and xyz[1] == pytest.approx(2000.0)
    naive = el.ObjectPlacement.RelativePlacement.Location.Coordinates
    assert float(naive[0]) == pytest.approx(5.0)               # 순진한 읽기는 5 (= 1000m 오차)
    # 형상 경로도 같은 원점을 봐야 한다
    assert ifc_io.read_elements(p)[0].bbox_min[0] == pytest.approx(1005.0, abs=1e-3)


def test_inspect_reports_units_and_readiness(tmp_path):
    ready = ifc_io.inspect(_make_ifc(tmp_path / "geo.ifc"))
    assert ready["ready"] is True and ready["blockers"] == []
    assert ready["length_unit"] == "m" and ready["n_with_geometry"] == ready["n_elements"]

    bare = ifc_io.inspect(_unit_ifc(tmp_path / "bare.ifc", "MILLIMETERS"))
    assert bare["ready"] is False
    assert any("IfcMapConversion" in b for b in bare["blockers"])
    assert any("기준점" in b for b in bare["blockers"])         # 무엇을 받아야 하는지 말한다
    assert bare["length_unit"] == "mm"
    assert any("mm" in n for n in bare["notes"])


def test_inspect_flags_shapeless_and_unmappable_elements(tmp_path):
    p = _make_ifc(tmp_path / "mixed.ifc", add_shapeless=True)
    info = ifc_io.inspect(p)
    assert info["n_with_geometry"] < info["n_elements"]
    assert any("형상 없는 부재" in n for n in info["notes"])


def test_ifc43_bridge_types_map_to_members():
    """IFC4.3 교량 확장 타입 — 실 BIM 산출물이 이 타입을 쓰기 시작했다."""
    from inframon.bim.elements import member_from_ifc_type
    assert member_from_ifc_type("IfcBridgePart") == "deck"
    assert member_from_ifc_type("IfcDeepFoundation") == "pier"
    assert member_from_ifc_type("IfcCaisson") == "pier"
    assert member_from_ifc_type("IfcBearing") == "bearing"
    # 프록시는 타입으로 못 정하고 이름에 기대야 한다
    assert member_from_ifc_type("IfcBuildingElementProxy") is None
    assert member_from_ifc_type("IfcBuildingElementProxy", "P3 교각") == "pier"
