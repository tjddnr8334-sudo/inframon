"""FRAM 6측면 함수망 — 결합 토폴로지·기능 criticality·임계 전파경로."""

from __future__ import annotations

import numpy as np

from inframon.contracts.schema import FRAM_FUNCTIONS
from inframon.fram.network import FRAM_COUPLING, function_network


def test_function_network_structure_and_driver():
    names = ["thermal", "load", "bearing", "foundation"]
    R = np.zeros((4, 4, 8))
    R[0, 2, -2:] = 1.0                       # thermal→bearing 최근 강하게
    fn = function_network(R, names)
    assert fn["driver"] == "thermal"         # 가장 많이 구동
    assert len(fn["edges"]) == len(FRAM_COUPLING)
    assert {"Input", "Output", "Resource", "Control", "Time"} <= set(fn["aspects"])
    assert fn["criticality"]["thermal"] > 0 and fn["criticality"]["bearing"] > 0
    assert fn["critical_path"]               # 비어있지 않음
    assert fn["cohesion"] >= 0


def test_function_network_edges_reference_known_functions():
    fn = function_network(np.abs(np.random.default_rng(0).normal(0, 1, (4, 4, 10))),
                          list(FRAM_FUNCTIONS))
    for e in fn["edges"]:
        assert e["from"] in FRAM_FUNCTIONS and e["to"] in FRAM_FUNCTIONS
        assert e["aspect"] and e["weight"] >= 0


def test_fram_outputs_function_network(tmp_path):
    from inframon.config import PipelineConfig
    from inframon.contracts.io import ProjectStore
    from inframon.orchestrator.pipeline import run_pipeline

    cfg = PipelineConfig(n_points=30, n_dates=14,
                         engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    run_pipeline(tmp_path / "p.h5", cfg)
    with ProjectStore(tmp_path / "p.h5", mode="r") as s:
        fn = s.read_json_attr("fram", "function_network")
    assert {"driver", "criticality", "edges", "aspects", "critical_path", "cohesion"} <= set(fn)
    assert set(fn["criticality"]) == set(FRAM_FUNCTIONS)
    assert fn["driver"] in FRAM_FUNCTIONS
