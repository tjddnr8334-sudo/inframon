"""모듈 4: FRAM 고도화 (Phase 5) — 공명 → CRI, stub 휴리스틱 결함 3종 수정.

stub 대비 개선:
  1. **load 하드코딩 제거** — Vi[load] 를 |∇thermal|·0.3 가짜값이 아니라 PINN 의 실제
     comp_load(역학적 처짐)에서 계산. (PINN real 과 결합 시 진짜 하중 변동 반영)
  2. **R_ij 공간정보 복원** — 기능간 공명을 전 점에 동일 broadcast 하지 않고 **점별**
     윈도우 상관으로 산출(`_pointwise_resonance`). 공간적으로 다른 공명 패턴 포착.
  3. **절대 보정** — self-max 정규화(데이터셋 상대값) 대신 물리 스케일 기반 포화함수
     sat(x,s)=x/(x+s) 로 변위속도/가속/공간기울기를 절대적으로 [0,1] 매핑.
     스케일(mm/yr 등)은 cfg 로 조정(기본값 제공). → CRI 가 데이터셋 의존이 아닌 절대 의미.
  4. **함수망 공명(N-K)** — 시스템 공명행렬 R_ij 를 기능 결합 네트워크로 보고
     스펙트럼 반경(`_network_resonance`)으로 시스템 공명 강도 S(t)[M] 를 산출,
     점별 결합항을 S 로 증폭해 CRI 에 반영(쌍별 평균이 못 잡는 창발적 다기능 공명).

계약(FRAMOutput)·CRI[N,M]·resonance_Rij[4,4,M] 형상 보존(+선택 network_resonance[M]).
핫스왑 `--engine fram=real`.
"""

from __future__ import annotations

import numpy as np

from ..config import PipelineConfig
from ..contracts.io import ProjectStore
from ..contracts.schema import (
    FRAM_FUNCTIONS,
    MEMBER_TYPES,
    FRAMOutput,
    FRAMWarning,
    InSAROutput,
    PINNOutput,
)


def grade_alert_factor(grade) -> float:
    """교량 종별 → CRI 임계 배율. 1종(중요)은 낮은 임계로 조기경보, 3종은 높게."""
    return {"1종": 0.85, "2종": 1.0, "3종": 1.15, "기타": 1.1}.get(grade, 1.0)


def terrain_alert_factor(terrain) -> float:
    """지형 → CRI 임계 배율. 산지·해상은 바람 노출·환경하중이 커 낮은 임계로 조기경보.

    (개활 지형일수록 풍하중·와류진동 노출↑ → 동적 공명 위험↑. 평지=1.0 기준.)
    """
    return {"평지": 1.0, "산지": 0.92, "해상": 0.85}.get(terrain, 1.0)


def _sat(x: np.ndarray, scale: float) -> np.ndarray:
    """포화 절대 매핑: x/(x+scale) ∈ [0,1). x=scale 에서 0.5."""
    return x / (np.abs(x) + scale + 1e-12)


def _windowed_corr(series: np.ndarray, win: int = 6) -> np.ndarray:
    """[n_func,M] → [n_func,n_func,M] 윈도우 시간상관 (시스템 수준 공명 행렬)."""
    n, M = series.shape
    R = np.zeros((n, n, M))
    for k in range(M):
        a = max(0, k - win + 1)
        seg = series[:, a : k + 1]
        if seg.shape[1] < 2:
            R[:, :, k] = np.eye(n)
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            R[:, :, k] = np.nan_to_num(np.corrcoef(seg))
    return R


def _network_resonance(R_ij: np.ndarray) -> np.ndarray:
    """[n_func,n_func,M] 기능 결합 네트워크 → [M] 시스템 공명 강도(정규화 스펙트럼 반경).

    각 시점의 `|R_ij|` 비대각을 가중 인접행렬(FRAM 기능망)으로 보고 **지배 고유값
    (스펙트럼 반경)** 을 구한다. 기능들이 함께 공진할수록 결합 그래프가 조밀해져
    고유값이 커진다 — 쌍별 상관의 평균(점별 R_res)이 못 잡는 **창발적 다기능 공명**
    (N-K 결합)을 포착한다. `n_func-1` 로 정규화해 [0,1]. (4노드 그래프엔 numpy
    `eigvalsh` 로 충분 — networkx 같은 무거운 의존성 불필요.)
    """
    n, _, M = R_ij.shape
    S = np.zeros(M)
    for k in range(M):
        C = np.abs(R_ij[:, :, k]).copy()
        np.fill_diagonal(C, 0.0)               # 자기결합 제외(순수 기능간 결합망)
        S[k] = float(np.linalg.eigvalsh(C).max())  # 대칭 비음수 → 최대 고유값=스펙트럼 반경
    return np.clip(S / max(n - 1, 1), 0.0, 1.0)


def _pointwise_resonance(Vi: np.ndarray, win: int = 6) -> np.ndarray:
    """[n_func,N,M] → [N,M] 점별 기능간 공명(윈도우 |Pearson| 의 비대각 평균).

    각 점에서 기능들의 시간변동이 동조(상관)하면 공명 위험↑. 점마다 다르게 계산
    (stub 의 전 점 동일 broadcast 결함 해소).
    """
    n_func, N, M = Vi.shape
    R = np.zeros((N, M))
    pairs = 0
    for i in range(n_func):
        for j in range(i + 1, n_func):
            a, b = Vi[i], Vi[j]                       # [N,M] 각
            for k in range(M):
                s = max(0, k - win + 1)
                aw, bw = a[:, s : k + 1], b[:, s : k + 1]
                if aw.shape[1] < 2:
                    continue
                am = aw - aw.mean(axis=1, keepdims=True)
                bm = bw - bw.mean(axis=1, keepdims=True)
                denom = np.sqrt((am ** 2).sum(1) * (bm ** 2).sum(1)) + 1e-12
                R[:, k] += np.abs((am * bm).sum(1) / denom)
            pairs += 1
    return R / max(pairs, 1)


def _function_states(V_func: np.ndarray, names, lo: float = 0.33, hi: float = 0.66) -> dict:
    """기능별 상태 — 최근 정규화 변동(V_func_series[f] 의 마지막 1/4 평균) → 정상/주의/위험.

    V_func 는 기능별 [0,1] 정규화 시계열이라 최근값이 1 에 가까우면 그 기능이 지금
    가장 활발(전조). 설계 §5.7 function_states 실현.
    """
    M = V_func.shape[1]
    w = max(1, M // 4)
    out = {}
    for i, name in enumerate(names):
        recent = float(V_func[i, -w:].mean())
        out[name] = "위험" if recent >= hi else "주의" if recent >= lo else "정상"
    return out


def _forecast_to_threshold(series: np.ndarray, dates: np.ndarray, thr: float) -> float | None:
    """최근 절반 추세를 선형 외삽해 series 가 thr(위험)에 도달할 때까지 예상 일수.

    이미 도달했거나 상승 추세가 아니면 None. 설계 '예상 붕괴까지 시간'의 전방 예측.
    """
    n = len(series)
    if n < 3:
        return None
    k = max(2, n // 2)
    slope, intercept = np.polyfit(dates[-k:], series[-k:], 1)
    cur = float(series[-1])
    if cur >= thr or slope <= 1e-9:
        return None
    t_hit = (thr - intercept) / slope
    return float(t_hit - dates[-1]) if t_hit > dates[-1] else None


def _forecast_residual(series: np.ndarray, dates: np.ndarray, win: int = 6) -> np.ndarray:
    """롤링 선형예측 잔차 [N,M] — 각 시점에서 과거 win 점 선형 외삽 대비 실제 이탈.

    설계 [공명4] `‖d_observed − d_linear(t)‖`. 선형 추세면 ≈0, 비선형 가속(붕괴 전조)
    이면 커진다. 변위 잔차[mm] (관측이 선형 예측에서 얼마나 발산하는가).
    """
    N, M = series.shape
    res = np.zeros((N, M))
    for t in range(M):
        a = max(0, t - win)
        if t - a < 2:                                    # 선형 외삽엔 ≥2점 필요
            continue
        x = dates[a:t]
        y = series[:, a:t]                               # [N,k] 과거 윈도우
        xm = x - x.mean()
        slope = (y - y.mean(axis=1, keepdims=True)) @ xm / (float((xm ** 2).sum()) + 1e-12)
        pred = y.mean(axis=1) + slope * (dates[t] - x.mean())   # 시점 t 선형 예측
        res[:, t] = np.abs(series[:, t] - pred)
    return res


def run_fram_real(
    store: ProjectStore, insar: InSAROutput, pinn: PINNOutput, cfg: PipelineConfig
) -> FRAMOutput:
    los = store.read_array(insar.longitudinal_ds)            # [N,M] mm (종축)
    # (opt-in) asc+desc 융합 연직(vertical_ds, mm)이 있으면 연직 침하를 CRI 에 직접 반영.
    # 단일궤도 deprojection 은 수평 종축을 가정해 연직 침하 패턴을 왜곡하고, 종축항만으로는
    # 연직우세 손상(침하·처짐)을 못 짚는다. vertical_ds 없으면 None → 경로 불변(게이트 안전).
    use_vert = bool(getattr(cfg, "fram_use_vertical", True)) and insar.vertical_ds is not None
    vert = store.read_array(insar.vertical_ds) if use_vert else None
    xyz = store.read_array(insar.xyz_ds)
    member = store.read_array(insar.member_ds)
    dates = store.read_array(insar.dates_ds).astype(float)   # [M] days
    V_func = store.read_array(pinn.V_func_series_ds)         # [4,M] (시스템 공명행렬용)
    comp_thermal = store.read_array(pinn.comp_thermal_ds)
    comp_load = store.read_array(pinn.comp_load_ds)          # ★ 실제 하중 (하드코딩 제거)
    comp_anom = store.read_array(pinn.comp_anomaly_ds)
    comp_settle = store.read_array(pinn.comp_settle_ds)
    N, M = los.shape
    w1, w2, w3, w4 = cfg.cri_weights

    # 절대 보정 스케일 (조정 가능, mm/yr 등)
    vel_scale = float(getattr(cfg, "fram_vel_scale", 10.0))      # 변위속도 [mm/yr]
    grad_scale = float(getattr(cfg, "fram_grad_scale", 6.0))     # 공간기울기 [mm/yr/점]
    fore_scale = float(getattr(cfg, "fram_forecast_scale", 3.0))  # 예측 이탈 [mm]
    win = int(getattr(cfg, "fram_corr_win", 6))

    def ddt(a: np.ndarray) -> np.ndarray:
        return np.gradient(a, dates, axis=1) * 365.0            # /일 → /년

    rate = ddt(los)                                             # [N,M] mm/yr

    # 점·시점·기능별 변동 [4,N,M] — load 는 실제 comp_load 에서
    Vi = np.stack([
        np.abs(ddt(comp_thermal)),    # thermal
        np.abs(ddt(comp_load)),       # load  (★ 하드코딩 0.3 제거)
        np.abs(comp_anom),            # bearing
        np.abs(ddt(comp_settle)),     # foundation
    ], axis=0)

    # [공명1] 점별 기능간 공명 (공간정보 보존)
    R_res = np.clip(_pointwise_resonance(Vi, win), 0, 1)        # [N,M]

    # [공명2] 증폭률 A — 변위속도 절대 보정
    A = _sat(np.abs(rate), vel_scale)

    # [공명3] 공간 전파 — x 정렬 후 변위속도 공간기울기, 절대 보정
    order = np.argsort(xyz[:, 0])
    sp = np.zeros_like(rate)
    sp[order] = np.abs(np.gradient(rate[order], axis=0))
    R_spatial = _sat(sp, grad_scale)

    # [공명4] 예측 발산 — 롤링 선형예측 잔차(관측이 선형 외삽에서 발산하는 정도) 절대 보정
    R_div = _sat(_forecast_residual(los, dates, win), fore_scale)

    # [opt-in] 연직 융합 — 연직 침하의 속도/공간기울기/발산을 동일 절대 스케일로 평가해
    # 종축항과 max 결합(점이 종축·연직 어느 쪽으로든 위험하면 포착). 가중치·[0,1] 스케일
    # 불변이라 보수적. vertical_ds 없으면 이 블록은 건너뜀.
    if vert is not None:
        rate_v = ddt(vert)
        A = np.maximum(A, _sat(np.abs(rate_v), vel_scale))
        sp_v = np.zeros_like(rate_v)
        sp_v[order] = np.abs(np.gradient(rate_v[order], axis=0))
        R_spatial = np.maximum(R_spatial, _sat(sp_v, grad_scale))
        R_div = np.maximum(R_div, _sat(_forecast_residual(vert, dates, win), fore_scale))

    # 시스템 수준 공명 행렬 [4,4,M] + 함수망 공명 강도 [M] (N-K 결합)
    R_ij = _windowed_corr(V_func, win)
    S_net = _network_resonance(R_ij)                           # [M] ∈ [0,1]
    # 윈도우가 덜 찬 초기 시점은 상관이 불안정(2점이면 |corr|=1) → 신뢰도 램프로 가중.
    # (FRAM 문서 경고 "짧은 윈도우 노이즈 증폭" 대응; 샘플<3 이면 기여 0.)
    fill = np.minimum(np.arange(M) + 1, win)
    S_net = S_net * np.clip((fill - 2) / max(win - 2, 1), 0.0, 1.0)
    # 시스템 공명이 점별 결합 위험을 증폭(A>1 의미: S 클수록 동조가 더 위험). [0,1] clip.
    R_couple = np.clip(R_res * (1.0 + S_net[None, :]), 0, 1)   # [N,M]

    # [종합] CRI (가중치 합 ~1 → 절대 [0,1])
    CRI = np.clip(w1 * A + w2 * R_couple + w3 * R_spatial + w4 * R_div, 0, 1)

    cri_max = float(CRI.max())
    # 경보 차등: 종별(1종 중요) × 지형(산지·해상 바람노출) → 낮은 CRI 에서 조기경보.
    _ef = (grade_alert_factor(getattr(cfg, "bridge_grade", None))
           * terrain_alert_factor(getattr(cfg, "bridge_terrain", None)))
    t_lo, t_mid, t_hi = (min(t * _ef, 1.0) for t in cfg.cri_thresholds)

    g = "/fram"
    store.write_array(f"{g}/R_ij", R_ij)
    store.write_array(f"{g}/amplification", A)
    store.write_array(f"{g}/spatial_prop", R_spatial)
    store.write_array(f"{g}/divergence", R_div)
    store.write_array(f"{g}/CRI", CRI)
    store.write_array(f"{g}/network_resonance", S_net.astype(np.float32))

    # ③ 선택 isotonic 캘리브레이터 → 절대 붕괴확률 보정맵(경보 근거로도 사용).
    calibrated_ds = None
    calibrated = None
    cal_spec = getattr(cfg, "fram_calibrator", None)
    if cal_spec is not None:
        from .calibration import IsotonicCalibrator
        cal = IsotonicCalibrator.from_dict(cal_spec)
        calibrated = cal.predict(CRI).astype(np.float32)
        store.write_array(f"{g}/calibrated_risk", calibrated)
        calibrated_ds = f"{g}/calibrated_risk"

    def _level(v: float) -> str:
        return ("위험" if v >= t_hi else "경고" if v >= t_mid
                else "주의" if v >= t_lo else "정상")

    # 경보 근거: 보정확률 있으면 절대확률, 없으면 원시 CRI (③)
    if calibrated is not None:
        level, basis = _level(float(calibrated.max())), "calibrated_probability"
    else:
        level, basis = _level(cri_max), "cri"
    last = CRI[:, -1]
    crit = sorted({MEMBER_TYPES[member[i]] for i in np.where(last >= t_mid)[0]})
    cri_t = CRI.max(axis=0)
    over = np.where(cri_t >= t_mid)[0]
    lead = float(dates[-1] - dates[over[0]]) if len(over) else None        # 후방 경과
    lead_fwd = _forecast_to_threshold(cri_t, dates, t_hi)                  # ④ 전방 예측
    fstates = _function_states(V_func, FRAM_FUNCTIONS)                     # ① 기능별 상태

    # 6측면 함수망 진단 — 결합이 어느 기능을 통해 어느 경로로 전파되는지(설계 §5.3)
    from .network import function_network
    store.write_json_attr("fram", "function_network",
                          function_network(R_ij, list(FRAM_FUNCTIONS)))
    # 관측: 연직 융합이 CRI 에 반영됐는지(운영자가 경보 근거를 추적할 수 있게).
    store.write_json_attr("fram", "vertical_term",
                          {"used": vert is not None,
                           "source": insar.vertical_ds if vert is not None else None})

    out = FRAMOutput(
        n_points=N, n_dates=M,
        resonance_Rij_ds=f"{g}/R_ij", amplification_ds=f"{g}/amplification",
        spatial_prop_ds=f"{g}/spatial_prop", divergence_ds=f"{g}/divergence",
        CRI_ds=f"{g}/CRI", network_resonance_ds=f"{g}/network_resonance",
        calibrated_risk_ds=calibrated_ds,
        cri_global_max=cri_max,
        warning=FRAMWarning(level=level, lead_time_days=lead, critical_members=crit,
                            function_states=fstates, lead_time_forecast_days=lead_fwd,
                            basis=basis),
    )
    store.write_meta("fram", out)
    return out
