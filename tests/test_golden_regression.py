"""골든 회귀 테스트 — 고정 시드 데모 산출물의 계약·수치 스냅샷.

고정 설정(seed=42)에서 파이프라인은 결정론적이다. 엔진을 stub↔real 로 교체하거나
리팩터링하다가

  (1) 계약이 깨지거나 (데이터셋 누락/형상·dtype 변경, V_func_series 순서 변동 등)
  (2) 수치 출력이 의도치 않게 바뀌면

골든과 달라져 즉시 잡힌다. 의도된 변경이면 골든을 갱신한다:

  UPDATE_GOLDEN=1 pytest tests/test_golden_regression.py     (bash)
  $env:UPDATE_GOLDEN=1; pytest tests/test_golden_regression.py  (PowerShell)

골든 파일은 tests/golden/ 에 저장된다(베이스라인으로 커밋).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import h5py
import numpy as np
import pytest

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import FRAMOutput, PINNOutput
from inframon.orchestrator.pipeline import run_pipeline

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_META = GOLDEN_DIR / "demo_fingerprint.json"
GOLDEN_ARRAYS = GOLDEN_DIR / "demo_arrays.npz"

# 고정·결정론적·작게(빠른 테스트). seed 는 PipelineConfig 기본값 42.
GOLDEN_CFG = {"n_points": 60, "n_dates": 24}
RTOL, ATOL = 1e-6, 1e-9


def _structural(path: Path) -> dict:
    """데이터셋 형상/dtype + 핵심 스칼라(사람이 읽는 계약 지문)."""
    datasets: dict[str, dict] = {}
    with h5py.File(path, "r") as f:
        f.visititems(
            lambda name, obj: datasets.__setitem__(
                name, {"shape": list(obj.shape), "dtype": str(obj.dtype)}
            )
            if isinstance(obj, h5py.Dataset)
            else None
        )
    with ProjectStore(path, mode="r") as store:
        fram = store.read_meta("fram", FRAMOutput)
        pinn = store.read_meta("pinn", PINNOutput)
    scalars = {
        "cri_global_max": round(float(fram.cri_global_max), 6),
        "level": fram.warning.level,
        "critical_members": sorted(fram.warning.critical_members),
        "lead_time_days": (
            None
            if fram.warning.lead_time_days is None
            else round(float(fram.warning.lead_time_days), 3)
        ),
        # ★ FRAM 공명 입력의 행 순서 계약 — 절대 보존 대상
        "func_names": list(pinn.func_names),
    }
    return {"datasets": dict(sorted(datasets.items())), "scalars": scalars}


def _arrays(path: Path) -> dict[str, np.ndarray]:
    """모든 데이터셋의 실제 값(수치 회귀 비교용)."""
    out: dict[str, np.ndarray] = {}
    with h5py.File(path, "r") as f:
        f.visititems(
            lambda name, obj: out.__setitem__(name.replace("/", "__"), np.asarray(obj[()]))
            if isinstance(obj, h5py.Dataset)
            else None
        )
    return out


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """고정 설정으로 파이프라인을 1회 실행. 골든이 없거나 갱신 요청 시 생성 후 skip."""
    out = tmp_path_factory.mktemp("golden") / "project.h5"
    run_pipeline(out, PipelineConfig(**GOLDEN_CFG))

    update = os.environ.get("UPDATE_GOLDEN") == "1"
    if update or not (GOLDEN_META.exists() and GOLDEN_ARRAYS.exists()):
        GOLDEN_DIR.mkdir(exist_ok=True)
        GOLDEN_META.write_text(
            json.dumps(_structural(out), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        np.savez_compressed(GOLDEN_ARRAYS, **_arrays(out))
        pytest.skip(f"골든 {'갱신' if update else '생성'}됨 ({GOLDEN_DIR}) — 다시 실행해 비교하세요")
    return out


def test_structural_contract_matches_golden(project):
    """데이터셋 형상/dtype/존재 + 핵심 스칼라가 골든과 일치하는지."""
    golden = json.loads(GOLDEN_META.read_text(encoding="utf-8"))
    current = _structural(project)

    # 데이터셋 집합 자체가 바뀌면(누락/추가) 먼저 명확히 알린다
    assert set(current["datasets"]) == set(golden["datasets"]), (
        "데이터셋 구성 변경: "
        f"추가={sorted(set(current['datasets']) - set(golden['datasets']))}, "
        f"누락={sorted(set(golden['datasets']) - set(current['datasets']))}"
    )
    assert current["datasets"] == golden["datasets"], "데이터셋 형상/dtype 변경"
    assert current["scalars"] == golden["scalars"], (
        f"핵심 스칼라 변경:\n  현재={current['scalars']}\n  골든={golden['scalars']}"
    )


def test_numerical_output_matches_golden(project):
    """모든 데이터셋의 수치 값이 허용오차 내에서 골든과 일치하는지."""
    golden = np.load(GOLDEN_ARRAYS, allow_pickle=False)
    current = _arrays(project)

    assert set(current) == set(golden.files), "데이터셋 키 집합 불일치"

    mismatches = []
    for key in sorted(current):
        a, b = current[key], golden[key]
        if a.shape != b.shape:
            mismatches.append(f"{key}: 형상 {a.shape} != {b.shape}")
        elif a.dtype.kind in "fc":  # float/complex
            if not np.allclose(a, b, rtol=RTOL, atol=ATOL, equal_nan=True):
                mismatches.append(f"{key}: 수치 변동 (max|Δ|={np.abs(a - b).max():.3e})")
        elif not np.array_equal(a, b):
            mismatches.append(f"{key}: 값 불일치")

    assert not mismatches, "골든 회귀 불일치 (의도된 변경이면 UPDATE_GOLDEN=1 로 갱신):\n" + "\n".join(
        mismatches
    )


def test_pipeline_is_deterministic(tmp_path):
    """동일 설정 2회 실행이 비트 단위로 동일한지(결정론성 보장 = 골든의 전제)."""
    cfg_kwargs = {"n_points": 40, "n_dates": 18}
    p1 = tmp_path / "a.h5"
    p2 = tmp_path / "b.h5"
    run_pipeline(p1, PipelineConfig(**cfg_kwargs))
    run_pipeline(p2, PipelineConfig(**cfg_kwargs))

    a, b = _arrays(p1), _arrays(p2)
    assert set(a) == set(b)
    for key in a:
        assert np.array_equal(a[key], b[key], equal_nan=True), f"{key} 비결정론적"
