"""BIM 좌표 정합 — IfcMapConversion 변환·기준점 적합·잔차 게이트."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from inframon.bim.georef import (
    AlignmentError,
    MapConversion,
    fit_map_conversion,
    to_ifc_local,
)


def _mc(rot_deg=30.0, e=200000.0, n=550000.0, h=12.0, scale=1.0) -> MapConversion:
    t = math.radians(rot_deg)
    return MapConversion(eastings=e, northings=n, orthogonal_height=h,
                         x_axis_abscissa=math.cos(t), x_axis_ordinate=math.sin(t),
                         scale=scale, target_crs="EPSG:5186", source="manual")


def test_map_conversion_matches_ifc_formula():
    """IFC4 정의식 그대로여야 한다: E=E0+s(x·a−y·o), N=N0+s(x·o+y·a)."""
    mc = _mc(rot_deg=30.0)
    a, o = mc.x_axis_abscissa, mc.x_axis_ordinate
    local = np.array([[10.0, 5.0, 3.0]])
    got = mc.to_map(local)[0]
    assert got[0] == pytest.approx(mc.eastings + (10 * a - 5 * o))
    assert got[1] == pytest.approx(mc.northings + (10 * o + 5 * a))
    assert got[2] == pytest.approx(mc.orthogonal_height + 3.0)


def test_roundtrip_local_map_local():
    mc = _mc(rot_deg=-17.3, scale=1.0)
    rng = np.random.default_rng(0)
    local = rng.uniform(-300, 300, (200, 3))
    back = mc.to_local(mc.to_map(local))
    assert np.allclose(back, local, atol=1e-9)


def test_roundtrip_with_scale():
    mc = _mc(rot_deg=12.0, scale=0.9995)          # 축척 보정이 있는 모델
    local = np.array([[100.0, -40.0, 7.0], [0.0, 0.0, 0.0]])
    assert np.allclose(mc.to_local(mc.to_map(local)), local, atol=1e-9)


def test_rotation_deg_reported():
    assert _mc(rot_deg=45.0).rotation_deg == pytest.approx(45.0)
    assert _mc(rot_deg=-90.0).rotation_deg == pytest.approx(-90.0)


def test_zero_axis_is_rejected():
    mc = MapConversion(x_axis_abscissa=0.0, x_axis_ordinate=0.0)
    with pytest.raises(AlignmentError, match="회전축"):
        mc.to_map(np.array([[1.0, 1.0]]))


# ── 기준점 적합 ────────────────────────────────────────────────────────
def test_fit_recovers_known_transform():
    truth = _mc(rot_deg=23.7, e=198765.4, n=551234.5, h=8.0)
    local = np.array([[0.0, 0.0, 0.0], [120.0, 0.0, 0.0], [120.0, 18.0, 4.0], [0.0, 18.0, 4.0]])
    mapped = truth.to_map(local)
    got = fit_map_conversion(local, mapped)
    assert got.rotation_deg == pytest.approx(truth.rotation_deg, abs=1e-6)
    assert got.eastings == pytest.approx(truth.eastings, abs=1e-6)
    assert got.northings == pytest.approx(truth.northings, abs=1e-6)
    assert got.orthogonal_height == pytest.approx(truth.orthogonal_height, abs=1e-6)
    assert got.fit["rms_m"] < 1e-6
    assert got.fit["height_fitted"] is True
    assert got.source == "control_points"


def test_fit_rejects_bad_control_points():
    """대응이 틀린 기준점 → 잔차 초과 → 실패. 조용히 틀린 정합보다 실패가 낫다."""
    truth = _mc(rot_deg=10.0)
    local = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 20.0], [0.0, 20.0]])
    mapped = truth.to_map(local)
    mapped[2] = mapped[2][::-1]                    # 한 점의 E/N 을 뒤바꿔 오대응 모사
    with pytest.raises(AlignmentError, match="RMS 잔차"):
        fit_map_conversion(local, mapped, max_rms_m=0.5)


def test_fit_reports_residuals_before_failing():
    truth = _mc(rot_deg=5.0)
    local = np.array([[0.0, 0.0], [50.0, 0.0], [50.0, 10.0]])
    mapped = truth.to_map(local)
    mapped[1, 0] += 3.0
    with pytest.raises(AlignmentError) as exc:
        fit_map_conversion(local, mapped, max_rms_m=0.1)
    assert "점별 잔차" in str(exc.value)


def test_fit_needs_two_points():
    with pytest.raises(AlignmentError, match="최소 2개"):
        fit_map_conversion(np.array([[0.0, 0.0]]), np.array([[1.0, 1.0]]))


def test_fit_rejects_coincident_points():
    p = np.zeros((3, 2))
    with pytest.raises(AlignmentError, match="겹쳐"):
        fit_map_conversion(p, np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]))


def test_fix_scale_keeps_unit_scale():
    """축척을 풀어두면 기준점 오차를 축척이 흡수해 정합이 그럴듯해 보인다."""
    truth = _mc(rot_deg=8.0, scale=1.0)
    local = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 30.0], [0.0, 30.0]])
    mapped = truth.to_map(local) * 1.0
    mapped[:, 0] += np.array([0.0, 0.3, 0.3, 0.0])      # 한 축으로 늘어난 오차
    fixed = fit_map_conversion(local, mapped, fix_scale=True, max_rms_m=1.0)
    free = fit_map_conversion(local, mapped, fix_scale=False, max_rms_m=1.0)
    assert fixed.scale == 1.0
    assert free.scale != 1.0
    assert free.fit["rms_m"] <= fixed.fit["rms_m"]      # 자유 축척이 잔차를 흡수


# ── CRS 체인 ───────────────────────────────────────────────────────────
def test_to_ifc_local_reprojects_wgs84():
    pytest.importorskip("pyproj")
    mc = _mc(rot_deg=0.0, e=0.0, n=0.0)
    mc.target_crs = "EPSG:5186"
    lonlat = np.array([[127.1068, 37.3653], [127.1075, 37.3707]])
    local, meta = to_ifc_local(lonlat, mc, source_crs="EPSG:4326")
    assert meta["reprojected"] is True
    assert local.shape == (2, 2)
    # 두 점의 실제 거리(약 600m)가 로컬 좌표에서도 보존돼야 한다
    d = float(np.linalg.norm(local[0] - local[1]))
    assert 500.0 < d < 750.0


def test_to_ifc_local_no_reprojection_when_same_crs():
    mc = _mc(rot_deg=0.0, e=100.0, n=200.0)
    xy = np.array([[100.0, 200.0], [110.0, 220.0]])
    local, meta = to_ifc_local(xy, mc, source_crs="EPSG:5186")
    assert meta["reprojected"] is False
    assert np.allclose(local[0], [0.0, 0.0])


def test_use_z_requires_fitted_height():
    """표고 오프셋이 검증 안 됐는데 3D 를 쓰면 막는다 (지오이드고 ~25m 오차)."""
    local2d = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 20.0]])
    mc = fit_map_conversion(local2d, _mc(rot_deg=3.0).to_map(local2d))
    assert mc.fit["height_fitted"] is False
    with pytest.raises(AlignmentError, match="표고"):
        to_ifc_local(np.array([[0.0, 0.0, 5.0]]), mc, source_crs="EPSG:5186", use_z=True)


def test_dict_roundtrip():
    mc = _mc(rot_deg=33.0)
    back = MapConversion.from_dict(mc.to_dict())
    assert back.rotation_deg == pytest.approx(mc.rotation_deg)
    assert back.target_crs == mc.target_crs


def test_load_control_points(tmp_path):
    from inframon.bim import load_control_points
    f = tmp_path / "cp.json"
    f.write_text(json.dumps({"target_crs": "EPSG:5186", "points": [
        {"name": "BM1", "local": [0, 0, 0], "map": [200000, 550000, 10]},
        {"name": "BM2", "local": [100, 0, 0], "map": [200100, 550000, 10]},
    ]}), encoding="utf-8")
    loc, mp, crs = load_control_points(str(f))
    assert loc.shape == (2, 3) and mp.shape == (2, 3) and crs == "EPSG:5186"
