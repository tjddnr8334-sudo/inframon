"""모듈 2: PINN — 구조 건전성 PDE/ODE 엔진 (Phase 0 STUB).

실제 구현 예정: PyTorch PINN + Euler-Bernoulli PDE 손실 + FEM 연계.
지금은 InSAR 변위를 성분 분해(열팽창/하중/침하/이상)하고,
물리이탈로부터 변동 V 를 산출해 FRAM 입력을 만든다.

성분 분해는 데모용 최소 모델(계절 사인 + 선형 추세 회귀)이지만,
출력 구조와 변동 V 의 의미는 실제 PINN과 동일하게 맞춘다.
"""

from __future__ import annotations

import numpy as np

from ..config import PipelineConfig
from ..contracts.io import ProjectStore
from ..contracts.schema import FRAM_FUNCTIONS, InSAROutput, PINNOutput


def run_pinn(store: ProjectStore, insar: InSAROutput, cfg: PipelineConfig) -> PINNOutput:
    los = store.read_array(insar.longitudinal_ds)        # [N,M]
    dates = store.read_array(insar.dates_ds)             # [M]
    member = store.read_array(insar.member_ds)           # [N]
    N, M = los.shape
    t = dates / 365.0

    # 설계행렬: [1, t, sin, cos]  → 침하(선형) + 열팽창(계절) 분리
    X = np.stack([np.ones(M), t, np.sin(2 * np.pi * t), np.cos(2 * np.pi * t)], axis=1)
    beta, *_ = np.linalg.lstsq(X, los.T, rcond=None)     # [4, N]
    fit = (X @ beta).T                                   # [N,M]

    comp_settle = (t[None, :] * beta[1][:, None])        # 선형 침하 성분
    comp_thermal = (np.sin(2 * np.pi * t)[None, :] * beta[2][:, None]
                    + np.cos(2 * np.pi * t)[None, :] * beta[3][:, None])
    comp_anomaly = los - fit                             # 회귀 잔차 = 이상 성분
    comp_load = np.zeros_like(los)                       # (데모: 미사용)

    # --- 구조 응답 (가짜) ---
    # 처짐 ~ 종방향 변위, 변형률 ~ 처짐의 공간 2차미분 흉내, 응력 = E*strain
    deflection = los
    strain = np.gradient(np.gradient(deflection, axis=0), axis=0)
    E = 2.1e11
    stress = E * strain
    natural_freq = np.array([3.2, 8.1, 15.7])           # 모드 3개 (가짜)

    # --- 물리 파라미터 역산 (가짜) ---
    EI = np.full(N, 1.0e9)
    EI[member == 1] *= 0.7                               # 교각 휨강성 저하 가정
    alpha = np.full(N, 1.2e-5)

    # ───────── 변동 V (★ FRAM 입력) — 물리모델 이탈 정도 ─────────
    # V_thermal: 열팽창 선형성 이탈 = 1 - R²(temp-disp)  (문서 5.4 변동4)
    ss_res = np.sum(comp_anomaly ** 2, axis=1)
    ss_tot = np.sum((los - los.mean(axis=1, keepdims=True)) ** 2, axis=1) + 1e-9
    V_thermal = np.clip(ss_res / ss_tot, 0, 1)           # [N]
    # V_settle: 침하 속도 크기 (정규화)
    V_settle = np.clip(np.abs(beta[1]) / (np.abs(beta[1]).max() + 1e-9), 0, 1)
    # V_anomaly: 잔차 표준편차 (정규화)
    resid_std = comp_anomaly.std(axis=1)
    V_anomaly = np.clip(resid_std / (resid_std.max() + 1e-9), 0, 1)
    V_load = np.full(N, 0.1)

    # 변동 시계열 [n_func, M] — FRAM 공명(시간상관)용
    # 각 시점에서 측정점들의 |성분 변화율|을 기능별로 집계
    def series(comp: np.ndarray) -> np.ndarray:
        d = np.abs(np.gradient(comp, axis=1))            # [N,M] 시간 변화율
        s = d.mean(axis=0)                               # [M]
        return s / (s.max() + 1e-9)

    V_func_series = np.stack([
        series(comp_thermal),    # thermal
        series(comp_load + comp_thermal * 0.3),  # load (데모 대용)
        series(comp_anomaly),    # bearing  (이상 성분이 받침 거동 변동 대용)
        series(comp_settle),     # foundation
    ], axis=0)                                            # [4, M]

    # 저장
    g = "/pinn"
    paths = {}
    for name, arr in [
        ("comp_thermal", comp_thermal), ("comp_load", comp_load),
        ("comp_settle", comp_settle), ("comp_anomaly", comp_anomaly),
        ("strain", strain), ("stress", stress), ("deflection", deflection),
        ("natural_freq", natural_freq), ("EI", EI), ("alpha", alpha),
        ("V_thermal", V_thermal), ("V_load", V_load),
        ("V_settle", V_settle), ("V_anomaly", V_anomaly),
        ("V_func_series", V_func_series),
    ]:
        paths[name] = store.write_array(f"{g}/{name}", arr)

    out = PINNOutput(
        n_points=N, n_dates=M,
        comp_thermal_ds=paths["comp_thermal"], comp_load_ds=paths["comp_load"],
        comp_settle_ds=paths["comp_settle"], comp_anomaly_ds=paths["comp_anomaly"],
        strain_ds=paths["strain"], stress_ds=paths["stress"],
        deflection_ds=paths["deflection"], natural_freq_ds=paths["natural_freq"],
        EI_ds=paths["EI"], alpha_ds=paths["alpha"],
        V_thermal_ds=paths["V_thermal"], V_load_ds=paths["V_load"],
        V_settle_ds=paths["V_settle"], V_anomaly_ds=paths["V_anomaly"],
        V_func_series_ds=paths["V_func_series"],
        func_names=list(FRAM_FUNCTIONS),
    )
    store.write_meta("pinn", out)
    return out
