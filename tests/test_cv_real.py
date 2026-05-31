"""CV 실구현(Phase 2) — 영상처리 파이프라인(Otsu→CC→PCA→부재). 합성 영상으로 검증."""

from __future__ import annotations

import h5py
import numpy as np
import pytest

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import MEMBER_TYPES, CVOutput, FRAMOutput
from inframon.cv.real_engine import run_cv_real


def test_cv_real_fills_contract(tmp_path):
    cfg = PipelineConfig(image_h=200, image_w=360)
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        out = run_cv_real(store, cfg)
        roi = store.read_array(out.roi_mask_ds)
        gd = store.read_array(out.grid_density_ds)
        masks = {m: store.read_array(out.member_label_ds[m]) for m in MEMBER_TYPES}

    assert isinstance(out, CVOutput)
    assert out.image_shape == (200, 360)
    assert roi.shape == (200, 360)
    assert roi.sum() > 50                                  # ROI 비어있지 않음
    assert set(out.member_label_ds) == set(MEMBER_TYPES)
    assert masks["deck"].sum() == roi.sum()                # deck = ROI 전체
    assert masks["pier"].sum() > 0 and masks["abutment"].sum() > 0
    assert (gd[roi > 0] > 0).all() and (gd[roi == 0] == 0).all()  # 밀도는 ROI 내부만
    assert out.geometry.bridge_length > 0 and out.geometry.bridge_width > 0
    assert len(out.geometry.centerline) >= 2
    # shadow/layover 가 산출됨(더 이상 None 아님)
    assert out.shadow_ds is not None and out.layover_ds is not None


def test_cv_real_pca_recovers_axis(tmp_path):
    """합성 영상의 교량이 대체로 수평(±20°) → PCA 방위각이 그 범위."""
    cfg = PipelineConfig(image_h=200, image_w=400, seed=7)
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        out = run_cv_real(store, cfg)
    # 주축이 가로(긴 변) → |azimuth| 가 0 또는 180 근처(가로축). 길이>폭.
    az = abs(out.geometry.azimuth_angle) % 180
    assert (az < 35 or az > 145)
    assert out.geometry.bridge_length > out.geometry.bridge_width


def test_cv_transformer_backend_routes(tmp_path, monkeypatch):
    """cv_backend='transformer' 면 transformer 분할 경로를 타고 하류가 처리한다."""
    import inframon.cv.real_engine as ce

    called = {}

    def fake_tf(img, model_name=None):
        called["x"] = True
        H, W = img.shape
        m = np.zeros((H, W), dtype=bool)
        m[H // 2 - 6 : H // 2 + 6, W // 5 : 4 * W // 5] = True   # 가로 띠
        return m

    monkeypatch.setattr(ce, "_transformer_bridge_mask", fake_tf)
    cfg = PipelineConfig(image_h=140, image_w=320)
    cfg.cv_backend = "transformer"
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        out = ce.run_cv_real(store, cfg)
    assert called.get("x") is True
    assert out.geometry.bridge_length > out.geometry.bridge_width


def test_cv_transformer_falls_back_to_classical(tmp_path, monkeypatch):
    """transformers/가중치 없을 때(예외) classical 로 폴백, 크래시 없이 산출."""
    import inframon.cv.real_engine as ce

    def boom(img, model_name=None):
        raise ImportError("no transformers")

    monkeypatch.setattr(ce, "_transformer_bridge_mask", boom)
    cfg = PipelineConfig(image_h=140, image_w=320)
    cfg.cv_backend = "transformer"
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = ce.run_cv_real(store, cfg)
    assert out.geometry.bridge_length > 0          # classical 폴백으로 정상 산출


# ── 지오레퍼런스(geo_transform/crs) 산출 ──
def test_cv_real_no_geo_is_none(tmp_path):
    """합성 영상·override 없음 → geo_transform/crs 는 None(InSAR identity 폴백)."""
    cfg = PipelineConfig(image_h=140, image_w=300)
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        out = run_cv_real(store, cfg)
    assert out.geometry.geo_transform is None
    assert out.geometry.crs is None


def test_cv_real_geo_from_cfg_override(tmp_path):
    """cfg.cv_geo_transform/cv_crs override 가 CVGeometry 에 그대로 실린다."""
    gt = (300000.0, 0.5, 0.0, 600000.0, 0.0, -0.5)
    cfg = PipelineConfig(image_h=140, image_w=300)
    cfg.cv_geo_transform = gt
    cfg.cv_crs = "EPSG:5179"
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        out = run_cv_real(store, cfg)
    assert out.geometry.geo_transform == gt
    assert out.geometry.crs == "EPSG:5179"


def test_cv_real_length_meters_when_geo(tmp_path):
    """geo_transform(등방 0.5 m/px)이면 길이/폭이 픽셀×0.5 미터로 환산되고 length_unit='m'."""
    base = PipelineConfig(image_h=200, image_w=400, seed=11)
    with ProjectStore(tmp_path / "px.h5", mode="w") as store:
        px = run_cv_real(store, base).geometry          # geo 없음 → 픽셀

    geo_cfg = PipelineConfig(image_h=200, image_w=400, seed=11)
    geo_cfg.cv_geo_transform = (300000.0, 0.5, 0.0, 600000.0, 0.0, -0.5)  # 0.5 m/px 등방
    geo_cfg.cv_crs = "EPSG:5179"
    with ProjectStore(tmp_path / "m.h5", mode="w") as store:
        m = run_cv_real(store, geo_cfg).geometry        # 동일 영상 → 동일 픽셀 ROI

    assert px.length_unit == "pixel" and m.length_unit == "m"
    assert m.bridge_length == pytest.approx(0.5 * px.bridge_length, rel=1e-6)
    assert m.bridge_width == pytest.approx(0.5 * px.bridge_width, rel=1e-6)


def test_cv_real_geo_transform_bad_length_raises(tmp_path):
    cfg = PipelineConfig(image_h=140, image_w=300)
    cfg.cv_geo_transform = (1.0, 2.0, 3.0)  # 6 아님
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        with pytest.raises(ValueError, match="6-tuple"):
            run_cv_real(store, cfg)


def test_cv_real_geo_from_geotiff(tmp_path):
    """GeoTIFF 입력이면 transform/crs 를 읽어 geo_transform(GDAL 6-tuple)으로 적재."""
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import Affine

    H, W = 160, 320
    gt = (300000.0, 0.5, 0.0, 600000.0, 0.0, -0.5)  # GDAL (c,a,b,f,d,e)
    tif = tmp_path / "bridge.tif"
    img = np.zeros((H, W), dtype=np.uint8)
    img[H // 2 - 5 : H // 2 + 5, W // 6 : 5 * W // 6] = 200  # 가로 띠(교량)
    with rasterio.open(
        tif, "w", driver="GTiff", height=H, width=W, count=1, dtype="uint8",
        crs="EPSG:5179", transform=Affine.from_gdal(*gt),
    ) as ds:
        ds.write(img, 1)

    cfg = PipelineConfig(image_h=H, image_w=W)
    cfg.cv_image_path = str(tif)
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        out = run_cv_real(store, cfg)
    assert out.geometry.crs is not None and "5179" in out.geometry.crs
    assert np.allclose(out.geometry.geo_transform, gt, atol=1e-6)


def test_cv_real_geo_chains_to_insar(tmp_path):
    """CV real 이 낸 geo_transform 을 InSAR real 이 소비해 geo 정합(frame=cv_geo)."""
    from inframon.insar import geo
    from inframon.insar.real_engine import run_insar_real

    gt = (300000.0, 0.5, 0.0, 600000.0, 0.0, -0.5)
    cfg = PipelineConfig(image_h=160, image_w=320)
    cfg.cv_geo_transform = gt
    cfg.cv_crs = "EPSG:5179"
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        cv = run_cv_real(store, cfg)
        roi = store.read_array(cv.roi_mask_ds)
        inside = np.argwhere(roi > 0)
        sel = inside[np.random.default_rng(3).choice(len(inside), 6, replace=False)]
        col, row = sel[:, 1].astype(float), sel[:, 0].astype(float)
        world = geo.pixel_to_world(gt, col, row)  # CV 가 낸 gt 로 world 좌표 생성

        track = tmp_path / "track.h5"
        with h5py.File(track, "w") as f:
            f.create_dataset("ps_lonlat", data=world.astype(np.float64))
            f.create_dataset("epochs", data=np.array([20200101, 20200113], dtype=np.int32))
            f.create_dataset("los_mm", data=np.zeros((6, 2), dtype=np.float32))
            f.create_dataset("temp_coh", data=np.full(6, 0.8, dtype=np.float32))
            f.attrs["crs"] = "EPSG:5179"
        cfg.insar_source_h5 = str(track)

        out = run_insar_real(store, cv, cfg)
        src = store.read_json_attr("insar", "insar_source")

    assert cv.geometry.geo_transform == gt        # CV 가 실제 geo_transform 산출
    assert src["frame"] == "cv_geo"               # InSAR 가 그걸로 geo 정합
    assert src["registration"] == "geo_affine"
    assert out.n_points == 6


def test_cv_real_registered():
    from inframon.orchestrator import engines

    assert engines.resolve("cv", "real") is run_cv_real
    assert "real" in engines.available_modes("cv")


def test_pipeline_cv_real(tmp_path):
    """cv=real → InSAR stub 이 grid_density/member/image_shape 를 받아 끝까지."""
    from inframon.orchestrator.pipeline import run_pipeline

    cfg = PipelineConfig(n_points=40, n_dates=10,
                         engines={"cv": "real", "insar": "stub", "pinn": "stub", "fram": "stub"})
    fram = run_pipeline(tmp_path / "out.h5", cfg)
    assert isinstance(fram, FRAMOutput)
    assert 0.0 <= fram.cri_global_max <= 1.0
