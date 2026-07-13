"""② 단면 정밀화 — 폭·높이·형식 → 단면적 A·단면2차 I·기하 EI·ρA(밀도×A)."""
from __future__ import annotations
import pytest
from inframon.structure import (BridgeProfile, MATERIAL_DENSITY, MATERIAL_RHO_A,
                                _TYPE_AREA_FACTOR, _TYPE_I_FACTOR)


def test_section_area_and_rho_a_refined():
    prof = BridgeProfile(bridge_type="box_girder", material="prestressed_concrete",
                         width_m=12.0, section_depth_m=3.0)
    A = 12.0 * 3.0 * _TYPE_AREA_FACTOR["box_girder"]        # 폭×높이×충실도
    assert prof.section_area_m2() == pytest.approx(A)
    # ρA = 밀도(콘크리트 2500) × A
    assert prof.rho_a() == pytest.approx(2500.0 * A)


def test_second_moment_and_geometric_ei():
    prof = BridgeProfile(bridge_type="box_girder", material="steel",
                         width_m=10.0, section_depth_m=2.0)
    I = 10.0 * 2.0**3 / 12.0 * _TYPE_I_FACTOR["box_girder"]
    assert prof.second_moment_I_m4() == pytest.approx(I)
    assert prof.geometric_EI() == pytest.approx(prof.youngs() * I)   # E·I


def test_rho_a_fallback_when_no_width():
    # 폭 미상 → 재료 대표 상수(골든 안전, 기존 동작)
    prof = BridgeProfile(material="steel", width_m=None)
    assert prof.section_area_m2() is None
    assert prof.geometric_EI() is None
    assert prof.rho_a() == MATERIAL_RHO_A["steel"]


def test_mass_per_len_explicit_wins():
    prof = BridgeProfile(width_m=12.0, section_depth_m=3.0, mass_per_len=5.0e4)
    assert prof.rho_a() == 5.0e4                              # 명시값 우선
