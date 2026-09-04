"""곡선 교량 데크 호길이 station — 폴리라인 투영·주곡선 추정(직선 X정렬 한계 극복)."""

from __future__ import annotations

import numpy as np

from inframon.insar.deck_geometry import (
    deck_station,
    principal_curve_station,
    project_to_polyline,
)


def test_principal_curve_station_straight_matches_x_order():
    # 직선 데크: station 순서 = X 순서(하위호환)
    x = np.array([0.0, 30.0, 10.0, 20.0, 40.0])
    pts = np.column_stack([x, np.full_like(x, 500.0)])   # y 큰 값 → 미터로 취급
    st = principal_curve_station(pts)
    assert np.array_equal(np.argsort(st), np.argsort(x))


def test_principal_curve_station_semicircle_is_monotone_along_arc():
    # 반원 데크: X 는 왕복(비단조)이지만 호길이 station 은 호를 따라 단조 → X정렬은 틀림
    theta = np.linspace(0.0, np.pi, 40)
    R = 100.0
    pts = np.column_stack([R * np.cos(theta), R * np.sin(theta) + 300.0])  # 미터
    st = principal_curve_station(pts)
    order = np.argsort(st)
    # 호(theta) 순서 또는 그 역과 일치(양끝 어느 쪽에서 출발하든)
    assert np.array_equal(order, np.arange(40)) or np.array_equal(order, np.arange(40)[::-1])
    # X 정렬과는 명확히 다르다(곡선이므로)
    assert not np.array_equal(order, np.argsort(pts[:, 0]))


def test_project_to_polyline_station_and_offset():
    # ㄱ자(꺾인) 폴리라인: (0,0)->(0,50)->(50,50) [lat,lon] 근사 미터스케일 대신 소각도 lon/lat
    poly = [[37.0000, 127.0000], [37.0000, 127.0006], [37.0004, 127.0006]]
    # 데크 위 점(폴리라인 근처) + 약간의 수직 오프셋
    pts = np.array([[127.0000, 37.00005],   # 첫 세그먼트 시작 부근
                    [127.0006, 37.00005],   # 꺾임 부근
                    [127.0006, 37.00035]])  # 둘째 세그먼트 끝 부근
    st, off = project_to_polyline(pts, poly)
    assert st[0] < st[1] < st[2]                      # 데크를 따라 station 증가
    assert (off < 20.0).all()                         # 데크 근처(수직오프셋 작음, m)


def test_deck_station_prefers_polyline():
    poly = [[37.0, 127.0], [37.0, 127.001]]
    pts = np.array([[127.0002, 37.0], [127.0008, 37.0]])
    st = deck_station(pts, geometry_latlon=poly)
    assert st[0] < st[1]                              # 폴리라인 방향으로 station


def test_deck_station_fallback_no_polyline():
    pts = np.array([[0.0, 100.0], [5.0, 100.0], [10.0, 100.0]])  # 미터
    st = deck_station(pts, geometry_latlon=None)
    assert np.argsort(st).tolist() in ([0, 1, 2], [2, 1, 0])


def test_short_input_is_safe():
    assert len(principal_curve_station(np.array([[0.0, 0.0]]))) == 1
    assert len(principal_curve_station(np.zeros((2, 2)))) == 2


def test_fram_curve_invariance(tmp_path):
    """곡선 교량 처리 핵심: 같은 데크 거동이면 평면 형상(직선/곡선)과 무관하게 CRI 동일.

    FRAM 공간전파를 **데크 호길이 station** 순으로 정렬하므로, 반원으로 놓인 데크도
    직선 데크와 동일한 위험 판정을 받는다(예전 X정렬은 곡선에서 순서가 뒤엉켜 실패).
    """
    from inframon.config import PipelineConfig
    from inframon.contracts.io import ProjectStore
    from inframon.fram.real_engine import run_fram_real
    from inframon.insar.track_reader import write_insar_contract
    from inframon.pinn.engine import run_pinn

    N, M = 60, 24
    dates = np.arange(M, dtype=float) * 24.0
    t = dates / 365.0
    station = np.linspace(0.0, 120.0, N)
    rng = np.random.default_rng(0)
    los = (4 * np.sin(2 * np.pi * t)[None, :] + rng.normal(0, 10, (N, M))).astype(np.float32)
    loc = np.exp(-((station - 60.0) ** 2) / (2 * 8.0 ** 2))     # station 60 부근 국소 손상
    los += (-40 * loc[:, None] * np.clip(t - 0.6, 0, None)[None, :] ** 2).astype(np.float32)

    def cri_for(curved: bool):
        if curved:
            th = station / 120.0 * np.pi
            xy = np.column_stack([100 * np.cos(th), 100 * np.sin(th) + 300.0])
        else:
            xy = np.column_stack([station, np.full(N, 300.0)])
        xyz = np.column_stack([xy[:, 0], xy[:, 1], np.zeros(N)])
        cfg = PipelineConfig(n_points=N, n_dates=M)
        with ProjectStore(tmp_path / f"c{curved}.h5", mode="w") as st:
            ins = write_insar_contract(
                st, xyz=xyz, member=np.zeros(N, np.int8),
                coherence=np.full(N, 0.7, np.float32), l_from_fixed=station.astype(np.float32),
                los=los, longitudinal=los, dates=dates, date_labels=None,
                deck_station=station.astype(np.float32))
            pinn = run_pinn(st, ins, cfg)
            fram = run_fram_real(st, ins, pinn, cfg)
            return st.read_array(fram.CRI_ds)

    straight, curved = cri_for(False), cri_for(True)
    assert np.abs(straight - curved).max() < 1e-6      # 평면 형상 불변(호길이 정렬 덕분)


def test_import_track_h5_polyline_station(tmp_path):
    """import_track_h5(geometry_latlon=...) 이 곡선 데크를 폴리라인 투영 station 으로 저장."""
    import h5py

    from inframon.contracts.io import ProjectStore
    from inframon.insar.track_reader import import_track_h5

    th = np.linspace(0, np.pi, 30)
    R = 0.001
    lon = 127.0 + R * np.cos(th)
    lat = 37.0 + R * np.sin(th)                        # 반원 데크
    M = 6
    trk = tmp_path / "track.h5"
    with h5py.File(trk, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack([lon, lat]))
        f.create_dataset("los_mm", data=np.random.default_rng(0).normal(0, 3, (30, M)))
        f.create_dataset("epochs", data=np.array([20240101, 20240213, 20240508,
                                                  20240801, 20241101, 20250201]))
        f.create_dataset("temp_coh", data=np.full(30, 0.7))
    geom = [[la, lo] for la, lo in zip(lat, lon)]
    proj = tmp_path / "p.h5"
    with ProjectStore(proj, mode="a") as st:
        import_track_h5(st, trk, geometry_latlon=geom)
    with h5py.File(proj, "r") as f:
        ds = f["insar/deck_station"][()]
        lf = f["insar/l_from_fixed"][()]
    # 호를 따라 단조(반원 순서), X정렬과 다름, l_from_fixed=station
    assert np.array_equal(np.argsort(ds), np.arange(30)) or \
        np.array_equal(np.argsort(ds), np.arange(30)[::-1])
    assert not np.array_equal(np.argsort(ds), np.argsort(lon))
    assert np.allclose(lf, ds)
    assert ds.max() > 300.0                            # 반원 호길이 ≈ π·100m
