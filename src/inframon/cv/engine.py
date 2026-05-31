"""모듈 3: CV — ROI 산정 자동화 엔진 (Phase 0 STUB).

실제 구현: cv/real_engine.py (Transformer 시맨틱분할[SegFormer/DETR] 또는 Otsu+CC → PCA 축선 → 부재). YOLO 아님.
지금은 가짜 ROI 마스크/부재 라벨/축선을 생성해 계약(CVOutput)을 채운다.
"""

from __future__ import annotations

import numpy as np

from ..config import PipelineConfig
from ..contracts.io import ProjectStore
from ..contracts.schema import MEMBER_TYPES, CVGeometry, CVOutput


def run_cv(store: ProjectStore, cfg: PipelineConfig) -> CVOutput:
    rng = np.random.default_rng(cfg.seed)
    H, W = cfg.image_h, cfg.image_w

    # --- 가짜 교량: 영상 중앙을 가로지르는 수평 띠 ---
    roi = np.zeros((H, W), dtype=np.uint8)
    band = slice(H // 2 - 12, H // 2 + 12)
    roi[band, :] = 1

    # 부재 마스크: 바닥판(deck) 전역, 교각(pier)은 일정 간격 세로 기둥
    members: dict[str, str] = {}
    deck = roi.copy()
    pier = np.zeros_like(roi)
    for cx in range(W // 8, W, W // 4):          # 교각 4개
        pier[band, cx - 4 : cx + 4] = 1
    deck[pier == 1] = 0
    abutment = np.zeros_like(roi)
    abutment[band, :6] = 1
    abutment[band, -6:] = 1
    bearing = np.zeros_like(roi)
    bearing[band, ::W // 4] = 1

    mask_by_member = {
        "deck": deck, "pier": pier, "abutment": abutment, "bearing": bearing,
    }
    for m in MEMBER_TYPES:
        members[m] = store.write_array(f"/cv/member_{m}", mask_by_member[m])

    store.write_array("/cv/roi_mask", roi)

    # 격자 밀도: 교각/받침 조밀(2.0), 그 외 표준(1.0)  — 문서 4.3 (5)
    density = roi.astype(np.float32)
    density[pier == 1] = 2.0
    density[bearing == 1] = 2.0
    store.write_array("/cv/grid_density", density)

    geom = CVGeometry(
        centerline=[(0.0, float(H // 2)), (float(W), float(H // 2))],
        azimuth_angle=float(rng.uniform(-10, 10)),
        bridge_length=float(W), bridge_width=24.0,
    )

    out = CVOutput(
        roi_mask_ds="/cv/roi_mask",
        member_label_ds=members,
        geometry=geom,
        grid_density_ds="/cv/grid_density",
        image_shape=(H, W),
    )
    store.write_meta("cv", out)
    return out
