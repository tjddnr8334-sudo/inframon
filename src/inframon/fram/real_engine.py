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


def inspection_alert_factor(inspect_grade) -> float:
    """최종안전점검결과(A~E) → CRI 임계 배율. 상태등급이 낮을수록(손상·열화) 낮은 임계로
    조기경보. A(우수)1.0·B(양호)0.97·C(보통)0.90·D(미흡)0.80·E(불량)0.70. 미상=1.0."""
    key = str(inspect_grade or "").strip().upper()[:1]
    return {"A": 1.0, "B": 0.97, "C": 0.90, "D": 0.80, "E": 0.70}.get(key, 1.0)


def _current_year() -> int:
    from datetime import date
    return date.today().year


def age_alert_factor(build_year, as_of_year: int | None = None) -> float:
    """공용연수 → CRI 임계 배율. 노후화(강성저하·피로누적)로 오래될수록 낮은 임계로 조기경보.

    공용 10년 이내 1.0, 이후 80년에 걸쳐 0.82까지 선형 감소. 준공연도 미상=1.0.
    (준공연도 문자열/일자에서 앞 4자리 연도 추출.)
    """
    digits = "".join(ch for ch in str(build_year or "") if ch.isdigit())
    if len(digits) < 4:
        return 1.0
    by = int(digits[:4])
    if by < 1800 or by > 2200:
        return 1.0
    age = max(0, (as_of_year or _current_year()) - by)
    return round(1.0 - min(max(age - 10, 0) / 80.0, 1.0) * 0.18, 3)


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


def _robust_secular_rate(series: np.ndarray, dates: np.ndarray, *,
                         min_days: float = 270.0, min_pts: int = 3
                         ) -> tuple[np.ndarray, np.ndarray]:
    """확장(누적) 로버스트 secular 속도 [N,M] mm/yr + 점별 노이즈 std [N] mm.

    **왜**: 점-대-점 미분(np.gradient)은 InSAR 에폭 노이즈(σ~10mm)를 수백 mm/yr 로
    폭증시켜, 멀쩡한 교량도 변위속도가 포화(A≈1)돼 CRI 가 높게 나온다(실측 확인).
    각 시점 t 는 **[0..t] 전체 관측**으로 로버스트 추세를 적합해 노이즈를 시간으로
    평균한다. 기간 ≥ min_days 면 계절(sin·cos) 기저를 함께 적합해 **가역 열팽창을
    제거**하고 비가역 secular(침하·크리프)만 남긴다 — SHM 건강지표는 계절 호흡·측정
    노이즈가 아니라 누적 손상에 반응해야 한다. 관측이 부족한 초기 시점은 첫 안정
    추정으로 backfill. secular 속도가 시점마다 산출돼 붕괴 누적 시 단조 상승한다.

    반환: (secular_rate[N,M] mm/yr, point_noise[N] mm = 전구간 추세제거 잔차 std).
    """
    N, M = series.shape
    out = np.zeros((N, M))
    total_dur = float(dates[-1] - dates[0])
    short_record = total_dur < min_days             # 전체 관측이 ~1년 미만 → 계절 분리 불가
    for t in range(M):
        x = dates[: t + 1]
        dur = float(x[-1] - x[0])
        if t + 1 < min_pts:
            continue
        # 긴 관측(≥min_days)의 초기 에폭: 계절제거 가능해질 때까지 0(짧은 창 계절램프 오인 방지).
        # 짧은 관측 전체: 계절을 못 지우지만 **선형 secular 라도 내서 실제 급변위를 잡는다**
        # (0 으로 침묵하면 붕괴를 놓친다 — 거짓음성). 대신 잠정 플래그로 표시.
        if not short_record and dur < min_days:
            continue
        cols = [np.ones_like(x), x - x.mean()]
        if dur >= min_days:                          # 계절(sin·cos) 제거(가역 열팽창)
            ph = 2.0 * np.pi * x / 365.0
            cols += [np.sin(ph), np.cos(ph)]
        X = np.column_stack(cols)
        beta, *_ = np.linalg.lstsq(X, series[:, : t + 1].T, rcond=None)
        out[:, t] = beta[1] * 365.0                  # secular slope [N]
    # 점별 노이즈: 전구간 추세(+계절) 적합 잔차 std
    xf = dates
    colf = [np.ones_like(xf), xf - xf.mean()]
    if float(xf[-1] - xf[0]) >= min_days:
        phf = 2.0 * np.pi * xf / 365.0
        colf += [np.sin(phf), np.cos(phf)]
    Xf = np.column_stack(colf)
    betaf, *_ = np.linalg.lstsq(Xf, series.T, rcond=None)
    noise = (series - (Xf @ betaf).T).std(axis=1)
    return out, noise


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

    # 방어적 시간정렬: dates 가 비단조(예: 스타네트워크 기준일이 중간이라 [0,-636,…,+168])이면
    # obs_span_days·np.gradient·_robust_secular_rate 가 모두 오작동한다. 인제스트에서 이미
    # 정렬하지만, 다른 경로로 들어온 계약도 안전하도록 여기서 한 번 더 강제 정렬한다.
    if not np.all(np.diff(dates) >= 0):
        _o = np.argsort(dates, kind="stable")
        dates = dates[_o] - dates[_o][0]
        los = los[:, _o]
        comp_thermal = comp_thermal[:, _o]; comp_load = comp_load[:, _o]
        comp_anom = comp_anom[:, _o]; comp_settle = comp_settle[:, _o]
        V_func = V_func[:, _o]
        if vert is not None:
            vert = vert[:, _o]

    N, M = los.shape
    w1, w2, w3, w4 = cfg.cri_weights

    # 데크 정렬(공간전파 항용): 곡선 교량은 X좌표만으로 정렬하면 순서가 뒤엉킨다 →
    # **호길이 station**(데크를 따라 잰 거리) 순으로 정렬. 저장된 /insar/deck_station 이
    # 있으면(인제스트 때 폴리라인 투영) 쓰고, 없으면 점군에서 주곡선으로 추정(직선이면 X순 동일).
    if store.has_array("/insar/deck_station"):
        station = np.asarray(store.read_array("/insar/deck_station"), float)
    else:
        from ..insar.deck_geometry import principal_curve_station
        station = principal_curve_station(xyz[:, :2])
    order = np.argsort(station)                                 # 데크를 따라가는 순서

    # 절대 보정 스케일 (조정 가능, mm/yr 등)
    vel_scale = float(getattr(cfg, "fram_vel_scale", 10.0))      # 변위속도 [mm/yr]
    grad_scale = float(getattr(cfg, "fram_grad_scale", 6.0))     # 공간기울기 [mm/yr/점]
    accel_scale = float(getattr(cfg, "fram_accel_scale", 8.0))   # 가속 [mm/yr²]
    gate_scale = float(getattr(cfg, "fram_gate_scale", 6.0))     # 공명 활동도 게이트 [mm/yr]
    win = int(getattr(cfg, "fram_corr_win", 6))

    def ddt(a: np.ndarray) -> np.ndarray:
        return np.gradient(a, dates, axis=1) * 365.0            # /일 → /년

    # ★ 변위속도는 **로버스트 secular 속도**(확장윈도우 추세+계절제거)로 — 점-대-점 미분의
    # 노이즈 폭증(건강 교량이 고CRI 로 오인되던 근본원인) 제거. 기능성분(thermal/load/settle)은
    # 공명(상관, 스케일불변)용이라 원 ddt 유지(활동도 게이트로 크기 반영).
    rate, los_noise = _robust_secular_rate(los, dates)          # [N,M] mm/yr, [N] mm
    # 관측 충분성: ~1년 미만이면 계절(가역 열팽창)을 secular 에서 분리 못 해 판정이 **잠정**.
    # (짧은 관측에서도 급변위는 잡되, 계절 오인 가능 → 운영자에게 잠정임을 알린다.)
    obs_span_days = float(dates[-1] - dates[0])
    obs_sufficient = obs_span_days >= 270.0

    # 점·시점·기능별 변동 [4,N,M] — load 는 실제 comp_load 에서
    Vi = np.stack([
        np.abs(ddt(comp_thermal)),    # thermal
        np.abs(ddt(comp_load)),       # load  (★ 하드코딩 0.3 제거)
        np.abs(comp_anom),            # bearing
        np.abs(ddt(comp_settle)),     # foundation
    ], axis=0)

    # [공명1] 점별 기능간 공명 (공간정보 보존)
    R_res = np.clip(_pointwise_resonance(Vi, win), 0, 1)        # [N,M]

    # [공명2] 증폭률 A — 로버스트 secular 변위속도 절대 보정(계절·노이즈 무시, 누적손상만)
    A = _sat(np.abs(rate), vel_scale)

    # [공명3] 공간 전파 — 데크 호길이 station 순으로 secular 속도의 공간기울기(곡선 대응)
    sp = np.zeros_like(rate)
    sp[order] = np.abs(np.gradient(rate[order], axis=0))
    R_spatial = _sat(sp, grad_scale)

    # [공명4] 예측 발산 — secular 속도의 **가속(시간변화율)**. 매끈한 secular 라 안정적:
    # 선형 침하면 ≈0, 붕괴 전조(2차 가속)면 커진다. 점별 노이즈가 만드는 속도바닥을 빼서
    # 측정 노이즈가 발산으로 오인되지 않게.
    accel = np.abs(np.gradient(rate, dates, axis=1) * 365.0)    # mm/yr²
    span = float(dates[-1] - dates[0]) or 1.0
    vel_noise_floor = (los_noise / span * 365.0)[:, None]       # 노이즈가 만드는 속도 스케일
    R_div = _sat(np.maximum(accel - vel_noise_floor, 0.0), accel_scale)

    # [opt-in] 연직 융합 — 연직 침하의 secular 속도/공간기울기/가속을 동일 절대 스케일로
    # 평가해 종축항과 max 결합. vertical_ds 없으면 이 블록은 건너뜀.
    rate_for_gate = rate
    if vert is not None:
        rate_v, vert_noise = _robust_secular_rate(vert, dates)
        A = np.maximum(A, _sat(np.abs(rate_v), vel_scale))
        sp_v = np.zeros_like(rate_v)
        sp_v[order] = np.abs(np.gradient(rate_v[order], axis=0))
        R_spatial = np.maximum(R_spatial, _sat(sp_v, grad_scale))
        accel_v = np.abs(np.gradient(rate_v, dates, axis=1) * 365.0)
        vfloor_v = (vert_noise / span * 365.0)[:, None]
        R_div = np.maximum(R_div, _sat(np.maximum(accel_v - vfloor_v, 0.0), accel_scale))
        rate_for_gate = np.where(np.abs(rate_v) > np.abs(rate), rate_v, rate)

    # 시스템 수준 공명 행렬 [4,4,M] + 함수망 공명 강도 [M] (N-K 결합)
    R_ij = _windowed_corr(V_func, win)
    S_net = _network_resonance(R_ij)                           # [M] ∈ [0,1]
    # 윈도우가 덜 찬 초기 시점은 상관이 불안정(2점이면 |corr|=1) → 신뢰도 램프로 가중.
    # (FRAM 문서 경고 "짧은 윈도우 노이즈 증폭" 대응; 샘플<3 이면 기여 0.)
    fill = np.minimum(np.arange(M) + 1, win)
    S_net = S_net * np.clip((fill - 2) / max(win - 2, 1), 0.0, 1.0)
    # 시스템 공명이 점별 결합 위험을 증폭(S 클수록 동조가 더 위험). [0,1] clip.
    # ★ 활동도 게이트: 공명(|상관|)은 스케일 불변이라 미세한 계절 호흡만으로도 높게 나온다
    # → 실제 secular 변형이 있는 점에서만 공명을 위험으로 계상(healthy 인플레 제거).
    act = _sat(np.abs(rate_for_gate), gate_scale)              # [N,M] ∈[0,1]
    R_couple = np.clip(R_res * (1.0 + S_net[None, :]), 0, 1) * act   # [N,M]

    # [종합] CRI (가중치 합 ~1 → 절대 [0,1])
    CRI = np.clip(w1 * A + w2 * R_couple + w3 * R_spatial + w4 * R_div, 0, 1)

    cri_max = float(CRI.max())
    # 경보 차등: 종별(1종 중요) × 지형(산지·해상 바람노출) × 안전점검(A~E 상태) × 노후화
    # (공용연수) → 낮은 CRI 에서 조기경보. 미상 인자는 1.0(불변).
    _ef = (grade_alert_factor(getattr(cfg, "bridge_grade", None))
           * terrain_alert_factor(getattr(cfg, "bridge_terrain", None))
           * inspection_alert_factor(getattr(cfg, "bridge_inspect_grade", None))
           * age_alert_factor(getattr(cfg, "bridge_build_year", None),
                              getattr(cfg, "bridge_as_of_year", None)))
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

    # 경보 근거 우선순위: 정상범위(건강 인구 대비) > 보정확률 > 원시 CRI 절대임계.
    ref_spec = getattr(cfg, "fram_reference_range", None)
    ref_assess = None
    if ref_spec is not None:
        from .reference_range import ReferenceRange
        ref = ReferenceRange.from_dict(ref_spec)
        point_max = CRI.max(axis=1)                        # 점별 시간최대 CRI
        ref_assess = ref.classify(point_max)
        store.write_array(f"{g}/cri_percentile", ref.percentile_of(point_max).astype(np.float32))
        store.write_array(f"{g}/cri_robust_z", ref.robust_z(point_max).astype(np.float32))
        # 관측규모 부적합 경고(노이즈·기간·에폭수가 학습 조건과 크게 다르면 → 판정 잠정)
        mismatch = ref.regime_mismatch(noise_mm=float(np.median(los_noise)),
                                       span_days=obs_span_days, n_epochs=M)
        ref_assess = {**ref_assess, "regime_mismatch": mismatch,
                      "provisional": mismatch is not None}
        store.write_json_attr("fram", "reference_range", ref_assess)

    if ref_assess is not None:
        level, basis = ref_assess["level"], "reference_range"
    elif calibrated is not None:
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
    # 관측 충분성(잠정 판정 여부) — 짧은 관측은 계절 분리 불가로 판정이 잠정임을 명시.
    store.write_json_attr("fram", "observation",
                          {"span_days": round(obs_span_days, 1), "sufficient": obs_sufficient,
                           "note": None if obs_sufficient
                           else "관측<1년: 계절 분리 불가 → 잠정(급변위는 감지, 계절 오인 가능)"})

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
