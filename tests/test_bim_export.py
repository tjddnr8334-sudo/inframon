"""InSAR → BIM 오버레이 내보내기 — GeoJSON·CSV·값별 색·EPSG 투영."""
from __future__ import annotations

import json

import h5py
import numpy as np

from inframon.insar.bim_export import export_insar_for_bim, map_cri_to_points


def _make_h5(path):
    with h5py.File(path, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.array([[127.109, 37.368],
                                                        [127.110, 37.369]]))
        f.create_dataset("ts_sbas_mm", data=np.array([[0.0, 2.0, 5.0], [0.0, -1.0, -4.0]]))
        f.create_dataset("velocity_mm_yr", data=np.array([5.0, -4.0]))
        f.create_dataset("sbas_coherence", data=np.array([0.8, 0.6]))
        f.create_dataset("qps_class", data=np.array([2, 1], np.int8))   # PS·DS
    return str(path)


def test_export_geojson_and_csv(tmp_path):
    h5 = _make_h5(tmp_path / "psi.h5")
    r = export_insar_for_bim(h5, tmp_path / "bim", ifc_crs="EPSG:5186", incidence_deg=39.0)
    assert r["n_points"] == 2 and r["ifc_crs"] == "EPSG:5186"
    # LOS속도·연직·누적 세 값 모두 내보내짐(UI 토글용)
    assert set(["los_velocity_mm_yr", "vertical_velocity_mm_yr", "cumulative_mm"]) <= set(r["values"])
    gj = json.loads(open(r["geojson"], encoding="utf-8").read())
    assert gj["type"] == "FeatureCollection" and len(gj["features"]) == 2
    pr = gj["features"][0]["properties"]
    # WGS84 geometry + EPSG:5186 투영좌표 + 값 + 값별 색
    assert gj["features"][0]["geometry"]["coordinates"][0] == 127.109
    assert "x_5186" in pr and pr["x_5186"] > 100000
    assert pr["los_velocity_mm_yr"] == 5.0 and pr["class"] == "PS"
    assert pr["cumulative_mm"] == 5.0                       # ts[-1]-ts[0]
    assert pr["vertical_velocity_mm_yr"] > pr["los_velocity_mm_yr"]   # /cos(39°)
    assert pr["color_los_velocity_mm_yr"].startswith("#")


def test_export_legend_symmetric(tmp_path):
    h5 = _make_h5(tmp_path / "psi.h5")
    r = export_insar_for_bim(h5, tmp_path / "bim", incidence_deg=39.0)
    lg = r["legend"]["los_velocity_mm_yr"]
    assert lg["vmin"] == -lg["vmax"] and lg["cmap"] == "RdBu"   # 0 중심 대칭 발산맵


def _make_fram_h5(path):
    with h5py.File(path, "w") as f:
        g = f.create_group("insar")
        g.create_dataset("xyz", data=np.array([[127.109, 37.368, 0.0],
                                               [127.110, 37.369, 0.0]]))
        fr = f.create_group("fram")
        fr.create_dataset("CRI", data=np.array([[0.2, 0.4, 0.9], [0.1, 0.3, 0.5]]))  # [N,M]
    return str(path)


def test_map_cri_to_points_nearest(tmp_path):
    fram = _make_fram_h5(tmp_path / "fram.h5")
    # 점1은 FRAM점0(127.109,37.368) 근처, 점2는 FRAM점1 근처
    lonlat = np.array([[127.1091, 37.3681], [127.1099, 37.3689]])
    cri = map_cri_to_points(lonlat, fram, reduce="max")
    assert cri[0] == 0.9 and cri[1] == 0.5           # 각 최근접의 시점축 max
    # 멀면 NaN
    far = map_cri_to_points(np.array([[128.0, 36.0]]), fram, max_dist_m=150)
    assert np.isnan(far[0])


def test_export_with_cri(tmp_path):
    h5 = _make_h5(tmp_path / "psi.h5")             # 점 (127.109,37.368),(127.110,37.369)
    fram = _make_fram_h5(tmp_path / "fram.h5")
    r = export_insar_for_bim(h5, tmp_path / "bim", fram_project_h5=fram, incidence_deg=39.0)
    assert "cri" in r["values"]
    assert r["legend"]["cri"]["cmap"] == "RdYlGn_r" and r["legend"]["cri"]["vmax"] == 1.0
    gj = json.loads(open(r["geojson"], encoding="utf-8").read())
    pr = gj["features"][0]["properties"]
    assert "cri" in pr and "color_cri" in pr


def test_export_csv_has_all_columns(tmp_path):
    import csv
    h5 = _make_h5(tmp_path / "psi.h5")
    r = export_insar_for_bim(h5, tmp_path / "bim", incidence_deg=39.0)
    rows = list(csv.DictReader(open(r["csv"], encoding="utf-8-sig")))
    assert len(rows) == 2
    for col in ("lon", "lat", "x_5186", "class", "los_velocity_mm_yr", "color_cumulative_mm"):
        assert col in rows[0]
