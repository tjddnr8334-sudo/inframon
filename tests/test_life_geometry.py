"""LOS→연직 기하 투영 — 사용성 한계는 연직 규정인데 관측은 LOS 다.

이 파일이 고정하는 회귀: LOS 를 연직인 척 쓰면 변위가 cos θ 배로 과소평가되고
잔존수명이 1/cos θ ≈ 1.29 배(39°) 낙관적으로 나온다.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import InSAROutput, PINNOutput
from inframon.life import estimate_remaining_life
from inframon.life.geometry import DEFAULT_INCIDENCE_DEG, los_to_vertical
from inframon.orchestrator.pipeline import run_pipeline


# ── 단위 변환 ──────────────────────────────────────────────────────────
def test_projection_divides_by_cos_incidence():
    los = np.array([[1.0, 2.0, 3.0]])
    v, meta = los_to_vertical(los, 39.0)
    assert np.allclose(v, los / math.cos(math.radians(39.0)))
    assert meta["scale_1_over_cos"]["median"] == pytest.approx(1.2868, abs=1e-3)
    assert meta["incidence_assumed"] is False


def test_projection_uses_per_point_incidence():
    los = np.ones((3, 2))
    inc = np.array([30.0, 39.0, 45.0])
    v, meta = los_to_vertical(los, inc)
    assert np.allclose(v[:, 0], 1.0 / np.cos(np.radians(inc)))
    assert meta["incidence_deg"]["min"] == 30.0 and meta["incidence_deg"]["max"] == 45.0


def test_projection_falls_back_to_representative_angle():
    v, meta = los_to_vertical(np.ones((2, 2)), None)
    assert np.allclose(v, 1.0 / math.cos(math.radians(DEFAULT_INCIDENCE_DEG)))
    assert meta["incidence_assumed"] is True
    assert "Sentinel-1 IW" in meta["incidence_source"]


def test_out_of_range_incidence_is_replaced_not_trusted():
    """라디안이 들어오거나(0.68) 각도 정의가 다르면 cos 이 1 에 가까워져 투영이 무력화된다."""
    los = np.ones((3, 1))
    inc = np.array([0.68, 39.0, 120.0])          # 라디안·정상·이상값
    v, meta = los_to_vertical(los, inc)
    assert meta["n_out_of_range"] == 2
    assert np.allclose(v, 1.0 / math.cos(math.radians(39.0)))   # 전부 39° 로 수렴


def test_scalar_incidence_is_broadcast():
    v, meta = los_to_vertical(np.ones((4, 2)), np.array([35.0]))
    assert v.shape == (4, 2)
    assert meta["incidence_deg"]["median"] == 35.0


def test_wrong_incidence_length_raises():
    with pytest.raises(ValueError, match="입사각 개수"):
        los_to_vertical(np.ones((3, 2)), np.array([39.0, 39.0]))


def test_projection_records_the_assumption():
    _, meta = los_to_vertical(np.ones((2, 2)), 39.0)
    assert "단일 궤도 연직 가정" in meta["assumption"]
    assert "asc+desc" in meta["better"]


# ── 파이프라인 회귀 ────────────────────────────────────────────────────
@pytest.fixture()
def project(tmp_path):
    out = tmp_path / "project.h5"
    run_pipeline(str(out), PipelineConfig(n_points=60, n_dates=24))
    return str(out)


def _inject_los(path: str, rate_mm_yr: float, *, incidence: float | None) -> None:
    with ProjectStore(path) as s:
        ins = s.read_meta("insar", InSAROutput)
        days = s.read_array(ins.dates_ds)
        t = (days - days[0]) / 365.25
        n = int(ins.n_points)
        los = (rate_mm_yr * t)[None, :] * np.ones((n, 1))
        s.write_array(ins.los_ds, los)
        pn = s.read_meta("pinn", PINNOutput)
        s.write_array(pn.comp_thermal_ds, np.zeros_like(los))
        if incidence is not None:
            s.write_array("/insar/incidence_deg", np.full(n, incidence, dtype=np.float32))
            ins.incidence_ds = "/insar/incidence_deg"
            s.write_meta("insar", ins)


def test_stored_incidence_is_used_end_to_end(project):
    _inject_los(project, -2.0, incidence=33.0)
    with ProjectStore(project) as s:
        out = estimate_remaining_life(s, PipelineConfig())
        rate = s.read_array(out.rate_ds)
    assert np.allclose(rate, 2.0 / math.cos(math.radians(33.0)), atol=0.05)
    pj = out.assumptions["vertical_projection"]
    assert pj["incidence_assumed"] is False
    assert pj["incidence_deg"]["median"] == pytest.approx(33.0, abs=0.01)


def test_missing_incidence_still_projects_with_assumption(project):
    _inject_los(project, -2.0, incidence=None)
    with ProjectStore(project) as s:
        out = estimate_remaining_life(s, PipelineConfig())
        rate = s.read_array(out.rate_ds)
    assert np.allclose(rate, 2.0 / math.cos(math.radians(DEFAULT_INCIDENCE_DEG)), atol=0.05)
    assert out.assumptions["vertical_projection"]["incidence_assumed"] is True


def test_projection_shortens_remaining_life(project):
    """회귀 고정 — 투영을 빠뜨리면 잔존수명이 1/cos θ 만큼 낙관적이 된다."""
    _inject_los(project, -2.0, incidence=39.0)
    with ProjectStore(project) as s:
        out = estimate_remaining_life(s, PipelineConfig(), user_limits={"settlement_mm": 25.0})
    naive = 25.0 / 2.0                                   # LOS 를 연직으로 오인했을 때
    correct = 25.0 / (2.0 / math.cos(math.radians(39.0)))
    assert out.rsl_years == pytest.approx(correct, rel=0.05)
    assert out.rsl_years < naive * 0.85                  # 뚜렷하게 더 짧다


def test_fused_vertical_skips_projection(project):
    """asc+desc 융합 연직이 있으면 가정 없이 그대로 쓴다."""
    _inject_los(project, -2.0, incidence=39.0)
    with ProjectStore(project) as s:
        ins = s.read_meta("insar", InSAROutput)
        los = s.read_array(ins.los_ds)
        ins.vertical_ds = s.write_array("/insar/vertical", np.asarray(los))
        s.write_meta("insar", ins)
        out = estimate_remaining_life(s, PipelineConfig())
        rate = s.read_array(out.rate_ds)
    assert out.assumptions["vertical_projection"] is None
    assert np.allclose(rate, 2.0, atol=0.05)             # 투영 없음
    assert "융합 연직" in out.assumptions["displacement_source"]
