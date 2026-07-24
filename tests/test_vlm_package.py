"""VLM 입력 패키지 — 폴더 번들·자기기술 manifest·summary 다이제스트·연직채널·ZIP·figures."""

from __future__ import annotations

import json

import numpy as np
import pytest

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import InSAROutput
from inframon.export import COLUMNS
from inframon.orchestrator.pipeline import run_pipeline
from inframon.vlm_package import RISK_NOTE, SCHEMA, build_summary, export_vlm_package


def _project(tmp_path, n_points=14, n_dates=8, with_vertical=False):
    cfg = PipelineConfig(n_points=n_points, n_dates=n_dates,
                         engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    out = tmp_path / "p.h5"
    run_pipeline(out, cfg)
    if with_vertical:
        with ProjectStore(out, mode="a") as s:
            ins = s.read_meta("insar", InSAROutput)
            N, M = ins.n_points, ins.n_dates
            vert = np.zeros((N, M), np.float32)
            vert[N // 2 - 2 : N // 2 + 2] = np.linspace(0, -25, M).astype(np.float32)[None, :]
            s.write_array("/insar/vertical", vert)
            ins.vertical_ds = "/insar/vertical"
            s.write_meta("insar", ins)
    return out


def test_bundle_has_all_files_no_figures(tmp_path):
    out = _project(tmp_path)
    r = export_vlm_package(out, tmp_path / "pkg", bridge_id="B1", with_figures=False)
    pkg = tmp_path / "pkg"
    for f in ("manifest.json", "displacement.csv", "summary.json", "narrative.md"):
        assert (pkg / f).exists(), f
    assert r["figures"] == [] and not (pkg / "figures").exists()
    assert r["rows"] == 14 * 8


def test_manifest_is_self_describing(tmp_path):
    out = _project(tmp_path)
    export_vlm_package(out, tmp_path / "pkg", bridge_id="B1", with_figures=False)
    man = json.loads((tmp_path / "pkg" / "manifest.json").read_text(encoding="utf-8"))
    assert man["schema"] == SCHEMA
    assert man["csv_columns"] == COLUMNS                      # VLM 팀이 추측 불필요
    assert man["enums"]["member"] == ["deck", "pier", "abutment", "bearing"]
    assert "mm" == man["units"]["displacement"]
    assert man["risk_disclaimer"] == RISK_NOTE               # 시방서 판정 아님 명시


def test_summary_digest_structure(tmp_path):
    out = _project(tmp_path)
    with ProjectStore(out, mode="r") as s:
        summ = build_summary(s, bridge_id="B1")
    assert summ["schema"] == SCHEMA and summ["bridge_id"] == "B1"
    assert summ["observation"]["n_points"] == 14
    assert len(summ["settlement_hotspots"]) == 5
    assert summ["channels_present"]["fram_cri"] is True
    # CRI 포함하되 '참고' 명시
    assert summ["risk_reference"]["note"] == RISK_NOTE
    assert "cri_global_max" in summ["risk_reference"]
    # PINN 다이제스트
    assert summ["pinn"] is not None and "EI_Nm2" in summ["pinn"]
    assert len(summ["pinn"]["natural_frequencies_hz"]) >= 1


def test_observational_caveats_reflect_channels(tmp_path):
    """관측 한계: 단일궤도는 '연직 직접 측정 안 됨' 경고, 융합은 '연직 분리됨' (VLM 과대해석 방지)."""
    out_s = _project(tmp_path / "s", with_vertical=False)
    out_v = _project(tmp_path / "v", with_vertical=True)
    with ProjectStore(out_s, mode="r") as s:
        cav_s = build_summary(s, bridge_id="B")["observational_caveats"]
    with ProjectStore(out_v, mode="r") as s:
        cav_v = build_summary(s, bridge_id="B")["observational_caveats"]
    assert any("직접 측정되지 않음" in c for c in cav_s)      # 단일궤도 경고
    assert any("연직 변위" in c and "분리" in c for c in cav_v)  # 융합 신뢰
    assert not any("직접 측정되지 않음" in c for c in cav_v)
    # InSAR 본질 한계는 항상 포함
    assert any("산란체" in c for c in cav_s) and any("LOS" in c for c in cav_s)


def test_vertical_channel_selected_when_fused(tmp_path):
    out_v = _project(tmp_path / "v", with_vertical=True)
    out_s = _project(tmp_path / "s", with_vertical=False)
    with ProjectStore(out_v, mode="r") as s:
        sv = build_summary(s, bridge_id="B")
    with ProjectStore(out_s, mode="r") as s:
        ss = build_summary(s, bridge_id="B")
    assert sv["channels_present"]["vertical_fused"] is True
    assert "vertical_mm" in sv["displacement"]
    assert sv["settlement_hotspots"][0]["channel"] == "vertical"
    # 단일궤도는 los 채널
    assert ss["channels_present"]["vertical_fused"] is False
    assert ss["settlement_hotspots"][0]["channel"] == "los"


def test_zip_packaging(tmp_path):
    import zipfile
    out = _project(tmp_path)
    r = export_vlm_package(out, tmp_path / "pkg", bridge_id="B1",
                           with_figures=False, zip_it=True)
    assert r["zip"] is not None
    zp = tmp_path / "pkg.zip"
    assert zp.exists()
    with zipfile.ZipFile(zp) as zf:
        names = zf.namelist()
    assert "manifest.json" in names and "summary.json" in names


def test_figures_rendered_when_enabled(tmp_path):
    pytest.importorskip("matplotlib")
    out = _project(tmp_path, with_vertical=True)
    r = export_vlm_package(out, tmp_path / "pkg", bridge_id="B1", with_figures=True)
    figdir = tmp_path / "pkg" / "figures"
    # 연직+PINN+FRAM 다 있으니 4종 전부
    for name in ("displacement_map.png", "cri_heatmap.png",
                 "hotspot_timeseries.png", "pinn_components.png"):
        assert (figdir / name).exists() and (figdir / name).stat().st_size > 0, name
    assert len(r["figures"]) == 4


def test_insar_only_project_graceful(tmp_path):
    """InSAR 만(PINN/FRAM 없음, --import-track-h5 산출) 프로젝트도 우아하게 패키징.

    pinn/risk_reference=None, 핫스팟 채널=los, figures 는 PINN/CRI 의존분 생략(2종).
    """
    pytest.importorskip("matplotlib")
    import h5py

    from inframon.contracts.io import ProjectStore
    from inframon.insar.track_reader import import_track_h5

    # 최소 Track H5 → InSAR 만 적재
    N, M = 10, 6
    track = tmp_path / "t.h5"
    with h5py.File(track, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.random.default_rng(0).random((N, 2)) * 5 + 1)
        f.create_dataset("epochs", data=np.array([20240101 + i for i in range(M)], dtype=np.int64))
        f.create_dataset("los_mm", data=np.random.default_rng(1).normal(0, 3, (N, M)).astype("float32"))
        f.create_dataset("coh", data=np.full(N, 0.8, "float32"))
    out = tmp_path / "insar_only.h5"
    with ProjectStore(out, mode="a") as s:
        import_track_h5(s, track)

    export_vlm_package(out, tmp_path / "pkg", bridge_id="IO", with_figures=True)
    summ = json.loads((tmp_path / "pkg" / "summary.json").read_text(encoding="utf-8"))
    assert summ["pinn"] is None and summ["risk_reference"] is None
    assert summ["channels_present"] == {"vertical_fused": False, "pinn": False,
                                        "fram_cri": False, "virtual_sensing": False}
    assert summ["settlement_hotspots"][0]["channel"] == "los"
    # CRI/PINN 없으니 figures 는 변위맵·핫스팟시계열 2종만
    figs = {p.name for p in (tmp_path / "pkg" / "figures").glob("*.png")}
    assert figs == {"displacement_map.png", "hotspot_timeseries.png"}


def test_cli_export_vlm_smoke(tmp_path):
    import subprocess
    import sys
    out = _project(tmp_path)
    pkg = tmp_path / "pkg"
    r = subprocess.run(
        [sys.executable, "-m", "inframon", "--export-vlm", str(pkg),
         "--out", str(out), "--bridge-id", "ID-9", "--no-figures"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    assert (pkg / "summary.json").exists()
    summ = json.loads((pkg / "summary.json").read_text(encoding="utf-8"))
    assert summ["bridge_id"] == "ID-9"
