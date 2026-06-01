"""교량 구조 프로파일 — 재료/단면/스팬 해석 단위 테스트."""

from __future__ import annotations

import numpy as np

from inframon.structure import BridgeProfile, resolve_profile


def test_material_defaults():
    steel = BridgeProfile(material="steel")
    conc = BridgeProfile(material="concrete")
    assert steel.youngs() == 2.1e11
    assert conc.youngs() < steel.youngs()              # 콘크리트가 더 무름
    assert conc.rho_a() > steel.rho_a()                # 콘크리트가 더 무거움
    assert steel.half_depth() == 0.5                   # section_depth 1.0 → y=0.5


def test_explicit_overrides_material():
    p = BridgeProfile(material="steel", youngs_modulus=1.0e11, mass_per_len=5.0e3)
    assert p.youngs() == 1.0e11                        # 명시값이 재료표보다 우선
    assert p.rho_a() == 5.0e3


def test_resolve_from_dict_and_span_from_xyz():
    class Cfg:
        bridge_profile = {"material": "concrete", "section_depth_m": 2.5}

    xyz = np.column_stack([np.linspace(0, 80, 10), np.zeros(10), np.zeros(10)])  # 80m 스팬
    p = resolve_profile(Cfg(), xyz)
    assert p.material == "concrete" and p.section_depth_m == 2.5
    assert abs(p.length_m - 80.0) < 1.0                # xyz 에서 스팬 추정


def test_resolve_default_when_absent():
    class Cfg:
        pass

    p = resolve_profile(Cfg(), None)
    assert p.bridge_type == "girder" and p.material == "steel"  # 기본 = 강재 거더교
    assert p.length_m is None


def test_resolve_lonlat_degrees_to_meters():
    class Cfg:
        bridge_profile = None

    # 경위도(작은 ptp) → degree×111000
    xyz = np.column_stack([127.10 + np.linspace(0, 9e-4, 8), 37.36 + np.zeros(8), np.zeros(8)])
    p = resolve_profile(Cfg(), xyz)
    assert 80 < p.length_m < 120                       # ~0.0009도 × 111km ≈ 100m
