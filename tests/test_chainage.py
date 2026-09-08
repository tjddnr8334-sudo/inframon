"""교축 프로파일 — 투영·선별·구간집계와 "색띠로 실어도 되는가" 게이트."""

from __future__ import annotations

import h5py
import numpy as np
import pytest

from inframon.insar.chainage import (MIN_COVERAGE, MIN_SNR, ChainageProfile,
                                     build_profile)

# 동서로 뻗은 100m 데크(위도 36.45). 경도 1도 ≈ 89.4km 이므로 100m ≈ 0.001119도.
DECK = [(36.45, 126.80), (36.45, 126.801119)]


def _track(tmp_path, *, n=40, coh=0.8, seed=0, spread_m=5.0, vel=None):
    """합성 트랙 h5 — 데크선 위에 점을 흩뿌린다."""
    rng = np.random.default_rng(seed)
    t = rng.uniform(0.0, 1.0, n)
    lon = 126.80 + t * 0.001119
    lat = 36.45 + rng.normal(0.0, spread_m / 111_000.0, n)
    v = np.asarray(vel(t) if callable(vel) else rng.normal(0.0, 0.1, n), float)
    p = tmp_path / "track.h5"
    with h5py.File(p, "w") as f:
        f["pixel_lonlat"] = np.column_stack([lon, lat])
        f["temp_coh"] = np.full(n, float(coh))
        f["los_velocity_mm_yr"] = v
        f["incidenceAngle"] = np.full(n, 39.0)
        f.attrs["HEADING"] = -13.0
    return p


def test_투영으로_교축거리와_직각거리가_나온다(tmp_path):
    p = build_profile(_track(tmp_path), DECK, bin_m=25.0, min_quality=0.5,
                      correct_shift=False)
    assert p.deck_length_m == pytest.approx(100.0, abs=3.0)
    assert 0.0 <= p.chainage_m.min() and p.chainage_m.max() <= p.deck_length_m + 1e-6
    assert np.abs(p.offset_m).max() < 30.0


def test_직각거리_부호로_데크_좌우가_갈린다(tmp_path):
    p = build_profile(_track(tmp_path, spread_m=8.0), DECK, bin_m=25.0,
                      min_quality=0.5, correct_shift=False)
    assert (p.offset_m > 0).any() and (p.offset_m < 0).any()


def test_품질_미달_점은_선별에서_빠진다(tmp_path):
    p = build_profile(_track(tmp_path, coh=0.3), DECK, bin_m=25.0, min_quality=0.7,
                      correct_shift=False)
    assert p.selected.sum() == 0
    assert p.coverage() == 0.0


def test_데크폭_밖의_지반점은_교량_위가_아니다(tmp_path):
    """추출 버퍼가 폭보다 넓으면 지반 점이 섞인다 — max_offset_m 이 그것을 자른다."""
    tr = _track(tmp_path, spread_m=40.0)
    wide = build_profile(tr, DECK, bin_m=25.0, min_quality=0.5, correct_shift=False)
    narrow = build_profile(tr, DECK, bin_m=25.0, min_quality=0.5, correct_shift=False,
                           max_offset_m=10.0)
    assert narrow.selected.sum() < wide.selected.sum()
    assert np.abs(narrow.offset_m[narrow.selected]).max() <= 10.0


def test_결측구간이_gaps_로_보고된다(tmp_path):
    """교축 앞쪽 절반에만 점이 있으면 뒤쪽이 결측으로 잡혀야 한다."""
    tr = _track(tmp_path, n=30, seed=1)
    with h5py.File(tr, "a") as f:
        ll = np.asarray(f["pixel_lonlat"][()], float)
        ll[:, 0] = 126.80 + (ll[:, 0] - 126.80) * 0.4      # 앞 40% 로 모은다
        f["pixel_lonlat"][...] = ll
    p = build_profile(tr, DECK, bin_m=20.0, min_quality=0.5, correct_shift=False)
    assert p.coverage() < 1.0
    assert p.gaps(), "점이 없는 교축 구간은 결측으로 보고해야 한다"


def test_잡음만_있으면_색띠_게이트를_통과하지_못한다(tmp_path):
    """구간간 차이가 구간내 잡음과 구별되지 않으면 3D 색띠로 실으면 안 된다."""
    rng = np.random.default_rng(7)
    p = build_profile(_track(tmp_path, n=60, vel=lambda t: rng.normal(0, 1.0, t.size)),
                      DECK, bin_m=20.0, min_quality=0.5, correct_shift=False)
    ok, why = p.is_publishable()
    assert not ok
    assert p.signal_to_noise() < MIN_SNR
    assert "σ_b/σ_w" in why


def test_종방향_구조가_뚜렷하면_게이트를_통과한다(tmp_path):
    """구간마다 값이 크게 다르고 구간 안은 조용하면 프로파일이 구조를 담는다."""
    p = build_profile(_track(tmp_path, n=80, spread_m=3.0, seed=3,
                             vel=lambda t: 20.0 * t), DECK, bin_m=20.0,
                      min_quality=0.5, correct_shift=False)
    ok, why = p.is_publishable()
    assert ok, why
    assert p.coverage() >= MIN_COVERAGE
    assert p.signal_to_noise() >= MIN_SNR


def test_점이_없으면_판단_불가로_막는다(tmp_path):
    p = build_profile(_track(tmp_path, n=3, coh=0.9), DECK, bin_m=20.0,
                      min_quality=0.5, correct_shift=False)
    ok, _ = p.is_publishable()
    assert not ok


def test_쉬프트_보정이_좌표를_옮기고_근거를_남긴다(tmp_path):
    tr = _track(tmp_path, n=30, seed=5)
    off = build_profile(tr, DECK, bin_m=25.0, min_quality=0.5, correct_shift=False)
    on = build_profile(tr, DECK, bin_m=25.0, min_quality=0.5, correct_shift=True,
                       bridge_height_m=10.0, bridge_width_m=20.0)
    assert on.shift and on.shift["applied"] is True
    assert on.shift["mean_abs_m"] > 0.0
    assert not np.allclose(off.chainage_m, on.chainage_m)


def test_품질값이_없으면_선별_불가를_알린다(tmp_path):
    p = tmp_path / "bare.h5"
    with h5py.File(p, "w") as f:
        f["pixel_lonlat"] = np.array([[126.80, 36.45], [126.801, 36.45]])
        f["los_velocity_mm_yr"] = np.array([0.1, 0.2])
    with pytest.raises(ValueError, match="품질값"):
        build_profile(p, DECK, correct_shift=False)


def test_describe_가_커버리지와_결측을_함께_말한다(tmp_path):
    p = build_profile(_track(tmp_path), DECK, bin_m=25.0, min_quality=0.5,
                      correct_shift=False)
    d = p.describe()
    assert "커버리지" in d and "교축" in d
    assert isinstance(p, ChainageProfile)


# ── 2단 선별 + 노이즈 제거 ────────────────────────────────────────────────
from inframon.insar.chainage import remove_noise, select_points  # noqa: E402


def _tr(n=30, seed=0, adi=True):
    rng = np.random.default_rng(seed)
    tr = {"lonlat": np.zeros((n, 2)), "coh": rng.uniform(0.3, 0.95, n)}
    if adi:
        tr["amplitude_dispersion"] = rng.uniform(0.1, 0.8, n)
    return tr


def test_엄격은_ADI_0_25_완화는_ADI_0_4_와_γ_0_6():
    tr = _tr()
    s, how_s = select_points(tr, mode="strict")
    r, how_r = select_points(tr, mode="relaxed")
    assert how_s == "ADI ≤ 0.25" and "ADI ≤ 0.4" in how_r and "γ_temp ≥ 0.6" in how_r
    assert np.array_equal(s, tr["amplitude_dispersion"] <= 0.25)
    assert np.array_equal(r, (tr["amplitude_dispersion"] <= 0.4) & (tr["coh"] >= 0.6))


def test_ADI_없으면_γ만_쓰고_그_사실을_남긴다():
    tr = _tr(adi=False)
    r, how = select_points(tr, mode="relaxed")
    assert "ADI 없음" in how
    assert np.array_equal(r, tr["coh"] >= 0.6)
    s, how_s = select_points(tr, mode="strict")
    assert "ADI 없음" in how_s and np.array_equal(s, tr["coh"] >= 0.8)


def test_노이즈_제거는_이웃_대비_튀는_점을_뺀다():
    st = np.linspace(0, 100, 21)
    val = np.zeros(21)
    val[10] = 50.0                     # 이웃은 전부 0인데 혼자 50
    sel = np.ones(21, bool)
    keep, noisy = remove_noise(sel, st, val, None, bin_m=10.0)
    assert noisy[10] and not keep[10]
    assert keep.sum() == 20


def test_노이즈_제거는_고립점을_몰지_않는다():
    """이웃이 3점 미만이면 판단하지 않는다 — 점 하나뿐인 구간을 노이즈로 몰면 결측만 는다."""
    st = np.array([0.0, 50.0, 100.0])
    val = np.array([0.0, 50.0, 0.0])
    keep, noisy = remove_noise(np.ones(3, bool), st, val, None, bin_m=10.0)
    assert not noisy.any()


def test_노이즈_제거_MAD_하한이_있다():
    """이웃 값이 우연히 똑같으면 MAD≈0 → 멀쩡한 점의 z 가 폭발한다. 하한으로 막는다."""
    st = np.linspace(0, 40, 9)
    val = np.array([1.0, 1.0, 1.0, 1.0, 1.3, 1.0, 1.0, 1.0, 5.0])
    keep, noisy = remove_noise(np.ones(9, bool), st, val, None, bin_m=10.0)
    assert not noisy[4], "0.3 차이는 노이즈가 아니다"


def test_build_profile_은_프로젝트_h5_도_읽는다(tmp_path):
    import h5py
    p = tmp_path / "proj.h5"
    rng = np.random.default_rng(2)
    n = 30
    t = rng.uniform(0, 1, n)
    with h5py.File(p, "w") as f:
        g = f.create_group("insar")
        g["xyz"] = np.column_stack([126.80 + t * 0.001119, np.full(n, 36.45), np.zeros(n)])
        g["temporal_coherence"] = np.full(n, 0.8)
        g["velocity_mm_yr"] = rng.normal(0, .1, n)
        g["incidence_deg"] = np.full(n, 39.0)
    prof = build_profile(p, DECK, bin_m=20.0, correct_shift=False)
    assert prof.selected.sum() > 0
    assert "ADI 없음" in prof.meta["selection"]


# ── 속도 95% CI · 교대 기준점 ─────────────────────────────────────────────
from inframon.insar.chainage import (_epoch_days, reference_to_abutment,  # noqa: E402
                                     velocity_ci)


def test_velocity_ci_는_선형_추세와_불확실도를_되찾는다():
    days = np.arange(0, 365 * 5, 12, dtype=float)
    rng = np.random.default_rng(0)
    los = 2.0 * days[None, :] / 365.25 + rng.normal(0, .5, (5, days.size))
    v, ci = velocity_ci(los, days)
    assert np.allclose(v, 2.0, atol=.15)
    assert (ci > 0).all() and (ci < .3).all()


def test_시점이_적으면_CI_가_넓다():
    """25시점/4.8년(청양교)은 201시점/8.8년(정자교)보다 CI 가 훨씬 넓다 — 영상 수 문제."""
    rng = np.random.default_rng(1)
    few = np.linspace(0, 4.8 * 365.25, 25)
    many = np.linspace(0, 8.8 * 365.25, 201)
    _, ci_few = velocity_ci(rng.normal(0, 5, (1, 25)), few)
    _, ci_many = velocity_ci(rng.normal(0, 5, (1, 201)), many)
    assert ci_few[0] > 3 * ci_many[0]


def test_epoch_days_는_정수_YYYYMMDD_도_날짜로_푼다():
    d = _epoch_days({"dates": np.array([20201217, 20180713, 20180911], np.int32)})
    assert d[1] == 0.0 and d[0] > d[2] > 0        # 첫 시점 기준, 정렬 무관


def test_교대_기준점은_양끝_점_중앙값을_뺀다():
    st = np.array([1.0, 3.0, 50.0, 97.0, 99.0])
    los = np.ones((5, 4)) * np.array([[1, 2, 3, 4]])   # 모든 점 같은 드리프트
    los[2] += 10.0                                   # 중앙 점만 +10
    out, meta = reference_to_abutment(los, st, 100.0, zone_m=8.0)
    assert meta["applied"] and meta["n_ref"] == 4
    assert np.allclose(out[0], 0.0) and np.allclose(out[2], 10.0)


def test_교대_구역에_점이_없으면_기준점을_안_건드린다():
    st = np.array([40.0, 50.0, 60.0])
    los = np.ones((3, 3))
    out, meta = reference_to_abutment(los, st, 100.0)
    assert not meta["applied"] and np.array_equal(out, los)
    assert "2개 미만" in meta["reason"]


# ── 쉬프트 δh: 잔차고도 집단평균 우선 ────────────────────────────────────
from inframon.insar.chainage import resolve_shift_dh  # noqa: E402


def _rh_track(on_dh, off_dh, sigma=3.0, n=20):
    st = np.linspace(0, 100, n)
    of = np.where(np.arange(n) % 2 == 0, 2.0, 40.0)        # 짝수=교면 위, 홀수=밖
    rh = np.where(of < 10, on_dh, off_dh) + np.random.default_rng(0).normal(0, sigma, n)
    return {"residual_height_m": rh, "residual_height_sigma_m": np.full(n, sigma)}, st, of


def test_잔차고도가_있으면_집단평균_차이를_δh로_쓴다():
    tr, st, of = _rh_track(10.0, 0.0)
    dh, meta = resolve_shift_dh(tr, st, of, 100.0, fallback_m=6.0, half_width_m=10.0)
    assert meta["dh_source"].startswith("잔차고도")
    assert abs(dh - 10.0) < 3.0 and meta["z"] > 1.0
    assert meta["fallback_m"] == 6.0


def test_잔차고도가_없으면_형하고_가정():
    dh, meta = resolve_shift_dh({}, np.zeros(3), np.zeros(3), 100.0, fallback_m=6.0,
                                half_width_m=10.0)
    assert dh == 6.0 and meta["dh_source"] == "형하고 가정"


def test_집단_차이가_유의하지_않으면_가정을_유지하고_사유를_남긴다():
    tr, st, of = _rh_track(0.5, 0.0, sigma=6.0)
    dh, meta = resolve_shift_dh(tr, st, of, 100.0, fallback_m=6.0, half_width_m=10.0)
    assert dh == 6.0 and "유지" in meta["reason"]


def test_점별_잔차고도는_쓰지_않는다():
    """σ 20 m 급 점별 값을 쓰면 쉬프트 잡음이 화소보다 커진다 — 항상 스칼라 δh."""
    tr, st, of = _rh_track(10.0, 0.0, sigma=20.0)
    dh, _ = resolve_shift_dh(tr, st, of, 100.0, fallback_m=6.0, half_width_m=10.0)
    assert isinstance(dh, float)
