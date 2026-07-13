"""상용 FEM 교차검증 — 해석식·솔버검증·EI 신뢰성·모델 타당성."""
from __future__ import annotations

import json
import math

import h5py
import numpy as np
import pytest

from inframon import fem_crosscheck as fx


def test_analytical_ss_closed_form():
    # 단순지지 1차: f_1 = (π/2)·√(EI/(m·L⁴))
    EI, m, L = 1.0e10, 1.0e4, 50.0
    f = fx.analytical_frequencies(EI, m, L, "simply_supported", 3)
    f1_expected = (math.pi / 2) * math.sqrt(EI / (m * L ** 4))
    assert f[0] == pytest.approx(f1_expected, rel=1e-9)
    assert f[1] == pytest.approx(4 * f[0], rel=1e-9)     # n² 비례(2²=4×)
    assert f[2] == pytest.approx(9 * f[0], rel=1e-9)


def test_boundary_ordering_stiffer_higher_freq():
    EI, m, L = 1.0e10, 1.0e4, 50.0
    ss = fx.analytical_frequencies(EI, m, L, "simply_supported")[0]
    fixed = fx.analytical_frequencies(EI, m, L, "fixed")[0]
    cant = fx.analytical_frequencies(EI, m, L, "cantilever")[0]
    assert cant < ss < fixed                             # 캔틸레버<단순<고정


def test_midspan_deflection_ss():
    q, EI, L = 1.0e4, 1.0e10, 40.0
    d = fx.midspan_deflection_mm(q, EI, L, "simply_supported")
    assert d == pytest.approx(5.0 / 384.0 * q * L ** 4 / EI * 1000.0, rel=1e-9)


def test_crosscheck_solver_validation_matches():
    # PINN FEM freq 를 해석식(동일 EI·SS)과 같게 주면 솔버오차≈0
    EI, m, L = 5.0e10, 1.0e4, 40.0
    ana = fx.analytical_frequencies(EI, m, L, "simply_supported", 3)
    r = fx.crosscheck(EI_identified=EI, EI_geometric=EI, m_per_len=m, span_m=L,
                      q_N_m=1.0e4, boundary="simply_supported", freq_pinn_fem=ana)
    assert all(e < 1e-6 for e in r.solver_error_pct)
    assert r.ei_ratio == pytest.approx(1.0)
    assert r.identified_reliable and r.model_plausible
    assert r.assessment.startswith("✅")


def test_crosscheck_flags_clipped_ei():
    # 식별 EI 가 클립 천장(1e14) → 부정확 플래그
    r = fx.crosscheck(EI_identified=1.0e14, EI_geometric=4.0e10, m_per_len=1.0e4,
                      span_m=108.0, q_N_m=1.4e5, boundary="simply_supported",
                      freq_pinn_fem=[13.0, 52.0, 118.0])
    assert not r.identified_reliable
    assert r.ei_ratio > 100
    # 이 케이스는 설계처짐도 과대 → 모델부적합이 먼저 잡히거나 클립경고
    assert "⚠️" in r.assessment


def test_crosscheck_flags_stiffness_loss():
    # 식별 강성이 설계의 50% → 강성저하 의심
    EI_geom = 1.0e11
    r = fx.crosscheck(EI_identified=0.5 * EI_geom, EI_geometric=EI_geom, m_per_len=1.0e4,
                      span_m=40.0, q_N_m=1.0e4, boundary="simply_supported",
                      freq_pinn_fem=[])
    assert r.ei_ratio == pytest.approx(0.5)
    assert "강성저하" in r.assessment


def test_crosscheck_no_geometric_ei():
    # 폭 없어 기하 EI 미상 → 솔버검증만
    r = fx.crosscheck(EI_identified=5.0e10, EI_geometric=None, m_per_len=1.0e4,
                      span_m=40.0, q_N_m=1.0e4, freq_pinn_fem=[1.0])
    assert r.EI_geometric is None and r.ei_ratio is None
    assert r.deflection_design_mm is None


def _write_pinn_h5(path, inputs, freqs):
    with h5py.File(path, "w") as h:
        g = h.create_group("pinn")
        g.create_dataset("natural_freq", data=np.array(freqs, dtype=float))
        g.attrs["inputs"] = json.dumps(inputs)
    return str(path)


def test_crosscheck_project_reads_pinn(tmp_path):
    inputs = {"material": "prestressed_concrete", "span_m": 108.0,
              "EI_global": 1.0e14, "geometric_EI_Nm2": 4.19e10,
              "q_effective_N_m": 1.4e5, "section_area_m2": 4.19,
              "boundary": "simply_supported"}
    p = _write_pinn_h5(tmp_path / "proj.h5", inputs, [13.16, 52.63, 118.45])
    r = fx.crosscheck_project(p)
    assert r.span_m == 108.0
    assert r.EI_identified == 1.0e14
    assert r.m_per_len == pytest.approx(2500.0 * 4.19)     # 밀도×단면적
    assert len(r.freq_pinn_fem) == 3
    assert "⚠️" in r.assessment                            # 클립/모델 경고


def test_effective_boundary_continuous_maps_to_fixed():
    assert fx._effective_boundary("continuous") == "fixed"
    assert fx._effective_boundary("simply_supported") == "simply_supported"


def test_analytical_continuous_equals_fixed():
    # 연속교 내부경간은 고정단 근사 → 두 진동수 동일
    EI, m, L = 1.0e10, 1.0e4, 30.0
    fc = fx.analytical_frequencies(EI, m, L, "continuous")
    ff = fx.analytical_frequencies(EI, m, L, "fixed")
    assert fc == pytest.approx(ff)
    # 연속(고정) 1차 > 단순지지 1차
    fs = fx.analytical_frequencies(EI, m, L, "simply_supported")
    assert fc[0] > fs[0]


def test_crosscheck_project_uses_structural_span(tmp_path):
    # 다경간: structural_span_m(18) 이 span_m(108)보다 우선 사용
    inputs = {"material": "prestressed_concrete", "span_m": 108.0,
              "structural_span_m": 18.0, "n_spans": 6, "boundary": "continuous",
              "EI_global": 1.0e14, "geometric_EI_Nm2": 4.19e10,
              "q_effective_N_m": 1.4e5, "section_area_m2": 4.19}
    p = _write_pinn_h5(tmp_path / "multi.h5", inputs, [1073.8, 2960.2, 5805.3])
    r = fx.crosscheck_project(p)
    assert r.span_m == 18.0                                # 연장 108 아닌 단일경간 18
    assert r.boundary == "continuous"
    # 단일경간 처짐은 물리적(사용성 통과) — 연장 단일SS 모델의 L/18 부적합이 해소
    assert r.model_plausible


def test_crosscheck_project_missing_pinn_raises(tmp_path):
    p = tmp_path / "empty.h5"
    with h5py.File(p, "w") as h:
        h.create_group("insar")
    with pytest.raises(ValueError, match="pinn"):
        fx.crosscheck_project(str(p))
