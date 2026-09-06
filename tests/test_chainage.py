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
