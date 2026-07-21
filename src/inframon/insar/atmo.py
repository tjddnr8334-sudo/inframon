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


def common_mode(los: np.ndarray, coherence: np.ndarray | None = None, *,
                min_coh: float = 0.7, min_points: int = 5) -> dict:
    """공간 기준망 공통성분(APS 근사) — 안정점(고coh) 집합의 **에폭별 중앙값** 시계열.

    단일 기준점은 그 점 노이즈를 전 점에 주입하지만, 다수 안정점의 중앙값은 개별 노이즈를
    평균해 지워 **참 공통 대기/궤도 램프**만 남긴다. 이를 전 점에서 빼면 시간변동이 준다
    (공간 reference network 보정). 안정점이 부족하면 전체 점 중앙값으로 폴백한다.

    반환: cm[M] 공통성분 시계열 + meta(안정점 수·기준방식).
    """
    los = np.asarray(los, dtype=np.float64)
    n = los.shape[0]
    coh = (np.asarray(coherence, dtype=np.float64).ravel()
           if coherence is not None else np.ones(n))
    stable = np.where(coh >= min_coh)[0]
    if stable.size >= min_points:
        cm = np.median(los[stable], axis=0)
        basis, n_used = "stable_high_coh", int(stable.size)
    else:                                        # 폴백: 안정점 부족 → 전체 중앙값
        cm = np.median(los, axis=0)
        basis, n_used = "all_points_median", n
    return {"common_mode": cm, "meta": {"basis": basis, "n_stable": n_used, "min_coh": float(min_coh)}}


def correct_los_field(
    los: np.ndarray,
    coherence: np.ndarray | None = None,
    height: np.ndarray | None = None,
    *,
    reference: bool = True,
    height_corr: bool = True,
    min_ref_coh: float = 0.7,
    min_height_spread_m: float = 1.0,
) -> dict:
    """LOS 시계열 [N,M] 정확도 보정 체인 — 인제스트에서 project.h5 에 반영할 결정론적 보정.

    세 보정을 순서대로 적용한다(모두 네트워크 불필요, 재현 가능):
      1. **공통성분(APS) 제거**(reference): 안정점 집합의 에폭별 중앙값(=공간 기준망)을 전
         점에서 빼 공통 대기/궤도 램프를 제거한다 → 시간변동 저감. 단일점 대신 중앙값을
         써 그 점 노이즈 주입을 피한다.
      2. **고도상관(성층 대류권) 보정**(height_corr): 고도차가 있을 때 시점별 los~height
         선형기울기를 제거(GACOS 대안). 고도차 < min_height_spread_m 이면 건너뛴다.

    반환: corrected[N,M] float32 + meta(적용 단계·기준·시간변동 감소율). 열팽창 분리는
    온도 시계열이 필요하므로 여기서 하지 않고 `temporal_decompose` 로 대시보드/분석에서 한다.
    """
    los = np.asarray(los, dtype=np.float64)
    meta: dict = {"applied": [], "n_points": int(los.shape[0]), "n_dates": int(los.shape[1])}
    if los.ndim != 2 or los.shape[1] < 2:
        meta["skipped"] = f"los 형상 {los.shape} — 보정 불가(그대로 반환)"
        return {"corrected": los.astype(np.float32), "meta": meta}

    std_before = float(np.mean(los.std(axis=1)))
    out = los

    if reference and los.shape[0] >= 3:
        cmres = common_mode(out, coherence, min_coh=min_ref_coh)
        cand = out - cmres["common_mode"][None, :]       # 공통성분 제거(전 점 동일 에폭 보정)
        # 이득 가드: 이미 APS 필터된 데이터(SARvey 등)엔 공통성분이 거의 없어 되레 노이즈를
        # 더할 수 있다. 시간변동이 실제로 줄 때만 채택 → 보정이 품질을 악화시키지 않게 보장.
        std_cm = float(np.mean(cand.std(axis=1)))
        if std_cm < std_before:
            out = cand
            meta["applied"].append("reference")
            meta["reference"] = {**cmres["meta"], "std_reduction_mm": round(std_before - std_cm, 4)}
        else:
            meta["reference_skipped"] = (
                f"공통성분 제거가 시간변동을 안 줄임({std_before:.3f}→{std_cm:.3f}mm) "
                "— 이미 APS 보정된 입력으로 판단, 원본 유지"
            )
    elif reference:
        meta["reference_skipped"] = f"점 수 {los.shape[0]} < 3"

    if height_corr and height is not None:
        h = np.asarray(height, dtype=np.float64).ravel()
        spread = float(h.max() - h.min()) if h.size else 0.0
        if np.isfinite(h).all() and spread >= min_height_spread_m:
            hc = height_correlated_correction(out, h)
            out = hc["corrected"]
            sl = np.asarray(hc["slope_mm_per_m"], dtype=np.float64)
            meta["applied"].append("height_correlated")
            meta["height_spread_m"] = round(spread, 2)
            meta["height_slope_mm_per_m"] = {
                "mean": round(float(sl.mean()), 4),
                "abs_max": round(float(np.abs(sl).max()), 4),
            }
        else:
            meta["height_correlated_skipped"] = (
                f"고도차 {spread:.2f}m < {min_height_spread_m}m 또는 비유한값"
            )
    elif height_corr:
        meta["height_correlated_skipped"] = "고도(height) 없음"

    std_after = float(np.mean(out.std(axis=1)))
    meta["temporal_std_before_mm"] = round(std_before, 4)
    meta["temporal_std_after_mm"] = round(std_after, 4)
    meta["temporal_std_reduction_pct"] = round(
        100.0 * (1.0 - std_after / (std_before + 1e-12)), 2
    )
    return {"corrected": out.astype(np.float32), "meta": meta}


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
