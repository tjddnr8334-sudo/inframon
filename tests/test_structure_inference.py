"""형식→재료·단면·경계 추론(PINN 보충) — 강재 고정 가정 해소."""
from __future__ import annotations
from inframon.structure import infer_structural_defaults, resolve_profile, BridgeProfile
from inframon.bridge_info import profile_from_osm


def test_infer_material_from_type():
    # PSC box: material 태그 없음 → prestressed_concrete
    r = infer_structural_defaults("box_girder", has_material_tag=False, length_m=650, max_span_m=90)
    assert r["material"] == "prestressed_concrete"
    assert 0.4 <= r["section_depth_m"] <= 8.0
    # 라멘 → fixed
    assert infer_structural_defaults("rahmen", has_material_tag=False, length_m=40, max_span_m=40)["boundary"] == "fixed"
    # 트러스 → steel
    assert infer_structural_defaults("truss", has_material_tag=False, length_m=200, max_span_m=120)["material"] == "steel"


def test_infer_boundary_continuous():
    # 다경간(연장/최대경간>1.5) → continuous
    r = infer_structural_defaults("girder", has_material_tag=False, length_m=650, max_span_m=50)
    assert r["boundary"] == "continuous"
    r2 = infer_structural_defaults("girder", has_material_tag=False, length_m=45, max_span_m=45)
    assert r2["boundary"] == "simply_supported"


def test_infer_respects_material_tag():
    # material 태그 있으면 형식추론이 재료 덮지 않음
    r = infer_structural_defaults("box_girder", has_material_tag=True, length_m=650, max_span_m=90)
    assert "material" not in r


class _Bridge:
    def __init__(self, tags, length): self.tags=tags; self.length_m=length; self.name="t"; self.osm_url="u"


def test_profile_from_osm_psc_box_is_concrete():
    b = _Bridge({"bridge:structure": "box-girder"}, 650.0)
    prof = profile_from_osm(b)
    assert prof.bridge_type == "box_girder"
    assert prof.material == "prestressed_concrete"       # 강재 아님!
    assert prof.youngs() < 1e11                          # 콘크리트 E
    assert prof.section_depth_m != 1.0                   # 스팬 기반
    assert prof.boundary in ("continuous", "simply_supported")


def test_profile_from_osm_material_tag_wins():
    b = _Bridge({"bridge:structure": "box-girder", "material": "steel"}, 650.0)
    prof = profile_from_osm(b)
    assert prof.material == "steel"                       # 명시 태그 우선


def test_golden_default_unchanged():
    # cfg 없음(기본 BridgeProfile) → 강재 거더 기본 유지(골든 회귀 안전)
    prof = resolve_profile(type("C", (), {"bridge_profile": None})())
    assert prof.material == "steel" and prof.section_depth_m == 1.0
