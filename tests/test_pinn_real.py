"""PINN 실구현(Phase 4) — PyTorch PINN + Euler-Bernoulli + FEM. torch 없으면 skip."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("torch")

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import FRAM_FUNCTIONS, FRAMOutput
from inframon.cv.engine import run_cv
from inframon.insar.engine import run_insar
from inframon.pinn.real_engine import (
    _fem_beam_frequencies,
    _identify_EI_from_pde,
    _structural_span,
    run_pinn_real,
)
from inframon.structure import BridgeProfile


def test_identify_EI_from_pde_formula():
    """EI = q·L⁴/(w_scale·d4) + 물리 범위 클립."""
    assert _identify_EI_from_pde(1.0, 100.0, q=1e4, w_scale_m=1.0) == pytest.approx(1e12)
    assert _identify_EI_from_pde(10.0, 100.0, q=1e4, w_scale_m=1.0) == pytest.approx(1e11)  # d4↑→EI↓
    assert _identify_EI_from_pde(0.0, 100.0) == pytest.approx(1e14)      # 강체→상한 클립
    assert _identify_EI_from_pde(1e30, 1.0) == pytest.approx(1e6)        # 과대→하한 클립


def test_identify_EI_recovers_known_beam():
    """단순보 균일하중 해석해(∂⁴w/∂x⁴=q/EI 상수)로부터 EI 를 정확히 회수한다."""
    EI_true, q, L, w_scale = 5.0e10, 1.0e4, 80.0, 0.02
    d4_phys = q / EI_true                                # 물리 4차도함수(상수)
    d4_hat = (L**4 / w_scale) * d4_phys                  # 정규화 d4 = (L⁴/w_scale)·d4_phys
    EI_rec = _identify_EI_from_pde(d4_hat, L, q=q, w_scale_m=w_scale)
    assert abs(EI_rec - EI_true) / EI_true < 1e-6


def test_fem_matches_analytic_simply_supported():
    # 단순지지 보 해석해: f_n = (n²·π / (2 L²))·√(EI/m)
    EI, m, L = 2.0e10, 1.0e4, 100.0
    f = _fem_beam_frequencies(EI, m, L, n_elem=24, n_modes=3)
    fa = [(n ** 2 * np.pi / (2 * L ** 2)) * np.sqrt(EI / m) for n in (1, 2, 3)]
    assert len(f) == 3
    assert f[0] < f[1] < f[2]
    assert abs(f[0] - fa[0]) / fa[0] < 0.05   # 1차 모드 해석해와 5% 이내


def test_fem_fixed_boundary_higher_than_ss():
    # 고정단(연속교 내부경간 근사) 1차 진동수 > 단순지지
    EI, m, L = 2.0e10, 1.0e4, 40.0
    f_ss = _fem_beam_frequencies(EI, m, L, "simply_supported")
    f_fix = _fem_beam_frequencies(EI, m, L, "fixed")
    assert f_fix[0] > f_ss[0]
    # 고정단 1차 계수 ≈ (4.730)²/(π²) ≈ 2.27× 단순지지
    assert f_fix[0] / f_ss[0] == pytest.approx((4.730041 ** 2) / (np.pi ** 2), rel=0.05)


def test_structural_span_single_vs_multi():
    # 단경간(단순지지·고정): 연장 그대로, n=1 (골든 불변)
    for bnd in ("simply_supported", "fixed"):
        prof = BridgeProfile(bridge_type="girder", length_m=40.0, boundary=bnd)
        span, n = _structural_span(prof, 40.0)
        assert span == 40.0 and n == 1
    # 다경간 연속교: 연장을 경간수로 분할(span<length, n>1)
    prof = BridgeProfile(bridge_type="girder", length_m=108.0, boundary="continuous")
    span, n = _structural_span(prof, 108.0)
    assert n > 1 and span < 108.0
    assert span * n == pytest.approx(108.0, abs=n)     # 균등분할


def _pinn(tmp_path, n_points=30, n_dates=10, epochs=60):
    cfg = PipelineConfig(n_points=n_points, n_dates=n_dates)
    cfg.pinn_epochs = epochs
    proj = tmp_path / "p.h5"
    store = ProjectStore(proj, mode="w").__enter__()
    cv = run_cv(store, cfg)
    insar = run_insar(store, cv, cfg)
    out = run_pinn_real(store, insar, cfg)
    return cfg, store, insar, out


def test_real_pinn_fills_contract(tmp_path):
    cfg, store, insar, out = _pinn(tmp_path)
    try:
        assert out.func_names == list(FRAM_FUNCTIONS)
        Vfs = store.read_array(out.V_func_series_ds)
        assert Vfs.shape == (4, 10)                       # [n_func, M], 순서 보존
        assert np.isfinite(Vfs).all()

        for ds in (out.comp_thermal_ds, out.comp_load_ds, out.comp_settle_ds,
                   out.comp_anomaly_ds, out.strain_ds, out.stress_ds, out.deflection_ds):
            a = store.read_array(ds)
            assert a.shape == (30, 10) and np.isfinite(a).all()

        EI = store.read_array(out.EI_ds)
        alpha = store.read_array(out.alpha_ds)
        nf = store.read_array(out.natural_freq_ds)
        assert EI.shape == (30,) and (EI > 0).all()
        assert alpha.shape == (30,) and (alpha > 0).all()
        assert len(nf) == 3 and (nf > 0).all() and nf[0] < nf[1]

        for ds in (out.V_thermal_ds, out.V_load_ds, out.V_settle_ds, out.V_anomaly_ds):
            v = store.read_array(ds)
            assert v.shape == (30,) and (v >= 0).all() and (v <= 1).all()
    finally:
        store.__exit__(None, None, None)


def _insar_for_pinn(tmp_path, n_points=25, n_dates=12, epochs=120):
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = PipelineConfig(n_points=n_points, n_dates=n_dates)
    cfg.pinn_epochs = epochs
    store = ProjectStore(tmp_path / "p.h5", mode="w").__enter__()
    cv = run_cv(store, cfg)
    insar = run_insar(store, cv, cfg)
    return cfg, store, insar


def test_pinn_uses_vertical_channel(tmp_path):
    """InSAR 에 vertical_ds 가 있으면 PINN 이 연직 채널로 처짐/침하를 분리한다."""
    cfg, store, insar = _insar_for_pinn(tmp_path)
    N, M = insar.n_points, insar.n_dates
    # 합성 연직 변위([N,M]) 적재 + 계약 필드 연결(융합 산출 모사)
    rng = np.random.default_rng(0)
    vert = (rng.normal(0, 1.0, size=(N, M)) - np.linspace(0, 3, M)).astype(np.float32)  # 침하 추세
    store.write_array("/insar/vertical", vert)
    insar.vertical_ds = "/insar/vertical"
    try:
        out = run_pinn_real(store, insar, cfg)
        inp = store.read_json_attr("pinn", "inputs")
        defl = store.read_array(out.deflection_ds)
        settle = store.read_array(out.comp_settle_ds)
    finally:
        store.__exit__(None, None, None)
    assert inp["vertical_observed"] is True
    assert defl.shape == (N, M) and np.isfinite(defl).all()
    assert settle.shape == (N, M) and np.isfinite(settle).all()


def test_pinn_profile_scales_response(tmp_path):
    """단면 깊이 2배 → 변형률 2배(같은 곡률, 프로파일이 응답 스케일만 바꿈, 결정론)."""
    cfg1, s1, ins1 = _insar_for_pinn(tmp_path / "a")
    o1 = run_pinn_real(s1, ins1, cfg1)
    strain1 = abs(s1.read_array(o1.strain_ds)).mean()
    s1.__exit__(None, None, None)

    cfg2, s2, ins2 = _insar_for_pinn(tmp_path / "b")
    cfg2.bridge_profile = {"section_depth_m": 2.0}     # 기본 1.0 → 2.0
    o2 = run_pinn_real(s2, ins2, cfg2)
    strain2 = abs(s2.read_array(o2.strain_ds)).mean()
    s2.__exit__(None, None, None)
    assert strain2 / (strain1 + 1e-30) == pytest.approx(2.0, rel=1e-3)


def test_pinn_temperature_drives_thermal(tmp_path):
    """온도 ΔT 데이터 → 열팽창 성분이 온도를 추종(thermal=α·L·ΔT), α 식별."""
    cfg, store, insar = _insar_for_pinn(tmp_path)
    dates = store.read_array(insar.dates_ds)
    ty = (dates - dates[0]) / 365.0
    temp = 15.0 + 10.0 * np.sin(2 * np.pi * ty)        # 계절 온도(°C)
    cfg.pinn_temperature = temp
    try:
        out = run_pinn_real(store, insar, cfg)
        ct = store.read_array(out.comp_thermal_ds)
        alpha = store.read_array(out.alpha_ds)
        inp = store.read_json_attr("pinn", "inputs")
    finally:
        store.__exit__(None, None, None)
    assert inp["temperature_driven"] is True
    corr = np.corrcoef(ct.mean(axis=0), temp)[0, 1]
    assert corr > 0.9                                  # 열팽창이 실측 온도를 추종
    assert (alpha > 0).all()


def test_pinn_traffic_modulates_load(tmp_path):
    """교통량 데이터 → 하중 성분이 교통량으로 변조됨(메커니즘 engaged + 결과 변화)."""
    cfg, store, insar = _insar_for_pinn(tmp_path / "base")
    base = run_pinn_real(store, insar, cfg)
    load_base = store.read_array(base.comp_load_ds).copy()
    store.__exit__(None, None, None)

    cfg2, s2, ins2 = _insar_for_pinn(tmp_path / "traf")
    cfg2.pinn_traffic = 5000.0 + 3000.0 * np.abs(np.sin(np.linspace(0, 3, 12)))
    out = run_pinn_real(s2, ins2, cfg2)
    load_traf = s2.read_array(out.comp_load_ds)
    inp = s2.read_json_attr("pinn", "inputs")
    s2.__exit__(None, None, None)
    assert inp["traffic_driven"] is True
    assert np.isfinite(load_traf).all()
    assert not np.allclose(load_traf, load_base)       # 교통량이 하중 성분을 실제로 바꿈


def test_pinn_inputs_attr_records_profile(tmp_path):
    cfg, store, insar = _insar_for_pinn(tmp_path)
    cfg.bridge_profile = {"name": "테스트교", "bridge_type": "girder",
                          "material": "concrete", "source": "manual"}
    try:
        run_pinn_real(store, insar, cfg)
        inp = store.read_json_attr("pinn", "inputs")
    finally:
        store.__exit__(None, None, None)
    assert inp["material"] == "concrete" and inp["bridge_type"] == "girder"
    assert inp["profile_source"] == "manual" and inp["span_m"] > 0


def test_pde_terms_and_params():
    """형식별 PDE 항 매핑 + 학습 파라미터 활성화."""
    import torch

    from inframon.pinn.pde import PDE_TERMS, make_pde_params
    assert PDE_TERMS["girder"] == (False, False, False)
    assert PDE_TERMS["cable_stayed"][1] is True       # 탄성지지 항
    assert PDE_TERMS["arch"][0] is True               # 축력 항
    # girder: 둘 다 None / cable_stayed: p0만 / arch: p2만
    assert make_pde_params("girder", torch) == (None, None)
    p2, p0 = make_pde_params("cable_stayed", torch)
    assert p2 is None and p0 is not None
    p2, p0 = make_pde_params("arch", torch)
    assert p2 is not None and p0 is None


@pytest.mark.parametrize("bridge_type,axial,found", [
    ("girder", False, False),
    ("cable_stayed", False, True),
    ("arch", True, False),
    ("suspension", True, False),
])
def test_pinn_form_specific_pde(tmp_path, bridge_type, axial, found):
    """각 형식이 자기 지배 PDE로 학습되고 형식별 파라미터가 기록된다."""
    cfg, store, insar = _insar_for_pinn(tmp_path / bridge_type, n_points=18, n_dates=10, epochs=80)
    cfg.bridge_profile = {"bridge_type": bridge_type}
    try:
        out = run_pinn_real(store, insar, cfg)
        inp = store.read_json_attr("pinn", "inputs")
        ei = store.read_array(out.EI_ds)
    finally:
        store.__exit__(None, None, None)
    assert inp["pde_form"] == bridge_type
    assert (inp["pde_axial_p2"] is not None) == axial         # 아치·현수만 축력항
    assert (inp["pde_foundation_k"] is not None) == found     # 사장교만 탄성지지항
    if found:
        assert inp["pde_foundation_k"] >= 0                   # softplus → k≥0
    assert (ei > 0).all() and np.isfinite(ei).all()


def test_real_pinn_registered():
    from inframon.orchestrator import engines

    assert engines.resolve("pinn", "real") is run_pinn_real
    assert "real" in engines.available_modes("pinn")


def test_pipeline_hotswap_pinn_real(tmp_path):
    from inframon.orchestrator.pipeline import run_pipeline

    cfg = PipelineConfig(n_points=25, n_dates=8,
                         engines={"cv": "stub", "insar": "stub", "pinn": "real", "fram": "stub"})
    cfg.pinn_epochs = 50
    fram = run_pipeline(tmp_path / "out.h5", cfg)
    assert isinstance(fram, FRAMOutput)
    assert fram.n_points == 25
    assert 0.0 <= fram.cri_global_max <= 1.0
