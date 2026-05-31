"""라벨된 합성 교량 영상 — CV 정확도 게이트(G2) 검증용.

회전각·길이·폭을 지정해 밝은 교량(데크+교각)을 그리고, **ground-truth ROI 마스크와
축선 방위각**을 함께 돌려준다. 실 영상/가중치 없이도 분할 mIoU·축선 오차·검출 위치를
정량 평가할 수 있다(실데이터 mAP 게이트의 전 단계).
"""

from __future__ import annotations

import numpy as np


def make_synthetic_bridge(
    H: int = 200,
    W: int = 400,
    angle_deg: float = 12.0,
    length_frac: float = 0.72,
    width_frac: float = 0.06,
    n_piers: int = 5,
    seed: int = 0,
    return_members: bool = False,
):
    """(image[H,W] in [0,1], roi_gt[H,W] bool, angle_deg[, members]) 반환.

    angle_deg 만큼 회전한 직사각 데크(밝기 0.85) + 등간격 교각(0.97), 배경 잡음.
    roi_gt 는 교량 구조 전체(데크∪교각), 축선 방위각은 angle_deg(무방향).
    return_members=True 면 부재별 GT 마스크 dict(deck/pier/abutment/bearing)도 돌려준다
    (엔진 의미와 정합: deck=전체 ROI, pier=교각 protrusion, abutment=축 양끝, bearing=교각∩데크).
    """
    rng = np.random.default_rng(seed)
    img = rng.normal(0.20, 0.04, (H, W))
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    cx, cy = W / 2.0, H / 2.0
    th = np.deg2rad(angle_deg)
    u = (xx - cx) * np.cos(th) + (yy - cy) * np.sin(th)      # 축선 방향
    v = -(xx - cx) * np.sin(th) + (yy - cy) * np.cos(th)     # 직교 방향
    half_L, half_Wd = length_frac * W / 2.0, width_frac * H

    deck = (np.abs(u) < half_L) & (np.abs(v) < half_Wd)
    img[deck] = 0.85
    roi_gt = deck.copy()
    pier_gt = np.zeros((H, W), dtype=bool)
    for uu in np.linspace(-half_L * 0.8, half_L * 0.8, n_piers):
        pier = (np.abs(u - uu) < half_Wd * 0.5) & (np.abs(v) < half_Wd * 2.2)
        img[pier] = 0.97
        roi_gt |= pier                                       # 교량 구조 전체(데크∪교각)
        pier_gt |= pier
    img += rng.normal(0.0, 0.02, (H, W))
    img = np.clip(img, 0.0, 1.0)

    if not return_members:
        return img, roi_gt, float(angle_deg)

    members = {
        "deck": roi_gt.copy(),                               # 엔진 deck=전체 ROI
        "pier": pier_gt,
        "abutment": roi_gt & (np.abs(u) > half_L * 0.88),    # 축 양끝
        "bearing": pier_gt & (np.abs(v) < half_Wd),          # 교각이 데크와 만나는 핵심
    }
    return img, roi_gt, float(angle_deg), members
