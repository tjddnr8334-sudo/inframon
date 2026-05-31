"""전(全) real 엔진 통합 — CV+InSAR+PINN+FRAM real 을 합성으로 한 번에 관통(capstone).

각 엔진 real 을 개별·stub 조합으로만 검증해 왔으나, 여기서는 4대 real 을 동시에 켜고
end-to-end 로 돌려 (1) 계약이 단계 간 일관(N/M 전파)되고 (2) 이번 세션의 좌표 체인
(CV geo_transform → InSAR world 정합 → world xyz → 고정단 거리)과 신규 출력
(network_resonance)이 끝까지 흐르는지 본다. torch 필요(pinn real).
"""

from __future__ import annotations

import h5py
import numpy as np
import pytest

pytest.importorskip("torch")

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import FRAM_FUNCTIONS, FRAMOutput, InSAROutput, PINNOutput
from inframon.cv.real_engine import run_cv_real
from inframon.orchestrator.pipeline import run_pipeline

EPOCHS = np.array([20230101, 20230113, 20230125, 20230206, 20230218, 20230302], dtype=np.int32)


def _aligned_track(tmp_path, cfg, *, n_points, geo=None, seed=0):
    """cv=real ROI 를 탐침해 그 안에 정렬된 Track H5 를 만든다(결정론 → 본 실행과 동일 ROI)."""
    with ProjectStore(tmp_path / "probe.h5", mode="w") as s:
        cv = run_cv_real(s, cfg)
        roi = s.read_array(cv.roi_mask_ds)
    inside = np.argwhere(roi > 0)                       # [K,2] (row,col)
    rng = np.random.default_rng(seed)
    sel = inside[rng.choice(len(inside), n_points, replace=False)]
    col, row = sel[:, 1].astype(float), sel[:, 0].astype(float)
    if geo is not None:
        from inframon.geotransform import pixel_to_world
        coords = pixel_to_world(geo, col, row)          # world (x,y)
    else:
        coords = np.column_stack([col, row])            # pixel (identity)
    track = tmp_path / "track.h5"
    with h5py.File(track, "w") as f:
        f.create_dataset("pixel_lonlat", data=coords.astype(np.float64))
        f.create_dataset("epochs", data=EPOCHS)
        f.create_dataset("los_mm", data=rng.normal(0, 2.0, (n_points, len(EPOCHS))).astype(np.float32))
        f.create_dataset("coh", data=np.full(n_points, 0.82, dtype=np.float32))
        f.create_dataset("height", data=rng.uniform(5, 25, n_points).astype(np.float32))
        if geo is not None:
            f.attrs["crs"] = "EPSG:5179"
    return str(track)


def _all_real_cfg(**kw):
    cfg = PipelineConfig(engines={"cv": "real", "insar": "real", "pinn": "real", "fram": "real"},
                         **kw)
    cfg.pinn_epochs = 40                                # 통합 검증용 경량 학습
    return cfg


def test_all_real_engines_geo_chain(tmp_path):
    """4대 real + geo 좌표 체인: CV geo_transform → InSAR world 정합 → world xyz → FRAM."""
    gt = (300000.0, 0.5, 0.0, 600000.0, 0.0, -0.5)
    cfg = _all_real_cfg(image_h=160, image_w=320)
    cfg.cv_geo_transform = gt
    cfg.cv_crs = "EPSG:5179"
    cfg.insar_source_h5 = _aligned_track(tmp_path, cfg, n_points=30, geo=gt, seed=1)

    fram = run_pipeline(tmp_path / "out.h5", cfg)        # validate_contracts=True → 단계간 N/M 강제

    assert isinstance(fram, FRAMOutput)
    assert 0.0 <= fram.cri_global_max <= 1.0
    with ProjectStore(tmp_path / "out.h5", mode="r") as s:
        ins = s.read_meta("insar", InSAROutput)
        src = s.read_json_attr("insar", "insar_source")
        xyz = s.read_array(ins.xyz_ds)
        pinn = s.read_meta("pinn", PINNOutput)
        EI = s.read_array(pinn.EI_ds)
        net = s.read_array(fram.network_resonance_ds)
        cri = s.read_array(fram.CRI_ds)

    # 좌표 체인 관통
    assert src["frame"] == "cv_geo" and src["registration"] == "geo_affine"
    assert src["xyz_frame"].startswith("world:") and src["z_source"] == "track_height"
    assert np.all(xyz[:, 0] > 100000.0)                 # world 좌표(픽셀 아님)
    assert np.any(xyz[:, 2] != 0.0)                     # DEM 고도 z 적재
    # 계약 N/M 전파
    assert ins.n_points == 30 and fram.n_points == 30 and pinn.n_points == 30
    assert ins.n_dates == len(EPOCHS) == fram.n_dates
    assert list(pinn.func_names) == list(FRAM_FUNCTIONS)  # V_func 순서 보존
    # 물리·수치 건전
    assert (EI > 0).all() and np.isfinite(EI).all()
    assert net.shape == (len(EPOCHS),) and (net >= 0).all() and (net <= 1).all()
    assert cri.shape == (30, len(EPOCHS)) and np.isfinite(cri).all()


def test_all_real_engines_identity_path(tmp_path):
    """4대 real + identity 폴백(CV geo 없음): Track 좌표=픽셀로 정합, 끝까지 관통."""
    cfg = _all_real_cfg(image_h=160, image_w=320)
    cfg.insar_source_h5 = _aligned_track(tmp_path, cfg, n_points=25, geo=None, seed=2)

    fram = run_pipeline(tmp_path / "out.h5", cfg)

    assert isinstance(fram, FRAMOutput)
    with ProjectStore(tmp_path / "out.h5", mode="r") as s:
        src = s.read_json_attr("insar", "insar_source")
        cri = s.read_array(fram.CRI_ds)
    assert src["frame"] == "cv_pixel" and src["registration"] == "identity"
    assert fram.n_points == 25
    assert cri.shape == (25, len(EPOCHS)) and np.isfinite(cri).all()
    assert 0.0 <= fram.cri_global_max <= 1.0
