"""DEM 래스터 고도 샘플링(insar/dem.py) + real 엔진 z 폴백(track_height>dem_raster>zero).

Track 결과에 점별 고도가 없을 때 DEM GeoTIFF 에서 z 를 샘플링하는 경로를 합성 DEM
으로 검증한다. rasterio/pyproj 미설치 환경은 skip.
"""

from __future__ import annotations

import h5py
import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin  # noqa: E402

from inframon.config import PipelineConfig  # noqa: E402
from inframon.contracts.io import ProjectStore  # noqa: E402
from inframon.cv.engine import run_cv  # noqa: E402
from inframon.insar import geo  # noqa: E402
from inframon.insar.dem import DemError, sample_dem  # noqa: E402
from inframon.insar.real_engine import run_insar_real  # noqa: E402


def _write_dem(path, crs, west, north, res, data, nodata=None):
    """north-up 합성 DEM GeoTIFF. 픽셀(r,c) 중심 = (west+(c+.5)res, north-(r+.5)res)."""
    h, w = data.shape
    transform = from_origin(west, north, res, res)
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1,
        dtype="float32", crs=crs, transform=transform, nodata=nodata,
    ) as dst:
        dst.write(data.astype("float32"), 1)


def _ramp(west, res, h, w, slope=0.05):
    """열별 선형 램프: z(c) = slope·((c+.5)·res). x 만의 함수라 샘플 검증이 쉽다."""
    cols = np.arange(w)
    row = slope * ((cols + 0.5) * res)
    return np.tile(row, (h, 1)).astype(np.float32)


# ───────────────────────── sample_dem 단위 ─────────────────────────

def test_sample_dem_ramp(tmp_path):
    """램프 DEM 을 알려진 좌표에서 샘플 → z ≈ slope·(x-west) (픽셀 이산 오차 안)."""
    west, north, res, slope = 300000.0, 4100100.0, 2.0, 0.05
    dem = tmp_path / "ramp.tif"
    _write_dem(dem, "EPSG:5179", west, north, res, _ramp(west, res, 350, 600, slope))

    xs = np.array([300100.0, 300500.0, 301000.0])
    ys = np.full(3, 4100000.0)
    xy = np.column_stack([xs, ys])
    out = sample_dem(xy, "EPSG:5179", str(dem))

    expected = slope * (xs - west)
    assert np.allclose(out.z, expected, atol=slope * res)   # ≤ 한 픽셀 슬로프
    assert out.meta["ok"] and out.meta["n_nodata_or_outside"] == 0
    assert out.meta["dem_crs"].endswith("5179")
    assert out.z.dtype == np.float32


def test_sample_dem_nodata_filled_with_median(tmp_path):
    """nodata/범위밖 점은 유효값 중앙값으로 메워진다."""
    west, north, res = 300000.0, 4100100.0, 2.0
    data = _ramp(west, res, 200, 400, slope=0.05)
    nodata = -9999.0
    data[:, :50] = nodata                       # 왼쪽 띠를 nodata 로
    dem = tmp_path / "nd.tif"
    _write_dem(dem, "EPSG:5179", west, north, res, data, nodata=nodata)

    # 1점은 nodata 띠(x<west+100m), 1점은 래스터 밖(동쪽), 2점은 유효
    xy = np.array([
        [300010.0, 4100000.0],   # nodata 띠
        [999999.0, 4100000.0],   # 래스터 범위 밖
        [300400.0, 4100000.0],   # 유효
        [300600.0, 4100000.0],   # 유효
    ])
    out = sample_dem(xy, "EPSG:5179", str(dem))
    assert out.meta["n_nodata_or_outside"] == 2
    fill = out.meta["fill_value"]
    assert out.z[0] == pytest.approx(fill, abs=1e-3)
    assert out.z[1] == pytest.approx(fill, abs=1e-3)
    assert out.z[2] > 0 and out.z[3] > 0        # 유효점은 램프값
    assert not np.isnan(out.z).any()


def test_sample_dem_reprojects_world_to_dem_crs(tmp_path):
    """world_crs ≠ DEM CRS 면 점을 DEM CRS 로 재투영해 샘플(상수 DEM 으로 검증)."""
    pyproj = pytest.importorskip("pyproj")
    # WGS84 입력점(정자교 부근) → EPSG:5179 DEM 에서 샘플.
    lon = np.array([127.1085, 127.1090, 127.1096])
    lat = np.array([37.3636, 37.3640, 37.3644])
    tf = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    px, py = tf.transform(lon, lat)
    west, north = float(min(px)) - 200, float(max(py)) + 200
    span_w = int((max(px) - min(px)) / 2) + 200
    span_h = int((max(py) - min(py)) / 2) + 200
    data = np.full((span_h, span_w), 42.0, dtype=np.float32)   # 상수 DEM
    dem = tmp_path / "const_5179.tif"
    _write_dem(dem, "EPSG:5179", west, north, 2.0, data)

    xy_wgs = np.column_stack([lon, lat])
    out = sample_dem(xy_wgs, "EPSG:4326", str(dem))
    assert np.allclose(out.z, 42.0, atol=1e-3)
    assert out.meta["n_nodata_or_outside"] == 0
    assert out.meta["dem_crs"].endswith("5179")
    assert out.meta["world_crs"] == "EPSG:4326"


def test_sample_dem_all_invalid_raises(tmp_path):
    """유효 고도를 한 점도 못 얻으면 DemError(z=0 폴백은 호출 측 책임)."""
    west, north, res = 300000.0, 4100100.0, 2.0
    dem = tmp_path / "small.tif"
    _write_dem(dem, "EPSG:5179", west, north, res, _ramp(west, res, 50, 50))
    far = np.array([[500000.0, 4500000.0], [500100.0, 4500100.0]])  # 전부 범위 밖
    with pytest.raises(DemError, match="유효 고도"):
        sample_dem(far, "EPSG:5179", str(dem))


def test_sample_dem_missing_file_raises(tmp_path):
    with pytest.raises(DemError, match="열기/샘플 실패"):
        sample_dem(np.array([[300000.0, 4100000.0]]), "EPSG:5179", str(tmp_path / "nope.tif"))


# ───────────────────────── real 엔진 통합 ─────────────────────────

def _cv_geo_store(tmp_path, n, seed):
    """geo_transform 부여한 CV + ROI 안 n점의 world 좌표를 돌려준다."""
    cfg = PipelineConfig()
    store = ProjectStore(tmp_path / "project.h5", mode="w").__enter__()
    cv = run_cv(store, cfg)
    roi = store.read_array(cv.roi_mask_ds)
    inside = np.argwhere(roi > 0)
    sel = inside[np.random.default_rng(seed).choice(len(inside), n, replace=False)]
    col, row = sel[:, 1].astype(float), sel[:, 0].astype(float)
    gt = (300000.0, 2.0, 0.0, 4100000.0, 0.0, -2.0)
    cv.geometry.geo_transform = gt
    cv.geometry.crs = "EPSG:5179"
    world = geo.pixel_to_world(gt, col, row)
    return cfg, cv, store, world


def test_real_insar_samples_dem_when_no_track_height(tmp_path):
    """Track 에 고도가 없고 --insar-dem 이 있으면 z 가 DEM 에서 샘플된다(dem_raster)."""
    cfg, cv, store, world = _cv_geo_store(tmp_path, n=5, seed=11)
    try:
        west, north, res, slope = 299800.0, 4100200.0, 2.0, 0.05
        dem = tmp_path / "scene_dem.tif"
        _write_dem(dem, "EPSG:5179", west, north, res, _ramp(west, res, 700, 800, slope))

        track = tmp_path / "noh_track.h5"
        with h5py.File(track, "w") as f:
            f.create_dataset("ps_lonlat", data=world.astype(np.float64))
            f.create_dataset("epochs", data=np.array([20200101, 20200113], dtype=np.int32))
            f.create_dataset("los_mm", data=np.zeros((5, 2), dtype=np.float32))
            f.create_dataset("temp_coh", data=np.full(5, 0.8, dtype=np.float32))
            f.attrs["crs"] = "EPSG:5179"
        cfg.insar_source_h5 = str(track)
        cfg.insar_dem_geotiff = str(dem)

        out = run_insar_real(store, cv, cfg)
        xyz = store.read_array(out.xyz_ds)
        src = store.read_json_attr("insar", "insar_source")
    finally:
        store.__exit__(None, None, None)

    assert src["z_source"] == "dem_raster"
    assert src["dem"] is not None and src["dem"]["ok"] is True
    assert src["dem"]["n_nodata_or_outside"] == 0
    # z 가 램프(0.05·(x-west))와 일치 — 0 이 아니라 실제 DEM 고도
    expected = slope * (xyz[:, 0] - west)
    assert np.allclose(xyz[:, 2], expected, atol=slope * res)
    assert not np.allclose(xyz[:, 2], 0.0)


def test_real_insar_dem_failure_falls_back_to_zero(tmp_path):
    """DEM 경로가 실패하면 z=0 으로 우아하게 폴백하고 사유를 기록한다."""
    cfg, cv, store, world = _cv_geo_store(tmp_path, n=5, seed=12)
    try:
        track = tmp_path / "noh_track2.h5"
        with h5py.File(track, "w") as f:
            f.create_dataset("ps_lonlat", data=world.astype(np.float64))
            f.create_dataset("epochs", data=np.array([20200101, 20200113], dtype=np.int32))
            f.create_dataset("los_mm", data=np.zeros((5, 2), dtype=np.float32))
            f.create_dataset("temp_coh", data=np.full(5, 0.8, dtype=np.float32))
            f.attrs["crs"] = "EPSG:5179"
        cfg.insar_source_h5 = str(track)
        cfg.insar_dem_geotiff = str(tmp_path / "does_not_exist.tif")

        out = run_insar_real(store, cv, cfg)
        xyz = store.read_array(out.xyz_ds)
        src = store.read_json_attr("insar", "insar_source")
    finally:
        store.__exit__(None, None, None)

    assert src["z_source"] == "zero"
    assert src["dem"] is not None and src["dem"]["ok"] is False
    assert np.allclose(xyz[:, 2], 0.0)


def test_real_insar_track_height_beats_dem(tmp_path):
    """점별 고도가 있으면 DEM 이 지정돼도 track_height 가 우선한다."""
    cfg, cv, store, world = _cv_geo_store(tmp_path, n=5, seed=13)
    try:
        west, north, res = 299800.0, 4100200.0, 2.0
        dem = tmp_path / "ignored_dem.tif"
        _write_dem(dem, "EPSG:5179", west, north, res, _ramp(west, res, 700, 800))
        heights = np.array([5.0, 12.0, 7.5, 20.0, 3.0], dtype=np.float32)

        track = tmp_path / "h_track.h5"
        with h5py.File(track, "w") as f:
            f.create_dataset("ps_lonlat", data=world.astype(np.float64))
            f.create_dataset("epochs", data=np.array([20200101, 20200113], dtype=np.int32))
            f.create_dataset("los_mm", data=np.zeros((5, 2), dtype=np.float32))
            f.create_dataset("temp_coh", data=np.full(5, 0.8, dtype=np.float32))
            f.create_dataset("height", data=heights)
            f.attrs["crs"] = "EPSG:5179"
        cfg.insar_source_h5 = str(track)
        cfg.insar_dem_geotiff = str(dem)

        out = run_insar_real(store, cv, cfg)
        xyz = store.read_array(out.xyz_ds)
        src = store.read_json_attr("insar", "insar_source")
    finally:
        store.__exit__(None, None, None)

    assert src["z_source"] == "track_height"
    assert src["dem"] is None                       # DEM 샘플 자체를 안 함
    assert np.allclose(np.sort(xyz[:, 2]), np.sort(heights), atol=1e-4)


# ─────────────── import_track_h5 DEM 경로 (WGS84 lon/lat) ───────────────

def test_import_track_h5_dem_sampling_engages_height_correction(tmp_path):
    """import_track_h5(dem_geotiff): height 없는 Track 을 WGS84 DEM 으로 z 채우고
    고도차가 생겨 고도상관 대기보정이 실제 적용되는지 검증."""
    from inframon.insar.track_reader import import_track_h5

    # WGS84 lon/lat 램프 DEM: z(lon) = 1000·(lon-127.0) → 경도 0.01°당 10m
    west, north, res = 127.0, 37.40, 0.001              # ~100m 격자
    h, w = 100, 100
    cols = np.arange(w)
    zrow = 1000.0 * ((west + (cols + 0.5) * res) - 127.0)
    data = np.tile(zrow, (h, 1)).astype(np.float32)
    dem = tmp_path / "wgs84_ramp.tif"
    _write_dem(dem, "EPSG:4326", west, north, res, data)

    # DEM 범위 안 40점 (경도 다양 → z 스프레드 확보), 간단한 los 시계열
    rng = np.random.default_rng(0)
    lon = rng.uniform(127.01, 127.09, 40)
    lat = rng.uniform(37.32, 37.38, 40)
    M = 8
    los = rng.normal(0, 1.0, (40, M)).astype(np.float32)
    track = tmp_path / "track_nohgt.h5"
    with h5py.File(track, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack([lon, lat]).astype(np.float64))
        f.create_dataset("epochs", data=np.arange(20200101, 20200101 + M, dtype=np.int32))
        f.create_dataset("los_mm", data=los)
        f.create_dataset("coh", data=np.full(40, 0.85, dtype=np.float32))

    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        out = import_track_h5(store, track, dem_geotiff=str(dem), apply_corrections=True)
        xyz = store.read_array("/insar/xyz")
        src = store.read_json_attr("insar", "track_source")

    z = xyz[:, 2]
    assert src["z_source"] == "dem_raster"
    assert src["dem"]["ok"] is True
    assert (z.max() - z.min()) > 5.0                    # DEM 램프로 실제 고도차
    # z 스프레드가 생겨 고도상관 보정이 적용됨(전엔 z=0 → skip)
    assert "height_correlated" in src["corrections"]["applied"]
    assert out.n_points == 40


def test_import_track_h5_no_dem_z_zero(tmp_path):
    """dem_geotiff 없고 Track height 없으면 z=0·z_source=zero (기존 동작 불변)."""
    from inframon.insar.track_reader import import_track_h5

    lon = np.linspace(127.01, 127.05, 6)
    lat = np.linspace(37.33, 37.35, 6)
    track = tmp_path / "t.h5"
    with h5py.File(track, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack([lon, lat]).astype(np.float64))
        f.create_dataset("epochs", data=np.array([20200101, 20200113], dtype=np.int32))
        f.create_dataset("los_mm", data=np.zeros((6, 2), dtype=np.float32))
        f.create_dataset("coh", data=np.full(6, 0.8, dtype=np.float32))
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        import_track_h5(store, track)
        xyz = store.read_array("/insar/xyz")
        src = store.read_json_attr("insar", "track_source")
    assert src["z_source"] == "zero"
    assert np.allclose(xyz[:, 2], 0.0)
