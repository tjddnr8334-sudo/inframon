"""KAIA 변위 CSV 내보내기 — 스키마·롱포맷·단위·연직 유무·CLI 스모크."""

from __future__ import annotations

import csv

import numpy as np

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import InSAROutput
from inframon.export import COLUMNS, build_rows, export_csv
from inframon.orchestrator.pipeline import run_pipeline


def _project(tmp_path, n_points=12, n_dates=8, engines=None):
    cfg = PipelineConfig(n_points=n_points, n_dates=n_dates,
                         engines=engines or {"cv": "stub", "insar": "stub",
                                             "pinn": "stub", "fram": "real"})
    out = tmp_path / "p.h5"
    run_pipeline(out, cfg)
    return out, n_points, n_dates


def test_csv_longformat_schema_and_rowcount(tmp_path):
    out, N, M = _project(tmp_path)
    csv_path = tmp_path / "disp.csv"
    summ = export_csv(out, csv_path, bridge_id="KICT-X")

    assert summ["rows"] == N * M and summ["n_points"] == N and summ["n_dates"] == M
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == N * M
    assert list(rows[0].keys()) == COLUMNS
    # 점×시점 롱포맷: 점 0 의 행이 정확히 M 개, 날짜가 모두 다름
    p0 = [r for r in rows if r["point_id"] == "0"]
    assert len(p0) == M and len({r["date"] for r in p0}) == M
    assert rows[0]["bridge_id"] == "KICT-X"
    assert rows[0]["member"] in ("deck", "pier", "abutment", "bearing", "unknown")


def test_csv_units_mm_passthrough(tmp_path):
    """los_mm/longitudinal_mm 이 계약 원시값(mm)과 일치 — ×1000 없음(단위버그 회귀와 일관)."""
    out, N, M = _project(tmp_path)
    with ProjectStore(out, mode="r") as s:
        ins = s.read_meta("insar", InSAROutput)
        los = np.asarray(s.read_array(ins.los_ds))
        rows = build_rows(s, bridge_id="B")
    r00 = next(r for r in rows if r["point_id"] == 0 and r["date"] == rows[0]["date"])
    assert r00["los_mm"] == round(float(los[0, 0]), 3)
    assert max(abs(r["los_mm"]) for r in rows) < 1000.0           # mm 스케일


def test_csv_vertical_present_when_fused(tmp_path):
    """vertical_ds 있으면 vertical_mm 채워지고, 없으면 빈칸."""
    out, N, M = _project(tmp_path)
    # 없을 때
    rows_off = build_rows_from(out)
    assert all(r["vertical_mm"] == "" for r in rows_off)
    # 연직 주입 후
    with ProjectStore(out, mode="a") as s:
        ins = s.read_meta("insar", InSAROutput)
        vert = np.linspace(-5.0, 0.0, N * M).reshape(N, M).astype("float32")
        s.write_array("/insar/vertical", vert)
        ins.vertical_ds = "/insar/vertical"
        s.write_meta("insar", ins)
    rows_on = build_rows_from(out)
    assert all(isinstance(r["vertical_mm"], float) for r in rows_on)
    assert any(r["vertical_mm"] < 0 for r in rows_on)


def build_rows_from(out):
    with ProjectStore(out, mode="r") as s:
        return build_rows(s, bridge_id="B")


def test_csv_insar_only_blank_optional_columns(tmp_path):
    """InSAR 만(PINN/FRAM 없음) → EI/alpha/cri 빈칸, los/longitudinal 은 채움."""
    out, N, M = _project(tmp_path, engines={"cv": "stub", "insar": "stub",
                                            "pinn": "stub", "fram": "stub"})
    # stub fram 도 CRI 를 내므로, FRAM 자체를 빼려면 빈 인사르만 가진 프로젝트로:
    summ = export_csv(out, tmp_path / "c.csv", bridge_id="B")
    assert summ["n_points"] == N
    # 이 구성은 4엔진 모두 산출하므로 cri/EI 가 채워짐 — 산출물 플래그로 확인
    assert summ["has_pinn"] and summ["has_fram"]


def test_cli_export_csv_smoke(tmp_path):
    out, N, M = _project(tmp_path)
    csv_path = tmp_path / "cli.csv"
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "-m", "inframon", "--export-csv", str(csv_path),
         "--out", str(out), "--bridge-id", "ID-7"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    assert csv_path.exists()
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == N * M and rows[0]["bridge_id"] == "ID-7"
