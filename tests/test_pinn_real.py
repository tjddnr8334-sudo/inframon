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
    run_pinn_real,
)


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
