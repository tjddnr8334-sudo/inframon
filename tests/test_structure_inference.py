"""형식→재료·단면·경계 추론(PINN 보충) — 강재 고정 가정 해소."""
from __future__ import annotations
from inframon.structure import infer_structural_defaults, resolve_profile
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


def _cfg(profile):
    return type("C", (), {"bridge_profile": profile})()


def test_dict_type_only_infers_structure():
    """형식만 준 dict 도 형식별 단면·경계·재료·자중을 추론해야 한다.

    회귀: 예전엔 이 추론이 CSV/OSM 인제스트에만 있어, cfg.bridge_profile 로 형식만 주면
    전부 거더 기본값(단면 1.0m·단순지지)으로 돌았다 — 형식이 결과에 반영 안 됨.
    """
    arch = resolve_profile(_cfg({"bridge_type": "arch", "length_m": 60.0}))
    girder = resolve_profile(_cfg({"bridge_type": "girder", "length_m": 60.0}))
    rahmen = resolve_profile(_cfg({"bridge_type": "rahmen", "length_m": 40.0}))
    # 아치·라멘은 콘크리트, 거더는 강재 (형식→재료)
    assert "concrete" in arch.material and girder.material == "steel"
    # 라멘은 고정단(형식→경계)
    assert rahmen.boundary == "fixed" and girder.boundary == "simply_supported"
    # 단면높이가 형식별 스팬비로 달라진다(전부 1.0m 가 아니다)
    assert arch.section_depth_m != 1.0
    assert girder.section_depth_m != arch.section_depth_m


def test_explicit_fields_beat_type_inference():
    """사용자가 명시한 값은 형식 추론이 덮지 않는다(명시 > 추론 > 기본)."""
    prof = resolve_profile(_cfg({"bridge_type": "arch", "length_m": 50.0,
                                 "section_depth_m": 5.5, "material": "steel"}))
    assert prof.section_depth_m == 5.5          # 명시 단면 유지
    assert prof.material == "steel"             # 명시 재료 유지(아치라도 강재)


def test_type_inference_needs_explicit_type():
    """형식을 안 주면(빈 dict) 추론하지 않는다 — 기존 경로 불변(골든 안전)."""
    prof = resolve_profile(_cfg({"length_m": 60.0}))
    assert prof.material == "steel" and prof.section_depth_m == 1.0
