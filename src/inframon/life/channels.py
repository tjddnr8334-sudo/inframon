"""한계상태 채널별 잔존수명 계산 — 사용성(점별)과 강성열화(전역).

사용성 채널은 두 하위 한계를 동시에 본다.

- **절대 변위**: 점의 누적 연직변위가 부재별 허용치를 넘는가.
- **부등침하(각변위)**: 이웃한 두 점의 침하 속도 차이가 만드는 기울기가
  1/500 을 넘는가. 절대 침하보다 구조적으로 지배적인 경우가 많아 별도로 센다.

점별 잔존수명은 두 하위 한계 중 **먼저 닿는 쪽**이고, 어느 쪽이 지배했는지를
`sublimit` 으로 남긴다(0=검열·1=절대·2=부등).
"""

from __future__ import annotations

import math

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

# 강성열화 채널 게이트 — 이 채널은 쉽게 거짓말을 한다(아래 stiffness 참조).
STIFFNESS_MIN_YEARS = 3.0      # 관측기간 하한
STIFFNESS_MAX_CV = 0.5         # EI 식별 변동계수 상한
STIFFNESS_MAX_SATURATED = 0.2  # 클립 경계에 붙은 표본 비율 상한
EI_CLIP = (1e6, 1e14)          # _identify_EI_from_pde 의 물리 클립 범위


def stiffness(
    t_years: np.ndarray,
    ei: np.ndarray,
    *,
    observed_years: float,
    r_limit: float = 0.80,
    alpha: float = 0.05,
    min_years: float = STIFFNESS_MIN_YEARS,
    max_cv: float = STIFFNESS_MAX_CV,
    geometric_ei: float | None = None,
) -> dict:
    """강성열화 채널 — `EI(t)/EI₀ < r_limit` 까지 남은 시간[년].

    모형은 지수 열화 `EI(t) = EI₀·exp(−λt)` 이고, `log EI` 를 Theil–Sen 으로 회귀해
    λ 를 얻는다. 기준 EI₀ 는 **관측 시작 시점의 자기 값**(회귀 절편)이다 — 설계
    기하 EI 를 기준으로 삼으면 식별 EI 와의 계통 편차가 통째로 열화로 잡힌다.
    그래서 설계 대비 비는 참고로만 보고하고 외삽에는 쓰지 않는다.

    **이 채널은 쉽게 거짓말을 한다.** EI 식별은 4차 도함수에 의존해 노이즈에 매우
    민감하고, 표본이 ~12개뿐이다. 그래서 값을 내기 전에 네 겹으로 막는다.

    1. 관측기간 < `min_years`(기본 3년) → 비활성. 짧은 창에서는 어떤 추세도 유의하지 않다.
    2. EI 표본이 물리 클립 경계에 몰림 → 비활성("식별 포화"). 합성·저휨 데이터에서
       d4≈0 이면 EI 가 상한에 박혀 추세가 전부 인공물이다.
    3. 변동계수 > `max_cv` → 비활성("식별 불안정").
    4. λ 의 신뢰구간이 0 을 포함 → 검열. 강성이 **증가**하는 방향이면 열화가 아니다.

    게이트 없이 EI 추세를 노출하면 사용자는 노이즈를 열화로 읽는다.
    """
    t = np.asarray(t_years, dtype=np.float64).ravel()
    e = np.asarray(ei, dtype=np.float64).ravel()
    detail: dict = {"n_samples": int(e.size), "observed_years": round(float(observed_years), 3),
                    "r_limit": float(r_limit)}

    def off(reason):
        return {"active": False, "inactive_reason": reason, "rsl_years": None,
                "rsl_lower_years": None, "censored": False, "detail": detail}

    if e.size != t.size or e.size < 4:
        return off(f"시간분해 EI 표본 부족({e.size}개, 최소 4)")
    finite = np.isfinite(e) & np.isfinite(t) & (e > 0)
    if int(finite.sum()) < 4:
        return off("유효한 EI 표본이 4개 미만")
    t, e = t[finite], e[finite]

    # 식별 품질 게이트를 관측기간보다 **먼저** 본다. 포화/불안정은 시간이 지나도 해결되지
    # 않는데, "관측 3년 미만"이라고만 알리면 사용자가 3년을 더 기다린 뒤에야 진짜 원인을
    # 알게 된다. 더 근본적인 차단 사유를 먼저 말해야 한다.
    sat = float(np.mean((e <= EI_CLIP[0] * 1.001) | (e >= EI_CLIP[1] * 0.999)))
    detail["saturated_fraction"] = round(sat, 3)
    cv = float(np.std(e) / (np.mean(e) + 1e-30))
    detail["coefficient_of_variation"] = round(cv, 4)
    detail["EI_median_Nm2"] = float(np.median(e))
    if geometric_ei:
        detail["EI_over_geometric"] = round(float(np.median(e) / geometric_ei), 4)
        detail["geometric_EI_Nm2"] = float(geometric_ei)

    if sat > STIFFNESS_MAX_SATURATED:
        return off(f"EI 식별이 물리 클립 경계에 포화({sat * 100:.0f}%) — 휨이 약해 d4≈0 인 "
                   "경우로 추세가 전부 인공물이다. 관측을 더 모아도 해결되지 않는다"
                   "(실측 처짐이 있는 데이터가 필요).")
    if cv > max_cv:
        return off(f"EI 식별 변동계수 {cv:.2f} > {max_cv} — 식별 불안정")
    if observed_years < min_years:
        return off(f"관측 {observed_years:.1f}년 < {min_years:.0f}년 — EI 추세는 "
                   "4차 도함수 기반이라 짧은 창에서 유의하지 않다")

    fit = theil_sen(t, np.log(e)[None, :], alpha=alpha)
    slope, lo, hi = float(fit["slope"][0]), float(fit["lo"][0]), float(fit["hi"][0])
    detail["log_ei_slope_per_year"] = round(slope, 6)
    detail["log_ei_slope_ci"] = [round(lo, 6), round(hi, 6)]

    if hi >= 0:      # 열화 방향(감소)이 유의하지 않음 — 증가/무변화 포함
        detail["direction"] = "강성 증가 또는 무변화" if slope >= 0 else "감소하나 유의하지 않음"
        return {"active": True, "inactive_reason": None, "rsl_years": None,
                "rsl_lower_years": None, "censored": True, "detail": detail}

    lam, lam_hi = -slope, -lo                  # λ>0 이 열화. lo 가 가장 가파른 감소.
    budget = -math.log(float(r_limit))         # ln(1/r_limit) > 0
    detail["lambda_per_year"] = round(lam, 6)
    detail["half_life_years"] = round(math.log(2.0) / lam, 2) if lam > 0 else None
    rsl = max(0.0, budget / lam - float(observed_years))
    rsl_lo = max(0.0, budget / lam_hi - float(observed_years))
    return {"active": True, "inactive_reason": None,
            "rsl_years": round(rsl, 3), "rsl_lower_years": round(rsl_lo, 3),
            "censored": False, "detail": detail}


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
