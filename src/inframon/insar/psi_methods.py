"""PSI 시계열 방법론 — PS · SBAS(TS) · QPS 를 각 방법론대로.

InSAR 시계열 분석 3계열을 순수 numpy 로 구현(네트워크·SLC 처리와 분리, 단위테스트 용이):

- **PS (Persistent Scatterer, Ferretti 2001)**: 단일 마스터 스택에서 **진폭분산지수
  ADI=σ_amp/μ_amp < 0.25** 인 점같은(point-like) 영구 산란체. 위상 안정도(temporal
  coherence)로 정밀화. 도심 건물·구조물에 최적.
- **SBAS (Small BAseline Subset, Berardino 2002) → TS**: **소 시·공간 baseline 간섭쌍
  네트워크**를 최소제곱으로 역산해 누적 변위 시계열(TS)을 얻는다. 분포 산란체(DS)에
  강건, 시간 비간섭 완화.
- **QPS (Quasi-PS, SqueeSAR 류)**: PS(ADI) ∪ DS(SBAS 위상 안정도) **하이브리드** — 점같은
  PS 와 통계적으로 균질한 DS 를 함께 써 점밀도↑.
"""

from __future__ import annotations

import numpy as np


# ── PS (Persistent Scatterer) ──────────────────────────────────────────────
def amplitude_dispersion_index(amp: np.ndarray) -> np.ndarray:
    """진폭분산지수 ADI = σ_amp / μ_amp (시간축). amp:[N,M] → [N]. 낮을수록 안정(PS)."""
    amp = np.asarray(amp, dtype=np.float64)
    mu = amp.mean(axis=1)
    return amp.std(axis=1) / (mu + 1e-12)


def temporal_coherence(residual_phase: np.ndarray) -> np.ndarray:
    """위상 시간결맞음 γ = |⟨exp(i·resid)⟩| (0~1). residual_phase:[N,K] 모델 잔차위상[rad]."""
    r = np.asarray(residual_phase, dtype=np.float64)
    return np.abs(np.mean(np.exp(1j * r), axis=1))


def ps_selection(adi: np.ndarray, *, adi_max: float = 0.25,
                 temporal_coh: np.ndarray | None = None,
                 coh_min: float | None = None) -> np.ndarray:
    """PS 마스크 — ADI < adi_max (Ferretti). temporal_coh 주면 γ≥coh_min 도 요구."""
    mask = np.asarray(adi) < adi_max
    if temporal_coh is not None and coh_min is not None:
        mask = mask & (np.asarray(temporal_coh) >= coh_min)
    return mask


# ── SBAS (Small BAseline Subset) → 시계열(TS) ───────────────────────────────
def sbas_design_matrix(pairs: list, n_epochs: int) -> np.ndarray:
    """소baseline 네트워크 설계행렬 G[n_pairs, n_epochs]. pair(i,j) → +1(j)·−1(i).

    간섭쌍 위상 d_ij ≈ D_j − D_i (D=누적변위). G·D = d.
    """
    G = np.zeros((len(pairs), n_epochs))
    for k, (i, j) in enumerate(pairs):
        G[k, int(j)] = 1.0
        G[k, int(i)] = -1.0
    return G


def sbas_invert(pairs: list, disp, n_epochs: int, *, ref_epoch: int = 0):
    """소baseline 네트워크 페어변위 → 기준 epoch 누적 변위 시계열(최소제곱).

    disp: [n_pairs] 또는 [N, n_pairs] 페어별 LOS 변위. ref_epoch 을 0 으로 고정
    (열 제거해 rank 확보). 연결 안 된 네트워크는 lstsq 최소노름 해. 반환 [n_epochs]
    또는 [N, n_epochs] (ref_epoch 열=0).
    """
    G = sbas_design_matrix(pairs, n_epochs)
    cols = [c for c in range(n_epochs) if c != ref_epoch]
    Gr = G[:, cols]
    d = np.asarray(disp, dtype=np.float64)
    if d.ndim == 1:
        m = np.linalg.lstsq(Gr, d, rcond=None)[0]
        D = np.zeros(n_epochs); D[cols] = m
        return D
    m = np.linalg.lstsq(Gr, d.T, rcond=None)[0]            # [n_epochs-1, N]
    D = np.zeros((d.shape[0], n_epochs)); D[:, cols] = m.T
    return D


def network_redundancy(pairs: list, n_epochs: int) -> dict:
    """네트워크 연결성·중복도 — rank·연결여부·epoch별 페어수(SBAS 품질)."""
    G = sbas_design_matrix(pairs, n_epochs)
    rank = int(np.linalg.matrix_rank(G))
    per_epoch = np.abs(G).sum(axis=0).astype(int)          # 각 epoch 참여 페어수
    return {"n_pairs": len(pairs), "n_epochs": n_epochs, "rank": rank,
            "connected": rank == n_epochs - 1,             # ref 고정 시 완전연결
            "min_pairs_per_epoch": int(per_epoch.min()) if n_epochs else 0}


# ── QPS (Quasi-PS) — PS ∪ DS 하이브리드 ────────────────────────────────────
def qps_classification(adi: np.ndarray, temporal_coh: np.ndarray, *,
                       adi_max: float = 0.25, ds_coh_min: float = 0.7) -> np.ndarray:
    """QPS 분류 — 2=PS(ADI<adi_max), 1=DS(γ≥ds_coh_min, PS 아님), 0=제외.

    PS(점같은 영구산란체)와 DS(위상 안정 분포산란체)를 함께 채택(SqueeSAR 류) → 점밀도↑.
    """
    adi = np.asarray(adi); tc = np.asarray(temporal_coh)
    is_ps = adi < adi_max
    is_ds = (~is_ps) & (tc >= ds_coh_min)
    return np.where(is_ps, 2, np.where(is_ds, 1, 0)).astype(np.int8)
