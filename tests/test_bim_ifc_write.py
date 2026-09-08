"""부재 테이블 → IFC4 작성. **왕복**(쓰고 되읽기)이 핵심 계약이다."""

from __future__ import annotations

import math

import numpy as np
import pytest

from inframon.bim.elements import Element
from inframon.bim.georef import MapConversion
from inframon.bim.proxy_model import bridge_elements

ios = pytest.importorskip("ifcopenshell", reason="IFC 작성은 ifcopenshell 이 있어야 한다")

from inframon.bim.ifc_io import read_elements, read_map_conversion  # noqa: E402
from inframon.bim.ifc_write import write_elements                   # noqa: E402


def _mc() -> MapConversion:
    az = math.radians(30.0)
    return MapConversion(eastings=209_655.2, northings=529_919.5, orthogonal_height=37.2,
                         x_axis_abscissa=math.cos(az), x_axis_ordinate=math.sin(az),
                         target_crs="EPSG:5186", source="proxy_placement")


def _bridge() -> list[Element]:
    return bridge_elements(length_m=108.0, width_m=26.5, n_spans=5,
                           clearance_m=6.0, name="테스트교")


def test_부재를_쓰면_같은_수가_되읽힌다(tmp_path):
    els = _bridge()
    out = tmp_path / "b.ifc"
    info = write_elements(els, out)
    assert info["n_elements"] == len(els)
    assert out.exists()
    assert len(read_elements(out)) == len(els)


def test_GlobalId_가_보존된다(tmp_path):
    """재생성해도 결합이 유지되려면 GUID 가 그대로여야 한다."""
    els = _bridge()
    out = tmp_path / "b.ifc"
    write_elements(els, out)
    got = {e.guid for e in read_elements(out)}
    assert got == {e.guid for e in els}


def test_bbox_가_형상에서_그대로_되읽힌다(tmp_path):
    """배치 원점이 아니라 **형상 AABB** 로 되읽어야 부재 결합이 맞는다."""
    els = _bridge()
    out = tmp_path / "b.ifc"
    write_elements(els, out)
    back = {e.guid: e for e in read_elements(out)}
    for e in els:
        b = back[e.guid]
        assert b.extra.get("bbox_source") == "geometry"
        assert np.allclose(b.bbox_min, e.bbox_min, atol=1e-3), e.name
        assert np.allclose(b.bbox_max, e.bbox_max, atol=1e-3), e.name


def test_부재_종류와_라벨이_유지된다(tmp_path):
    els = _bridge()
    out = tmp_path / "b.ifc"
    write_elements(els, out)
    back = {e.guid: e for e in read_elements(out)}
    for e in els:
        assert back[e.guid].ifc_type == e.ifc_type, e.name
        assert back[e.guid].member == e.member, e.name


def test_지오참조가_왕복한다(tmp_path):
    mc = _mc()
    out = tmp_path / "b.ifc"
    info = write_elements(_bridge(), out, map_conversion=mc)
    assert info["has_map_conversion"] is True
    got = read_map_conversion(out)
    assert got is not None
    assert got.target_crs == "EPSG:5186"
    assert got.eastings == pytest.approx(mc.eastings, abs=1e-3)
    assert got.northings == pytest.approx(mc.northings, abs=1e-3)
    assert got.orthogonal_height == pytest.approx(mc.orthogonal_height, abs=1e-3)
    assert got.rotation_deg == pytest.approx(mc.rotation_deg, abs=1e-6)


def test_지오참조를_안_주면_없이_쓰인다(tmp_path):
    """지오참조 없는 IFC 도 유효하다 — 기준점 정합이 정상 경로다."""
    out = tmp_path / "b.ifc"
    info = write_elements(_bridge(), out)
    assert info["has_map_conversion"] is False
    assert read_map_conversion(out) is None


def test_프록시라는_사실이_파일에_남는다(tmp_path):
    """실 BIM 이 아니라는 것이 산출물에서 읽혀야 한다."""
    out = tmp_path / "b.ifc"
    write_elements(_bridge(), out)
    text = out.read_text(encoding="utf-8", errors="replace")
    assert "proxy" in text.lower()


def test_빈_부재는_거부한다(tmp_path):
    with pytest.raises(ValueError, match="부재가 비었"):
        write_elements([], tmp_path / "b.ifc")


def test_낯선_IFC_타입은_프록시로_떨어진다(tmp_path):
    """모르는 타입에 죽지 않고 IfcBuildingElementProxy 로 쓴다."""
    e = Element(guid="0aBcDeFgHiJkLmNoPqRsTu", name="X", ifc_type="IfcNotARealType",
                member="deck", bbox_min=(0, 0, 0), bbox_max=(1, 1, 1))
    out = tmp_path / "b.ifc"
    write_elements([e], out)
    back = read_elements(out)
    assert len(back) == 1
    assert back[0].ifc_type == "IfcBuildingElementProxy"


def test_형식이_아닌_GUID는_새로_발급한다(tmp_path):
    """22자 IFC GlobalId 가 아니면 그대로 쓸 수 없다 — 파일이 깨지느니 새로 발급한다."""
    e = Element(guid="짧다", name="X", ifc_type="IfcSlab", member="deck",
                bbox_min=(0, 0, 0), bbox_max=(2, 2, 1))
    out = tmp_path / "b.ifc"
    write_elements([e], out)
    back = read_elements(out)
    assert len(back) == 1
    assert len(back[0].guid) == 22 and back[0].guid != "짧다"


def test_납작한_부재도_형상이_생긴다(tmp_path):
    """두께 0 인 bbox 는 압출 깊이 0 이 되어 형상 생성이 실패한다 — 최소 두께를 준다."""
    e = Element(guid="0aBcDeFgHiJkLmNoPqRsTu", name="Flat", ifc_type="IfcSlab",
                member="deck", bbox_min=(0, 0, 5), bbox_max=(10, 4, 5))
    out = tmp_path / "b.ifc"
    write_elements([e], out)
    back = read_elements(out)
    assert back[0].extra.get("bbox_source") == "geometry"
