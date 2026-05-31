"""Track H5 투입 사전검증(preflight) — 실데이터 인제스트 게이트.

정상 파일은 ready, 각종 결함(누락·형상불일치·적은 점·날짜파싱·coherence·NaN)은 차단
오류 또는 경고로 잡되 **절대 예외를 내지 않는다**(리포트로 안전 처리).
"""

from __future__ import annotations

import h5py
import numpy as np

from inframon.insar.track_preflight import preflight_track_h5


def _write_track(path, *, lonlat=None, n_points=12, n_dates=6, epochs=None,
                 coh=None, los=None, height=False, crs=None):
    if lonlat is None:
        # 투영 좌표(EPSG:5179 풍) — 경위도로 오인되지 않게 큰 값
        lonlat = np.column_stack([300000 + np.arange(n_points) * 2.0,
                                  600000 + np.zeros(n_points)])
    if epochs is None:
        epochs = np.array([20200101, 20200113, 20200125, 20200206, 20200218, 20200302],
                          dtype=np.int32)[:n_dates]
    if coh is None:
        coh = np.full(n_points, 0.8, dtype=np.float32)
    if los is None:
        los = np.zeros((n_points, n_dates), dtype=np.float32)
    with h5py.File(path, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.asarray(lonlat, dtype=np.float64))
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=np.asarray(los, dtype=np.float32))
        f.create_dataset("coh", data=np.asarray(coh, dtype=np.float32))
        if height:
            f.create_dataset("height", data=np.zeros(len(lonlat), dtype=np.float32))
        if crs:
            f.attrs["crs"] = crs
    return path


def test_preflight_ready(tmp_path):
    p = _write_track(tmp_path / "good.h5", height=True, crs="EPSG:5179")
    rep = preflight_track_h5(p)
    assert rep.is_ready
    assert not rep.errors
    assert rep.n_points == 12 and rep.n_dates == 6
    assert rep.date_first == "20200101" and rep.date_last == "20200302"
    assert rep.has_height and rep.crs == "EPSG:5179"
    assert rep.to_dict()["is_ready"] is True


def test_preflight_missing_file(tmp_path):
    rep = preflight_track_h5(tmp_path / "nope.h5")
    assert not rep.is_ready
    assert any("파일이 없습니다" in e for e in rep.errors)


def test_preflight_missing_dataset(tmp_path):
    p = tmp_path / "nolos.h5"
    with h5py.File(p, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.zeros((5, 2)))
        f.create_dataset("epochs", data=np.array([20200101, 20200113], dtype=np.int32))
        f.create_dataset("coh", data=np.full(5, 0.8, dtype=np.float32))
    rep = preflight_track_h5(p)
    assert not rep.is_ready
    assert any("los_mm" in e for e in rep.errors)


def test_preflight_shape_mismatch(tmp_path):
    p = _write_track(tmp_path / "bad.h5", n_points=10, n_dates=4,
                     coh=np.full(7, 0.8, dtype=np.float32))   # coh 7 ≠ 10
    rep = preflight_track_h5(p)
    assert not rep.is_ready
    assert any("coherence 점수" in e for e in rep.errors)


def test_preflight_too_few_points(tmp_path):
    p = _write_track(tmp_path / "few.h5", n_points=1, n_dates=4,
                     lonlat=np.array([[300000.0, 600000.0]]),
                     coh=np.full(1, 0.8, dtype=np.float32),
                     los=np.zeros((1, 4), dtype=np.float32))
    rep = preflight_track_h5(p)
    assert not rep.is_ready
    assert any("측정점" in e for e in rep.errors)


def test_preflight_bad_epochs(tmp_path):
    p = _write_track(tmp_path / "baddate.h5", n_dates=3,
                     epochs=np.array([b"2020-01", b"xx", b"y"]))
    rep = preflight_track_h5(p)
    assert not rep.is_ready
    assert any("epochs" in e for e in rep.errors)


def test_preflight_coherence_out_of_range_warns(tmp_path):
    p = _write_track(tmp_path / "coh.h5", n_points=8, n_dates=4,
                     coh=np.full(8, 1.7, dtype=np.float32), height=True, crs="EPSG:5179")
    rep = preflight_track_h5(p)
    assert rep.is_ready                                  # 경고일 뿐 차단 아님
    assert any("coherence 가 [0,1]" in w for w in rep.warnings)


def test_preflight_los_nan_warns(tmp_path):
    los = np.zeros((8, 4), dtype=np.float32)
    los[0, 0] = np.nan
    p = _write_track(tmp_path / "nan.h5", n_points=8, n_dates=4, los=los,
                     height=True, crs="EPSG:5179")
    rep = preflight_track_h5(p)
    assert rep.is_ready
    assert rep.los_finite_frac < 1.0
    assert any("NaN/Inf" in w for w in rep.warnings)


def test_preflight_no_height_and_no_crs_warn(tmp_path):
    p = _write_track(tmp_path / "plain.h5")               # height/crs 없음
    rep = preflight_track_h5(p)
    assert rep.is_ready
    assert any("고도" in w for w in rep.warnings)
    assert any("CRS" in w for w in rep.warnings)


def test_preflight_detects_geographic_coords(tmp_path):
    # 경위도(작은 ptp) → looks_geographic
    lonlat = np.column_stack([127.05 + np.arange(10) * 1e-4, 36.5 + np.zeros(10)])
    p = _write_track(tmp_path / "geo.h5", n_points=10, n_dates=4, lonlat=lonlat)
    rep = preflight_track_h5(p)
    assert rep.looks_geographic


def test_preflight_corrupt_file_no_crash(tmp_path):
    p = tmp_path / "corrupt.h5"
    p.write_bytes(b"not an hdf5 file")
    rep = preflight_track_h5(p)
    assert not rep.is_ready
    assert rep.errors                                    # 예외 없이 오류로 보고
