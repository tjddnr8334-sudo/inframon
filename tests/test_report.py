"""리포트(PDF) 생성 회귀 — 데모 project.h5 로 PDF 가 정상 생성되는지."""
from __future__ import annotations

from pathlib import Path

from inframon.config import PipelineConfig
from inframon.dashboard.report import build_report
from inframon.orchestrator.pipeline import run_pipeline


def test_build_report_creates_valid_pdf(tmp_path):
    proj = tmp_path / "project.h5"
    run_pipeline(str(proj), PipelineConfig(n_points=30, n_dates=8))   # stub 데모
    out = build_report(str(proj), tmp_path / "report.pdf", bridge_name="테스트교")
    assert Path(out).exists()
    data = Path(out).read_bytes()
    assert data[:4] == b"%PDF"                 # 유효 PDF 헤더
    assert len(data) > 5000                    # 비어있지 않은 리포트(차트 포함)


def test_build_report_without_bridge_name(tmp_path):
    proj = tmp_path / "p.h5"
    run_pipeline(str(proj), PipelineConfig(n_points=20, n_dates=6))
    out = build_report(str(proj), tmp_path / "r.pdf")
    assert Path(out).exists() and Path(out).read_bytes()[:4] == b"%PDF"
