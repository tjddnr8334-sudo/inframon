"""모듈 4: FRAM — PINN 결합 안전 진단 엔진.

설계 문서 5.5 의 공명(Resonance) → CRI 산출을 NumPy로 구현한다.
이 모듈은 외부 라이브러리가 필요 없어 Phase 0 에서도 '거의 실제'로 동작한다.

  CRI(x,t) = w1·A + w2·ΣR_ij + w3·R_spatial + w4·R_div   (문서 5.5 [종합])
"""

from __future__ import annotations

import numpy as np

from ..config import PipelineConfig
from ..contracts.io import ProjectStore
from ..contracts.schema import MEMBER_TYPES, FRAMOutput, FRAMWarning, InSAROutput, PINNOutput


def _windowed_corr(series: np.ndarray, win: int = 6) -> np.ndarray:
    """[n_func, M] → [n_func, n_func, M] 윈도우 시간상관 (공명1)."""
    n, M = series.shape
    R = np.zeros((n, n, M))
    for k in range(M):
        a = max(0, k - win + 1)
        seg = series[:, a : k + 1]
        if seg.shape[1] < 2:
            R[:, :, k] = np.eye(n)
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            c = np.corrcoef(seg)   # 분산 0 구간은 nan → 아래에서 0 처리
        R[:, :, k] = np.nan_to_num(c)
    return R


def run_fram(
    store: ProjectStore, insar: InSAROutput, pinn: PINNOutput, cfg: PipelineConfig
) -> FRAMOutput:
    los = store.read_array(insar.longitudinal_ds)        # [N,M]
    xyz = store.read_array(insar.xyz_ds)                 # [N,3]
    member = store.read_array(insar.member_ds)           # [N]
    dates = store.read_array(insar.dates_ds)             # [M]
    V_func = store.read_array(pinn.V_func_series_ds)     # [n_func, M]
    comp_anom = store.read_array(pinn.comp_anomaly_ds)   # [N,M]
    comp_settle = store.read_array(pinn.comp_settle_ds)  # [N,M]
    comp_thermal = store.read_array(pinn.comp_thermal_ds)
    N, M = los.shape
    w1, w2, w3, w4 = cfg.cri_weights

    # 점·시점별 기능 변동 V_i(x,t)  (성분 변화율)
    Vi = np.stack([
        np.abs(np.gradient(comp_thermal, axis=1)),
        np.abs(np.gradient(comp_thermal, axis=1)) * 0.3,
        np.abs(comp_anom),
        np.abs(np.gradient(comp_settle, axis=1)),
    ], axis=0)                                            # [n_func, N, M]
    Vi = Vi / (Vi.max() + 1e-9)
    sum_Vi = Vi.sum(axis=0)                               # [N,M]

    # [공명2] 증폭률 A = V_total / Σ V_i   (>1 이면 상호작용 증폭)
    V_total = np.abs(np.gradient(los, axis=1))
    V_total = V_total / (V_total.max() + 1e-9)
    A = V_total / (sum_Vi + 1e-3)
    A = np.clip(A / 3.0, 0, 1)                            # 정규화

    # [공명1] 기능간 시간상관 R_ij(t)  → 점별로 동일 적용
    R_ij = _windowed_corr(V_func)                        # [n_func,n_func,M]
    offdiag = R_ij.sum(axis=(0, 1)) - np.trace(R_ij)     # ΣR_ij(t)
    n = V_func.shape[0]
    sumR = np.clip(offdiag / (n * (n - 1)), 0, 1)         # [M] 0~1
    sumR_pt = np.broadcast_to(sumR[None, :], (N, M))

    # [공명3] 공간 전파 R_spatial = ∇·V  (x 정렬 후 변동 공간기울기)
    order = np.argsort(xyz[:, 0])
    spatial = np.zeros_like(V_total)
    spatial[order] = np.abs(np.gradient(V_total[order], axis=0))
    R_spatial = spatial / (spatial.max() + 1e-9)

    # [공명4] 예측 발산 R_div = 비선형 가속 (시간 2차미분 크기)
    accel = np.abs(np.gradient(np.gradient(los, axis=1), axis=1))
    R_div = accel / (accel.max() + 1e-9)

    # [종합] CRI
    CRI = w1 * A + w2 * sumR_pt + w3 * R_spatial + w4 * R_div
    CRI = np.clip(CRI, 0, 1)

    # 경보 판정
    cri_max = float(CRI.max())
    from .real_engine import grade_alert_factor
    _gf = grade_alert_factor(getattr(cfg, 'bridge_grade', None))
    t_lo, t_mid, t_hi = (min(t * _gf, 1.0) for t in cfg.cri_thresholds)
    if cri_max >= t_hi:
        level = "위험"
    elif cri_max >= t_mid:
        level = "경고"
    elif cri_max >= t_lo:
        level = "주의"
    else:
        level = "정상"

    # 위험 부재 (마지막 시점 CRI 상위 점들의 부재)
    last = CRI[:, -1]
    hot = np.where(last >= t_mid)[0]
    crit = sorted({MEMBER_TYPES[member[i]] for i in hot})

    # 전조 리드타임: CRI 전역 시계열이 임계를 처음 넘는 시점 이후 잔여 일수
    cri_t = CRI.max(axis=0)                               # [M]
    over = np.where(cri_t >= t_mid)[0]
    lead = float(dates[-1] - dates[over[0]]) if len(over) else None

    g = "/fram"
    store.write_array(f"{g}/R_ij", R_ij)
    store.write_array(f"{g}/amplification", A)
    store.write_array(f"{g}/spatial_prop", R_spatial)
    store.write_array(f"{g}/divergence", R_div)
    store.write_array(f"{g}/CRI", CRI)

    out = FRAMOutput(
        n_points=N, n_dates=M,
        resonance_Rij_ds=f"{g}/R_ij", amplification_ds=f"{g}/amplification",
        spatial_prop_ds=f"{g}/spatial_prop", divergence_ds=f"{g}/divergence",
        CRI_ds=f"{g}/CRI", cri_global_max=cri_max,
        warning=FRAMWarning(level=level, lead_time_days=lead, critical_members=crit),
    )
    store.write_meta("fram", out)
    return out
