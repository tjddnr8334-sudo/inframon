"""모듈 1: InSAR — 변위 추출 엔진 (Phase 0 STUB).

실제 구현 예정: 다중포맷 어댑터(SNAP/ISCE/GAMMA) → PS/DS → 언래핑 →
시계열 역산 → LOS→종방향 분해. (Phase 1 에서 MintPy 등 래핑)
지금은 CV의 ROI/부재 라벨을 받아 가짜 LOS 변위 시계열을 생성한다.

가짜 변위는 의도적으로 '물리적으로 그럴듯하게' 만든다:
  열팽창(계절 사인) + 선형 침하 + 부재별 이상 = PINN/FRAM 데모에 의미가 생김.
"""

from __future__ import annotations

import numpy as np

from ..config import PipelineConfig
from ..contracts.io import ProjectStore
from ..contracts.schema import MEMBER_TYPES, CVOutput, InSAROutput


def run_insar(store: ProjectStore, cv: CVOutput, cfg: PipelineConfig) -> InSAROutput:
    rng = np.random.default_rng(cfg.seed + 1)
    N, M = cfg.n_points, cfg.n_dates
    H, W = cv.image_shape

    # ROI 안에서 N개 측정점을 부재 라벨과 함께 샘플링 (격자 밀도 가중)
    density = store.read_array(cv.grid_density_ds)
    ys, xs = np.where(density > 0)
    w = density[ys, xs]
    idx = rng.choice(len(xs), size=N, p=w / w.sum())
    px, py = xs[idx], ys[idx]

    # 각 점의 부재 라벨 결정
    member_idx = np.zeros(N, dtype=np.int8)
    for mi, m in enumerate(MEMBER_TYPES):
        mmask = store.read_array(cv.member_label_ds[m])
        member_idx[mmask[py, px] == 1] = mi

    # 좌표 (가짜 EPSG:5179) + 고정단까지 거리 L (열팽창용)
    xyz = np.stack([px.astype(float), py.astype(float), np.zeros(N)], axis=1)
    l_from_fixed = np.abs(px - W / 2).astype(float)

    # 시점 (epoch days, 월간)
    dates = np.arange(M, dtype=float) * 30.0

    # --- 가짜 변위 합성 ---
    t = dates / 365.0
    los = np.zeros((N, M))
    seasonal = np.sin(2 * np.pi * t)[None, :]                  # 계절 주기
    # 열팽창: 고정단 거리에 비례
    los += (l_from_fixed[:, None] / W) * 4.0 * seasonal
    # 선형 침하: 교각(pier=1)에서 가속
    settle_rate = np.where(member_idx == 1, -2.5, -0.4)[:, None]
    los += settle_rate * t[None, :]
    # 한 교각에 이상 손상 주입 (후반부 가속) → FRAM 전조 데모용
    bad = (member_idx == 1) & (px > W * 0.6) & (px < W * 0.85)
    los[bad] += -3.0 * np.clip(t - 1.5, 0, None)[None, :] ** 2
    # 관측 잡음
    los += rng.normal(0, 0.3, size=(N, M))

    # 종방향 분해 (데모: 축선 방위각 보정만 흉내)
    longitudinal = los * np.cos(np.deg2rad(cv.geometry.azimuth_angle))

    coherence = rng.uniform(0.5, 0.99, size=N)

    # 저장
    g = "/insar"
    store.write_array(f"{g}/point_id", np.arange(N))
    store.write_array(f"{g}/xyz", xyz)
    store.write_array(f"{g}/member", member_idx)
    store.write_array(f"{g}/coherence", coherence)
    store.write_array(f"{g}/l_from_fixed", l_from_fixed)
    store.write_array(f"{g}/los", los)
    store.write_array(f"{g}/longitudinal", longitudinal)
    store.write_array(f"{g}/dates", dates)
    store.write_array(f"{g}/temporal_coherence", coherence)

    out = InSAROutput(
        n_points=N, n_dates=M,
        point_id_ds=f"{g}/point_id", xyz_ds=f"{g}/xyz", member_ds=f"{g}/member",
        coherence_ds=f"{g}/coherence", l_from_fixed_ds=f"{g}/l_from_fixed",
        los_ds=f"{g}/los", longitudinal_ds=f"{g}/longitudinal",
        dates_ds=f"{g}/dates", temporal_coherence_ds=f"{g}/temporal_coherence",
    )
    store.write_meta("insar", out)
    return out
