"""InSAR 정확도 향상 — 기준점 정합 · 온도회귀(열팽창 분리) · 고도상관 대기보정.

- reference_correction: 안정 기준점 대비 상대변위(전역 편향 제거).
- temporal_decompose: los(t)=a+b·t+c·T(t) 회귀 → 변형속도 b(열팽창 분리), 열계수 c.
- height_correlated_correction: 성층 대류권 지연 근사(고도-위상 선형상관 제거, GACOS 대안).
"""
from __future__ import annotations

import numpy as np


def reference_correction(los: np.ndarray, ref_idx: int) -> np.ndarray:
    """기준점(ref_idx) 시계열을 전 점에서 빼 상대변위로. [N,M] → [N,M]."""
    los = np.asarray(los, dtype=np.float64)
    return los - los[int(ref_idx)][None, :]


def most_stable_index(los: np.ndarray, coherence: np.ndarray | None = None) -> int:
    """기준점 후보 — coherence 높고 시간변동(std) 작은 점."""
    los = np.asarray(los, dtype=np.float64)
    var = los.std(axis=1)
    score = var / (var.max() + 1e-12)
    if coherence is not None:
        score = score + (1.0 - np.asarray(coherence, dtype=np.float64))
    return int(np.argmin(score))


# 기준점은 초안정 PS 여야 정확한 상대변위 기준이 된다. 시간 결맞음 임계(권장 0.98).
REF_MIN_COHERENCE = 0.98


def select_reference_point(los: np.ndarray, coherence: np.ndarray, *,
                           min_coh: float = REF_MIN_COHERENCE) -> dict:
    """기준점 선정 — **시간 결맞음 ≥ min_coh(기본 0.98)** 인 초안정 점 중 시간변동 최소.

    reference point 는 전역 위상 기준이라 아주 안정된 PS(coh≥0.98)여야 상대변위가 신뢰된다.
    coh≥min_coh 후보가 없으면(=ROI 도심밀도 부족 신호) 최고 coherence 점으로 폴백하되
    meets_threshold=False 로 알린다 → ROI 를 도심 쪽으로 넓혀 재선정 필요.

    반환: index·coherence·temporal_std·meets_threshold·n_candidates·min_coh.
    """
    los = np.asarray(los, dtype=np.float64)
    coh = np.asarray(coherence, dtype=np.float64).ravel()
    var = los.std(axis=1)
    elig = np.where(coh >= min_coh)[0]
    if elig.size:
        idx = int(elig[np.argmin(var[elig])])       # 초안정 후보 중 시간변동 최소
        met = True
    else:
        idx = int(np.argmax(coh))                    # 폴백: 최고 coherence(임계 미달)
        met = False
    return {"index": idx, "coherence": float(coh[idx]), "temporal_std": float(var[idx]),
            "meets_threshold": met, "n_candidates": int(elig.size), "min_coh": float(min_coh)}


def temporal_decompose(los: np.ndarray, days: np.ndarray,
                       temperature: np.ndarray | None = None) -> dict:
    """점별 los(t)=a+b·t(yr)[+c·T] 최소제곱. 반환: 속도 b[mm/yr]·열계수 c·비열팽창 변형.

    온도 T 주면 열팽창 성분 c·T 를 분리 → deformation = los − c·T (계절 열변형 제거).
    """
    los = np.asarray(los, dtype=np.float64)                 # [N,M]
    t = np.asarray(days, dtype=np.float64) / 365.25         # 년
    cols = [np.ones_like(t), t]
    has_T = temperature is not None
    if has_T:
        T = np.asarray(temperature, dtype=np.float64)
        cols.append(T - T.mean())
    A = np.vstack(cols).T                                   # [M,k]
    coef, *_ = np.linalg.lstsq(A, los.T, rcond=None)        # [k,N]
    velocity = coef[1]                                      # [N] mm/yr
    thermal_c = coef[2] if has_T else np.zeros(los.shape[0])
    deformation = los.copy()
    if has_T:
        deformation = los - np.outer(thermal_c, (T - T.mean()))   # 열팽창 제거
    resid = los - (A @ coef).T
    return {"velocity_mm_yr": velocity, "thermal_coef": thermal_c,
            "deformation": deformation, "resid_std": resid.std(axis=1),
            "used_temperature": has_T}


def height_correlated_correction(los: np.ndarray, height: np.ndarray) -> dict:
    """성층 대류권 근사: 시점별 los~height 선형회귀 → 고도상관 성분 제거(GACOS 대안).

    반환: 보정 los[N,M] + 시점별 기울기(mm/m). height 없으면 호출 측에서 skip.
    """
    los = np.asarray(los, dtype=np.float64)                 # [N,M]
    h = np.asarray(height, dtype=np.float64)                # [N]
    N, M = los.shape
    corr = los.copy()
    slopes = np.zeros(M)
    G = np.vstack([h, np.ones_like(h)]).T                   # [N,2]
    for k in range(M):
        (s, b), *_ = np.linalg.lstsq(G, los[:, k], rcond=None)
        corr[:, k] = los[:, k] - (s * h + b)                # 고도상관+상수 제거
        slopes[k] = s
    return {"corrected": corr, "slope_mm_per_m": slopes}
