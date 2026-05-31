"""레시피 → SARvey 번들 생성 (InSAR F 준비) 검증."""

from __future__ import annotations

import json

import pytest

from inframon.insar.recipe import (
    BridgeTarget,
    MasterSelection,
    SceneWeather,
    SelectionCriteria,
    TrackSelection,
    save_bridge_target,
    save_master_selection,
    save_selection_criteria,
    save_track_selection,
)
from inframon.insar.sarvey_config import RecipeBundle, build_sarvey_config, write_sarvey_bundle


def _seed_recipes(d):
    save_bridge_target(d / "bridge_target.json", BridgeTarget(
        name="정자교", name_ko="정자교", selected_lat=37.3667, selected_lon=127.1075,
        osm_type="way", osm_id=51473197, bbox=(127.1084, 37.3685, 127.1096, 37.3686),
        length_m=110.5, distance_m=218.0, tags={"bridge": "yes"}))
    save_selection_criteria(d / "selection_criteria.json", SelectionCriteria())
    save_track_selection(d / "track_selection.json", TrackSelection(
        flight_direction="ASCENDING", path=127, frame=115, polarization="VV",
        n_scenes=3, first_date="20230112", last_date="20230205",
        scene_dates=["20230112", "20230124", "20230205"],
        scene_names=["s12", "s24", "s05"]))
    save_master_selection(d / "master_selection_era5.json", MasterSelection(
        selected_master="20230124", master_scene="s24", lat=37.3667, lon=127.1075,
        scenes=[SceneWeather(date="20230124", precip_mm=0.0, humidity_pct=34.6, combined=0.9)]))


def test_write_bundle_creates_both(tmp_path):
    _seed_recipes(tmp_path)
    paths = write_sarvey_bundle(tmp_path)
    assert paths["manifest"].exists() and paths["config"].exists()


def test_manifest_carries_stack_params(tmp_path):
    _seed_recipes(tmp_path)
    paths = write_sarvey_bundle(tmp_path)
    man = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    assert man["stack"]["orbit_direction"] == "ASCENDING"
    assert man["stack"]["relative_orbit"] == 127
    assert man["stack"]["frame"] == 115
    assert man["stack"]["polarization"] == "VV"
    assert man["stack"]["reference_date"] == "20230124"   # master
    assert man["stack"]["num_scenes"] == 3
    assert man["baseline"]["max_perp_baseline_m"] == 150.0
    assert man["aoi"]["bbox_lonlat"] == [127.1084, 37.3685, 127.1096, 37.3686]


def test_sarvey_config_dates_and_tbase(tmp_path):
    _seed_recipes(tmp_path)
    paths = write_sarvey_bundle(tmp_path)
    cfg = json.loads(paths["config"].read_text(encoding="utf-8"))

    assert cfg["preparation"]["start_date"] == "2023-01-12"
    assert cfg["preparation"]["end_date"] == "2023-02-05"
    # 시간 baseline 미설정 → 기본 100
    assert cfg["preparation"]["max_tbase"] == 100
    assert "general" in cfg and "filtering" in cfg


def test_temporal_baseline_flows_into_config(tmp_path):
    _seed_recipes(tmp_path)
    # 시간 baseline 36일 설정 → max_tbase 36
    save_selection_criteria(tmp_path / "selection_criteria.json",
                            SelectionCriteria(temporal_baseline_max_days=36.0))
    cfg = build_sarvey_config(RecipeBundle(tmp_path))
    assert cfg["preparation"]["max_tbase"] == 36


def test_requires_target_and_track(tmp_path):
    # 빈 디렉터리 → 필수 레시피 없음
    with pytest.raises(FileNotFoundError):
        write_sarvey_bundle(tmp_path)
