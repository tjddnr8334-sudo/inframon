"""run_mtinsar 가 StaMPS 처리폴더를 그대로 입력으로 받는지 — 갈아 끼우기가 되는가.

읽기(`stamps_io`)가 되는 것과 **드라이버에 물리는 것**은 다른 문제다. SNAP 경로는
`track_rh_full.h5` + `snaphu_*/wrapped.dim` 을 전제로 짜여 있어서, StaMPS 를 붙이면
B⊥·입사각·슬랜트거리가 다른 자리에서 온다. 여기서는 SNAP 산출물이 **하나도 없는**
교량 폴더를 만들어, StaMPS 만으로 끝까지 도는지 본다.
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from datetime import date
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

rm = pytest.importorskip("run_mtinsar")
from inframon.insar import stamps_io as si                             # noqa: E402


def args(**kw) -> Namespace:
    """main() 이 만드는 것과 같은 모양의 인자 묶음 — 기본은 인터넷을 안 탄다."""
    base = dict(root="docs/bridges", only=None, gamma_min=0.35, aps_radius=400.0,
                aps_source_frac=0.5, iters=1, max_btemp=400.0, max_bperp=150.0,
                stamps=None, stamps_root=None, slant_range=880_000.0,
                temperature_csv=None, no_fetch=True,
                json_out="x.json", summary="x.png")
    base.update(kw)
    return Namespace(**base)


def make_bridge(tmp_path: Path, name="가상교", *, n_epochs=24) -> tuple[Path, Path]:
    """교량 폴더(bridge.json · deck_polyline.json)와 StaMPS 처리폴더를 만든다.

    점은 교축을 따라 늘어놓되, 교량 위(0~10 m)와 먼 맨땅(100~200 m) 양쪽에 충분히
    둔다 — `deck_split` 의 채점이 그 두 무리를 쓴다.
    """
    from scipy.io import savemat

    rng = np.random.default_rng(3)
    folder = tmp_path / name
    folder.mkdir()
    lat0, lon0 = 37.5700, 126.8600
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = m_per_deg_lat * np.cos(np.radians(lat0))

    # 교축은 동서로 800 m.
    poly = [[lat0, lon0], [lat0, lon0 + 800.0 / m_per_deg_lon]]
    (folder / "deck_polyline.json").write_text(
        json.dumps({"geometry": poly}), encoding="utf-8")
    (folder / "bridge.json").write_text(
        json.dumps({"name": name, "lat": lat0, "lon": lon0}), encoding="utf-8")

    along = rng.uniform(0, 800, 40)
    perp = np.concatenate([rng.uniform(-8, 8, 20),        # 교량 위
                           rng.uniform(110, 190, 20)])    # 먼 맨땅
    lon = lon0 + along / m_per_deg_lon
    lat = lat0 + perp / m_per_deg_lat
    lonlat = np.column_stack([lon, lat])
    n = lonlat.shape[0]

    days = [date(2019, 1, 1).toordinal() + 24 * k for k in range(n_epochs)]
    datenum = np.asarray(days, float) + (si.MATLAB_EPOCH_OFFSET
                                         - date(1970, 1, 1).toordinal())
    bperp = rng.uniform(-110, 110, n_epochs)
    t = (np.asarray(days, float) - days[0]) / 365.25
    # 속도 + 연주기 + 잡음을 위상으로 되돌려 넣는다(부호 규약은 stamps_io 가 맡는다).
    los_mm = (np.outer(rng.normal(0, 1.5, n), t)
              + 3.0 * np.sin(2 * np.pi * t)[None, :]
              + rng.normal(0, 2.0, (n, n_epochs)))
    lam = si.RADAR_WAVELENGTH_M
    ph = los_mm / 1000.0 / (-lam / (4 * np.pi))

    d = tmp_path / "stamps" / name
    d.mkdir(parents=True)
    savemat(str(d / "ps2.mat"), dict(lonlat=lonlat, day=datenum,
                                     master_day=datenum[0], bperp=bperp,
                                     mean_incidence=39.4, mean_range=880_000.0))
    savemat(str(d / "phuw2.mat"), dict(ph_uw=ph))
    savemat(str(d / "bp2.mat"), dict(bperp_mat=np.tile(bperp, (n, 1))))
    savemat(str(d / "la2.mat"), dict(la=np.full(n, np.radians(39.4))))
    savemat(str(d / "pm2.mat"), dict(coh_ps=rng.uniform(.4, .95, n)))
    return folder, d


# ── 폴더 고르기 ────────────────────────────────────────────────────────────
def test_stamps_root_아래_교량이름으로_찾는다(tmp_path):
    folder, d = make_bridge(tmp_path)
    a = args(stamps_root=str(d.parent))
    assert rm.stamps_dir(folder, {}, a) == d


def test_bridge_json_에_적어_둔_경로도_쓴다(tmp_path):
    folder, d = make_bridge(tmp_path)
    assert rm.stamps_dir(folder, {"stamps": str(d)}, args()) == d


def test_아무_데도_없으면_None(tmp_path):
    folder, _ = make_bridge(tmp_path)
    assert rm.stamps_dir(folder, {}, args()) is None


# ── 읽어 들이기 ────────────────────────────────────────────────────────────
def test_점별_수직기선은_시점_중앙값으로_줄인다(tmp_path):
    """K = 1000·B⊥/(R sinθ) 는 시점별 1차원을 받는다. 한 교량 안에서는 점마다
    차이가 없으니 중앙값으로 줄이고, 줄였다는 사실을 출처에 남긴다."""
    _, d = make_bridge(tmp_path, n_epochs=20)
    S = rm.load_stamps(d, args())
    assert S["bperp"].shape == (20,)
    assert "중앙값" in S["sources"]["bperp"]
    assert S["R"] == pytest.approx(880_000.0)


def test_mean_range_가_없으면_기본값을_쓰고_그_사실을_적는다(tmp_path):
    from scipy.io import savemat

    _, d = make_bridge(tmp_path)
    ps = si._load_mat(d / "ps2.mat")
    ps.pop("mean_range")
    savemat(str(d / "ps2.mat"), ps)
    S = rm.load_stamps(d, args(slant_range=800_000.0))
    assert S["R"] == pytest.approx(800_000.0)
    assert "치우친다" in S["sources"]["slant_range"]     # 조용히 넘어가지 않는다


# ── 끝까지 도는가 ──────────────────────────────────────────────────────────
def test_SNAP_산출물이_없어도_StaMPS_만으로_끝까지_돈다(tmp_path, monkeypatch):
    folder, d = make_bridge(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert not (folder / "track_rh_full.h5").exists()    # SNAP 쪽 입력이 아예 없다

    rec = rm.one(folder, args(stamps_root=str(d.parent)))
    assert rec is not None and not rec.get("skipped"), rec
    assert rec["origin"]["toolchain"] == "StaMPS"
    assert rec["n_epochs"] == 24 and rec["n_points"] == 40
    assert rec["master"] == "20190101"
    assert rec["incidence_deg"] == pytest.approx(39.4, abs=1e-2)
    assert rec["slant_range_m"] == pytest.approx(880_000.0)
    # MT-InSAR 네 가지가 실제로 돈 흔적 — 기선망 · γ · σΔh · APS
    assert rec["network"]["n_pairs"] > 0
    assert 0.0 <= rec["gamma_median"] <= 1.0
    assert rec["sigma_dh_median_m"] > 0
    assert rec["aps_rms_mm"] >= 0.0
    # SARPROZ 와 같은 꼴의 산출물이 교량 폴더에 남는다
    for f in ("mtinsar.json", "mtinsar_points.csv", "mtinsar_baselines.csv"):
        assert (folder / f).exists(), f


def test_StaMPS_가_깨졌으면_이유를_남기고_건너뛴다(tmp_path, monkeypatch):
    """조용히 SNAP 으로 되돌아가면 어느 자료로 나온 값인지 알 수 없게 된다."""
    from scipy.io import savemat

    folder, d = make_bridge(tmp_path)
    monkeypatch.chdir(tmp_path)
    savemat(str(d / "phuw2.mat"), dict(ph_uw=np.zeros((40, 5))))   # 시점 수 불일치
    rec = rm.one(folder, args(stamps_root=str(d.parent)))
    assert rec["skipped"].startswith("StaMPS:")
    assert "시점 수" in rec["skipped"]
