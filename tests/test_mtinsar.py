"""MT-InSAR 네 가지 — 합성 자료로 '정말 되찾는가' 를 확인한다.

참값을 아는 자료를 만들어 넣고 그 값이 돌아오는지 본다. 돌아오지 않으면 실제
교량에서 나온 숫자도 믿을 수 없다.
"""

from __future__ import annotations

import numpy as np
import pytest

from inframon.insar import mtinsar as mt


def _synth(n_pts=60, n_ep=51, seed=0):
    """속도·DEM오차·대기를 넣은 합성 LOS 를 만든다."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0.0, 7.0, n_ep)                       # 년
    days = t * 365.25
    bperp = rng.uniform(-120, 120, n_ep)
    R, inc = 888_000.0, 36.25
    K = mt.height_phase_factor(bperp, R, inc)
    xy = rng.uniform(0, 1200, (n_pts, 2))                 # m
    v_true = rng.uniform(-4, 4, n_pts)                    # mm/년
    dh_true = rng.uniform(-20, 20, n_pts)                 # m
    # 대기 — 시점마다 공간적으로 매끄러운 면(평면 + 완만한 곡률)
    aps = np.zeros((n_pts, n_ep))
    for k in range(n_ep):
        g = rng.normal(0, 1, 3)
        aps[:, k] = 6.0 * (g[0] * xy[:, 0] / 1200 + g[1] * xy[:, 1] / 1200
                           + g[2] * (xy[:, 0] * xy[:, 1]) / 1200 ** 2)
    los = (v_true[:, None] * t[None, :] + dh_true[:, None] * K[None, :]
           + aps + rng.normal(0, 1.0, (n_pts, n_ep)))
    return dict(t=t, days=days, bperp=bperp, K=K, xy=xy, los=los,
                v_true=v_true, dh_true=dh_true, aps=aps, R=R, inc=inc)


# ── ① ADI ─────────────────────────────────────────────────────────────────
def test_adi_낮을수록_안정하다():
    rng = np.random.default_rng(1)
    steady = 100.0 + rng.normal(0, 1.0, (1, 40))
    jumpy = 100.0 + rng.normal(0, 40.0, (1, 40))
    a = mt.amplitude_dispersion(np.vstack([steady, jumpy]))
    assert a[0] < 0.1 < a[1]


def test_adi_대용은_마스터진폭에_영향받지_않는다():
    """star 망 |ifg| ∝ A_m·A_j — A_m 이 달라도 ADI 는 같아야 한다."""
    rng = np.random.default_rng(2)
    aj = np.abs(rng.normal(10, 2, (5, 30)))
    for scale in (1.0, 7.5, 0.3):
        got = mt.adi_from_star_ifg(aj * scale)["adi"]
        assert np.allclose(got, mt.amplitude_dispersion(aj))


def test_ps_마스크는_두_문턱을_모두_본다():
    adi = np.array([0.1, 0.1, 0.4])
    gam = np.array([0.9, 0.2, 0.9])
    m = mt.ps_mask(adi, adi_max=0.25, gamma=gam, gamma_min=0.5)
    assert list(m) == [True, False, False]


# ── ② 기선 네트워크 ────────────────────────────────────────────────────────
def test_네트워크는_문턱을_지킨다():
    s = _synth(n_pts=5)
    net = mt.baseline_network(s["days"], s["bperp"],
                              max_btemp_days=400, max_bperp_m=100)
    for i, j in net["pairs"]:
        assert abs(s["days"][j] - s["days"][i]) <= 400
        assert abs(s["bperp"][j] - s["bperp"][i]) <= 100
    assert net["n_pairs"] > 0


def test_문턱이_좁으면_망이_끊어진다():
    s = _synth(n_pts=5)
    tight = mt.baseline_network(s["days"], s["bperp"],
                                max_btemp_days=5, max_bperp_m=1)
    wide = mt.baseline_network(s["days"], s["bperp"],
                               max_btemp_days=3000, max_bperp_m=1000)
    assert not tight["connected"]
    assert wide["connected"]


def test_star에서_유도한_쌍은_닫힘오차가_0():
    """풀린 변위를 빼서 만든 쌍은 항등적으로 닫힌다 — 그 사실을 고정해 둔다."""
    s = _synth(n_pts=8)
    net = mt.baseline_network(s["days"], s["bperp"],
                              max_btemp_days=3000, max_bperp_m=1000)
    c = mt.closure_errors(net["pairs"][:60], s["los"])
    assert c["n_triangles"] > 0
    assert c["rms_mm"] == pytest.approx(0.0, abs=1e-9)


# ── ③ 속도 + DEM 오차 ──────────────────────────────────────────────────────
def test_속도와_DEM오차를_같이_풀면_되찾는다():
    rng = np.random.default_rng(3)
    s = _synth(n_pts=40, seed=3)
    los = (s["v_true"][:, None] * s["t"][None, :]
           + s["dh_true"][:, None] * s["K"][None, :]
           + rng.normal(0, 0.5, (40, len(s["t"]))))
    fit = mt.solve_velocity_dem_error(s["t"], s["K"], los)
    assert np.allclose(fit.velocity_mm_yr, s["v_true"], atol=0.3)
    assert np.allclose(fit.dh_m, s["dh_true"], atol=4.0)
    assert np.all(fit.sigma_dh > 0)


def test_DEM오차를_빼면_속도가_틀어진다():
    """따로 풀면 안 되는 이유 — K 를 빼고 풀면 속도에 샌다."""
    s = _synth(n_pts=30, seed=4)
    los = s["v_true"][:, None] * s["t"][None, :] + s["dh_true"][:, None] * s["K"][None, :]
    both = mt.solve_velocity_dem_error(s["t"], s["K"], los)
    only_v = mt.solve_velocity_dem_error(s["t"], np.zeros_like(s["K"]), los)
    e_both = np.abs(both.velocity_mm_yr - s["v_true"]).mean()
    e_only = np.abs(only_v.velocity_mm_yr - s["v_true"]).mean()
    assert e_both < e_only


def test_잡음이_크면_감마가_떨어진다():
    s = _synth(n_pts=20, seed=5)
    rng = np.random.default_rng(6)
    clean = s["v_true"][:, None] * s["t"][None, :]
    noisy = clean + rng.normal(0, 12.0, clean.shape)
    g1 = mt.solve_velocity_dem_error(s["t"], s["K"], clean).gamma
    g2 = mt.solve_velocity_dem_error(s["t"], s["K"], noisy).gamma
    assert np.median(g1) > np.median(g2)


def test_K는_수직기선에_비례한다():
    k = mt.height_phase_factor([0.0, 100.0, -100.0], 888_000.0, 36.25)
    assert k[0] == pytest.approx(0.0)
    assert k[1] == pytest.approx(-k[2])
    assert 0.1 < abs(k[1]) < 0.3          # Sentinel-1 은 0.19 mm/m 언저리


def test_입사각이_비물리면_K는_0():
    assert np.allclose(mt.height_phase_factor([100.0], 888_000.0, 0.0), 0.0)
    assert np.allclose(mt.height_phase_factor([100.0], np.nan, 36.0), 0.0)


# ── ④ APS ─────────────────────────────────────────────────────────────────
def test_APS_는_매끄러운_부분만_집는다():
    """공간적으로 매끄러운 면 + 점별 잡음 → 저역통과가 면을 되찾아야 한다."""
    rng = np.random.default_rng(7)
    xy = rng.uniform(0, 1000, (300, 2))
    smooth = (xy[:, 0] * 0.01)[:, None] * np.ones((1, 4))
    noise = rng.normal(0, 5.0, (300, 4))
    got = mt.aps_estimate(smooth + noise, xy, radius_m=150.0)
    assert np.std(got - smooth) < np.std(noise)


def test_APS_제거_반복이_잔차를_줄인다():
    s = _synth(n_pts=120, n_ep=40, seed=8)
    out = mt.run(s["los"], s["t"], s["bperp"], s["xy"],
                 slant_range_m=s["R"], incidence_deg=s["inc"],
                 aps_radius_m=250.0, n_iter=2)
    r0 = out["iterations"][0]["resid_rms_mm"]
    r2 = out["iterations"][-1]["resid_rms_mm"]
    assert r2 < r0
    assert out["iterations"][-1]["gamma_median"] > out["iterations"][0]["gamma_median"]


def test_APS_를_빼면_속도를_더_잘_되찾는다():
    """단, **속도장이 공간적으로 매끄러울 때**다 — 실제 교량·지반이 그렇다.

    점마다 속도가 제각각이면 공간 필터가 도울 수 없다(이웃이 알려 줄 것이 없으니까).
    구조물은 이웃한 점이 함께 움직이므로 이웃이 정보를 갖는다. 대기는 시점마다
    제멋대로라 시간 고역통과로 갈라진다 — 그 둘을 나눌 수 있다는 것이 요점이다.
    """
    rng = np.random.default_rng(9)
    n_pts, n_ep = 200, 40
    t = np.linspace(0.0, 7.0, n_ep)
    bperp = rng.uniform(-120, 120, n_ep)
    R, inc = 888_000.0, 36.25
    K = mt.height_phase_factor(bperp, R, inc)
    xy = rng.uniform(0, 1200, (n_pts, 2))
    v_true = 3.0 * np.sin(xy[:, 0] / 600.0)          # 공간적으로 매끄러운 속도장
    aps = np.zeros((n_pts, n_ep))
    for k in range(n_ep):
        g = rng.normal(0, 1, 2)
        aps[:, k] = 8.0 * (g[0] * xy[:, 0] / 1200 + g[1] * xy[:, 1] / 1200)
    los = v_true[:, None] * t[None, :] + aps + rng.normal(0, 1.0, (n_pts, n_ep))

    raw = mt.solve_velocity_dem_error(t, K, los)
    out = mt.run(los, t, bperp, xy, slant_range_m=R, incidence_deg=inc,
                 aps_radius_m=250.0, n_iter=2, aps_source_frac=0.7)
    e_raw = np.abs(raw.velocity_mm_yr - v_true).mean()
    e_fix = np.abs(out["fit"].velocity_mm_yr - v_true).mean()
    assert e_fix < e_raw


# ── 전체 ──────────────────────────────────────────────────────────────────
def test_진폭이_없으면_ADI_를_지어내지_않는다():
    s = _synth(n_pts=30, n_ep=30, seed=10)
    out = mt.run(s["los"], s["t"], s["bperp"], s["xy"],
                 slant_range_m=s["R"], incidence_deg=s["inc"], n_iter=1)
    assert np.all(np.isnan(out["adi"]))
    assert "진폭이 없어" in out["adi_note"]
    assert out["n_kept"] <= out["n_points"]


def test_진폭을_주면_ADI_로도_거른다():
    s = _synth(n_pts=30, n_ep=30, seed=11)
    rng = np.random.default_rng(12)
    amp = np.abs(rng.normal(10, 0.2, (30, 30)))
    amp[:5] = np.abs(rng.normal(10, 9.0, (5, 30)))        # 불안정한 점 5개
    out = mt.run(s["los"], s["t"], s["bperp"], s["xy"],
                 slant_range_m=s["R"], incidence_deg=s["inc"],
                 amplitude=amp, adi_max=0.25, gamma_min=0.0, n_iter=1)
    assert not out["ps_mask"][:5].any()
    assert out["ps_mask"][5:].all()


def test_sigma_dh_가_형하고보다_크면_그대로_보고된다():
    """Sentinel-1 의 한계 — 숨기지 않고 σ 를 돌려준다."""
    s = _synth(n_pts=40, n_ep=51, seed=13)
    rng = np.random.default_rng(14)
    los = s["dh_true"][:, None] * s["K"][None, :] + rng.normal(0, 20.0, (40, 51))
    fit = mt.solve_velocity_dem_error(s["t"], s["K"], los)
    assert np.median(fit.sigma_dh) > 8.0


# ── SARPROZ 식 변수 묶음 — 열팽창 · ASI · 기선표 ─────────────────────────────
def test_열팽창계수를_되찾는다():
    """SARPROZ 의 thermal dilation — 보고서가 mm/°C 를 직접 재 놓아 눈금을 맞댈 수 있다."""
    rng = np.random.default_rng(20)
    n_ep = 60
    t = np.linspace(0, 5, n_ep)
    T = 12.0 + 14.0 * np.sin(2 * np.pi * (t - 0.3))          # 계절 기온
    bperp = rng.uniform(-120, 120, n_ep)
    K = mt.height_phase_factor(bperp, 888_000.0, 36.25)
    a_true = np.array([-1.46, -1.35, 0.0, 2.2])              # 보고서 EM_01·EM_02 값
    v_true = np.array([0.5, -1.0, 0.0, 3.0])
    los = (v_true[:, None] * t[None, :]
           + a_true[:, None] * (T - T.mean())[None, :]
           + rng.normal(0, 0.5, (4, n_ep)))
    fit = mt.solve_velocity_dem_error(t, K, los, temperature_C=T)
    assert np.allclose(fit.thermal_mm_per_C, a_true, atol=0.1)
    assert np.allclose(fit.velocity_mm_yr, v_true, atol=0.3)
    assert np.all(fit.sigma_thermal > 0)


def test_온도를_안주면_열팽창은_None():
    s = _synth(n_pts=5, n_ep=30, seed=21)
    fit = mt.solve_velocity_dem_error(s["t"], s["K"], s["los"])
    assert fit.thermal_mm_per_C is None
    assert fit.sigma_thermal is None


def test_열팽창을_빼면_속도로_샌다():
    """온도 항을 안 넣으면 계절 신축이 속도 추정으로 흘러든다."""
    rng = np.random.default_rng(22)
    t = np.linspace(0, 3.2, 40)                     # 정수 년이 아니라 계절이 안 상쇄된다
    T = 12.0 + 14.0 * np.sin(2 * np.pi * (t - 0.3))
    K = mt.height_phase_factor(rng.uniform(-100, 100, 40), 888_000.0, 36.25)
    v_true = np.zeros(6)
    a_true = rng.uniform(-2, 2, 6)
    los = v_true[:, None] * t[None, :] + a_true[:, None] * (T - T.mean())[None, :]
    with_T = mt.solve_velocity_dem_error(t, K, los, temperature_C=T)
    without = mt.solve_velocity_dem_error(t, K, los)
    assert np.abs(with_T.velocity_mm_yr).max() < np.abs(without.velocity_mm_yr).max()


def test_ASI_는_ADI_의_여집합():
    adi = np.array([0.1, 0.4, 0.9])
    assert np.allclose(mt.amplitude_stability_index(adi), 1.0 - adi)


def test_기선표는_마스터를_표시한다():
    dates = ["20220330", "20180619", "20180806"]
    days = np.array([0.0, -1380.0, -1332.0])
    bperp = np.array([0.0, -41.2, -54.7])
    tb = mt.baseline_table(dates, days, bperp, master="20220330")
    assert tb["n_epochs"] == 3
    assert sum(r["is_master"] for r in tb["epochs"]) == 1
    assert tb["bperp_span_m"] == pytest.approx(54.7, abs=0.1)


def test_높이모호도는_기선이_클수록_작다():
    a = mt.height_ambiguity_m(50.0, 888_000.0, 36.25)
    b = mt.height_ambiguity_m(150.0, 888_000.0, 36.25)
    assert b < a
    assert mt.height_ambiguity_m(0.0, 888_000.0, 36.25) == float("inf")


def test_run_은_온도를_받아_열팽창을_같이_낸다():
    s = _synth(n_pts=40, n_ep=40, seed=23)
    T = 12.0 + 14.0 * np.sin(2 * np.pi * (s["t"] - 0.3))
    out = mt.run(s["los"], s["t"], s["bperp"], s["xy"],
                 slant_range_m=s["R"], incidence_deg=s["inc"],
                 temperature_C=T, n_iter=1)
    assert out["has_thermal"]
    assert out["fit"].thermal_mm_per_C is not None
    assert out["asi"].shape == out["adi"].shape
