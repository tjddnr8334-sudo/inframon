"""강성열화 채널 — EI(t) 추세와 그것을 막는 네 겹의 게이트.

이 채널은 쉽게 거짓말을 한다. EI 식별은 4차 도함수에 의존해 노이즈에 매우 민감하고
표본이 ~12개뿐이다. 그래서 이 파일이 고정하는 것은 공식보다 **언제 값을 내지 않는가**다.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import PINNOutput, RemainingLifeOutput
from inframon.life import estimate_remaining_life
from inframon.life.channels import EI_CLIP, stiffness
from inframon.orchestrator.pipeline import run_pipeline

EI0 = 5.0e10


def _decaying(lam: float, years: float = 8.0, n: int = 13, noise: float = 0.0, seed: int = 0):
    """EI(t) = EI0·exp(−λt) 표본."""
    t = np.linspace(0.0, years, n)
    e = EI0 * np.exp(-lam * t)
    if noise:
        e = e * (1.0 + np.random.default_rng(seed).normal(0, noise, n))
    return t, e


# ── 값이 맞는가 ────────────────────────────────────────────────────────
def test_recovers_time_to_stiffness_limit():
    """λ=0.02/yr, 한계 0.80 → ln(1/0.8)/0.02 = 11.16년, 이미 8년 관측 → 3.16년 남음."""
    lam, observed = 0.02, 8.0
    t, e = _decaying(lam, years=observed)
    r = stiffness(t, e, observed_years=observed)
    assert r["active"] and not r["censored"]
    expected = -math.log(0.8) / lam - observed
    assert r["rsl_years"] == pytest.approx(expected, rel=0.02)
    assert r["rsl_lower_years"] <= r["rsl_years"]          # 하한이 더 보수적
    assert r["detail"]["lambda_per_year"] == pytest.approx(lam, rel=0.02)
    assert r["detail"]["half_life_years"] == pytest.approx(math.log(2) / lam, rel=0.02)


def test_already_past_limit_gives_zero():
    t, e = _decaying(0.05, years=10.0)                     # ln(1/0.8)/0.05 = 4.5년 < 10
    r = stiffness(t, e, observed_years=10.0)
    assert r["rsl_years"] == 0.0 and r["rsl_lower_years"] == 0.0


def test_stricter_limit_shortens_life():
    t, e = _decaying(0.02, years=6.0)
    lax = stiffness(t, e, observed_years=6.0, r_limit=0.70)
    tight = stiffness(t, e, observed_years=6.0, r_limit=0.90)
    assert tight["rsl_years"] < lax["rsl_years"]


def test_geometric_ei_is_reported_but_not_used_for_extrapolation():
    """설계 기하 EI 를 기준으로 삼으면 식별 EI 와의 계통 편차가 통째로 열화가 된다."""
    t, e = _decaying(0.02, years=6.0)
    with_geo = stiffness(t, e, observed_years=6.0, geometric_ei=EI0 * 3.0)
    without = stiffness(t, e, observed_years=6.0)
    assert with_geo["rsl_years"] == without["rsl_years"]    # 외삽은 자기기준으로만
    assert with_geo["detail"]["EI_over_geometric"] == pytest.approx(1 / 3, abs=0.02)


# ── 네 겹의 게이트 ─────────────────────────────────────────────────────
def test_gate_short_observation():
    t, e = _decaying(0.02, years=2.0)
    r = stiffness(t, e, observed_years=2.0)
    assert not r["active"] and "3년" in r["inactive_reason"]


def test_identification_quality_gates_come_before_duration():
    """포화는 시간이 지나도 안 풀린다 — '3년 미만'만 알리면 3년 뒤에야 진짜 원인을 안다."""
    t = np.linspace(0, 1.5, 13)
    e = np.full(13, EI_CLIP[1])
    r = stiffness(t, e, observed_years=1.5)               # 두 게이트 모두 해당
    assert "포화" in r["inactive_reason"]                  # 더 근본적인 쪽을 말한다
    assert "해결되지 않는다" in r["inactive_reason"]


def test_gate_saturated_identification():
    """합성·저휨 데이터는 d4≈0 → EI 가 상한에 박힌다. 그 추세는 전부 인공물이다."""
    t = np.linspace(0, 8, 13)
    e = np.full(13, EI_CLIP[1])
    r = stiffness(t, e, observed_years=8.0)
    assert not r["active"] and "포화" in r["inactive_reason"]
    assert r["detail"]["saturated_fraction"] == 1.0


def test_gate_unstable_identification():
    rng = np.random.default_rng(1)
    t = np.linspace(0, 8, 13)
    e = EI0 * np.exp(rng.normal(0, 1.2, 13))               # 변동계수 매우 큼
    r = stiffness(t, e, observed_years=8.0)
    assert not r["active"] and "불안정" in r["inactive_reason"]


def test_gate_too_few_samples():
    r = stiffness(np.array([0.0, 1.0, 2.0]), np.full(3, EI0), observed_years=8.0)
    assert not r["active"] and "표본 부족" in r["inactive_reason"]


def test_flat_ei_is_censored_not_infinite_life():
    """열화가 없으면 잔존수명은 '매우 김'이 아니라 정의되지 않음(검열)이다."""
    rng = np.random.default_rng(2)
    t = np.linspace(0, 8, 13)
    e = EI0 * (1.0 + rng.normal(0, 0.02, 13))
    r = stiffness(t, e, observed_years=8.0)
    assert r["active"] and r["censored"]
    assert r["rsl_years"] is None and r["rsl_lower_years"] is None


def test_stiffening_is_censored_with_reason():
    t = np.linspace(0, 8, 13)
    e = EI0 * np.exp(0.02 * t)                             # 강성이 오히려 증가
    r = stiffness(t, e, observed_years=8.0)
    assert r["censored"] and "증가" in r["detail"]["direction"]


def test_noise_does_not_fabricate_degradation():
    """추세 없는 잡음에서 열화가 나오면 안 된다(다중 시드)."""
    fabricated = 0
    for seed in range(20):
        rng = np.random.default_rng(seed)
        t = np.linspace(0, 8, 13)
        e = EI0 * np.exp(rng.normal(0, 0.15, 13))
        r = stiffness(t, e, observed_years=8.0)
        if r["active"] and not r["censored"]:
            fabricated += 1
    assert fabricated <= 2                                  # 95% CI → 오탐 소수


# ── 파이프라인 연결 ────────────────────────────────────────────────────
@pytest.fixture()
def project(tmp_path):
    out = tmp_path / "project.h5"
    run_pipeline(str(out), PipelineConfig(n_points=60, n_dates=24))
    return str(out)


def test_channel_inactive_without_ei_series(project):
    """stub PINN 은 EI_series 를 안 낸다 — 침묵하지 말고 사유를 남겨야 한다."""
    with ProjectStore(project) as s:
        out = estimate_remaining_life(s, PipelineConfig())
    ch = next(c for c in out.channels if c.name == "stiffness")
    assert not ch.active and "EI_series" in ch.inactive_reason


def _inject_ei(path: str, lam: float, years: float, n: int = 13) -> None:
    t = np.linspace(0.0, years, n)
    with ProjectStore(path) as s:
        pn = s.read_meta("pinn", PINNOutput)
        pn.EI_series_t_ds = s.write_array("/pinn/EI_series_t", t)
        pn.EI_series_ds = s.write_array("/pinn/EI_series", EI0 * np.exp(-lam * t))
        pn.n_ei_epochs = n
        s.write_meta("pinn", pn)


def test_stiffness_channel_engages_end_to_end(project):
    _inject_ei(project, lam=0.03, years=8.0)
    with ProjectStore(project) as s:
        # 관측 기간을 8년으로 늘려 게이트를 통과시킨다
        from inframon.contracts.schema import InSAROutput
        ins = s.read_meta("insar", InSAROutput)
        s.write_array(ins.dates_ds, np.linspace(0.0, 8 * 365.25, ins.n_dates))
        out = estimate_remaining_life(s, PipelineConfig())
    ch = next(c for c in out.channels if c.name == "stiffness")
    assert ch.active and not ch.censored
    assert ch.rsl_lower_years is not None
    assert ch.kind == "measured"


def test_governing_channel_is_the_shortest_active_one(project):
    """강성열화가 사용성보다 이르면 그쪽이 교량 대표값을 지배해야 한다."""
    _inject_ei(project, lam=0.20, years=8.0)               # 매우 빠른 열화 → 짧은 RSL
    with ProjectStore(project) as s:
        from inframon.contracts.schema import InSAROutput
        ins = s.read_meta("insar", InSAROutput)
        s.write_array(ins.dates_ds, np.linspace(0.0, 8 * 365.25, ins.n_dates))
        out = estimate_remaining_life(s, PipelineConfig())
    assert out.governing == "stiffness"
    ch = next(c for c in out.channels if c.name == "stiffness")
    assert out.rsl_lower_years == ch.rsl_lower_years


def test_contract_roundtrip_keeps_channel_detail(project):
    _inject_ei(project, lam=0.03, years=8.0)
    with ProjectStore(project) as s:
        from inframon.contracts.schema import InSAROutput
        ins = s.read_meta("insar", InSAROutput)
        s.write_array(ins.dates_ds, np.linspace(0.0, 8 * 365.25, ins.n_dates))
        estimate_remaining_life(s, PipelineConfig())
    with ProjectStore(project, "r") as s:
        back = s.read_meta("life", RemainingLifeOutput)
    ch = next(c for c in back.channels if c.name == "stiffness")
    assert "lambda_per_year" in ch.detail and "log_ei_slope_ci" in ch.detail
