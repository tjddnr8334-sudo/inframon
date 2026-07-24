"""현장 검증 프레임워크 — 기준 CSV 로드·정합·지표·LOS 투영·project 검증."""

from __future__ import annotations

import numpy as np
import pytest

from inframon import validation as v
from inframon.validation import Reference, load_reference_csv, validate


def test_load_reference_csv_header(tmp_path):
    p = tmp_path / "ref.csv"
    p.write_text("lon,lat,velocity\n127.10,37.32,-3.5\n127.11,37.33,2.0\n", encoding="utf-8")
    ref = load_reference_csv(p)
    assert ref.lonlat == [(127.10, 37.32), (127.11, 37.33)]
    assert ref.values == [-3.5, 2.0]


def test_load_reference_csv_no_header(tmp_path):
    p = tmp_path / "ref.csv"
    p.write_text("127.10,37.32,1.0\n127.11,37.33,2.0\n", encoding="utf-8")
    ref = load_reference_csv(p)
    assert len(ref.lonlat) == 2 and ref.values[0] == 1.0


def test_validate_metrics():
    # InSAR 점 = 기준점과 동일 위치, 값에 +1mm 편차 → bias +1, RMSE 1
    ll = [(127.100, 37.320), (127.101, 37.321), (127.102, 37.322)]
    ref = Reference(lonlat=ll, values=[0.0, 2.0, 4.0])
    insar_vals = [1.0, 3.0, 5.0]
    r = validate(ll, insar_vals, ref, max_dist_m=10.0, tolerance_mm=2.0)
    assert r.n_matched == 3
    assert r.bias == pytest.approx(1.0)
    assert r.rmse == pytest.approx(1.0)
    assert r.pearson_r == pytest.approx(1.0)
    assert r.passed is True                    # RMSE 1 ≤ 2


def test_validate_no_match():
    ref = Reference(lonlat=[(120.0, 35.0)], values=[1.0])
    r = validate([(127.1, 37.3)], [1.0], ref, max_dist_m=50.0)
    assert r.n_matched == 0 and r.passed is False


def test_validate_los_projection():
    # 연직 기준 10mm, 입사각 39° → LOS = 10·cos39 ≈ 7.77
    ll = [(127.10, 37.32)]
    ref = Reference(lonlat=ll, values=[10.0], vertical=True)
    r = validate(ll, [7.77], ref, insar_incidence=[39.0], max_dist_m=10.0,
                 tolerance_mm=1.0, project_to_los=True)
    assert r.n_matched == 1 and abs(r.bias) < 0.1     # 투영 후 거의 일치


def test_validate_project(tmp_path):
    h5py = pytest.importorskip("h5py")
    N = 5
    ll = np.column_stack([np.linspace(127.10, 127.11, N), np.full(N, 37.32)])
    # 선형 속도 2mm/yr 인 LOS(365일 간격 4시점 → [N,4])
    days = np.array([0, 365, 730, 1095])
    los = np.outer(np.full(N, 2.0), days / 365.25)     # [N,M] mm
    proj = tmp_path / "p.h5"
    with h5py.File(proj, "w") as f:
        g = f.create_group("insar")
        g.create_dataset("pixel_lonlat", data=ll)
        g.create_dataset("los", data=los.astype("float32"))
        g.create_dataset("date_labels", data=np.array(
            [b"20240101", b"20241231", b"20251231", b"20261231"]))
        g.create_dataset("incidenceAngle", data=np.full(N, 39.0, "float32"))
    ref = Reference(lonlat=[(p[0], p[1]) for p in ll], values=[2.0] * N, kind="velocity")
    r = v.validate_project(proj, ref, max_dist_m=10.0, tolerance_mm=0.5)
    assert r.n_matched == N
    assert r.rmse < 0.1 and r.passed is True           # 속도 복원 ≈ 2mm/yr
