"""대시보드 데이터 계층 (streamlit 비의존) — 신규 FRAM 출력 노출 검증.

network_resonance(함수망 공명)·calibrated_risk(보정 붕괴확률)가 있으면 패널 데이터로
노출되고, 없으면(stub/캘리브레이터 미설정) None 으로 안전 처리되는지 본다.
"""

from __future__ import annotations

import numpy as np

from inframon.config import PipelineConfig
from inframon.dashboard.data import (
    fram_function_diagram,
    fram_panel_data,
    has_group,
    read_arrays,
    read_meta,
)
from inframon.fram.calibration import IsotonicCalibrator
from inframon.orchestrator.pipeline import run_pipeline


def _run(tmp_path, engines, calibrator=None, n_points=20, n_dates=10):
    cfg = PipelineConfig(n_points=n_points, n_dates=n_dates, engines=engines)
    if calibrator is not None:
        cfg.fram_calibrator = calibrator
    out = tmp_path / "p.h5"
    run_pipeline(out, cfg)
    return str(out)


def test_fram_panel_exposes_network_and_calibrated(tmp_path):
    cal = IsotonicCalibrator().fit(np.array([0.1, 0.2, 0.8, 0.9]), np.array([0, 0, 1, 1]))
    path = _run(
        tmp_path,
        {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"},
        calibrator=cal.to_dict(),
    )
    data = fram_panel_data(path)

    assert data["cri"].shape == (20, 10)
    assert data["network_resonance"] is not None and data["network_resonance"].shape == (10,)
    assert data["calibrated_risk"] is not None and data["calibrated_risk"].shape == (20, 10)
    assert 0.0 <= data["calibrated_max"] <= 1.0
    # 보정확률은 [0,1], 캘리브레이터 매핑 그대로
    assert (data["calibrated_risk"] >= 0).all() and (data["calibrated_risk"] <= 1).all()


def test_fram_panel_exposes_reference_range_and_observation(tmp_path):
    from inframon.fram.reference_range import fit_reference_range
    ref = fit_reference_range([0.05, 0.1, 0.15, 0.2, 0.25]).to_dict()
    cfg = PipelineConfig(n_points=20, n_dates=12,
                         engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    cfg.fram_reference_range = ref
    out = tmp_path / "p.h5"
    run_pipeline(out, cfg)
    data = fram_panel_data(str(out))
    # 정상범위 판독(밴드·백분위·z) + 관측 충분성 노출
    rr = data["reference_range"]
    assert rr is not None and set(rr["band_counts"]) == {"정상", "주의", "경고", "위험"}
    assert "worst_percentile" in rr and "worst_robust_z" in rr
    assert data["observation"] is not None and "sufficient" in data["observation"]
    assert data["cri_percentile"] is not None and data["cri_percentile"].shape == (20,)


def test_fram_panel_reference_range_absent_is_none(tmp_path):
    path = _run(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    data = fram_panel_data(path)
    assert data["reference_range"] is None            # 정상범위 미설정 → None(안전)


def test_fram_panel_stub_has_no_extras(tmp_path):
    path = _run(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "stub"})
    data = fram_panel_data(path)
    assert data["network_resonance"] is None      # stub FRAM 은 미산출
    assert data["calibrated_risk"] is None         # 캘리브레이터 미설정
    assert data["cri"].shape == (20, 10)
    assert data["cri_max"] is not None


def test_fram_function_diagram(tmp_path):
    path = _run(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    diag = fram_function_diagram(path, k=5)
    assert diag is not None
    assert diag["variability"].shape == (4,)
    assert diag["coupling"].shape == (4, 4)
    assert len(diag["func_names"]) == 4
    assert diag["k"] == 5 and diag["n_dates"] == 10
    # k 범위 밖이면 클램프
    assert fram_function_diagram(path, k=999)["k"] == 9


def test_fram_function_diagram_none_without_fram(tmp_path):
    # /pinn·/fram 이 없으면(insar 만) None
    cfg = PipelineConfig(n_points=12, n_dates=6,
                         engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "stub"})
    out = tmp_path / "insar_only.h5"
    from inframon.contracts.io import ProjectStore
    from inframon.cv.engine import run_cv
    from inframon.insar.engine import run_insar
    with ProjectStore(out, mode="w") as store:
        run_insar(store, run_cv(store, cfg), cfg)      # cv+insar 만, pinn/fram 없음
    assert fram_function_diagram(str(out)) is None


def test_data_helpers_basic(tmp_path):
    path = _run(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    assert has_group(path, "fram") and has_group(path, "insar")
    assert not has_group(path, "nonexistent")
    cri = read_arrays(path, "/fram/CRI")
    assert cri.shape == (20, 10)
    assert read_arrays(path, "/fram/does_not_exist") is None
    assert read_meta(path, "fram").get("warning", {}).get("level") in {"정상", "주의", "경고", "위험"}
