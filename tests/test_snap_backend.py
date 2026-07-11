"""SNAP(Windows) InSAR 백엔드 — 순수함수·gpt인자·스타네트워크(프로세스 격리)·Track 빌더."""

from __future__ import annotations

import numpy as np
import pytest

from inframon.insar import snap_backend as sb
from inframon.insar.snap_backend import (
    BurstLoc,
    SnapError,
    SnapPairResult,
    ifg_band_names,
    scene_date,
)


# ── 순수함수 ──
def test_scene_date():
    assert scene_date("S1A_IW_SLC__1SDV_20240107T093202_20240107T093230_x.zip") == "20240107"


def test_scene_date_bad():
    with pytest.raises(SnapError):
        scene_date("not_a_granule.zip")


def test_snap_date_format():
    assert sb._snap_date("20240107") == "07Jan2024"
    assert sb._snap_date("20241231") == "31Dec2024"


def test_ifg_band_names():
    i, q, c = ifg_band_names("IW3", "20240107", "20240119")
    assert i == "i_ifg_IW3_VV_07Jan2024_19Jan2024"
    assert q == "q_ifg_IW3_VV_07Jan2024_19Jan2024"
    assert c == "coh_IW3_VV_07Jan2024_19Jan2024"


def test_point_in_poly():
    # 기울어진 사각형(실제 S1 burst 형태) 포함/제외
    poly = [(126.167, 37.120), (127.183, 37.263), (127.146, 37.429), (126.129, 37.286)]
    assert sb._point_in_poly(127.108, 37.322, poly) is True     # 교량 포함
    assert sb._point_in_poly(128.0, 37.0, poly) is False        # 밖


def test_edge_margin_km():
    # 단위 사각형 중심 → 각 변까지 margin 계산(위도 0 근처 근사)
    poly = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    m = sb._edge_margin_km(0.5, 0.5, poly)
    assert 50.0 < m < 60.0        # 0.5deg ~ 55km


def test_burstloc_contained_default():
    b = sb.BurstLoc("IW2", 1, 5.4, 37.3, 127.1)
    assert b.contained is True and b.covered is True


def test_find_gpt_explicit(tmp_path):
    fake = tmp_path / "gpt.exe"
    fake.write_text("")
    assert sb.find_gpt(str(fake)) == str(fake)


def test_find_gpt_missing(monkeypatch):
    monkeypatch.setattr(sb, "_GPT_CANDIDATES", ("/no/such/gpt",))
    monkeypatch.setattr("shutil.which", lambda x: None)
    with pytest.raises(SnapError):
        sb.find_gpt()


# ── gpt 인자 구성(subprocess 격리) ──
def test_run_pair_args(monkeypatch):
    captured = {}

    class _P:
        returncode = 0

    def fake_run(args, **kw):
        captured["args"] = args
        return _P()

    monkeypatch.setattr("subprocess.run", fake_run)
    burst = BurstLoc("IW3", 9, 2.6, 37.33, 127.13)
    rc = sb.run_pair("gpt", "graph.xml",
                     "S1A_IW_SLC__1SDV_20240107T093202_x.zip",
                     "S1A_IW_SLC__1SDV_20240119T093202_x.zip",
                     burst, "SRTM 1Sec HGT", "out.tif")
    assert rc == 0
    a = captured["args"]
    assert "-Psubswath=IW3" in a
    assert "-PfirstBurst=9" in a and "-PlastBurst=9" in a
    assert "-PiBand=i_ifg_IW3_VV_07Jan2024_19Jan2024" in a
    assert "-PqBand=q_ifg_IW3_VV_07Jan2024_19Jan2024" in a
    assert "-PdemName=SRTM 1Sec HGT" in a


# ── 스타 네트워크(run_pair·burst 판별 격리) ──
def test_process_star_network(monkeypatch, tmp_path):
    scenes = [f"S1A_IW_SLC__1SDV_{d}T093202_{d}T093230_x.zip"
              for d in ("20240107", "20240119", "20240131")]
    monkeypatch.setattr(sb, "find_gpt", lambda *a, **k: "gpt")
    monkeypatch.setattr(sb, "find_bridge_burst",
                        lambda *a, **k: BurstLoc("IW3", 9, 2.6, 37.33, 127.13))
    seen = []

    def fake_pair(gpt, graph, ref, sec, burst, dem, out_file, **kw):
        seen.append((scene_date(ref), scene_date(sec)))
        # 산출물 생성(존재 검사 통과용)
        from pathlib import Path
        Path(out_file).write_text("x")
        return 0

    monkeypatch.setattr(sb, "run_pair", fake_pair)
    res = sb.process_star_network(scenes, 37.33, 127.11, tmp_path)
    assert res.reference == "20240107"                 # 최이른 날짜
    assert [p.sec_date for p in res.pairs] == ["20240119", "20240131"]
    assert all(p.ok for p in res.pairs)
    assert seen == [("20240107", "20240119"), ("20240107", "20240131")]


# ── Track 빌더(합성 지오코딩 tif) ──
def _write_tif(path, lon0, lat0, phase, coh, inc, px=0.001):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin
    H, W = phase.shape
    tr = from_origin(lon0, lat0, px, px)
    with rasterio.open(path, "w", driver="GTiff", height=H, width=W, count=3,
                       dtype="float32", crs="EPSG:4326", transform=tr) as ds:
        ds.write(phase.astype("float32"), 1)
        ds.write(coh.astype("float32"), 2)
        ds.write(inc.astype("float32"), 3)


def test_build_track_h5(tmp_path):
    pytest.importorskip("rasterio")
    h5py = pytest.importorskip("h5py")
    rng = np.random.default_rng(0)
    H = W = 20
    lon0, lat0 = 127.10, 37.33
    lat_c, lon_c = 37.32, 127.11        # 격자 중심 근처(교량)
    pairs = []
    for k, sd in enumerate(("20240119", "20240131", "20240212"), start=1):
        ph = (rng.random((H, W)) - 0.5) * 2 * np.pi
        coh = np.full((H, W), 0.7)
        inc = np.full((H, W), 39.0)
        tif = tmp_path / f"tc_20240107_{sd}.tif"
        _write_tif(tif, lon0, lat0, ph, coh, inc)
        pairs.append(SnapPairResult("20240107", sd, str(tif), True))
    out = tmp_path / "track.h5"
    n = sb.build_track_h5(pairs, "20240107", out, lat=lat_c, lon=lon_c,
                          coh_min=0.3, radius_km=5.0, heading=-13.1)
    assert n > 0
    with h5py.File(out, "r") as f:
        assert f["los_mm"].shape == (n, 4)             # 기준 + 3 보조
        assert np.allclose(f["los_mm"][:, 0], 0.0)     # 기준일 변위 0
        assert list(f["epochs"][()]) == [20240107, 20240119, 20240131, 20240212]
        assert f.attrs["HEADING"] == pytest.approx(-13.1)
        assert "incidenceAngle" in f


# ── 배치(burst 그룹핑으로 코레지 재사용) ──
def test_run_batch_groups_by_burst(monkeypatch, tmp_path):
    scenes = ["S1A_IW_SLC__1SDV_20240107T093202_20240107T093230_x.zip",
              "S1A_IW_SLC__1SDV_20240119T093202_20240119T093230_x.zip"]
    # 교량 A,B 는 같은 burst(IW3#9), C 는 다른 burst(IW2#5)
    def fake_burst(ref, lat, lon):
        return BurstLoc("IW3", 9, 2.0, lat, lon) if lat > 37.3 else BurstLoc("IW2", 5, 1.0, lat, lon)
    monkeypatch.setattr(sb, "find_gpt", lambda *a, **k: "gpt")
    monkeypatch.setattr(sb, "find_bridge_burst", fake_burst)
    monkeypatch.setattr(sb, "platform_heading", lambda *a, **k: -13.1)
    calls = {"process": 0}

    def fake_process(scenes, lat, lon, out_dir, *, burst=None, **kw):
        calls["process"] += 1
        r = sb.SnapRunResult(reference="20240107", burst=burst)
        r.pairs = [SnapPairResult("20240107", "20240119", "p.tif", True)]
        return r

    monkeypatch.setattr(sb, "process_star_network", fake_process)
    monkeypatch.setattr(sb, "build_track_h5",
                        lambda pairs, ref, out, **kw: (Path(out).write_text("x"), 50)[1])
    bridges = [{"name": "A", "lat": 37.32, "lon": 127.10},
               {"name": "B", "lat": 37.33, "lon": 127.11},
               {"name": "C", "lat": 37.20, "lon": 127.05}]
    from pathlib import Path
    results = sb.run_batch(scenes, bridges, tmp_path)
    assert len(results) == 3
    assert calls["process"] == 2                 # burst 2종 → 코레지 2회(3회 아님)
    assert all(r.n_points == 50 for r in results)
    assert {r.name for r in results} == {"A", "B", "C"}


def test_run_batch_bad_bridge(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "find_gpt", lambda *a, **k: "gpt")
    monkeypatch.setattr(sb, "find_bridge_burst",
                        lambda *a, **k: BurstLoc("IW3", 9, 2.0, 37.3, 127.1))
    monkeypatch.setattr(sb, "platform_heading", lambda *a, **k: None)
    monkeypatch.setattr(sb, "process_star_network",
                        lambda *a, **k: sb.SnapRunResult("20240107", BurstLoc("IW3", 9, 2.0, 37.3, 127.1)))
    monkeypatch.setattr(sb, "build_track_h5", lambda *a, **k: 5)
    res = sb.run_batch(["S1A_IW_SLC__1SDV_20240107T093202_x.zip"],
                       [{"name": "bad"}], tmp_path)   # lat/lon 누락
    assert len(res) == 1 and res[0].error and res[0].track_h5 is None


# ── 교량 데크 버퍼 PS/DS 선별 ──
def test_seg_and_polyline_dist_km():
    # 데크 선분 (127.10,37.32)–(127.11,37.32) 에서 남쪽으로 떨어진 점
    d = sb._seg_dist_km(__import__("numpy").array([127.105]),
                        __import__("numpy").array([37.32 - 0.001]),
                        (127.10, 37.32), (127.11, 37.32))
    assert 0.10 < float(d[0]) < 0.12          # 0.001deg ~ 0.11km
    poly = [(127.10, 37.32), (127.11, 37.32), (127.12, 37.325)]
    dp = sb._polyline_dist_km(__import__("numpy").array([127.105]),
                              __import__("numpy").array([37.32]), poly)
    assert float(dp[0]) < 0.01                # 선 위 → ~0


def test_build_bridge_track_ps_ds(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("rasterio")
    h5py = pytest.importorskip("h5py")
    # 데크(수평선) 근처는 고코히런스, 멀면 저코히런스인 합성 tif
    lon0, lat0, px = 127.100, 37.325, 0.0001    # ~11m 픽셀
    H = W = 60
    deck_lat = 37.322
    rows, cols = np.mgrid[0:H, 0:W]
    lat = lat0 - rows * px
    coh = np.where(np.abs(lat - deck_lat) < 0.0003, 0.8, 0.1).astype("float32")  # 데크±33m 고코히런스
    ph = np.full((H, W), 0.5, "float32"); inc = np.full((H, W), 39.0, "float32")
    pairs = []
    for sd in ("20240119", "20240131"):
        tif = tmp_path / f"tc_20240107_{sd}.tif"
        _write_tif(tif, lon0, lat0, ph, coh, inc, px=px)
        pairs.append(SnapPairResult("20240107", sd, str(tif), True))
    geom = [[deck_lat, 127.103], [deck_lat, 127.106]]   # [lat,lon] 수평 데크
    r = sb.build_bridge_track_ps_ds(pairs, "20240107", tmp_path / "deck.h5",
                                    geometry_latlon=geom, buffer_m=30.0,
                                    coh_min=0.35, ps_coh=0.7, heading=-13.1)
    assert r["n_points"] > 0
    assert r["deck_dist_max_m"] <= 30.0 + 1e-6
    with h5py.File(tmp_path / "deck.h5") as f:
        assert "scatterer_class" in f and "los_velocity_mm_yr" in f
        assert f.attrs["deck_buffer_m"] == 30.0
        # 모든 점이 고코히런스(0.8) → PS(class 1)
        assert set(np.unique(f["scatterer_class"][()])) <= {0, 1}


# ── ADI(진폭분산) 기반 PS/DS ──
def _write_amp_tif(path, lon0, lat0, ref, sec, px=0.0001):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin
    H, W = ref.shape
    with rasterio.open(path, "w", driver="GTiff", height=H, width=W, count=2,
                       dtype="float32", crs="EPSG:4326",
                       transform=from_origin(lon0, lat0, px, px)) as ds:
        ds.write(ref.astype("float32"), 1)   # band1 = 기준 강도
        ds.write(sec.astype("float32"), 2)   # band2 = 보조 강도


def test_adi_at_points(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("rasterio")
    lon0, lat0, px = 127.10, 37.33, 0.0005
    H = W = 10
    # 안정 강도(모든 날짜 1e5 근처) → ADI 낮음
    ref = np.full((H, W), 1e5, "float32")
    tifs = []
    for k in range(3):
        sec = np.full((H, W), 1e5 + k * 10, "float32")   # 거의 일정 → 낮은 ADI
        t = tmp_path / f"amp_{k}.tif"
        _write_amp_tif(t, lon0, lat0, ref, sec, px)
        tifs.append(str(t))
    lons = np.array([127.101, 127.102]); lats = np.array([37.328, 37.327])
    adi = sb.adi_at_points(tifs, lons, lats)
    assert adi.shape == (2,)
    assert np.all(adi < 0.05)          # 안정 강도 → ADI ~ 0


def test_build_bridge_track_ps_ds_with_amp(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("rasterio")
    h5py = pytest.importorskip("h5py")
    lon0, lat0, px = 127.100, 37.325, 0.0001
    H = W = 60
    deck_lat = 37.322
    rows, _ = np.mgrid[0:H, 0:W]
    lat = lat0 - rows * px
    coh = np.where(np.abs(lat - deck_lat) < 0.0003, 0.8, 0.1).astype("float32")
    ph = np.full((H, W), 0.5, "float32"); inc = np.full((H, W), 39.0, "float32")
    pairs, amps = [], []
    for k, sd in enumerate(("20240119", "20240131")):
        tif = tmp_path / f"tc_20240107_{sd}.tif"
        _write_tif(tif, lon0, lat0, ph, coh, inc, px=px)
        pairs.append(SnapPairResult("20240107", sd, str(tif), True))
        # 안정 강도 → 낮은 ADI → PS
        amp = tmp_path / f"amp_0107_{sd}.tif"
        _write_amp_tif(amp, lon0, lat0, np.full((H, W), 1e5, "float32"),
                       np.full((H, W), 1e5, "float32"), px)
        amps.append(str(amp))
    geom = [[deck_lat, 127.103], [deck_lat, 127.106]]
    r = sb.build_bridge_track_ps_ds(pairs, "20240107", tmp_path / "d.h5",
                                    geometry_latlon=geom, buffer_m=30.0, coh_min=0.35,
                                    amp_pairs=amps, adi_max=0.25)
    assert r["class_method"].startswith("ADI<")
    assert r["n_ps"] == r["n_points"]       # 전부 낮은 ADI → 전부 PS
    with h5py.File(tmp_path / "d.h5") as f:
        assert "amplitude_dispersion" in f


# ── ⑤ ERA5 master 선정·씬 소거 ──
class _SW:
    def __init__(self, date, excluded=False):
        self.date = date; self.excluded = excluded


class _MS:
    def __init__(self, master, scenes):
        self.selected_master = master; self.scenes = scenes
        self.n_excluded = sum(s.excluded for s in scenes)


def test_select_master_era5(monkeypatch):
    scenes = [f"S1A_IW_SLC__1SDV_{d}T093202_x.zip"
              for d in ("20240107", "20240119", "20240131", "20240212")]
    # 0119 를 악천후로 소거, master=0131

    def fake_select(lat, lon, dates, scene_names=None, **k):
        sw = [_SW(d, excluded=(d == "20240119")) for d in dates]
        return _MS("20240131", sw)

    master, kept, ms = sb.select_master_era5(scenes, 37.32, 127.10, select_fn=fake_select)
    assert "20240131" in master                 # ERA5 master
    kept_dates = {sb.scene_date(s) for s in kept}
    assert "20240119" not in kept_dates         # 악천후 소거
    assert "20240131" in kept_dates and len(kept) == 3
    assert ms.n_excluded == 1


def test_run_uses_era5_master(monkeypatch, tmp_path):
    scenes = [f"S1A_IW_SLC__1SDV_{d}T093202_x.zip" for d in ("20240107", "20240119", "20240131")]
    called = {}

    def fake_select(lat, lon, dates, **k):
        return _MS("20240119", [_SW(d) for d in dates])   # master=0119, 소거 없음

    def fake_psn(sc, la, lo, od, *, reference=None, **k):
        called["reference"] = reference; called["n_scenes"] = len(sc)
        return sb.SnapRunResult("20240119", BurstLoc("IW2", 1, 5.0, la, lo))

    monkeypatch.setattr("inframon.insar.era5_master.select_master", fake_select)
    monkeypatch.setattr(sb, "process_star_network", fake_psn)
    monkeypatch.setattr(sb, "platform_heading", lambda *a, **k: -13.1)
    monkeypatch.setattr(sb, "build_track_h5", lambda *a, **k: 10)
    res = sb.run(scenes, 37.32, 127.10, tmp_path, era5_master=True)
    assert "20240119" in str(called["reference"])       # ERA5 master 를 reference 로
    assert res.weather is not None


def test_build_track_h5_no_points(tmp_path):
    pytest.importorskip("rasterio")
    # 모든 coh 낮음 → 점 없음 → SnapError
    ph = np.zeros((10, 10)); coh = np.zeros((10, 10)); inc = np.full((10, 10), 39.0)
    tif = tmp_path / "tc_20240107_20240119.tif"
    _write_tif(tif, 127.10, 37.33, ph + 0.1, coh, inc)
    pairs = [SnapPairResult("20240107", "20240119", str(tif), True)]
    with pytest.raises(SnapError):
        sb.build_track_h5(pairs, "20240107", tmp_path / "t.h5",
                          lat=37.32, lon=127.11, coh_min=0.5, radius_km=5.0)
