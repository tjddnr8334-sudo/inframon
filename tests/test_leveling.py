"""수준측량 성과표(회차별 표고) → 연직 침하속도 검증 기준 변환."""

from __future__ import annotations

import numpy as np
import pytest

from inframon.leveling import load_leveling_csv, robust_slope


def test_robust_slope_two_rounds():
    # 2회차: 1년에 표고 -8mm → -8mm/yr
    assert robust_slope([0.0, 1.0], [0.0, -8.0]) == pytest.approx(-8.0)


def test_robust_slope_median_resists_outlier():
    # 5회차, 실제 -5mm/yr 인데 4회차 하나가 크게 튐(+10) → 중앙값이 방어.
    # (회차가 적으면 '끝점' 이상치는 쌍의 절반을 오염시켜 방어 한계가 있다 — 물리적 성질)
    # ⚠️ 이상치를 **중앙 회차(t=2=평균)**에 두면 최소제곱 기울기에 지렛대가 0이라
    # OLS 도 정확히 -5 가 나온다(절편만 흔듦) — 대비가 성립하지 않으므로 t=3 에 둔다.
    t = [0.0, 1.0, 2.0, 3.0, 4.0]
    y = [0.0, -5.0, -10.0, +10.0, -20.0]   # y[3] 이 -15 대신 +10 로 튐
    s = robust_slope(t, y)
    assert s == pytest.approx(-5.0, abs=0.5)
    # 같은 데이터에 최소제곱은 이상치에 끌려간다(OLS≈-2.5 vs Theil–Sen=-5.0)
    ols = np.polyfit(t, y, 1)[0]
    assert abs(ols - (-5.0)) > abs(s - (-5.0)) + 1.0   # 부동소수 노이즈가 아닌 실질 차이


def _write_table(path, origin_epsg="EPSG:5186", vert_vel=None, dates=None):
    """WGS84 정자교 부근 점들을 원점계 X,Y + 회차 표고(m)로 성과표 작성."""
    from pyproj import Transformer
    if vert_vel is None:
        vert_vel = [-6.0, -3.0, 0.0, 2.0]
    if dates is None:
        dates = ["20200510", "20210512", "20220509", "20230515"]
    lon = np.linspace(127.106, 127.110, len(vert_vel))
    lat = np.full(len(vert_vel), 37.365)
    tf = Transformer.from_crs("EPSG:4326", origin_epsg, always_xy=True)
    tyr = np.array([0.0, 1.0, 2.0, 3.0])[: len(dates)]
    H0 = 12.0
    lines = ["측점,X,Y," + ",".join(dates)]
    for k in range(len(vert_vel)):
        x, y = tf.transform(lon[k], lat[k])
        Hs = [H0 + vert_vel[k] * tyr[j] / 1000.0 for j in range(len(dates))]
        lines.append(f"P{k+1},{x:.3f},{y:.3f}," + ",".join(f"{h:.5f}" for h in Hs))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lon, lat, vert_vel


def test_load_leveling_date_headers(tmp_path):
    pytest.importorskip("pyproj")
    p = tmp_path / "lev.csv"
    lon, lat, vv = _write_table(p)
    ref = load_leveling_csv(p, origin="중부원점")
    assert ref.vertical is True and ref.kind == "velocity"
    assert len(ref.values) == len(vv)
    # 표고 기울기 = 연직속도 복원
    assert ref.values[0] == pytest.approx(vv[0], abs=0.05)
    assert ref.values[-1] == pytest.approx(vv[-1], abs=0.05)
    # 중부원점 → WGS84 복원(정자교 경도대)
    assert ref.lonlat[0][0] == pytest.approx(lon[0], abs=1e-4)
    assert "중부원점" in ref.source


def test_load_leveling_round_labels_need_dates(tmp_path):
    # 헤더가 '1차','2차' 처럼 날짜가 아니면 dates 없이는 막아야
    from pyproj import Transformer
    tf = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
    x, y = tf.transform(127.108, 37.365)
    p = tmp_path / "lev.csv"
    p.write_text(f"측점,X,Y,1차,2차\nP1,{x:.2f},{y:.2f},12.000,11.994\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dates"):
        load_leveling_csv(p, origin="중부원점")
    # dates 를 주면 통과 — 1년에 -6mm
    ref = load_leveling_csv(p, origin="중부원점", dates=["2020-05-01", "2021-05-01"])
    assert ref.values[0] == pytest.approx(-6.0, abs=0.1)


def test_load_leveling_missing_position_columns(tmp_path):
    p = tmp_path / "lev.csv"
    p.write_text("이름,표고1,표고2\nP1,12.0,11.99\n", encoding="utf-8")
    with pytest.raises(ValueError, match="위치 열"):
        load_leveling_csv(p, origin="중부원점")


def test_load_leveling_end_to_end_against_project(tmp_path):
    """성과표 → validate_project 전 경로(연직→LOS 투영·프레임오프셋 제거)."""
    h5py = pytest.importorskip("h5py")
    pytest.importorskip("pyproj")
    from inframon.validation import validate_project

    # 정자교 부근 4점, 연직속도 지정
    vv = [-6.0, -4.0, -2.0, 0.0]
    p = tmp_path / "lev.csv"
    lon, lat, _ = _write_table(p, vert_vel=vv)
    ref = load_leveling_csv(p, origin="중부원점")

    # 같은 점에 InSAR LOS = 연직·cos(inc) 인 project.h5 (201에폭 불필요, 4에폭)
    inc = 39.0
    los_vel = np.array(vv) * np.cos(np.radians(inc))     # mm/yr
    days = np.array([0, 365, 730, 1095])
    los = np.outer(los_vel, days / 365.25)               # [N,M] mm
    proj = tmp_path / "p.h5"
    with h5py.File(proj, "w") as f:
        g = f.create_group("insar")
        g.create_dataset("xyz", data=np.column_stack([lon, lat, np.zeros(len(vv))]))
        g.create_dataset("los", data=los.astype("float32"))
        g.create_dataset("date_labels", data=np.array(
            [b"20200101", b"20201231", b"20211231", b"20221231"]))
        g.create_dataset("incidence_deg", data=np.full(len(vv), inc, "float32"))
    r = validate_project(proj, ref, max_dist_m=20.0, tolerance_mm=0.3, project_to_los=True)
    assert r.n_matched == len(vv)
    assert r.rmse_detrended < 0.2 and r.passed is True   # 투영 후 정합
