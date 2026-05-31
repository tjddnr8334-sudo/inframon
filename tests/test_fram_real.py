"""FRAM 고도화(Phase 5) — 점별 공명 + 실 load + 절대 보정. 계약·결함수정 검증."""

from __future__ import annotations

import numpy as np

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import FRAMOutput
from inframon.fram.real_engine import _pointwise_resonance, run_fram_real


def test_pointwise_resonance_is_spatial():
    # 점마다 다른 동조 패턴 → 점별로 다른 공명값이 나와야 (broadcast 결함 해소 확인)
    rng = np.random.default_rng(0)
    N, M = 20, 24
    Vi = rng.random((4, N, M))
    # 점 0~9 는 기능들이 동일(완전 동조) → 높은 공명
    for f in range(4):
        Vi[f, :10, :] = Vi[0, :10, :]
    R = _pointwise_resonance(Vi, win=6)
    assert R.shape == (N, M)
    assert R.var() > 0                          # 점별로 다름 (broadcast 아님)
    assert R[:10, 6:].mean() > R[10:, 6:].mean()  # 동조 점들이 더 높은 공명


def _build(tmp_path, engines, n_points=30, n_dates=12, pinn_epochs=None):
    cfg = PipelineConfig(n_points=n_points, n_dates=n_dates, engines=engines)
    if pinn_epochs:
        cfg.pinn_epochs = pinn_epochs
    from inframon.orchestrator.pipeline import run_pipeline
    return cfg, run_pipeline(tmp_path / "p.h5", cfg)


def test_fram_real_fills_contract(tmp_path):
    cfg, fram = _build(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    assert isinstance(fram, FRAMOutput)
    with ProjectStore(tmp_path / "p.h5", mode="r") as store:
        cri = store.read_array(fram.CRI_ds)
        rij = store.read_array(fram.resonance_Rij_ds)
        amp = store.read_array(fram.amplification_ds)
    assert cri.shape == (30, 12)
    assert rij.shape == (4, 4, 12)
    assert amp.shape == (30, 12)
    assert (cri >= 0).all() and (cri <= 1).all()
    assert np.isfinite(cri).all()
    assert 0.0 <= fram.cri_global_max <= 1.0


def test_fram_real_cri_varies_spatially(tmp_path):
    # 점별 공명·절대보정 → CRI 가 점마다 다르고, 마지막 시점 손상 단조성 유지
    cfg, fram = _build(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    with ProjectStore(tmp_path / "p.h5", mode="r") as store:
        cri = store.read_array(fram.CRI_ds)
    assert cri[:, -1].var() > 0                       # 공간적으로 다름
    assert cri[:, -3:].max() >= cri[:, :3].max()      # 주입 손상 누적 → 후반 ≥ 초반


def test_network_resonance_spectral():
    """함수망 공명 = 결합그래프 스펙트럼 반경/normalize: 완전동조=1, 무결합=0, 0.5=0.5."""
    from inframon.fram.real_engine import _network_resonance

    M = 5
    full = np.ones((4, 4, M))                              # 모든 기능쌍 |corr|=1
    assert np.allclose(_network_resonance(full), 1.0, atol=1e-6)
    ident = np.repeat(np.eye(4)[:, :, None], M, axis=2)    # 비대각 0 = 무결합
    assert np.allclose(_network_resonance(ident), 0.0, atol=1e-6)
    half = np.full((4, 4, M), 0.5)                         # 비대각 0.5 → λmax=1.5, /3=0.5
    assert np.allclose(_network_resonance(half), 0.5, atol=1e-6)


def test_fram_real_emits_network_resonance(tmp_path):
    """real FRAM 이 함수망 공명 시계열 [M]∈[0,1] 을 산출한다(stub 은 None)."""
    cfg, fram = _build(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    assert fram.network_resonance_ds is not None
    with ProjectStore(tmp_path / "p.h5", mode="r") as store:
        s = store.read_array(fram.network_resonance_ds)
    assert s.shape == (12,)
    assert (s >= 0).all() and (s <= 1).all() and np.isfinite(s).all()


def test_fram_stub_has_no_network_resonance(tmp_path):
    """stub FRAM 은 network_resonance 를 내지 않는다(None) → 골든·계약 무영향."""
    cfg, fram = _build(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "stub"})
    assert fram.network_resonance_ds is None


def test_fram_real_registered():
    from inframon.orchestrator import engines

    assert engines.resolve("fram", "real") is run_fram_real
    assert "real" in engines.available_modes("fram")


def test_pipeline_pinn_real_fram_real(tmp_path):
    # 실 PINN(실 load) → 실 FRAM 전체 체인
    cfg, fram = _build(tmp_path,
                       {"cv": "stub", "insar": "stub", "pinn": "real", "fram": "real"},
                       n_points=20, n_dates=8, pinn_epochs=50)
    assert isinstance(fram, FRAMOutput)
    assert 0.0 <= fram.cri_global_max <= 1.0
