"""MT-InSAR 네 가지 — ADI · 기선 네트워크 · 점별 DEM 오차 · APS.

한강 16개소는 **SNAP star-network + snaphu** 로 만들었다. 그건 간섭도를 풀어 LOS 로
바꾼 데까지다. 시계열 InSAR(MT-InSAR)가 그다음에 하는 일이 빠져 있었고, 그래서
'교면 위 점' 이라고 고른 것이 실제로는 강변 지반이었다. 빠진 네 가지를 여기 넣는다.

  ① **ADI**(진폭분산지수) — 어느 화소가 점같은(point-like) 영구 산란체인가.
     ADI = σ_A/μ_A 가 작을수록 위상이 안정하다(Ferretti 2001).
  ② **기선 네트워크** — 쌍마다의 수직기선 B⊥ · 시간기선 Bt. star 는 한 마스터에
     전부 매달려 시간기선이 최대 7년까지 벌어진다. 소기선 부분망을 뽑아 닫힘오차
     (closure error)로 언래핑 실수를 잡아낸다.
  ③ **점별 DEM 오차 Δh** — 제거되지 않은 지형위상은 B⊥ 에 비례해 남는다.
         LOS[mm] = K·Δh,   K = 1000·B⊥ / (R·sinθ)   [mm per m]
     속도와 **함께** 풀어야 서로 새지 않는다. 같이 풀고 나온 잔차로 시간결맞음 γ 를
     계산하면, 그게 곧 그 점을 믿을지 말지의 기준이다.
  ④ **APS**(대기위상) — 공간적으로는 매끄럽고 시간적으로는 제멋대로다. ①~③ 을 뺀
     잔차를 **시점마다 공간 저역통과**하면 APS 가 남는다. 그걸 빼면 신호만 남는다.

**Sentinel-1 의 한계를 숨기지 않는다.** B⊥ 가 ±100 m 안이라 K 가 ±0.19 mm/m 다 —
Δh 10 m 가 LOS 1.9 mm. 51시점·잡음 20 mm 면 점별 σ_Δh 가 20 m 를 넘어 형하고(8~25 m)
보다 크다. 그러니 Δh 는 **점 하나로 읽지 말고 묶어서** 읽어야 한다. 이 모듈은 σ 를
언제나 같이 돌려준다.

**진폭이 없을 때** — SNAP star 산출물에는 날짜별 진폭 밴드가 없고 간섭도 i/q 만 있다.
star 망에서 |ifg_j| ∝ A_master·A_j·γ_j 이고 A_master 는 시간에 대해 상수이므로
σ/μ 를 취하면 마스터 진폭이 약분된다 — 그래서 |ifg| 로 ADI 를 대신 쓸 수 있다.
다만 γ_j 의 변동이 섞이므로 **참 ADI 보다 크게(보수적으로) 나온다**. 그렇게 적는다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RADAR_WAVELENGTH_M = 0.05546576          # Sentinel-1 C-band


# ── ① ADI ─────────────────────────────────────────────────────────────────
def amplitude_dispersion(amp: np.ndarray) -> np.ndarray:
    """ADI = σ_A/μ_A (시간축). amp:[N,K] → [N]. 낮을수록 안정한 PS 후보."""
    a = np.asarray(amp, dtype=np.float64)
    mu = a.mean(axis=1)
    return a.std(axis=1) / np.where(np.abs(mu) < 1e-12, np.nan, mu)


def adi_from_star_ifg(ifg_magnitude: np.ndarray) -> dict:
    """star 망 간섭도 크기 → ADI 대용.

    |ifg_j| ∝ A_m·A_j·γ_j 이고 A_m 은 시간 상수라 σ/μ 에서 약분된다. γ_j 변동이
    섞여 **참 ADI 보다 크게** 나오므로, 문턱을 그대로 쓰면 점을 덜 뽑는다(보수적).
    """
    adi = amplitude_dispersion(ifg_magnitude)
    return {"adi": adi, "proxy": True,
            "note": ("star 망 간섭도 크기로 계산한 대용값이다. 마스터 진폭은 약분되지만 "
                     "결맞음 변동이 섞여 참 ADI 보다 크게 나온다 — 보수적으로 뽑힌다.")}


def amplitude_stability_index(adi: np.ndarray) -> np.ndarray:
    """ASI = 1 − ADI — SARPROZ 가 점을 고를 때 쓰는 이름. 높을수록 안정하다."""
    return 1.0 - np.asarray(adi, dtype=np.float64)


def ps_mask(adi: np.ndarray, *, adi_max: float = 0.25,
            gamma: np.ndarray | None = None,
            gamma_min: float | None = None) -> np.ndarray:
    """PS 선별 — ADI < adi_max, γ 를 주면 γ ≥ gamma_min 도 요구."""
    m = np.asarray(adi, dtype=np.float64) < float(adi_max)
    if gamma is not None and gamma_min is not None:
        m &= np.asarray(gamma, dtype=np.float64) >= float(gamma_min)
    return m


# ── ② 기선 네트워크 ────────────────────────────────────────────────────────
def baseline_network(days: np.ndarray, bperp_m: np.ndarray, *,
                     max_btemp_days: float = 400.0,
                     max_bperp_m: float = 150.0) -> dict:
    """소기선 쌍 목록 — |Δt| ≤ max_btemp, |ΔB⊥| ≤ max_bperp.

    star 망은 한 마스터에 전부 매달려 시간기선이 수 년까지 벌어진다. 풀린 위상은
    선형이므로 **쌍을 빼서** 소기선 망을 유도할 수 있다(φ_jk = φ_mj − φ_mk).
    """
    d = np.asarray(days, dtype=np.float64).ravel()
    b = np.asarray(bperp_m, dtype=np.float64).ravel()
    n = d.size
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)
             if abs(d[j] - d[i]) <= max_btemp_days
             and abs(b[j] - b[i]) <= max_bperp_m]
    deg = np.zeros(n, int)
    for i, j in pairs:
        deg[i] += 1
        deg[j] += 1
    # 연결성 — 한 덩어리로 이어지지 않으면 시계열을 하나로 못 푼다.
    seen, stack, comp = set(), [0] if n else [], 0
    adj: dict[int, list[int]] = {k: [] for k in range(n)}
    for i, j in pairs:
        adj[i].append(j)
        adj[j].append(i)
    while stack:
        k = stack.pop()
        if k in seen:
            continue
        seen.add(k)
        stack.extend(adj[k])
    comp = len(seen)
    return {"pairs": pairs, "n_pairs": len(pairs), "n_epochs": n,
            "degree_min": int(deg.min()) if n else 0,
            "degree_mean": float(deg.mean()) if n else 0.0,
            "connected": bool(comp == n),
            "n_in_largest_component": int(comp),
            "max_btemp_days": float(max_btemp_days),
            "max_bperp_m": float(max_bperp_m)}


def baseline_table(dates: list, days: np.ndarray, bperp_m: np.ndarray,
                   master: str | None = None) -> dict:
    """시점별 수직·시간 기선표 — SARPROZ 의 baseline plot 이 그리는 그 값이다.

    마스터를 기준으로 한 star 망의 좌표(B⊥, Bt)와, 거기서 유도한 소기선 쌍의
    통계를 함께 낸다. 높이 모호도(height ambiguity)는 'B⊥ 가 이만큼일 때 위상
    한 바퀴가 높이 몇 m 인가' 로, 잔차고도를 얼마나 잘 볼 수 있는지의 척도다.
    """
    d = np.asarray(days, dtype=np.float64).ravel()
    b = np.asarray(bperp_m, dtype=np.float64).ravel()
    rows = [{"date": str(dates[i]), "bperp_m": round(float(b[i]), 2),
             "btemp_days": round(float(d[i] - d.min()), 1),
             "is_master": bool(master is not None and str(dates[i]) == str(master))}
            for i in range(len(d))]
    return {"epochs": rows, "n_epochs": len(rows),
            "bperp_min_m": round(float(b.min()), 1),
            "bperp_max_m": round(float(b.max()), 1),
            "bperp_span_m": round(float(b.max() - b.min()), 1),
            "bperp_std_m": round(float(b.std()), 1),
            "btemp_span_days": round(float(d.max() - d.min()), 1)}


def height_ambiguity_m(bperp_m: float, slant_range_m: float, incidence_deg: float,
                       *, wavelength_m: float = RADAR_WAVELENGTH_M) -> float:
    """위상 한 바퀴(2π)에 해당하는 높이 [m] — 작을수록 높이를 잘 본다."""
    s = np.sin(np.radians(float(incidence_deg)))
    if abs(bperp_m) < 1e-9 or abs(s) < 1e-9:
        return float("inf")
    return float(wavelength_m * slant_range_m * s / (2.0 * abs(bperp_m)))


def closure_errors(pairs: list, los_mm: np.ndarray) -> dict:
    """세 시점 고리(i,j,k)의 닫힘오차 — 언래핑 실수를 잡는 지표.

    풀린 변위라면 (d_j−d_i) + (d_k−d_j) − (d_k−d_i) = 0 이어야 한다. star 망에서
    유도한 쌍은 항등적으로 0 이므로, 이 함수는 **원래 쌍별로 푼 경우**에만 뜻이 있다.
    여기서는 점별 잔차의 크기를 재서 '닫힘이 깨진 점' 을 표시하는 데 쓴다.
    """
    L = np.atleast_2d(np.asarray(los_mm, dtype=np.float64))
    have = {(i, j) for i, j in pairs}
    tri = [(i, j, k) for i, j in pairs for k in range(j + 1, L.shape[1])
           if (j, k) in have and (i, k) in have]
    if not tri:
        return {"n_triangles": 0, "rms_mm": None,
                "note": "삼각 고리가 없다 — 소기선 문턱을 늘려야 한다"}
    e = np.stack([(L[:, j] - L[:, i]) + (L[:, k] - L[:, j]) - (L[:, k] - L[:, i])
                  for i, j, k in tri], axis=1)
    return {"n_triangles": len(tri),
            "rms_mm": float(np.sqrt(np.mean(e ** 2))),
            "per_point_rms_mm": np.sqrt(np.mean(e ** 2, axis=1))}


# ── ③ 점별 DEM 오차 + 속도 (같이 푼다) ──────────────────────────────────────
def height_phase_factor(bperp_m, slant_range_m: float, incidence_deg: float) -> np.ndarray:
    """K = 1000·B⊥/(R·sinθ) [mm per m] — Δh 1 m 가 LOS 몇 mm 로 보이는가."""
    b = np.asarray(bperp_m, dtype=np.float64).ravel()
    s = np.sin(np.radians(float(incidence_deg)))
    if not np.isfinite(s) or abs(s) < 1e-9 or not np.isfinite(slant_range_m):
        return np.zeros_like(b)
    return 1000.0 * b / (float(slant_range_m) * s)


@dataclass
class PointFit:
    velocity_mm_yr: np.ndarray
    dh_m: np.ndarray
    sigma_v: np.ndarray
    sigma_dh: np.ndarray
    gamma: np.ndarray            # 시간결맞음 0~1
    residual_mm: np.ndarray      # [N,K]
    thermal_mm_per_C: np.ndarray | None = None
    sigma_thermal: np.ndarray | None = None


def solve_velocity_dem_error(t_years: np.ndarray, K: np.ndarray,
                             los_mm: np.ndarray, *,
                             temperature_C: np.ndarray | None = None,
                             wavelength_m: float = RADAR_WAVELENGTH_M) -> PointFit:
    """점마다 한 번에 푼다 — SARPROZ 의 다중영상 해석과 같은 변수 묶음.

        los = c + v·t + K·Δh [+ α·(T − T̄)]

    ① 속도 v, ② DEM 오차 Δh, ③ **열팽창 α**(온도를 주면), 그리고 잔차에서 ④ 시간결맞음.
    따로 풀면 서로 샌다 — B⊥ 도 온도도 시간과 완전히 독립이 아니기 때문이다.

    열팽창을 넣는 이유는 실용적이다. 보고서가 신축변위계로 **mm/°C 를 직접 재 놓았다**
    (양화대교 EM_01 −1.46 · EM_02 −1.35, R²=0.99). 같은 단위가 나오면 눈금을 맞대
    비교할 수 있다 — 연주기 위상만 보는 것보다 훨씬 강한 대조다.

    γ = |⟨e^{iφ_res}⟩| 는 '이 점이 모델을 얼마나 잘 따르는가' 이고 PS 선별의 문턱이다.
    """
    t = np.asarray(t_years, dtype=np.float64).ravel()
    k = np.asarray(K, dtype=np.float64).ravel()
    L = np.atleast_2d(np.asarray(los_mm, dtype=np.float64))
    cols = [np.ones_like(t), t, k]
    has_T = temperature_C is not None
    if has_T:
        T = np.asarray(temperature_C, dtype=np.float64).ravel()
        cols.append(T - float(np.mean(T)))
    A = np.vstack(cols).T                                    # [K,3] 또는 [K,4]
    coef, *_ = np.linalg.lstsq(A, L.T, rcond=None)
    resid = L.T - A @ coef
    dof = max(len(t) - A.shape[1], 1)
    s2 = np.sum(resid ** 2, axis=0) / dof
    cov = np.linalg.pinv(A.T @ A)
    # 잔차를 위상으로 되돌려 시간결맞음 — 부호 규약은 γ 에 영향을 주지 않는다.
    phi = -4.0 * np.pi / wavelength_m * (resid / 1000.0)
    gamma = np.abs(np.mean(np.exp(1j * phi), axis=0))
    return PointFit(velocity_mm_yr=coef[1], dh_m=coef[2],
                    sigma_v=np.sqrt(s2 * cov[1, 1]),
                    sigma_dh=np.sqrt(s2 * cov[2, 2]),
                    gamma=gamma, residual_mm=resid.T,
                    thermal_mm_per_C=(coef[3] if has_T else None),
                    sigma_thermal=(np.sqrt(s2 * cov[3, 3]) if has_T else None))


# ── ④ APS ─────────────────────────────────────────────────────────────────
def temporal_highpass(residual_mm: np.ndarray, t_years: np.ndarray, *,
                      window_years: float = 0.25) -> np.ndarray:
    """시간축 고역통과 — 계절처럼 **천천히** 변하는 것은 남기고 넘긴다.

    대기는 시점마다 제멋대로(시간 비상관)이고 계절 변형은 부드럽다. 시간 가우시안으로
    평활한 것을 빼면 빠른 성분만 남는다 — 그게 APS 후보다. 이 단계를 건너뛰면 다음
    공간 저역통과가 계절 변형까지 먹어 버린다.
    """
    R = np.atleast_2d(np.asarray(residual_mm, dtype=np.float64))
    t = np.asarray(t_years, dtype=np.float64).ravel()
    w = np.exp(-0.5 * ((t[:, None] - t[None, :]) / float(window_years)) ** 2)
    w /= w.sum(axis=1, keepdims=True)
    return R - R @ w.T


def aps_estimate(residual_mm: np.ndarray, xy_m: np.ndarray, *,
                 radius_m: float = 300.0,
                 weights: np.ndarray | None = None,
                 source_mask: np.ndarray | None = None,
                 exclude_self: bool = True) -> np.ndarray:
    """시점마다 **공간 저역통과** 해 대기위상을 뽑는다. [N,K] → [N,K]

    한 가지 함정이 있다. 최소제곱 잔차는 설계행렬과 직교하는데, 점들이 같은 시간축을
    쓰므로 **잔차를 점끼리 선형결합해도 여전히 직교한다** — 그래서 자기 잔차를
    평활해 빼면 속도 추정이 한 톨도 안 바뀐다(단위테스트로 고정해 둔 성질이다).

    그래서 실제 PS-InSAR 처럼 한다.
      · `source_mask` 로 **믿을 만한 점에서만** APS 를 추정하고,
      · `exclude_self` 로 자기 자신은 평균에서 뺀다.
    그러면 각 점이 받는 보정은 '이웃이 말해 주는 대기' 라 바깥 정보가 된다.
    """
    R = np.atleast_2d(np.asarray(residual_mm, dtype=np.float64))
    P = np.asarray(xy_m, dtype=np.float64)
    n = P.shape[0]
    w0 = (np.ones(n) if weights is None
          else np.asarray(weights, dtype=np.float64).ravel().copy())
    if source_mask is not None:
        w0 = np.where(np.asarray(source_mask, bool), w0, 0.0)
    if not np.any(w0 > 0):
        return np.zeros_like(R)
    out = np.zeros_like(R)
    r = float(radius_m)
    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(P)
        nb = tree.query_ball_point(P, r=3.0 * r)
    except ImportError:
        nb = None
    if nb is not None:
        for i, idx in enumerate(nb):
            idx = np.asarray(idx, int)
            if exclude_self:
                idx = idx[idx != i]
            if idx.size == 0:
                continue
            d = np.hypot(*(P[idx] - P[i]).T)
            w = w0[idx] * np.exp(-0.5 * (d / r) ** 2)
            sw = w.sum()
            if sw > 1e-12:
                out[i] = (w @ R[idx]) / sw
        return out
    step = max(1, int(2e7 // max(n, 1)))
    for a in range(0, n, step):
        b = min(a + step, n)
        d = np.hypot(P[a:b, 0, None] - P[None, :, 0],
                     P[a:b, 1, None] - P[None, :, 1])
        w = w0[None, :] * np.exp(-0.5 * (d / r) ** 2)
        if exclude_self:
            for k in range(a, b):
                w[k - a, k] = 0.0
        sw = w.sum(axis=1, keepdims=True)
        good = (sw > 1e-12).ravel()
        out[a:b][good] = ((w @ R)[good]
                          / sw[good])
    return out


def remove_aps(los_mm: np.ndarray, aps_mm: np.ndarray) -> np.ndarray:
    return np.asarray(los_mm, dtype=np.float64) - np.asarray(aps_mm, dtype=np.float64)


# ── 전체 ──────────────────────────────────────────────────────────────────
def run(los_mm: np.ndarray, t_years: np.ndarray, bperp_m: np.ndarray,
        xy_m: np.ndarray, *, slant_range_m: float, incidence_deg: float,
        amplitude: np.ndarray | None = None,
        temperature_C: np.ndarray | None = None,
        adi_max: float = 0.25, gamma_min: float = 0.5,
        aps_radius_m: float = 300.0, n_iter: int = 2,
        aps_source_frac: float = 0.5, aps_window_years: float = 0.25,
        wavelength_m: float = RADAR_WAVELENGTH_M) -> dict:
    """①~④ 를 순서대로 — 모델 적합 ↔ APS 제거를 몇 번 오간다.

    APS 와 변형은 서로를 모르면 못 나눈다. 그래서 (모델 적합 → 잔차의 매끄러운 부분을
    APS 로 보고 제거 → 다시 적합)을 `n_iter` 번 돈다. 두 번이면 대개 수렴한다.
    """
    L = np.atleast_2d(np.asarray(los_mm, dtype=np.float64)).copy()
    K = height_phase_factor(bperp_m, slant_range_m, incidence_deg)
    steps = []
    aps = np.zeros_like(L)
    fit = solve_velocity_dem_error(t_years, K, L, temperature_C=temperature_C,
                                   wavelength_m=wavelength_m)
    steps.append({"iter": 0, "gamma_median": float(np.nanmedian(fit.gamma)),
                  "resid_rms_mm": float(np.sqrt(np.nanmean(fit.residual_mm ** 2)))})
    for it in range(1, int(n_iter) + 1):
        # 믿을 만한 점(γ 상위)에서만 APS 를 추정해 나머지에 내삽한다. 자기 잔차를
        # 그대로 빼면 최소제곱 직교성 때문에 추정값이 하나도 안 바뀐다.
        src = fit.gamma >= np.nanpercentile(fit.gamma, 100.0 * (1.0 - aps_source_frac))
        a = aps_estimate(temporal_highpass(fit.residual_mm, t_years,
                                           window_years=aps_window_years),
                         xy_m, radius_m=aps_radius_m, weights=fit.gamma,
                         source_mask=src, exclude_self=True)
        aps += a
        L = remove_aps(L, a)
        fit = solve_velocity_dem_error(t_years, K, L, temperature_C=temperature_C,
                                       wavelength_m=wavelength_m)
        steps.append({"iter": it, "aps_rms_mm": float(np.sqrt(np.nanmean(a ** 2))),
                      "aps_source_points": int(src.sum()),
                      "gamma_median": float(np.nanmedian(fit.gamma)),
                      "resid_rms_mm": float(np.sqrt(np.nanmean(fit.residual_mm ** 2)))})

    if amplitude is not None:
        adi_info = {"adi": amplitude_dispersion(amplitude), "proxy": False,
                    "note": "날짜별 진폭에서 직접 계산"}
    else:
        adi_info = {"adi": np.full(L.shape[0], np.nan), "proxy": None,
                    "note": "진폭이 없어 ADI 를 계산하지 못했다 — γ 문턱만 쓴다"}
    adi = adi_info["adi"]
    keep = (ps_mask(adi, adi_max=adi_max, gamma=fit.gamma, gamma_min=gamma_min)
            if np.isfinite(adi).any() else fit.gamma >= gamma_min)
    return {"los_corrected_mm": L, "aps_mm": aps, "K_mm_per_m": K,
            "fit": fit, "adi": adi, "asi": amplitude_stability_index(adi),
            "adi_note": adi_info["note"],
            "has_thermal": bool(temperature_C is not None),
            "ps_mask": keep, "n_kept": int(keep.sum()), "n_points": int(L.shape[0]),
            "iterations": steps, "aps_radius_m": float(aps_radius_m),
            "aps_source_frac": float(aps_source_frac),
            "aps_window_years": float(aps_window_years),
            "sigma_dh_median_m": float(np.nanmedian(fit.sigma_dh)),
            "gamma_min": float(gamma_min), "adi_max": float(adi_max)}
