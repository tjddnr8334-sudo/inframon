"""잔존수명 후처리 통합 — project.h5 → /life 계약·검열·불변성."""
from __future__ import annotations

import numpy as np
import pytest

from inframon.config import PipelineConfig
from inframon.contracts.io import LIFE_GROUP, ProjectStore
from inframon.contracts.schema import InSAROutput, RemainingLifeOutput
from inframon.life import estimate_remaining_life, summarize
from inframon.orchestrator.pipeline import run_pipeline


@pytest.fixture()
def project(tmp_path):
    out = tmp_path / "project.h5"
    run_pipeline(str(out), PipelineConfig(n_points=60, n_dates=24))
    return str(out)


def _inject(path: str, make):
    """/insar/los 를 합성 시계열로 교체 — 잔존수명 로직만 격리해 검증.

    estimator 는 PINN 성분분해의 열성분을 빼고 외삽한다(설계상 옳다). 주입 시계열은
    이미 열성분이 없는 신호이므로, 원 LOS 로부터 만들어진 stale `comp_thermal` 이
    섞이지 않도록 함께 0 으로 둔다(= PINN 이 열성분을 찾지 못한 상태와 동일).
    """
    from inframon.contracts.schema import PINNOutput
    with ProjectStore(path) as s:
        ins = s.read_meta("insar", InSAROutput)
        xyz = s.read_array(ins.xyz_ds)
        days = s.read_array(ins.dates_ds)
        t = (days - days[0]) / 365.25
        los = make(t, xyz).astype(np.float64)
        s.write_array(ins.los_ds, los)
        pn = s.read_meta("pinn", PINNOutput)
        s.write_array(pn.comp_thermal_ds, np.zeros_like(los))


def test_life_group_absent_until_requested(project):
    """`--remaining-life` 를 쓰기 전엔 /life 가 아예 없어야 한다(기존 파이프라인 불변)."""
    import h5py
    with h5py.File(project, "r") as f:
        assert LIFE_GROUP not in f


def test_healthy_project_is_censored(project):
    rng = np.random.default_rng(0)
    _inject(project, lambda t, xyz: rng.normal(0, 0.5, (xyz.shape[0], t.shape[0])))
    with ProjectStore(project) as s:
        out = estimate_remaining_life(s, PipelineConfig())
    assert out.rsl_lower_years is None                 # 유의한 열화 군집 없음
    assert out.censored_fraction > 0.85
    assert "> " in summarize(out) or "없음" in summarize(out)


def test_settling_project_gives_finite_life_and_contract(project):
    def make(t, xyz):
        n = xyz.shape[0]
        rng = np.random.default_rng(1)
        return (-2.0 * t)[None, :] + rng.normal(0, 0.05, (n, t.shape[0]))

    _inject(project, make)
    with ProjectStore(project) as s:
        out = estimate_remaining_life(s, PipelineConfig(), user_limits={"settlement_mm": 25.0})
        # 계약 검증(형상·dtype·N 심볼 결속)이 통과해야 한다
        s.validate(LIFE_GROUP, out)
        rsl = s.read_array(out.rsl_point_ds)
        lo = s.read_array(out.rsl_lower_ds)
        rate = s.read_array(out.rate_ds)

    assert out.rsl_lower_years is not None
    assert out.rsl_lower_years <= out.rsl_years        # 하한이 더 보수적
    assert out.governing == "serviceability"
    assert np.allclose(rate, 2.0, atol=0.1)
    assert rsl.shape == lo.shape == (out.n_points,)
    assert out.censored_fraction < 0.05


def test_inactive_channels_carry_reasons(project):
    with ProjectStore(project) as s:
        out = estimate_remaining_life(s, PipelineConfig())
    names = {c.name for c in out.channels}
    assert names == {"serviceability", "stiffness", "fatigue", "durability"}
    for c in out.channels:
        if not c.active:
            assert c.inactive_reason, f"{c.name}: 비활성 사유가 비었습니다"
    kinds = {c.name: c.kind for c in out.channels}
    assert kinds["fatigue"] == "model_based" and kinds["serviceability"] == "measured"


def test_assumptions_are_recorded_with_sources(project):
    with ProjectStore(project) as s:
        out = estimate_remaining_life(s, PipelineConfig())
    a = out.assumptions
    assert "sources" in a and "values" in a
    assert a["sources"]["settlement_mm"].startswith("기본가정")
    assert "consumed_note" in a and "displacement_source" in a
    assert isinstance(a["thermal_removed"], bool)


def test_user_limits_override_is_labelled(project):
    with ProjectStore(project) as s:
        out = estimate_remaining_life(s, PipelineConfig(), user_limits={"settlement_mm": 40.0})
    assert out.assumptions["values"]["settlement_mm"] == 40.0
    assert out.assumptions["sources"]["settlement_mm"] == "사용자 지정"


def test_shorter_limit_gives_shorter_life(project):
    def make(t, xyz):
        rng = np.random.default_rng(2)
        return (-2.0 * t)[None, :] + rng.normal(0, 0.05, (xyz.shape[0], t.shape[0]))

    _inject(project, make)
    lives = []
    for lim in (10.0, 40.0):
        with ProjectStore(project) as s:
            out = estimate_remaining_life(s, PipelineConfig(), user_limits={"settlement_mm": lim})
        lives.append(out.rsl_lower_years)
    assert lives[0] < lives[1]                          # 한계가 낮을수록 잔존수명 짧다


def test_meta_roundtrip(project):
    with ProjectStore(project) as s:
        estimate_remaining_life(s, PipelineConfig())
    with ProjectStore(project, "r") as s:
        back = s.read_meta(LIFE_GROUP, RemainingLifeOutput)
    assert back.n_points > 0 and back.confidence in {"high", "medium", "low"}
    assert back.confidence_reason
