"""잔차고도(PSI DEM 오차) 추정 — B⊥ 에 비례하는 잔류 지형위상에서 Δh 를 되찾는다."""

from __future__ import annotations

import numpy as np
import pytest

from inframon.insar.residual_height import (estimate_residual_height, read_bperp_dim,
                                            run_snap_star)

R, INC = 871_000.0, 39.0


def _stack(dh_m, *, n_epochs=25, noise_mm=1.0, seed=0, vel=0.0):
    """합성 스택: 시점별 B⊥ 를 ±100 m 로 흩고, 점별 Δh 로 잔류 지형위상을 만든다."""
    rng = np.random.default_rng(seed)
    days = np.concatenate([[0.0], np.sort(rng.uniform(-900, 900, n_epochs - 1))])
    bperp = np.concatenate([[0.0], rng.uniform(-100, 100, n_epochs - 1)])
    dh = np.atleast_1d(np.asarray(dh_m, float))
    K = 1000.0 * bperp / (R * np.sin(np.radians(INC)))
    los = (vel * days / 365.25)[None, :] + dh[:, None] * K[None, :]
    los += rng.normal(0, noise_mm, los.shape)
    los[:, 0] = 0.0
    return los, days, bperp


def test_알려진_잔차고도를_되찾는다():
    truth = np.array([0.0, 10.0, 20.0, -5.0])
    los, days, bperp = _stack(truth, noise_mm=0.3)
    rh = estimate_residual_height(los, days, bperp, R, INC)
    assert np.allclose(rh.dh_m, truth, atol=2.0), rh.dh_m
    assert (rh.sigma_m > 0).all() and (rh.sigma_m < 5).all()


def test_속도와_잔차고도를_동시에_분리한다():
    los, days, bperp = _stack([10.0], noise_mm=0.3, vel=3.0)
    rh = estimate_residual_height(los, days, bperp, R, INC)
    assert abs(rh.dh_m[0] - 10.0) < 2.0
    assert abs(rh.vel_mm_yr[0] - 3.0) < 0.5


def test_잡음이_크면_σ가_커진다_감도_정직():
    """Sentinel-1 규모(B⊥ ±100 m)에서 잡음 7 mm 면 점별 σ 가 10 m 급이다 — 숨기지 않는다."""
    los, days, bperp = _stack([10.0], noise_mm=7.0)
    rh = estimate_residual_height(los, days, bperp, R, INC)
    assert rh.sigma_m[0] > 8.0


def test_집단_검정은_교면_위_묶음이_높으면_잡아낸다():
    """점별로는 못 갈라도 12점 vs 12점 묶으면 +10 m 차이가 z>2 로 나온다."""
    truth = np.array([10.0] * 12 + [0.0] * 12)
    los, days, bperp = _stack(truth, noise_mm=4.0, n_epochs=40, seed=3)
    rh = estimate_residual_height(los, days, bperp, R, INC)
    g = rh.group_test(np.array([True] * 12 + [False] * 12))
    assert g["ok"]
    assert abs(g["diff_m"] - 10.0) < 3.0 * g["se_diff_m"]    # 참값 10 m 가 3σ 안
    assert g["z"] > 2.0


def test_집단_검정은_차이가_없으면_유의하지_않다():
    los, days, bperp = _stack(np.zeros(20), noise_mm=4.0, n_epochs=40, seed=5)
    rh = estimate_residual_height(los, days, bperp, R, INC)
    g = rh.group_test(np.array([True] * 10 + [False] * 10))
    assert abs(g["z"]) < 2.5


def test_시점이_적으면_추정하지_않는다():
    los, days, bperp = _stack([10.0], n_epochs=4)
    rh = estimate_residual_height(los, days, bperp, R, INC)
    assert np.isnan(rh.dh_m[0])


def test_dim_에서_기선을_읽는다(tmp_path):
    dim = tmp_path / "wrapped.dim"
    dim.write_text('''<x>
<MDATTR name="slant_range_to_first_pixel" unit="m" type="float64">871109.03</MDATTR>
<MDATTR name="incidence_near" unit="deg" type="float64">36.27</MDATTR>
<MDElem name="Baselines"><MDATTR name="Perp Baseline" type="float64">0.0</MDATTR>
<MDATTR name="Temp Baseline" type="float64">0.0</MDATTR>
<MDATTR name="Perp Baseline" type="float64">75.79</MDATTR>
<MDATTR name="Temp Baseline" type="float64">888.0</MDATTR></MDElem></x>''', encoding="utf-8")
    b = read_bperp_dim(dim)
    assert b["bperp_m"] == pytest.approx(75.79)
    assert b["btemp_days"] == pytest.approx(888.0)
    assert b["slant_range_m"] == pytest.approx(871109.03)


def test_run_snap_star_는_트랙에_기록한다(tmp_path):
    import h5py
    truth = np.array([10.0, 0.0, 0.0])
    los, days, bperp = _stack(truth, noise_mm=0.3, n_epochs=6)
    master = "20200101"
    from datetime import datetime, timedelta
    d0 = datetime.strptime(master, "%Y%m%d")
    epochs = [int((d0 + timedelta(days=float(d))).strftime("%Y%m%d")) for d in days]
    order = np.argsort(days)
    tr = tmp_path / "t.h5"
    with h5py.File(tr, "w") as f:
        f["epochs"] = np.array(epochs, np.int32)[order]
        f["los_mm"] = los[:, order].astype(np.float32)
        f["incidenceAngle"] = np.full(3, INC, np.float32)
        f["pixel_lonlat"] = np.zeros((3, 2))
    for e, bp in zip(np.array(epochs)[order], bperp[order]):
        if int(e) == int(master):
            continue
        d = tmp_path / f"snaphu_{master}_{e}"
        d.mkdir()
        (d / "wrapped.dim").write_text(
            f'<MDATTR name="slant_range_to_first_pixel" type="float64">{R}</MDATTR>'
            f'<MDATTR name="incidence_near" type="float64">{INC}</MDATTR>'
            f'<MDATTR name="Perp Baseline" type="float64">{bp}</MDATTR>'
            f'<MDATTR name="Temp Baseline" type="float64">1.0</MDATTR>', encoding="utf-8")
    rh, g = run_snap_star(tr, tmp_path, master, on_deck=np.array([True, False, False]))
    assert abs(rh.dh_m[0] - 10.0) < 3.0
    with h5py.File(tr, "r") as f:
        assert "residual_height_m" in f and "residual_height_sigma_m" in f
        assert "residual_height" in f.attrs
