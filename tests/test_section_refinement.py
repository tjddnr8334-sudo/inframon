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


def test_refined_section_gives_physical_frequency():
    """재보정 단면제원이 실 교량 물리 진동수·질량을 준다(과강성 해소).

    36m 경간 콘크리트 거더: 1차 고유진동수가 물리범위(2~8Hz), 질량이 현실범위(30~90t/m).
    재보정 전(area 0.12·I 0.45)은 질량 과소·EI 과대로 ~10Hz 비물리였다.
    """
    from inframon.fem_crosscheck import analytical_frequencies
    prof = BridgeProfile(bridge_type="girder", material="prestressed_concrete",
                         width_m=36.0, section_depth_m=1.8, length_m=36.0,
                         boundary="continuous")
    m = prof.rho_a()
    assert 30e3 <= m <= 90e3                                  # 현실 질량 [kg/m]
    f1 = analytical_frequencies(prof.geometric_EI(), m, 36.0, "continuous")[0]
    assert 2.0 <= f1 <= 8.0                                   # 물리 1차모드 [Hz]
