"""한계상태 채널별 잔존수명 계산 — P1 은 사용성(serviceability).

사용성 채널은 두 하위 한계를 동시에 본다.

- **절대 변위**: 점의 누적 연직변위가 부재별 허용치를 넘는가.
- **부등침하(각변위)**: 이웃한 두 점의 침하 속도 차이가 만드는 기울기가
  1/500 을 넘는가. 절대 침하보다 구조적으로 지배적인 경우가 많아 별도로 센다.

점별 잔존수명은 두 하위 한계 중 **먼저 닿는 쪽**이고, 어느 쪽이 지배했는지를
`sublimit` 으로 남긴다(0=검열·1=절대·2=부등).
"""

from __future__ import annotations

import numpy as np

from .degradation import (
    adverse_rate,
    quadratic_acceleration,
    significant,
    theil_sen,
    time_to_limit_quadratic,
    z_for,
)

SUB_CENSORED, SUB_ABSOLUTE, SUB_DIFFERENTIAL = 0, 1, 2


def _pairwise_neighbors(xy: np.ndarray, radius_m: float, *, block: int = 512):
    """반경 안 이웃 쌍 (i, j, dist) 을 블록 단위로 만든다. i < j 만."""
    n = xy.shape[0]
    for a in range(0, n, block):
        b = min(n, a + block)
        d = np.linalg.norm(xy[a:b, None, :] - xy[None, :, :], axis=2)   # [nb, N]
        ii, jj = np.nonzero((d > 0) & (d <= radius_m))
        ii = ii + a
        keep = ii < jj
        if keep.any():
            yield ii[keep], jj[keep], d[ii[keep] - a, jj[keep]]


def default_radius(xy: np.ndarray, *, k: int = 8, cap_m: float = 120.0) -> float:
    """이웃 반경 기본값 — 점 밀도에서 유도(k번째 최근접 거리의 중앙값), 상한 cap.

    격자 간격을 모르는 실 데이터에서 반경을 고정하면 밀집/희소 교량 어느 한쪽이
    깨진다. 밀도에서 유도하면 두 경우 모두 이웃이 생긴다.
    """
    n = xy.shape[0]
    if n < 2:
        return cap_m
    idx = np.linspace(0, n - 1, min(n, 300)).astype(int)     # 표본으로 충분(비용 절감)
    d = np.linalg.norm(xy[idx][:, None, :] - xy[None, :, :], axis=2)
    d.sort(axis=1)
    kk = min(k, d.shape[1] - 1)
    return float(min(max(np.median(d[:, kk]), 1e-3), cap_m))


def serviceability(
    t_years: np.ndarray,
    disp: np.ndarray,
    *,
    xy: np.ndarray,
    point_limit_mm: np.ndarray,
    angular_limit: float,
    consumed_mm: np.ndarray | float = 0.0,
    alpha: float = 0.05,
    radius_m: float | None = None,
    min_separation_m: float = 5.0,
) -> dict:
    """사용성 채널 — 점별 잔존수명[년]과 근거.

    Args:
        t_years:        [M] 관측 시각[년]
        disp:           [N,M] 열성분이 제거된 변위[mm]
        xy:             [N,2] 평면 좌표[m] (이웃 거리 계산용)
        point_limit_mm: [N] 부재별 절대 변위 한계[mm]
        angular_limit:  부등침하 각변위 한계[rad] (예 1/500)
        consumed_mm:    관측 시작 이전에 이미 소진된 변위[mm]. 기본 0 = **낙관적 가정**
                        (실측 누적치가 있으면 넣어야 한다 — assumptions 에 명시된다).
        radius_m:       이웃 반경[m]. None 이면 점 밀도에서 유도.
        min_separation_m: 부등침하를 셀 최소 이격[m]. 각변위 한계(1/500)는 **인접
                        지지점 사이** 규정이라 근접 쌍에 적용하면 각변위가 발산한다
                        (1m 떨어진 두 점의 2mm/yr 차이 → 2e-3 rad/yr → 잔존수명 1년).
                        그 거리에서의 변위차는 구조 거동이 아니라 측정 노이즈에 가깝다.

    Returns dict: rsl, rsl_lower, rate, sigma, sublimit, censored, accel, meta
    """
    t = np.asarray(t_years, dtype=np.float64).ravel()
    D = np.atleast_2d(np.asarray(disp, dtype=np.float64))
    n = D.shape[0]

    fit = theil_sen(t, D, alpha=alpha)
    rate, rate_hi = adverse_rate(fit)                   # |mm/yr|, 보수적 상한
    sig = significant(fit["lo"], fit["hi"])             # CI 가 0 을 배제하는가

    lim = np.asarray(point_limit_mm, dtype=np.float64).ravel()
    consumed = np.broadcast_to(np.asarray(consumed_mm, dtype=np.float64), (n,))
    margin = np.maximum(lim - consumed, 0.0)            # 남은 변위 여유[mm]

    inf = np.inf

    # ── ① 절대 변위 한계 ──────────────────────────────────────────────
    with np.errstate(divide="ignore", invalid="ignore"):
        rsl_abs = np.where(sig & (rate > 0), margin / np.maximum(rate, 1e-12), inf)
        rsl_abs_lo = np.where(sig & (rate_hi > 0), margin / np.maximum(rate_hi, 1e-12), inf)

    # ── ② 부등침하(각변위) ────────────────────────────────────────────
    # 이웃 쌍의 속도차가 만드는 기울기 변화율[rad/yr]. 두 겹의 관문을 둔다:
    #   (a) 속도차가 두 점의 불확실도 합보다 확실히 클 것 — 노이즈 쌍 배제
    #   (b) 각변위를 **자기 추세가 유의한 점에만** 귀속시킬 것
    # (b)가 없으면 고립 이상치 한 점이 주변 정지점들에게까지 짧은 잔존수명을 전파해
    # 가짜 군집을 만들고, 공간 응집 규칙(고립점 배제)이 무력화된다.
    r = float(radius_m) if radius_m else default_radius(xy)
    min_sep = min(float(min_separation_m), r * 0.5)   # 반경보다 크면 쌍이 하나도 안 남는다
    slope = fit["slope"]
    sigma = fit["sigma"]
    z = z_for(alpha)
    dist_rate = np.zeros(n)                 # 점별 최대 각변위 변화율[rad/yr]
    for ii, jj, dd in _pairwise_neighbors(np.asarray(xy, dtype=np.float64), r):
        dv = slope[ii] - slope[jj]                                # [mm/yr]
        unc = z * np.sqrt(sigma[ii] ** 2 + sigma[jj] ** 2)
        ok = (np.abs(dv) > unc) & (dd >= min_sep)                 # 근접 쌍 배제
        if not ok.any():
            continue
        ang = np.abs(dv[ok]) / (dd[ok] * 1000.0)                  # mm/m/yr → rad/yr
        oi, oj = ii[ok], jj[ok]
        np.maximum.at(dist_rate, oi[sig[oi]], ang[sig[oi]])
        np.maximum.at(dist_rate, oj[sig[oj]], ang[sig[oj]])
    with np.errstate(divide="ignore", invalid="ignore"):
        rsl_diff = np.where(dist_rate > 0, float(angular_limit) / np.maximum(dist_rate, 1e-30), inf)

    # ── ③ 가속 열화 — 유의하면 2차 해와 min 결합(선형 외삽의 낙관성 보정) ──
    q = quadratic_acceleration(t, D, alpha=alpha)
    accel = q["accel"] & sig
    if accel.any():
        tq = time_to_limit_quadratic(q["b"][accel], q["c"][accel], margin[accel])
        rsl_abs[accel] = np.minimum(rsl_abs[accel], tq)
        rsl_abs_lo[accel] = np.minimum(rsl_abs_lo[accel], tq)

    # ── ④ 점별 지배 한계 ──────────────────────────────────────────────
    rsl = np.minimum(rsl_abs, rsl_diff)
    rsl_lo = np.minimum(rsl_abs_lo, rsl_diff)
    sublimit = np.full(n, SUB_CENSORED, dtype=np.int16)
    finite = np.isfinite(rsl)
    sublimit[finite & (rsl_abs <= rsl_diff)] = SUB_ABSOLUTE
    sublimit[finite & (rsl_diff < rsl_abs)] = SUB_DIFFERENTIAL

    # 이미 한계 초과(여유 0) — 잔존수명 0
    over = sig & (margin <= 0)
    rsl[over] = 0.0
    rsl_lo[over] = 0.0
    sublimit[over] = SUB_ABSOLUTE

    return {
        "rsl": rsl, "rsl_lower": rsl_lo,
        "rate": rate, "rate_upper": rate_hi, "sigma": sigma, "slope": slope,
        "sublimit": sublimit, "censored": ~finite, "accel": accel,
        "meta": {
            "neighbor_radius_m": round(r, 3),
            "differential_min_separation_m": round(min_sep, 3),
            "n_significant": int(sig.sum()),
            "n_accelerating": int(accel.sum()),
            "n_over_limit": int(over.sum()),
            "n_differential_pairs_engaged": int((dist_rate > 0).sum()),
            "alpha": float(alpha),
            "rate_median_mm_yr": round(float(np.median(rate)), 4),
            "rate_p95_mm_yr": round(float(np.percentile(rate, 95)), 4),
        },
    }
