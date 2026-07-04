"""VLM 백엔드 확장점 — Protocol·레지스트리(register/resolve)·template 스텁·평가."""

from __future__ import annotations

import json

import pytest

from inframon.config import PipelineConfig
from inframon.orchestrator.pipeline import run_pipeline
from inframon.vlm import (
    VLMAssessment,
    VLMBackend,
    available_backends,
    register_backend,
    resolve_backend,
    run_vlm_assessment,
)
from inframon.vlm.backend import TemplateBackend
from inframon.vlm_package import export_vlm_package


def _package(tmp_path):
    cfg = PipelineConfig(n_points=12, n_dates=6,
                         engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    out = tmp_path / "p.h5"
    run_pipeline(out, cfg)
    export_vlm_package(out, tmp_path / "pkg", bridge_id="B1", with_figures=False)
    return tmp_path / "pkg"


def test_template_backend_registered():
    assert "template" in available_backends()
    b = resolve_backend("template")
    assert b.name == "template"
    assert isinstance(b, VLMBackend)          # Protocol 만족(runtime_checkable)


def test_template_evaluate_is_not_code_judgment(tmp_path):
    pkg = _package(tmp_path)
    a = run_vlm_assessment(pkg, backend="template")
    assert a["schema"].startswith("inframon.vlm_assessment")
    assert a["backend"] == "template"
    assert a["is_code_judgment"] is False
    assert a["verdict"] is None               # 스텁은 판정하지 않음
    assert "시방서" in a["disclaimer"]
    assert (pkg / "assessment.json").exists()
    disk = json.loads((pkg / "assessment.json").read_text(encoding="utf-8"))
    assert disk == a


def test_grounded_context_reflects_package(tmp_path):
    pkg = _package(tmp_path)
    a = run_vlm_assessment(pkg, backend="template", write=False)
    gc = a["grounded_context"]
    assert gc["observation"]["n_points"] == 12
    assert gc["observation"]["n_dates"] == 6
    assert "fram_cri" in gc["channels_present"]


def test_resolve_unknown_backend_raises():
    with pytest.raises(NotImplementedError, match="template"):
        resolve_backend("no_such_model")


def test_missing_package_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_vlm_assessment(tmp_path / "not_a_package")


def test_register_custom_backend_plugs_in(tmp_path):
    """실 모델이 끼워지는 소켓 — 커스텀 백엔드가 판정을 채워 반환."""
    class _FakeVLM:
        name = "fake"

        def evaluate(self, package_dir):
            return VLMAssessment(backend="fake", bridge_id="B1", is_code_judgment=True,
                                 verdict="주의", findings=["처짐 허용치 근접(모의)"])

    register_backend("fake", _FakeVLM)
    try:
        assert "fake" in available_backends()
        pkg = _package(tmp_path)
        a = run_vlm_assessment(pkg, backend="fake")
        assert a["backend"] == "fake"
        assert a["is_code_judgment"] is True
        assert a["verdict"] == "주의"
    finally:
        from inframon.vlm import backend as _b
        _b._BACKENDS.pop("fake", None)        # 레지스트리 원복(테스트 격리)


def test_template_backend_satisfies_protocol():
    assert isinstance(TemplateBackend(), VLMBackend)
