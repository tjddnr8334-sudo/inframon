"""HyP3 클라우드 백엔드 — 이름 파싱·스타 계획·GeoTIFF→Track H5 변환·오케스트레이션.

네트워크(_search_bursts/_run_jobs)는 전부 monkeypatch — 합성 GeoTIFF 로 오프라인 검증한다.
변환 결과는 track_reader.read_track_h5 를 그대로 통과해야 한다(계약 호환이 완료 기준).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from inframon.insar import hyp3_backend as hb
from inframon.insar.hyp3_backend import (
    Hyp3Error,
    Hyp3PairProduct,
    find_product_dirs,
    infer_ref_date,
    plan_star_pairs,
    products_to_track_h5,
    two_dates,
)
from inframon.insar.snap_backend import WAVELENGTH_M

SCALE = -WAVELENGTH_M / (4.0 * math.pi) * 1000.0   # phase(rad) → mm


# ── 이름 파싱 ──
def test_two_dates_burst_and_gamma_names():
    assert two_dates("S1_136231_IW2_20200604_20200616_VV_INT80_ABCD") == ("20200604", "20200616")
    assert two_dates("S1AA_20200616T092000_20200604T092000_VVP012_INT80_G_ueF_1234") == \
        ("20200604", "20200616")                     # 역순이어도 오름차순 정규화


def test_two_dates_rejects_single_date():
    with pytest.raises(Hyp3Error):
        two_dates("S1A_IW_SLC_20240107_only")


# ── 스타 계획(순수) ──
def test_plan_star_pairs_middle_ref_and_order():
    series = [(f"2024010{d}", f"g{d}") for d in range(1, 6)]     # 5장
    ref_g, pairs = plan_star_pairs(series, count=5)
    assert ref_g == "g3"                                          # 중간 날짜 기준
    assert len(pairs) == 4
    for g1, g2 in pairs:                                          # HyP3: 이른 쪽이 첫 인자
        d = {g: dt for dt, g in series}
        assert d[g1] < d[g2]


def test_plan_star_pairs_count_limits_to_recent():
    series = [(f"202401{d:02d}", f"g{d}") for d in range(1, 11)]  # 10장
    _, pairs = plan_star_pairs(series, count=4)
    assert len(pairs) == 3                                        # 기준 제외 3쌍


def test_plan_star_pairs_needs_two():
    with pytest.raises(Hyp3Error):
        plan_star_pairs([("20240101", "g1")])


def test_infer_ref_date_star_vs_chain():
    star = [Hyp3PairProduct(Path("."), "20240101", "20240113"),
            Hyp3PairProduct(Path("."), "20240113", "20240125")]
    assert infer_ref_date(star) == "20240113"
    chain = [Hyp3PairProduct(Path("."), "20240101", "20240113"),
             Hyp3PairProduct(Path("."), "20240125", "20240206")]
    with pytest.raises(Hyp3Error):
        infer_ref_date(chain)


# ── 합성 HyP3 산출물 ──
def _write_product(root: Path, d1: str, d2: str, phase: float, *, lon0=127.10, lat0=37.33,
                   coh=0.8, lv_theta=0.87, dem=123.0, px=0.001, size=20) -> Path:
    """HyP3 INSAR 산출물 폴더 모사 — unw_phase/corr/lv_theta/dem GeoTIFF 4종."""
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin

    name = f"S1_136231_IW2_{d1}_{d2}_VV_INT80_TEST"
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    tr = from_origin(lon0, lat0, px, px)
    for suffix, val in (("unw_phase", phase), ("corr", coh), ("lv_theta", lv_theta),
                        ("dem", dem)):
        with rasterio.open(pdir / f"{name}_{suffix}.tif", "w", driver="GTiff",
                           height=size, width=size, count=1, dtype="float32",
                           crs="EPSG:4326", transform=tr) as ds:
            ds.write(np.full((size, size), val, dtype="float32"), 1)
    return pdir


def test_find_product_dirs_nested(tmp_path):
    pytest.importorskip("rasterio")
    _write_product(tmp_path, "20240107", "20240119", 1.0)
    _write_product(tmp_path, "20240119", "20240131", 1.0)
    prods = find_product_dirs(tmp_path)
    assert len(prods) == 2
    assert prods[0].date1 == "20240107" and prods[0].date2 == "20240119"


def test_find_product_dirs_empty_raises(tmp_path):
    with pytest.raises(Hyp3Error):
        find_product_dirs(tmp_path)


# ── 변환기: GeoTIFF → Track H5 (계약 호환이 완료 기준) ──
def test_products_to_track_h5_contract_and_sign(tmp_path):
    pytest.importorskip("rasterio")
    h5py = pytest.importorskip("h5py")
    from inframon.insar.track_reader import read_track_h5

    lat_c, lon_c = 37.32, 127.11
    # 기준 20240119. 쌍1=(0107,0119): 보조(0107)가 이른 쪽 → 부호 반전 대상.
    p1 = _write_product(tmp_path, "20240107", "20240119", phase=+2.0)
    p2 = _write_product(tmp_path, "20240119", "20240131", phase=-1.0)
    prods = find_product_dirs(tmp_path)
    out = tmp_path / "track_hyp3.h5"
    n = products_to_track_h5(prods, out, lat=lat_c, lon=lon_c, radius_km=5.0)
    assert n > 0
    assert p1.exists() and p2.exists()

    with h5py.File(out, "r") as f:
        assert list(f["epochs"][()]) == [20240119, 20240107, 20240131]  # [기준, 보조…]
        los = f["los_mm"][()]
        assert np.allclose(los[:, 0], 0.0)                              # 기준일 변위 0
        assert np.allclose(los[:, 1], -2.0 * SCALE, atol=1e-3)          # (보조,기준) → 부호 반전
        assert np.allclose(los[:, 2], -1.0 * SCALE, atol=1e-3)
        assert np.allclose(f["incidenceAngle"][()], 90.0 - math.degrees(0.87), atol=1e-3)
        assert np.allclose(f["height"][()], 123.0)                       # 동봉 DEM → 점별 고도

    td = read_track_h5(out)                # 계약 리더 관통(시간순 재정렬 포함)
    assert [d.decode() for d in td.date_labels] == ["20240107", "20240119", "20240131"]
    assert td.height is not None and td.incidence is not None


def test_products_to_track_h5_low_coherence_raises(tmp_path):
    pytest.importorskip("rasterio")
    _write_product(tmp_path, "20240107", "20240119", 1.0, coh=0.05)     # 전부 coh_min 미달
    with pytest.raises(Hyp3Error):
        products_to_track_h5(find_product_dirs(tmp_path), tmp_path / "t.h5",
                             lat=37.32, lon=127.11)


def test_track_h5_imports_into_project_contract(tmp_path):
    pytest.importorskip("rasterio")
    pytest.importorskip("h5py")
    from inframon.contracts.io import ProjectStore
    from inframon.insar.track_reader import import_track_h5

    _write_product(tmp_path, "20240107", "20240119", 0.5)
    _write_product(tmp_path, "20240119", "20240131", 0.7)
    out = tmp_path / "track_hyp3.h5"
    n = products_to_track_h5(find_product_dirs(tmp_path), out, lat=37.32, lon=127.11,
                             radius_km=5.0)
    with ProjectStore(tmp_path / "project.h5", mode="w") as s:
        meta = import_track_h5(s, out)
    assert meta.n_points == n and meta.n_dates == 3
    assert meta.incidence_ds is not None                                # lv_theta 반영


# ── 오케스트레이션(네트워크 전부 모사) ──
def test_run_orchestrates_search_jobs_convert(tmp_path, monkeypatch):
    pytest.importorskip("rasterio")
    series = [("20240107", "B_0107"), ("20240119", "B_0119"), ("20240131", "B_0131")]
    monkeypatch.setattr(hb, "_search_bursts", lambda *a, **k: ("136231_IW2", series))

    def fake_run_jobs(pairs, out_dir, *, name, username, password, token):
        done = []
        date_of = {g: d for d, g in series}
        for g1, g2 in pairs:
            d1, d2 = sorted((date_of[g1], date_of[g2]))
            done.append(((g1, g2), _write_product(Path(out_dir), d1, d2, 1.0), None))
        return done

    monkeypatch.setattr(hb, "_run_jobs", fake_run_jobs)
    res = hb.run(37.32, 127.11, tmp_path / "prod", tmp_path / "track.h5", count=3)
    assert res.n_points > 0 and res.n_ok == 2 and res.n_fail == 0
    assert res.ref_date == "20240119" and res.burst_id == "136231_IW2"
    assert Path(res.track_h5).exists()


def test_run_reports_partial_failures(tmp_path, monkeypatch):
    pytest.importorskip("rasterio")
    series = [("20240107", "B_0107"), ("20240119", "B_0119"), ("20240131", "B_0131")]
    monkeypatch.setattr(hb, "_search_bursts", lambda *a, **k: ("136231_IW2", series))

    def fake_run_jobs(pairs, out_dir, *, name, username, password, token):
        date_of = {g: d for d, g in series}
        done = []
        for i, (g1, g2) in enumerate(pairs):
            d1, d2 = sorted((date_of[g1], date_of[g2]))
            if i == 0:
                done.append(((g1, g2), None, "HyP3 잡 실패: FAILED"))
            else:
                done.append(((g1, g2), _write_product(Path(out_dir), d1, d2, 1.0), None))
        return done

    monkeypatch.setattr(hb, "_run_jobs", fake_run_jobs)
    res = hb.run(37.32, 127.11, tmp_path / "prod", tmp_path / "track.h5", count=3)
    assert res.n_ok == 1 and res.n_fail == 1 and len(res.failures) == 1
    assert Path(res.track_h5).exists()                   # 성공 쌍만으로도 Track 생성


def test_run_no_bursts_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(hb, "_search_bursts", lambda *a, **k: (None, []))
    with pytest.raises(Hyp3Error):
        hb.run(37.32, 127.11, tmp_path, tmp_path / "t.h5")
