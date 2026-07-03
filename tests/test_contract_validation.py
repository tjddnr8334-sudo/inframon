"""배열 레벨 계약 검증 + 실행 매니페스트 테스트 (Phase 1 고도화).

`schema.py` 의 Pydantic 계약(경로 문자열)만으로는 못 잡던 것들 —
데이터셋 누락/형상·dtype 어긋남/엔진 간 N·M 불일치 — 을 잡는지 확인한다.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from inframon.config import PipelineConfig
from inframon.contracts.array_schema import ContractViolation
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import FRAMOutput, InSAROutput, PINNOutput
from inframon.orchestrator.pipeline import run_pipeline

CFG = {"n_points": 30, "n_dates": 12}


@pytest.fixture()
def project(tmp_path):
    out = tmp_path / "project.h5"
    run_pipeline(out, PipelineConfig(**CFG))  # validate_contracts=True 가 이미 한 번 검증함
    return out


# ── 정상 경로 ──
def test_clean_pipeline_passes_validation(project):
    """검증을 켜고 끝까지 돌면(run_pipeline 내부 검증) 예외 없이 통과한다."""
    with ProjectStore(project, mode="r") as store:
        insar = store.read_meta("insar", InSAROutput)
        pinn = store.read_meta("pinn", PINNOutput)
        fram = store.read_meta("fram", FRAMOutput)
        sym = store.validate_all({"insar": insar, "pinn": pinn, "fram": fram})
    assert sym["N"] == 30 and sym["M"] == 12 and sym["F"] == 4


# ── 위반 탐지 ──
def test_shape_mismatch_raises(project):
    with ProjectStore(project, mode="a") as store:
        insar = store.read_meta("insar", InSAROutput)
        store.write_array(insar.los_ds, np.zeros((99, 12)))  # N 이 30→99 로 어긋남
        with pytest.raises(ContractViolation, match="N"):
            store.validate("insar", insar)


def test_dtype_mismatch_raises(project):
    with ProjectStore(project, mode="a") as store:
        insar = store.read_meta("insar", InSAROutput)
        store.write_array(insar.coherence_ds, np.zeros(30, dtype=np.int32))  # float 여야 함
        with pytest.raises(ContractViolation, match="dtype"):
            store.validate("insar", insar)


def test_missing_dataset_raises(project):
    with ProjectStore(project, mode="a") as store:
        insar = store.read_meta("insar", InSAROutput)
        del store._f[insar.los_ds]
        with pytest.raises(ContractViolation, match="없습니다"):
            store.validate("insar", insar)


def test_cross_engine_symbol_inconsistency_raises(project):
    """insar 의 N 과 pinn 의 N 이 어긋나면 공유 심볼 표가 잡아낸다."""
    with ProjectStore(project, mode="a") as store:
        insar = store.read_meta("insar", InSAROutput)
        pinn = store.read_meta("pinn", PINNOutput)
        # pinn 의 한 배열만 N 을 31 로 망가뜨린다
        store.write_array(pinn.comp_thermal_ds, np.zeros((31, 12)))
        symbols: dict[str, int] = {}
        store.validate("insar", insar, symbols)  # N=30 결속
        with pytest.raises(ContractViolation, match="N"):
            store.validate("pinn", pinn, symbols)  # 31 ↔ 30 충돌


def test_schema_version_major_mismatch_raises_on_read(project):
    with ProjectStore(project, mode="a") as store:
        raw = store._f["insar"].attrs["meta"]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        data["schema_version"] = "2.0"
        store._f["insar"].attrs["meta"] = json.dumps(data, ensure_ascii=False)
        with pytest.raises(ContractViolation, match="schema_version"):
            store.read_meta("insar", InSAROutput)


# ── 매니페스트 ──
def test_manifest_recorded(project):
    with ProjectStore(project, mode="r") as store:
        man = store.read_manifest()
    assert len(man["run_id"]) == 12
    assert man["schema_version"] == "1.2"
    assert man["engine_modes"] == {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "stub"}
    assert "insar/los" in man["dataset_hashes"]
    assert man["config"]["n_points"] == 30


def test_manifest_invisible_to_dataset_walk(project):
    """_meta 는 attribute 만 담아 데이터셋 목록(골든 회귀 기준)에 안 잡힌다."""
    import h5py

    names: list[str] = []
    with h5py.File(project, "r") as f:
        f.visititems(lambda n, o: names.append(n) if isinstance(o, h5py.Dataset) else None)
    assert not any(n.startswith("_meta") for n in names)


def test_two_runs_same_hashes_different_run_id(tmp_path):
    a, b = tmp_path / "a.h5", tmp_path / "b.h5"
    run_pipeline(a, PipelineConfig(**CFG))
    run_pipeline(b, PipelineConfig(**CFG))
    with ProjectStore(a, mode="r") as sa, ProjectStore(b, mode="r") as sb:
        ma, mb = sa.read_manifest(), sb.read_manifest()
    assert ma["run_id"] != mb["run_id"]            # 실행마다 고유
    assert ma["dataset_hashes"] == mb["dataset_hashes"]  # 결정론적 → 동일 데이터


def test_validation_can_be_disabled(tmp_path):
    out = tmp_path / "p.h5"
    fram = run_pipeline(out, PipelineConfig(validate_contracts=False, write_manifest=False, **CFG))
    assert isinstance(fram, FRAMOutput)
    with ProjectStore(out, mode="r") as store:
        assert store.read_manifest() == {}
