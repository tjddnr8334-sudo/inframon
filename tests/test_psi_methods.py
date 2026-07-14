"""PSI 시계열 방법론 — PS(ADI)·SBAS(네트워크 역산)·QPS(하이브리드)."""
from __future__ import annotations

import numpy as np
import pytest

from inframon.insar.psi_methods import (
    amplitude_dispersion_index, network_redundancy, ps_selection, qps_classification,
    sbas_design_matrix, sbas_invert, temporal_coherence,
)


def test_amplitude_dispersion_index():
    amp = np.array([[10, 10, 10, 10], [10, 5, 15, 10]], float)   # 안정 vs 변동
    adi = amplitude_dispersion_index(amp)
    assert adi[0] == pytest.approx(0.0)                          # 완전안정 → 0
    assert adi[1] > adi[0]


def test_temporal_coherence():
    stable = np.zeros((1, 20))                                   # 잔차 0 → γ=1
    rng = np.random.default_rng(0)
    noisy = rng.uniform(-np.pi, np.pi, (1, 200))                 # 랜덤 → γ≈0
    assert temporal_coherence(stable)[0] == pytest.approx(1.0)
    assert temporal_coherence(noisy)[0] < 0.2


def test_ps_selection_adi_and_coh():
    adi = np.array([0.1, 0.3, 0.2])
    tc = np.array([0.99, 0.99, 0.5])
    assert list(ps_selection(adi, adi_max=0.25)) == [True, False, True]
    # temporal coherence 요건 추가 → 3번째(γ0.5) 탈락
    assert list(ps_selection(adi, adi_max=0.25, temporal_coh=tc, coh_min=0.9)) == [True, False, False]


def test_sbas_design_matrix():
    G = sbas_design_matrix([(0, 1), (1, 2)], 3)
    assert G.shape == (2, 3)
    assert list(G[0]) == [-1, 1, 0] and list(G[1]) == [0, -1, 1]


def test_sbas_invert_recovers_timeseries():
    # 참 시계열 D=[0,3,5,9] (ref epoch0=0). 소baseline 인접+skip1 페어
    D_true = np.array([0.0, 3.0, 5.0, 9.0])
    pairs = [(0, 1), (1, 2), (2, 3), (0, 2), (1, 3)]
    disp = np.array([D_true[j] - D_true[i] for i, j in pairs])   # 페어변위
    D = sbas_invert(pairs, disp, 4, ref_epoch=0)
    assert np.allclose(D, D_true, atol=1e-9)


def test_sbas_invert_multipoint():
    D_true = np.array([[0, 2, 4], [0, -1, -3]], float)           # 2점
    pairs = [(0, 1), (1, 2), (0, 2)]
    disp = np.stack([[D_true[n, j] - D_true[n, i] for i, j in pairs] for n in range(2)])
    D = sbas_invert(pairs, disp, 3)
    assert np.allclose(D, D_true, atol=1e-9)


def test_network_redundancy_connected():
    r = network_redundancy([(0, 1), (1, 2), (0, 2)], 3)
    assert r["connected"] is True and r["rank"] == 2
    # 끊긴 네트워크(epoch3 고립)
    r2 = network_redundancy([(0, 1), (1, 2)], 4)
    assert r2["connected"] is False


def test_qps_classification():
    adi = np.array([0.1, 0.3, 0.4, 0.2])
    tc = np.array([0.99, 0.8, 0.5, 0.6])
    cls = qps_classification(adi, tc, adi_max=0.25, ds_coh_min=0.7)
    # 0:PS(ADI0.1) 1:DS(ADI0.3·γ0.8) 2:제외(γ0.5) 3:PS(ADI0.2)
    assert list(cls) == [2, 1, 0, 2]
