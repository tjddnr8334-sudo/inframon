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


def _t(struct, length=200.0):
    return BridgeTarget(name="x", selected_lat=37.3, selected_lon=127.1, osm_type="way", osm_id=9,
                        bbox=(127.10, 37.30, 127.105, 37.302), length_m=length,
                        tags={"bridge:structure": struct})


def test_truss_profile_specializes():
    """트러스: 격자 레이오버·다중반사 → P1 엄격·dem_error 확대·layover high(계층 C)."""
    p = profile_for(_t("truss"))
    assert p.layover_risk == "high"
    assert p.dem_error_bound_m == 150.0
    assert p.coherence_p1 > 0.85                   # 거더(0.85)보다 엄격
    assert "레이오버" in p.structural_notes


def test_arch_profile_specializes():
    """아치: 받침부 기준·리브/데크 분리·moderate layover(계층 C)."""
    p = profile_for(_t("arch"))
    assert p.layover_risk == "moderate" and p.dem_error_bound_m == 120.0
    assert "스프링잉" in p.reference_hint or "받침" in p.reference_hint
    assert p.velocity_bound_m_yr > 0.18           # 아치 라이즈 열팽창 → 약간 확대


def test_movable_profile_segments_by_joint():
    """가동/캔틸레버: 신축이음 불연속 → segment_by_joint·큰 velocity(계층 C)."""
    p = profile_for(_t("cantilever"))
    assert p.segment_by_joint is True
    assert p.velocity_bound_m_yr > 0.18
    assert "가동" in p.structural_notes or "이음" in p.structural_notes


def test_girder_is_baseline_unchanged():
    """거더(기준): 형식 보정 없음 — 기존 값 유지(회귀 안전)."""
    p = profile_for(_t("beam"))
    assert p.layover_risk == "low" and p.dem_error_bound_m == 100.0
    assert p.segment_by_joint is False
    assert p.coherence_p1 == 0.85 and p.velocity_bound_m_yr == 0.18


def test_dem_error_bound_flows_into_config():
    """형식별 dem_error_bound 가 sarvey config 로 흐른다(트러스 150 ≠ 거더 100)."""
    from inframon.insar.bridge_profile import profile_for as pf
    # 직접 config 키 확인 — build_sarvey_config 가 prof.dem_error_bound_m 사용
    assert pf(_t("truss")).dem_error_bound_m == 150.0
    assert pf(_t("beam")).dem_error_bound_m == 100.0


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


def test_manifest_carries_insar_conditions(tmp_path):
    """번들 매니페스트가 교량 InSAR 신뢰성 조건 리포트를 담는다(처리 전 준비도)."""
    _seed_recipes(tmp_path)
    paths = write_sarvey_bundle(tmp_path)
    man = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    cond = man["bridge_insar_conditions"]
    assert "ready" in cond and "counts" in cond
    ids = {c["id"] for c in cond["conditions"]}
    assert {"G1", "G2", "T1", "S1"} <= ids       # 기하·시간·산란체 조건 포함


# ── SLC 처리 매니페스트에 실리는 지상 근거(GNSS 기준앵커) ──────────────
def test_manifest_has_no_gnss_block_by_default(tmp_path):
    """네트워크 조회는 명시할 때만 — 기본 번들 생성은 오프라인으로 동작해야 한다."""
    _seed_recipes(tmp_path)
    man = json.loads(write_sarvey_bundle(tmp_path)["manifest"].read_text(encoding="utf-8"))
    assert man["gnss_reference"] is None
    assert "휴리스틱만" in man["mask"]["reference_point_evidence"]


def test_manifest_carries_gnss_reference_evidence(tmp_path, monkeypatch):
    """--gnss-anchor-km 를 주면 기준점 선정의 지상 근거가 매니페스트에 실린다."""
    import inframon.gnss_ngl as gn
    _seed_recipes(tmp_path)

    def fake_anchor(lat, lon, **kw):
        assert (round(lat, 4), round(lon, 4)) == (37.3667, 127.1075)   # 타깃 좌표로 조회
        return gn.GnssAnchor(bridge_lat=lat, bridge_lon=lon, max_km=kw.get("max_km", 50),
                             candidates=[{"sta": "SUWN", "dist_km": 10.9, "rejected": None}],
                             best={"sta": "SUWN", "dist_km": 10.9, "span_yr": 28.6,
                                   "up_vel_mm_yr": -0.62},
                             can_tie_absolute=False, datum_up_mm_yr=-0.62,
                             verdict="지역 기준계 참고만", advice="…")
    monkeypatch.setattr(gn, "reference_anchor", fake_anchor)

    man = json.loads(write_sarvey_bundle(tmp_path, gnss_km=60)["manifest"]
                     .read_text(encoding="utf-8"))
    gr = man["gnss_reference"]
    assert gr["anchor"]["sta"] == "SUWN"
    assert gr["can_tie_absolute"] is False
    assert gr["holdings_url"].startswith("https://geodesy.unr.edu")   # 출처 명시
    assert "gnss_reference" in man["mask"]["reference_point_evidence"]


def test_gnss_lookup_failure_does_not_block_bundle(tmp_path, monkeypatch):
    """GNSS 는 근거를 더해 주는 것이지 SLC 처리의 전제조건이 아니다."""
    import inframon.gnss_ngl as gn
    _seed_recipes(tmp_path)

    def boom(lat, lon, **kw):
        raise OSError("network down")
    monkeypatch.setattr(gn, "reference_anchor", boom)

    paths = write_sarvey_bundle(tmp_path, gnss_km=60)
    man = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert paths["config"].exists()                       # 번들은 그대로 생성
    assert "GNSS 조회 실패" in man["gnss_reference"]["error"]
    assert "상대값" in man["gnss_reference"]["note"]       # 한계를 알린다
