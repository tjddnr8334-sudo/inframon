"""StaMPS 네이티브 출력 읽기 — 가짜 처리폴더를 만들어 되찾는지 본다.

StaMPS 를 깔지 않고도 읽기 쪽을 붙잡아 두려면 .mat 을 직접 만들어 넣어 보는 수밖에
없다. 참값을 넣고 그 값이 돌아오는지, 그리고 **어긋나면 조용히 자르지 않고 멈추는지**
를 같이 확인한다.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from inframon.insar import stamps_io as si


def _write(folder, name, **arrays):
    from scipy.io import savemat

    savemat(str(folder / name), arrays)


def make_stack(tmp_path, n=25, m=12, *, bp2=True, la2=True, pm2=True,
               hgt2=True, parms=None, master_idx=0):
    """StaMPS 처리폴더 흉내 — ps2/phuw2/bp2/la2/pm2/hgt2."""
    rng = np.random.default_rng(0)
    d = tmp_path / "INSAR_20220330"
    d.mkdir()
    days = [date(2022, 1, 1).toordinal() + 365 + 12 * k for k in range(m)]
    datenum = np.asarray(days, float) + (si.MATLAB_EPOCH_OFFSET
                                         - date(1970, 1, 1).toordinal())
    lonlat = np.column_stack([126.86 + rng.normal(0, 1e-3, n),
                              37.57 + rng.normal(0, 1e-3, n)])
    ph = rng.normal(0, 1.0, (n, m))
    bperp = rng.uniform(-120, 120, m)
    _write(d, "ps2.mat", lonlat=lonlat, day=datenum,
           master_day=datenum[master_idx], bperp=bperp, mean_incidence=39.4)
    _write(d, "phuw2.mat", ph_uw=ph)
    if bp2:
        _write(d, "bp2.mat", bperp_mat=np.tile(bperp, (n, 1)))
    if la2:
        _write(d, "la2.mat", la=np.full(n, np.radians(39.4)))
    if pm2:
        _write(d, "pm2.mat", coh_ps=rng.uniform(.3, .95, n))
    if hgt2:
        _write(d, "hgt2.mat", hgt=rng.uniform(0, 40, n))
    if parms is not None:
        _write(d, "parms.mat", **{"lambda": parms})
    return d, dict(lonlat=lonlat, ph=ph, bperp=bperp, days=days, n=n, m=m)


# ── 날짜 ───────────────────────────────────────────────────────────────────
def test_datenum_을_날짜로_되돌린다():
    dn = si.MATLAB_EPOCH_OFFSET                       # 1970-01-01
    assert si.datenum_to_ymd(dn) == ["19700101"]


def test_이미_YYYYMMDD_면_그대로_둔다():
    assert si.datenum_to_ymd([20220330.0]) == ["20220330"]


# ── 위상 → 변위 ────────────────────────────────────────────────────────────
def test_위상을_변위로_바꾼다_부호규약():
    """d = −λ/4π·φ · 1000 — 양수 = 위성 접근. 리포 전체와 같은 규약."""
    lam = si.RADAR_WAVELENGTH_M
    got = si.phase_to_los_mm(np.array([[0.0, 2 * np.pi, -2 * np.pi]]), lam)
    assert got[0, 0] == pytest.approx(0.0)
    assert got[0, 1] == pytest.approx(-lam / 2 * 1000.0)      # 한 바퀴 = λ/2
    assert got[0, 2] == pytest.approx(+lam / 2 * 1000.0)


# ── 읽기 ───────────────────────────────────────────────────────────────────
def test_처리폴더를_읽어_되찾는다(tmp_path):
    d, t = make_stack(tmp_path)
    s = si.read_stamps(d)
    assert s.n_points == t["n"] and s.n_epochs == t["m"]
    assert np.allclose(s.lonlat, t["lonlat"])
    assert np.allclose(s.los_mm, si.phase_to_los_mm(t["ph"], s.wavelength_m))
    assert s.master == "20230101"
    assert np.allclose(s.incidence_deg, 39.4, atol=1e-3)


def test_점별_수직기선이_있으면_그것을_쓴다(tmp_path):
    d, t = make_stack(tmp_path, bp2=True)
    s = si.read_stamps(d)
    assert s.bperp_m.shape == (t["n"], t["m"])
    assert "bp2.mat" in s.sources["bperp"]


def test_bp2_가_없으면_ps2_의_기선을_쓴다(tmp_path):
    d, t = make_stack(tmp_path, bp2=False)
    s = si.read_stamps(d)
    assert s.bperp_m.shape == (t["m"],)
    assert np.allclose(s.bperp_m, t["bperp"])


def test_parms_의_파장이_기본값보다_우선한다(tmp_path):
    d, _ = make_stack(tmp_path, parms=0.031)          # X-band
    s = si.read_stamps(d)
    assert s.wavelength_m == pytest.approx(0.031)
    assert s.sources["lambda"] == "parms.mat"


def test_없는_파일은_사유를_적고_기본값으로_간다(tmp_path):
    d, t = make_stack(tmp_path, la2=False, pm2=False, hgt2=False)
    s = si.read_stamps(d)
    assert s.height_m is None
    assert np.allclose(s.coherence, 1.0)
    assert "없음" in s.sources["coherence"]
    assert "mean_incidence" in s.sources["incidence"]


def test_PATCH_폴더도_찾아간다(tmp_path):
    d, _ = make_stack(tmp_path)
    root = tmp_path / "proc"
    root.mkdir()
    d.rename(root / "PATCH_1")
    s = si.read_stamps(root)
    assert s.n_points > 0
    assert s.sources["dir"].endswith("PATCH_1")


# ── 어긋나면 멈춘다 ────────────────────────────────────────────────────────
def test_폴더가_아니면_이유를_말한다(tmp_path):
    with pytest.raises(si.StampsError, match="폴더가 없다"):
        si.read_stamps(tmp_path / "없는곳")


def test_ps2_가_없으면_이유를_말한다(tmp_path):
    (tmp_path / "빈곳").mkdir()
    with pytest.raises(si.StampsError, match="ps2.mat"):
        si.read_stamps(tmp_path / "빈곳")


def test_시점_수가_안_맞으면_자르지_않고_멈춘다(tmp_path):
    """조용히 잘라 맞추면 나중에 아무도 못 찾는다 — 멈추고 이유를 적는다."""
    d, t = make_stack(tmp_path)
    _write(d, "phuw2.mat", ph_uw=np.zeros((t["n"], t["m"] - 3)))
    with pytest.raises(si.StampsError, match="시점 수"):
        si.read_stamps(d)


def test_점_수가_안_맞으면_멈춘다(tmp_path):
    d, t = make_stack(tmp_path)
    _write(d, "phuw2.mat", ph_uw=np.zeros((t["n"] + 5, t["m"])))
    with pytest.raises(si.StampsError, match="점 수"):
        si.read_stamps(d)


# ── 트랙 H5 로 내보내기 ────────────────────────────────────────────────────
def test_트랙_H5_는_기존_형식과_같다(tmp_path):
    import h5py

    d, t = make_stack(tmp_path)
    s = si.read_stamps(d)
    p = si.write_track_h5(s, tmp_path / "track_stamps.h5")
    with h5py.File(p, "r") as h:
        assert set(("pixel_lonlat", "los_mm", "coh", "incidenceAngle",
                    "epochs")) <= set(h)
        assert h["los_mm"].shape == (t["n"], t["m"])
        assert h["bperp_m"].shape == (t["m"],)          # 점별이어도 시점 평균을 둔다
        assert "StaMPS" in h.attrs["source"]
        assert h.attrs["master"] == "20230101"


def test_트랙_H5_를_기존_읽기가_받아들인다(tmp_path):
    """SNAP·SARvey 경로와 같은 형식이어야 뒤 단계가 그대로 돈다."""
    from inframon.insar.track_reader import read_track_h5

    d, t = make_stack(tmp_path)
    p = si.write_track_h5(si.read_stamps(d), tmp_path / "track_stamps.h5")
    td = read_track_h5(p)
    assert td.los.shape == (t["n"], t["m"])
    assert td.lonlat.shape == (t["n"], 2)


def test_슬랜트_거리를_읽는다(tmp_path):
    """잔차고도 K = 1000·B⊥/(R·sinθ) 에 R 이 필요하다 — 없으면 없다고 적는다."""
    from scipy.io import savemat

    d, t = make_stack(tmp_path)
    assert si.read_stamps(d).slant_range_m is None            # mean_range 없음
    ps = si._load_mat(d / "ps2.mat")
    ps["mean_range"] = 880_000.0
    savemat(str(d / "ps2.mat"), ps)
    s = si.read_stamps(d)
    assert s.slant_range_m == pytest.approx(880_000.0)
    assert s.sources["slant_range"] == "ps2.mat mean_range"
