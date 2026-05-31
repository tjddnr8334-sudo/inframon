"""CV 정확도 게이트 G2 — 라벨된 합성 교량으로 mIoU·축선오차·검출 위치 검증.

실 mAP(실 가중치·실영상)는 환경 의존이라 여기서는 합성 ground-truth 로 측정 가능한
분할 mIoU≥0.6, 축선오차<3°, 검출 bbox IoU≥0.5, shadow/layover non-None 을 본다.
"""

from __future__ import annotations

import numpy as np
import pytest

import inframon.cv.real_engine as ce
from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import MEMBER_TYPES
from inframon.cv.metrics import axis_angle_error_deg, bbox_iou, iou, mean_iou
from inframon.cv.synthetic import make_synthetic_bridge


# ── 메트릭 단위 ──
def test_iou_basic():
    a = np.zeros((10, 10), bool)
    a[2:6, 2:6] = True
    assert iou(a, a) == 1.0
    b = np.zeros((10, 10), bool)
    b[6:9, 6:9] = True
    assert iou(a, b) == 0.0
    assert iou(np.zeros((4, 4), bool), np.zeros((4, 4), bool)) == 1.0  # 공허 일치


def test_bbox_iou_basic():
    a = np.zeros((10, 10), bool)
    a[2:6, 2:6] = True
    assert bbox_iou(a, a) == pytest.approx(1.0)
    b = np.zeros((10, 10), bool)
    b[0:1, 0:1] = True
    assert bbox_iou(a, b) == 0.0


def test_axis_angle_error_wraps_180():
    assert axis_angle_error_deg(179, 1) == pytest.approx(2.0)     # 무방향 → 2°
    assert axis_angle_error_deg(10, 170) == pytest.approx(20.0)
    assert axis_angle_error_deg(45, 45) == pytest.approx(0.0)
    assert axis_angle_error_deg(0, 90) == pytest.approx(90.0)
    assert axis_angle_error_deg(-5, 5) == pytest.approx(10.0)


# ── 게이트 G2 (합성) ──
@pytest.mark.parametrize("angle", [0.0, 8.0, -15.0, 20.0])
def test_cv_real_meets_g2_gate(tmp_path, monkeypatch, angle):
    img, roi_gt, true_ang = make_synthetic_bridge(H=200, W=400, angle_deg=angle, seed=1)
    monkeypatch.setattr(ce, "_synth_bridge_image", lambda H, W, seed: img)

    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        out = ce.run_cv_real(store, PipelineConfig(image_h=200, image_w=400))
        roi_pred = store.read_array(out.roi_mask_ds).astype(bool)

    assert iou(roi_pred, roi_gt) >= 0.6                              # mIoU 게이트
    assert axis_angle_error_deg(out.geometry.azimuth_angle, true_ang) < 3.0  # 축선 게이트
    assert bbox_iou(roi_pred, roi_gt) >= 0.5                         # 검출 위치(mAP@0.5 프록시)
    assert out.shadow_ds is not None and out.layover_ds is not None  # shadow/layover 산출


@pytest.mark.parametrize("angle", [0.0, 12.0, -15.0, 20.0])
def test_cv_real_member_segmentation_miou(tmp_path, monkeypatch, angle):
    """부재 시맨틱 분할 — 영상 형상 증거로 교각 검출 → 부재 mIoU≥0.6 (G2 부재)."""
    img, _, _, gt = make_synthetic_bridge(H=200, W=400, angle_deg=angle, seed=1,
                                          return_members=True)
    monkeypatch.setattr(ce, "_synth_bridge_image", lambda H, W, seed: img)

    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        out = ce.run_cv_real(store, PipelineConfig(image_h=200, image_w=400))
        pred = {k: store.read_array(out.member_label_ds[k]).astype(bool) for k in MEMBER_TYPES}

    assert mean_iou(pred, gt) >= 0.6                  # 부재 평균 mIoU 게이트
    assert iou(pred["pier"], gt["pier"]) >= 0.6       # 교각이 실제 위치에서 검출(축 추측 아님)
    assert pred["deck"].sum() > 0 and pred["abutment"].sum() > 0


def test_cv_member_backend_sam_falls_back(tmp_path, monkeypatch):
    """cv_member_backend='sam' 인데 SAM 미설치 → shape 폴백으로 정상 분할(크래시 없음)."""
    img, _, _, gt = make_synthetic_bridge(H=200, W=400, angle_deg=10.0, seed=2,
                                          return_members=True)
    monkeypatch.setattr(ce, "_synth_bridge_image", lambda H, W, seed: img)
    # SAM 경로가 항상 실패하도록 강제 → 폴백 검증
    monkeypatch.setattr(ce, "_sam_member_masks",
                        lambda *a, **k: (_ for _ in ()).throw(ImportError("no SAM")))

    cfg = PipelineConfig(image_h=200, image_w=400)
    cfg.cv_member_backend = "sam"
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = ce.run_cv_real(store, cfg)
        pred = {k: store.read_array(out.member_label_ds[k]).astype(bool) for k in MEMBER_TYPES}

    assert mean_iou(pred, gt) >= 0.6                  # shape 폴백이 게이트 충족
