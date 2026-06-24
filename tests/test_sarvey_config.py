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


# ───────────────────────── 교량 형식별 특화 ─────────────────────────
from inframon.insar.bridge_profile import (  # noqa: E402
    CABLE_STAYED, GIRDER, classify_bridge, profile_for, water_context_for,
)


def test_classify_from_osm_tags():
    assert classify_bridge({"bridge:structure": "cable-stayed"}) == CABLE_STAYED
    assert classify_bridge({"bridge:structure": "suspension"}) == "suspension"
    assert classify_bridge({"bridge:structure": "arch"}) == "arch"
    assert classify_bridge({"bridge:structure": "beam"}) == GIRDER
    # 태그 불충분 → 길이 폴백: 초장대는 케이블계 추정
    assert classify_bridge({"bridge": "yes"}, length_m=1500) == CABLE_STAYED
    assert classify_bridge({"bridge": "yes"}, length_m=110) == GIRDER


def test_water_context_marine_vs_river():
    assert water_context_for(CABLE_STAYED, 1500) == "marine"
    assert water_context_for(GIRDER, 110) == "river"
    assert water_context_for(GIRDER, 2500) == "marine"   # 초장대 거더 → 해상


def test_cable_bridge_profile_specializes():
    target = BridgeTarget(
        name="대교", selected_lat=35.0, selected_lon=129.0, osm_type="way", osm_id=1,
        bbox=(129.0, 35.0, 129.02, 35.01), length_m=1800.0,
        tags={"bridge:structure": "cable-stayed"})
    p = profile_for(target)
    assert p.bridge_class == CABLE_STAYED and p.water_context == "marine"
    assert p.velocity_bound_m_yr == 0.30          # 케이블계 → 넓은 속도 한계
    assert p.coherence_p1 == 0.90                 # 해상 → P1 엄격
    assert p.water_mask["strength"] == "strong"
    assert "주탑" in p.reference_hint


def test_river_girder_profile_specializes():
    target = BridgeTarget(
        name="정자교", selected_lat=37.36, selected_lon=127.10, osm_type="way", osm_id=2,
        bbox=(127.108, 37.368, 127.110, 37.369), length_m=110.5, tags={"bridge": "yes"})
    p = profile_for(target)
    assert p.bridge_class == GIRDER and p.water_context == "river"
    assert p.velocity_bound_m_yr == 0.18
    assert p.coherence_p1 == 0.85                 # 하천 → 조밀화 위해 완화
    assert p.water_mask["context"] == "river"
    assert "교대" in p.reference_hint


def test_config_grid_is_bridge_scaled(tmp_path):
    _seed_recipes(tmp_path)                        # 정자교 110.5m
    cfg = build_sarvey_config(RecipeBundle(tmp_path))
    # 도시용 200m 가 아니라 교량 길이 유도값(15~60m)
    assert 15 <= cfg["consistency_check"]["grid_size"] <= 60
    assert cfg["consistency_check"]["velocity_bound"] == 0.18
    assert cfg["densification"]["max_distance_to_p1"] <= 1000.0
    assert cfg["bridge_profile"]["water_context"] == "river"


def test_manifest_carries_water_mask(tmp_path):
    _seed_recipes(tmp_path)
    paths = write_sarvey_bundle(tmp_path)
    man = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert man["mask"]["water_mask"]["apply"] is True
    assert man["mask"]["water_mask"]["context"] == "river"
    assert man["mask"]["deck_buffer_m"] == 30
    assert man["bridge_profile"]["class_ko"] == "거더교"
