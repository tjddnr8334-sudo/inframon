"""InSAR 점을 교량 위에 올린다 — 고도 원천 캐스케이드.

z 를 주지 않으면 점이 0m 에 깔린다. 실제로 청양교 산출물이 그랬다: 48개 점이 z=0 인데
그 지점 지면 표고는 93m — **교량보다 93m 아래**에 점군이 있었다. 3D 로 보면 다리와
데이터가 따로 논다. 어떤 원천을 어떤 순서로 쓰는지, 무엇을 썼는지 남기는지를 고정한다.
"""

from __future__ import annotations

import numpy as np

from inframon.insar.deck_z import DEFAULT_CLEARANCE_M, clearance_from_profile, deck_elevation

LONLAT = np.array([[126.8071, 36.4506], [126.8074, 36.4507], [126.8077, 36.4508]])


def _dem(_lats, _lons):
    return [93.0]


def test_ifc_element_z_wins():
    """BIM 이 있으면 그 데크 레벨이 가장 정확하다 — 다른 후보보다 우선."""
    d = deck_elevation(LONLAT, element_z=[103.2, 103.3, 103.1], psi_elev=[99, 99, 99],
                       clearance_m=10.0, dem_fn=_dem)
    assert d.source == "ifc_element_top"
    assert np.allclose(d.z, [103.2, 103.3, 103.1])


def test_psi_used_when_no_ifc():
    """IFC 가 없으면 순수 InSAR(DEM+Δh 산란체 실고도)가 다음이다."""
    d = deck_elevation(LONLAT, psi_elev=[101.0, 102.0, 103.0], dem_fn=_dem)
    assert d.source == "psi_residual_height" and d.z[2] == 103.0


def test_dem_plus_measured_clearance():
    """IFC·PSI 가 없어도 지면표고 + 표준데이터 교량높이로 데크에 얹는다(임의 교량 기본)."""
    d = deck_elevation(LONLAT, clearance_m=10.0, dem_fn=_dem)
    assert d.source == "dem+clearance"
    assert np.allclose(d.z, 103.0)                 # 93 + 10
    assert d.ground_m == 93.0 and d.clearance_m == 10.0
    assert "교량높이" in d.describe()


def test_dem_without_clearance_says_it_assumed():
    """형하고를 모르면 기본값을 쓰되 **가정했다고 밝힌다**."""
    d = deck_elevation(LONLAT, dem_fn=_dem)
    assert d.clearance_m == DEFAULT_CLEARANCE_M
    assert "가정" in d.describe()


def test_track_height_plus_clearance():
    d = deck_elevation(LONLAT, track_height=[90.0, 91.0, 92.0], clearance_m=6.0)
    assert d.source == "track_height" and np.allclose(d.z, [96.0, 97.0, 98.0])


def test_flat_is_last_and_says_it_failed():
    """아무 근거도 없으면 0m 로 두되 '교량 위가 아니다'를 남긴다 — 조용히 깔면 안 된다."""
    d = deck_elevation(LONLAT, dem_fn=lambda *a: [], allow_network=False)
    assert d.source == "flat" and np.allclose(d.z, 0.0)
    assert "교량 위가 아니다" in d.describe()


def test_dem_failure_falls_through_not_crash():
    def _boom(*_a):
        raise OSError("네트워크 없음")

    d = deck_elevation(LONLAT, dem_fn=_boom)
    assert d.source == "flat"


class _Prof:
    def __init__(self, **ex):
        self.extra = ex


def test_clearance_from_profile_reads_measured_height():
    assert clearance_from_profile(_Prof(height_m=10.0)) == 10.0
    assert clearance_from_profile(_Prof(clearance_m=4.6)) == 4.6


def test_clearance_rejects_implausible_values():
    """형하고로 말이 안 되는 값(0·1000m)은 쓰지 않는다."""
    assert clearance_from_profile(_Prof(height_m=0.0)) is None
    assert clearance_from_profile(_Prof(height_m=1000.0)) is None
    assert clearance_from_profile(_Prof()) is None
