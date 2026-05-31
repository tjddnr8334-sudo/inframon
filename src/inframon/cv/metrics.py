"""CV 정확도 메트릭 (게이트 G2: mIoU≥0.6 / 축선오차<3° / 검출). 순수 numpy.

분할(IoU/mIoU)·검출 위치(bbox IoU)·축선 방위각 오차를 잰다. 교량 축선은 무방향
직선이라 각오차는 180° 모듈로(예: 179°와 1° 는 2° 차이)로 계산한다.
"""

from __future__ import annotations

import numpy as np


def iou(a: np.ndarray, b: np.ndarray) -> float:
    """두 이진 마스크의 교집합/합집합. 둘 다 비면 1.0(공허 일치)."""
    a = np.asarray(a, dtype=bool)
    b = np.asarray(b, dtype=bool)
    union = int((a | b).sum())
    if union == 0:
        return 1.0
    return float((a & b).sum()) / union


def mean_iou(pred: dict[str, np.ndarray], gt: dict[str, np.ndarray]) -> float:
    """클래스(부재)별 IoU 의 평균. 공통 키만 평가한다."""
    keys = [k for k in gt if k in pred]
    if not keys:
        return 0.0
    return float(np.mean([iou(pred[k], gt[k]) for k in keys]))


def bbox_of(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """마스크의 경계상자 (r0,c0,r1,c1) (반열림). 비면 None."""
    m = np.asarray(mask, dtype=bool)
    if not m.any():
        return None
    rows = np.where(m.any(axis=1))[0]
    cols = np.where(m.any(axis=0))[0]
    return int(rows[0]), int(cols[0]), int(rows[-1]) + 1, int(cols[-1]) + 1


def bbox_iou(a: np.ndarray, b: np.ndarray) -> float:
    """두 마스크 경계상자의 IoU(검출 위치 정확도; mAP@0.5 의 단일객체 프록시)."""
    ba, bb = bbox_of(a), bbox_of(b)
    if ba is None or bb is None:
        return 0.0
    r0, c0 = max(ba[0], bb[0]), max(ba[1], bb[1])
    r1, c1 = min(ba[2], bb[2]), min(ba[3], bb[3])
    inter = max(0, r1 - r0) * max(0, c1 - c0)
    area_a = (ba[2] - ba[0]) * (ba[3] - ba[1])
    area_b = (bb[2] - bb[0]) * (bb[3] - bb[1])
    union = area_a + area_b - inter
    return float(inter) / union if union > 0 else 0.0


def axis_angle_error_deg(pred_deg: float, true_deg: float) -> float:
    """무방향 축선 사이 각오차[°] (180° 모듈로, [0,90])."""
    d = abs(float(pred_deg) - float(true_deg)) % 180.0
    return min(d, 180.0 - d)
