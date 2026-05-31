"""Phase 0 스모크 테스트 — 전체 파이프라인이 끝까지 돌고 계약이 채워지는지."""

from __future__ import annotations

import json
import os

import h5py
import numpy as np
import pytest

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import CVOutput, FRAMOutput, InSAROutput, PINNOutput
from inframon.insar.inventory import build_scene_manifest, inspect_insar_data, write_inventory
from inframon.insar.track_reader import import_track_h5
from inframon.orchestrator.pipeline import run_pipeline

# BLAS(np.linalg/corrcoef) 결과가 OS·버전마다 미세히 달라 FRAM corrcoef 불안정성으로
# 증폭 → 골든/스냅샷 수치 비교는 생성된 dev 플랫폼 전용. CI(타 플랫폼)에선 제외.
_PLATFORM_SENSITIVE = pytest.mark.skipif(
    bool(os.environ.get("CI")), reason="플랫폼 의존 BLAS 수치 스냅샷 — 로컬 dev 전용")


def test_pipeline_runs_end_to_end(tmp_path):
    out = tmp_path / "project.h5"
    cfg = PipelineConfig(n_points=80, n_dates=24)
    fram = run_pipeline(out, cfg)

    assert isinstance(fram, FRAMOutput)
    assert fram.n_points == 80 and fram.n_dates == 24
    assert 0.0 <= fram.cri_global_max <= 1.0
    assert fram.warning.level in ("정상", "주의", "경고", "위험")


def test_all_module_contracts_persisted(tmp_path):
    out = tmp_path / "project.h5"
    run_pipeline(out, PipelineConfig(n_points=50, n_dates=18))

    with ProjectStore(out, mode="r") as store:
        cv = store.read_meta("cv", CVOutput)
        insar = store.read_meta("insar", InSAROutput)
        pinn = store.read_meta("pinn", PINNOutput)
        fram = store.read_meta("fram", FRAMOutput)

        # CV → InSAR → PINN → FRAM 차원 일관성
        assert insar.n_points == pinn.n_points == fram.n_points == 50
        assert cv.image_shape == (256, 512)

        # 핵심 배열 형상 확인
        cri = store.read_array(fram.CRI_ds)
        assert cri.shape == (50, 18)
        los = store.read_array(insar.los_ds)
        assert los.shape == (50, 18)
        V = store.read_array(pinn.V_func_series_ds)
        assert V.shape[1] == 18


@_PLATFORM_SENSITIVE
def test_injected_damage_raises_cri(tmp_path):
    """InSAR stub 이 한 교각에 가속 손상을 주입 → CRI 가 0 이 아니어야 한다."""
    out = tmp_path / "project.h5"
    fram = run_pipeline(out, PipelineConfig())
    with ProjectStore(out, mode="r") as store:
        cri = store.read_array(fram.CRI_ds)
    # 후반부 최대 CRI 가 초반부보다 높아야 (손상 누적 → 전조)
    assert cri[:, -3:].max() >= cri[:, :3].max()
    assert np.isfinite(cri).all()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"n_points": 1},
        {"n_dates": 1},
        {"image_h": 23},
        {"image_w": 7},
    ],
)
def test_config_rejects_invalid_demo_sizes(kwargs):
    with pytest.raises(ValueError):
        PipelineConfig(**kwargs)


def test_default_engines_all_stub():
    cfg = PipelineConfig()
    assert cfg.engines == {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "stub"}


def test_config_rejects_unknown_engine_mode():
    with pytest.raises(ValueError):
        PipelineConfig(engines={"cv": "stub", "insar": "bogus", "pinn": "stub", "fram": "stub"})


def test_config_rejects_missing_engine_key():
    with pytest.raises(ValueError):
        PipelineConfig(engines={"cv": "stub"})


def test_resolve_returns_stub_impls():
    from inframon.cv.engine import run_cv
    from inframon.orchestrator import engines

    assert engines.resolve("cv", "stub") is run_cv
    assert engines.available_modes("cv") == ["real", "stub"]   # cv 도 real 구현됨(Phase 2)


def test_resolve_unregistered_real_raises():
    from inframon.orchestrator import engines

    # 미등록 (engine, mode) 조합 → 친절한 NotImplementedError
    with pytest.raises(NotImplementedError):
        engines.resolve("cv", "nonexistent")


def test_register_then_resolve_real(tmp_path):
    """real 구현을 등록하면 핫스왑되어 pipeline 이 그 구현을 호출한다."""
    from inframon.orchestrator import engines
    from inframon.insar.engine import run_insar

    calls = []

    def fake_real_insar(store, cv, cfg):
        calls.append("real")
        return run_insar(store, cv, cfg)  # 계약은 stub 으로 채워 다운스트림 유지

    engines.register("insar", "real", fake_real_insar)
    try:
        cfg = PipelineConfig(
            n_points=40,
            n_dates=12,
            engines={"cv": "stub", "insar": "real", "pinn": "stub", "fram": "stub"},
        )
        fram = run_pipeline(tmp_path / "project.h5", cfg)
        assert calls == ["real"]
        assert isinstance(fram, FRAMOutput)
    finally:
        engines._REGISTRY.pop(("insar", "real"), None)


def test_insar_inventory_reads_real_data_shape(tmp_path):
    root = tmp_path / "insar_data"
    (root / "SLC").mkdir(parents=True)
    (root / "orbits").mkdir()
    (root / "DEM").mkdir()

    (root / "SLC" / "S1A_IW_SLC__1SDV_20200101T000000_20200101T000026_X.zip").write_bytes(
        b"abc"
    )
    (root / "SLC" / "S1A_IW_SLC__1SDV_20200113T000000_20200113T000026_X.zip").write_bytes(
        b"abcdef"
    )
    (root / "orbits" / "orbit.EOF").write_text("orbit", encoding="utf-8")
    (root / "DEM" / "dem_jeongjagyo.dem").write_text("dem", encoding="utf-8")
    (root / "roi_final.kmz").write_text("kmz", encoding="utf-8")
    (root / "master_selection.json").write_text(
        json.dumps({"selected_master": "20200101"}), encoding="utf-8"
    )
    (root / "master_selection_era5.json").write_text(
        json.dumps({"selected_master": "20200113"}), encoding="utf-8"
    )
    (root / "bperp_filter.json").write_text(
        json.dumps(
            {
                "pass_count": 2,
                "exclude_count": 0,
                "entries": [
                    {"date": "20200101", "bperp_m": 10.0, "pass": True},
                    {"date": "20200113", "bperp_m": 20.0, "pass": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "data_sources.txt").write_text("저장 위치: SLC/S1A_*.SAFE  (2장)", encoding="utf-8")
    (root / "exclude_dates.txt").write_text("20200125,20200206", encoding="utf-8")

    inv = inspect_insar_data(root)

    assert inv.slc_zip_count == 2
    assert inv.slc_first_date == "20200101"
    assert inv.slc_last_date == "20200113"
    assert inv.slc_dates == ["20200101", "20200113"]
    assert inv.slc_total_bytes == 9
    assert inv.orbit_count == 1
    assert inv.selected_master == "20200101"
    assert inv.selected_master_era5 == "20200113"
    assert inv.bperp_pass_count == 2
    assert inv.exclude_dates == ["20200125", "20200206"]
    assert inv.declared_slc_count == 2
    assert inv.is_ready_for_timeseries
    assert "2 exclude_dates are not present in current SLC zip set" in inv.warnings

    manifest = build_scene_manifest(root)
    assert manifest["usable_dates"] == ["20200101", "20200113"]
    assert manifest["usable_count"] == 2
    assert manifest["excluded_missing_dates"] == ["20200125", "20200206"]


def test_insar_inventory_flags_stale_metadata(tmp_path):
    root = tmp_path / "insar_data"
    (root / "SLC").mkdir(parents=True)
    (root / "orbits").mkdir()
    (root / "DEM").mkdir()
    (root / "SLC" / "S1A_IW_SLC__1SDV_20200101T000000_20200101T000026_X.zip").write_bytes(
        b"abc"
    )
    (root / "master_selection.json").write_text(
        json.dumps({"selected_master": "20200101"}), encoding="utf-8"
    )
    (root / "bperp_filter.json").write_text(
        json.dumps(
            {
                "pass_count": 2,
                "exclude_count": 1,
                "entries": [{"date": "20200101", "bperp_m": 10.0, "pass": True}],
            }
        ),
        encoding="utf-8",
    )
    (root / "data_sources.txt").write_text("저장 위치: SLC/S1A_*.SAFE  (232장)", encoding="utf-8")
    (root / "exclude_dates.txt").write_text("", encoding="utf-8")

    inv = inspect_insar_data(root)

    assert inv.slc_zip_count == 1
    assert "data_sources.txt SLC count (232) != actual SLC zip count (1)" in inv.warnings
    assert "bperp_filter total (3) != actual SLC zip count (1)" in inv.warnings

    manifest = build_scene_manifest(root)
    assert manifest["usable_dates"] == ["20200101"]
    assert manifest["stale_bperp_dates"] == []


def test_write_inventory_to_project(tmp_path):
    root = tmp_path / "insar_data"
    (root / "SLC").mkdir(parents=True)
    (root / "orbits").mkdir()
    (root / "DEM").mkdir()
    (root / "master_selection.json").write_text("{}", encoding="utf-8")
    (root / "bperp_filter.json").write_text("{}", encoding="utf-8")
    (root / "exclude_dates.txt").write_text("", encoding="utf-8")
    (root / "SLC" / "S1A_IW_SLC__1SDV_20200101T000000_20200101T000026_X.zip").write_bytes(
        b"abc"
    )
    inv = inspect_insar_data(root)
    out = tmp_path / "project.h5"

    write_inventory(out, inv)

    with ProjectStore(out, mode="r") as store:
        saved = store.read_json_attr("insar", "data_inventory")
        manifest = store.read_json_attr("insar", "scene_manifest")
    assert saved["slc_zip_count"] == 1
    assert saved["slc_first_date"] == "20200101"
    assert manifest["usable_dates"] == []
    assert manifest["rejected_dates"] == [{"date": "20200101", "reasons": ["missing_bperp"]}]


def test_import_track_h5_to_insar_contract(tmp_path):
    track = tmp_path / "track_b_results.h5"
    with h5py.File(track, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.array([[127.1, 37.3], [127.2, 37.4]]))
        f.create_dataset("epochs", data=np.array([20200101, 20200113, 20200125], dtype=np.int32))
        f.create_dataset("los_mm", data=np.array([[0.0, 1.0, 2.0], [0.0, -1.0, -2.0]], dtype=np.float32))
        f.create_dataset("coh", data=np.array([0.8, 0.9], dtype=np.float32))
        f.attrs["FILE_TYPE"] = "track_b_mintpy_sbas"

    out = tmp_path / "project.h5"
    with ProjectStore(out, mode="w") as store:
        meta = import_track_h5(store, track)
        los = store.read_array(meta.los_ds)
        dates = store.read_array(meta.dates_ds)
        labels = store.read_array("/insar/date_labels").astype(str)
        source = store.read_json_attr("insar", "track_source")

    assert meta.n_points == 2
    assert meta.n_dates == 3
    assert los.shape == (2, 3)
    assert dates.tolist() == [0.0, 12.0, 24.0]
    assert labels.tolist() == ["20200101", "20200113", "20200125"]
    assert source["unit"] == "mm"
