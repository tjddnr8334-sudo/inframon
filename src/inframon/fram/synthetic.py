"""붕괴 전조 합성 시나리오 (Morandi 류) — FRAM 검증·캘리브용 라벨된 데이터.

일부 측정점(손상 교각 인근)에 시간이 갈수록 **가속하는 침하**(붕괴 전조)를 주고,
나머지는 계절 변동만 둔다. 점별 ground-truth(failing 여부)를 함께 돌려준다 —
FRAM 의 CRI 가 이 failing 점을 구분하는지 ROC 로 검증(게이트 G5: ROC-AUC≥0.9),
또는 isotonic 캘리브의 라벨로 쓴다.

실데이터가 아니라 **물리적으로 동기화된 합성**이다: 가속 침하(2차항)는 PINN 의
예측 발산·FRAM 의 가속/속도 항을 끌어올려 CRI 상승으로 이어져야 한다.
"""

from __future__ import annotations

import numpy as np

from ..contracts.io import ProjectStore
from ..insar.track_reader import write_insar_contract


def make_collapse_scenario(
    store: ProjectStore,
    *,
    n_points: int = 60,
    n_dates: int = 36,
    fail_frac: float = 0.2,
    onset_frac: float = 0.55,
    accel_mm: float = 8.0,
    seasonal_mm: float = 3.0,
    noise_mm: float = 0.3,
    seed: int = 0,
) -> tuple[object, np.ndarray]:
    """라벨된 붕괴 전조 InSAR 계약을 store 에 적재하고 (InSAROutput, failing[N]) 반환.

    - 건전 점: 계절 변동(sin) + 소량 노이즈.
    - failing 점(가운데 한 구간): + 발생시점 t0 이후 **가속 침하** -(t-t0)²·k.
    """
    rng = np.random.default_rng(seed)
    dates = np.arange(n_dates, dtype=np.float64) * 30.0      # ~월간 일수
    t = dates / 365.0                                        # 년

    x = np.linspace(0.0, 100.0, n_points)                   # 종방향 위치(m)
    xyz = np.column_stack([x, np.zeros(n_points), np.zeros(n_points)])

    failing = np.zeros(n_points, dtype=bool)
    k = max(2, int(round(fail_frac * n_points)))
    start = n_points // 2 - k // 2                          # 가운데 한 구간(인접 교각군)
    failing[start : start + k] = True

    seasonal = seasonal_mm * np.sin(2 * np.pi * t)[None, :]  # [1,M] 공통 계절
    series = seasonal + rng.normal(0.0, noise_mm, size=(n_points, n_dates))

    t0 = onset_frac * t[-1]
    ramp = np.clip(t - t0, 0.0, None)[None, :]               # 발생 후만
    series[failing] += -accel_mm * ramp**2                   # 가속 침하(하강)

    longitudinal = series.astype(np.float32)
    los = longitudinal.copy()
    member = np.zeros(n_points, dtype=np.int8)
    member[failing] = 1                                      # MEMBER_TYPES[1]='pier'
    coherence = np.full(n_points, 0.85, dtype=np.float32)
    l_from_fixed = np.abs(x - x.mean()).astype(np.float32)

    out = write_insar_contract(
        store, xyz=xyz, member=member, coherence=coherence, l_from_fixed=l_from_fixed,
        los=los, longitudinal=longitudinal, dates=dates, date_labels=None,
    )
    return out, failing


def roc_auc(score: np.ndarray, label: np.ndarray) -> float:
    """ROC-AUC = P(score[pos] > score[neg]) + ½P(tie). 의존성 없이 정확(소규모)."""
    label = np.asarray(label, dtype=bool)
    pos, neg = np.asarray(score)[label], np.asarray(score)[~label]
    if pos.size == 0 or neg.size == 0:
        raise ValueError("ROC-AUC 에는 양성·음성 라벨이 모두 필요합니다")
    diff = pos[:, None] - neg[None, :]
    wins = (diff > 0).sum() + 0.5 * (diff == 0).sum()
    return float(wins / (pos.size * neg.size))
