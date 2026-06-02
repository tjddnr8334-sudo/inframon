"""FRAM 실시간 서빙 — read_monitor(순수) + FastAPI 엔드포인트."""

from __future__ import annotations

import pytest

from inframon.config import PipelineConfig
from inframon.orchestrator.pipeline import run_pipeline
from inframon.serve import read_monitor


def _project(tmp_path, fram="real"):
    cfg = PipelineConfig(n_points=30, n_dates=14,
                         engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": fram})
    out = tmp_path / "p.h5"
    run_pipeline(out, cfg)
    return out


# ── read_monitor (fastapi 불필요) ──
def test_read_monitor_fields(tmp_path):
    m = read_monitor(_project(tmp_path))
    assert m is not None
    assert m["level"] in {"정상", "주의", "경고", "위험"}
    assert m["basis"] in {"cri", "calibrated_probability"}
    assert set(m["function_states"]) and 0.0 <= m["cri_global_max"] <= 1.0
    assert len(m["cri_max_series"]) == 14
    assert m["function_network"] is not None and "driver" in m["function_network"]


def test_read_monitor_missing(tmp_path):
    assert read_monitor(tmp_path / "none.h5") is None
    empty = tmp_path / "empty.h5"
    from inframon.contracts.io import ProjectStore
    with ProjectStore(empty, mode="w"):
        pass
    assert read_monitor(empty) is None                 # /fram 없음


# ── FastAPI 엔드포인트 ──
def test_endpoints(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from inframon.serve import create_app
    client = TestClient(create_app(_project(tmp_path)))

    assert client.get("/health").json()["status"] == "ok"
    st = client.get("/status").json()
    assert st["level"] in {"정상", "주의", "경고", "위험"}
    assert "function_states" in st and "lead_time_forecast_days" in st
    cri = client.get("/cri").json()
    assert len(cri["cri_max_series"]) == 14
    fnet = client.get("/function-network").json()
    assert fnet["driver"] in fnet["criticality"]


def test_endpoint_404_without_fram(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from inframon.contracts.io import ProjectStore
    from inframon.serve import create_app
    empty = tmp_path / "empty.h5"
    with ProjectStore(empty, mode="w"):
        pass
    client = TestClient(create_app(empty))
    assert client.get("/health").json()["has_fram"] is False
    assert client.get("/status").status_code == 404
