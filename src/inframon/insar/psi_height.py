"""PSI 잔차높이(DEM 오차) Δh 역산 — 산란체 실고도.

SNAP 간섭도가 지형위상(subtractTopographicPhase)을 이미 제거하므로, 남은 LOS 시계열은
**잔차 지형위상(Δh=DEM 대비 높이오차) + 변형 + 대기 + 노이즈** 다. 각 PS 에서 수직baseline
B⊥ 상관 성분을 변형(시간 상관)과 **동시 최소자승 분리**해 Δh 를 얻는다(SARvey/StaMPS 의 DEM-오차
+ 속도 결합추정의 핵심). Δh + DEM 표고 = 산란체 절대고도 → 데크 산란체는 SRTM(지형)보다 높다.

모델(PS i, 에폭 k, master 기준 pair):
    los_mm[i,k] = (B⊥[k] / (R·sinθ_i))·1000 · Δh_i  +  v_i · t_year[k]  +  b_i  +  noise
LS 로 (Δh_i[m], v_i[mm/yr], b_i) 추정. 정밀도 σ_Δh ≈ σ_noise /(감도·√M) — B⊥ 스프레드가
작거나 노이즈가 크면 거칠다(정직 보고). 위상 언래핑은 SNAP 잔차가 작다는 전제(대 변형/큰 Δh 는
periodogram 필요 — 여기선 선형 LS).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

_S1_LAMBDA_M = 0.055465763        # Sentinel-1 C-band 파장


def estimate_residual_height(track_h5: str | Path, bperp: dict | np.ndarray, *,
                             master_date: str | None = None, slant_range_m: float = 880e3,
                             ref_dem: str | Path | None = None) -> dict:
    """Track H5 + 에폭별 B⊥ → 점별 Δh(잔차높이)·v·정밀도·절대고도.

    bperp: {YYYYMMDD: B⊥_m}(ASF 기준) 또는 에폭순 배열. master_date 기준 pair B⊥ 로 환산.
    ref_dem: 주면 절대고도 = DEM표고 + Δh 계산(SRTM 디렉터리/래스터).
    """
    import h5py
    from datetime import datetime as dt

    with h5py.File(str(track_h5), "r") as f:
        los = f["los_mm"][()].astype(float)                  # [N,M] mm (잔차)
        ep = [str(int(e)) for e in f["epochs"][()]]
        inc = np.asarray(f["incidenceAngle"][()], float)     # [N] deg
        lonlat = f["pixel_lonlat"][()]
    N, M = los.shape
    order = np.argsort(ep)
    ep = [ep[i] for i in order]
    los = los[:, order]
    los = los - los[:, :1]                                   # master 에폭 0
    days = np.array([(dt.strptime(x, "%Y%m%d") - dt.strptime(ep[0], "%Y%m%d")).days for x in ep], float)
    t_year = days / 365.0

    if isinstance(bperp, dict):
        bp = np.array([bperp.get(d, np.nan) for d in ep], float)
    else:
        bp = np.asarray(bperp, float)[order]
    if master_date is None:                                  # B⊥ 최소(≈master) 자동
        master_date = ep[int(np.nanargmin(np.abs(bp)))]
    bperp_pair = bp - bp[ep.index(master_date)]              # pair 수직baseline
    ok = np.isfinite(bperp_pair)
    bperp_pair = np.where(ok, bperp_pair, 0.0)

    sinth = np.sin(np.radians(np.clip(inc, 20, 60)))         # [N]
    # 점별 LS: 열 [Δh 감도(mm/m), t_year(v), 1]
    dh = np.full(N, np.nan); vel = np.full(N, np.nan)
    sig_dh = np.full(N, np.nan); resid_mm = np.full(N, np.nan)
    for i in range(N):
        a_dh = bperp_pair / (slant_range_m * sinth[i]) * 1000.0   # mm per m Δh
        A = np.column_stack([a_dh, t_year, np.ones(M)])
        y = los[i]
        m = np.isfinite(y) & ok
        if m.sum() < 5:
            continue
        beta, *_ = np.linalg.lstsq(A[m], y[m], rcond=None)
        dh[i], vel[i] = beta[0], beta[1]
        r = y[m] - A[m] @ beta
        resid_mm[i] = float(np.sqrt(np.mean(r ** 2)))
        # σ_Δh: (AᵀA)⁻¹ 대각 × σ²
        try:
            cov = np.linalg.inv(A[m].T @ A[m]) * (resid_mm[i] ** 2)
            sig_dh[i] = float(np.sqrt(max(cov[0, 0], 0)))
        except np.linalg.LinAlgError:
            pass

    out = {"dh_m": dh, "velocity_mm_yr": vel, "sigma_dh_m": sig_dh,
           "resid_mm": resid_mm, "master_date": master_date,
           "bperp_spread_m": float(np.nanstd(bperp_pair[ok])),
           "n_points": N, "n_epochs": M, "lonlat": lonlat, "incidence": inc}
    # 절대고도 = DEM + Δh
    if ref_dem is not None:
        from .gltf_export import _sample_dem
        dem_z = _sample_dem(lonlat, ref_dem)
        out["dem_z_m"] = dem_z
        out["abs_elev_m"] = dem_z + dh
    return out
