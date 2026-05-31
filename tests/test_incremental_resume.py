"""증분 재개(축 B) 테스트 — fingerprint 사슬, 계획(compute/reuse), 크래시 복구.

골든 회귀는 resume=False(기본)이므로 영향받지 않는다. 여기서는 resume=True 일 때
입력이 안 바뀐 단계를 건너뛰고, 바뀐 단계와 그 하류만 다시 도는지 검증한다.
"""

from __future__ import annotations

import numpy as np
import pytest

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import InSAROutput
from inframon.orchestrator import engines, incremental
from inframon.orchestrator.pipeline import run_pipeline

CFG = {"n_points": 30, "n_dates": 12}


# ── fingerprint 사슬 ──
def test_fingerprints_stable_and_chained():
    a = incremental.stage_fingerprints(PipelineConfig(**CFG))
    b = incremental.stage_fingerprints(PipelineConfig(**CFG))
    assert a == b  # 같은 cfg → 같은 fingerprint
    assert len(set(a.values())) == 4  # 단계마다 다름


def test_cfg_change_invalidates_all_stages():
    a = incremental.stage_fingerprints(PipelineConfig(**CFG))
    b = incremental.stage_fingerprints(PipelineConfig(n_points=31, n_dates=12))
    # n_points 는 cv_subset 에 들어가므로 cv 부터 전부 달라진다(cascade)
    assert all(a[s] != b[s] for s in incremental.STAGE_ORDER)


def test_engine_hotswap_invalidates_only_that_stage_and_downstream():
    base = PipelineConfig(**CFG)
    swap = PipelineConfig(
        engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"}, **CFG
    )
    a = incremental.stage_fingerprints(base)
    b = incremental.stage_fingerprints(swap)
    # fram 엔진만 바꿨으니 cv/insar/pinn 은 동일, fram 만 달라야 한다
    assert a["cv"] == b["cv"]
    assert a["insar"] == b["insar"]
    assert a["pinn"] == b["pinn"]
    assert a["fram"] != b["fram"]


def test_dynamic_cfg_attr_changes_fingerprint():
    """getattr 폴백 동적 속성(cv_backend 등)도 fingerprint 에 반영된다(vars 기반)."""
    base = PipelineConfig(**CFG)
    tuned = PipelineConfig(**CFG)
    tuned.cv_backend = "transformer"  # 동적 속성 — asdict 는 못 잡지만 vars 는 잡음
    assert incremental.stage_fingerprints(base)["cv"] != incremental.stage_fingerprints(tuned)["cv"]


# ── 계획(build_plan) ──
@pytest.fixture()
def project(tmp_path):
    out = tmp_path / "project.h5"
    run_pipeline(out, PipelineConfig(**CFG))  # 기준 실행 — fingerprint 기록됨
    return out


def _plan(project, cfg, force=()):
    with ProjectStore(project, mode="r") as store:
        prev = store.read_manifest().get("stage_fingerprints", {})
        plan, _ = incremental.build_plan(store, prev, cfg, set(force))
    return plan


def test_plan_all_reuse_when_unchanged(project):
    plan = _plan(project, PipelineConfig(resume=True, **CFG))
    assert plan == {"cv": "reuse", "insar": "reuse", "pinn": "reuse", "fram": "reuse"}


def test_plan_cascade_from_changed_stage(project):
    # n_dates 변경 → 전 단계 cascade
    plan = _plan(project, PipelineConfig(resume=True, n_points=30, n_dates=13))
    assert all(v == "compute" for v in plan.values())


def test_plan_force_stage_cascades_downstream(project):
    # pinn 강제 재계산 → pinn,fram compute / cv,insar reuse
    plan = _plan(project, PipelineConfig(resume=True, **CFG), force=("pinn",))
    assert plan == {"cv": "reuse", "insar": "reuse", "pinn": "compute", "fram": "compute"}


def test_plan_recomputes_when_output_corrupted(project):
    # insar 출력 손상(크래시 복구 시나리오) → insar 부터 재계산, cv 는 reuse
    with ProjectStore(project, mode="a") as store:
        insar = store.read_meta("insar", InSAROutput)
        del store._f[insar.los_ds]
    plan = _plan(project, PipelineConfig(resume=True, **CFG))
    assert plan["cv"] == "reuse"
    assert plan["insar"] == "compute" and plan["pinn"] == "compute" and plan["fram"] == "compute"


# ── 통합: 실제로 재계산을 건너뛰는가 ──
def test_resume_skips_recomputation(tmp_path):
    """resume 2회차에 stub 엔진이 한 번도 호출되지 않아야 한다(전부 reuse)."""
    calls: dict[str, int] = {s: 0 for s in incremental.STAGE_ORDER}
    from inframon.cv.engine import run_cv
    from inframon.fram.engine import run_fram
    from inframon.insar.engine import run_insar
    from inframon.pinn.engine import run_pinn

    base = {"cv": run_cv, "insar": run_insar, "pinn": run_pinn, "fram": run_fram}

    def make(stage, fn):
        def wrapper(store, *args):
            calls[stage] += 1
            return fn(store, *args)
        return wrapper

    for s, fn in base.items():
        engines.register(s, "stub", make(s, fn))
    try:
        out = tmp_path / "p.h5"
        run_pipeline(out, PipelineConfig(**CFG))                 # 1회차: 전부 compute
        assert calls == {"cv": 1, "insar": 1, "pinn": 1, "fram": 1}
        run_pipeline(out, PipelineConfig(resume=True, **CFG))    # 2회차: 전부 reuse
        assert calls == {"cv": 1, "insar": 1, "pinn": 1, "fram": 1}
    finally:
        for s, fn in base.items():
            engines.register(s, "stub", fn)  # 원복


def test_resume_partial_recompute_keeps_upstream(tmp_path):
    """force=pinn 으로 2회차 → pinn/fram 만 재계산, 결과는 1회차와 수치 동일(결정론)."""
    out = tmp_path / "p.h5"
    run_pipeline(out, PipelineConfig(**CFG))
    with ProjectStore(out, mode="r") as store:
        cv_hash_1 = store.read_manifest()["dataset_hashes"]["cv/roi_mask"]
        cri_1 = store.read_array(store.read_meta("fram", __import__(
            "inframon.contracts.schema", fromlist=["FRAMOutput"]).FRAMOutput).CRI_ds)

    run_pipeline(out, PipelineConfig(resume=True, force_stages=("pinn",), **CFG))
    with ProjectStore(out, mode="r") as store:
        man = store.read_manifest()
        cv_hash_2 = man["dataset_hashes"]["cv/roi_mask"]
        from inframon.contracts.schema import FRAMOutput
        cri_2 = store.read_array(store.read_meta("fram", FRAMOutput).CRI_ds)

    assert cv_hash_1 == cv_hash_2                  # cv 재사용 → 동일
    assert np.array_equal(cri_1, cri_2)            # 재계산해도 결정론적으로 동일


def test_resume_falls_back_to_full_when_no_prior(tmp_path):
    """resume=True 라도 기존 파일이 없으면 그냥 전체 실행(폴백)."""
    out = tmp_path / "fresh.h5"
    fram = run_pipeline(out, PipelineConfig(resume=True, **CFG))
    assert fram.n_points == 30
    with ProjectStore(out, mode="r") as store:
        assert len(store.read_manifest()["stage_fingerprints"]) == 4
