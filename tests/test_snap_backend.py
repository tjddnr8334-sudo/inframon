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
