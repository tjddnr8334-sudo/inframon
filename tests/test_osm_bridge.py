"""OSM 교량 조회·확인 + BridgeTarget 레시피 (InSAR A·B 단계) — 네트워크 없이 검증.

Overpass 응답을 monkeypatch 로 주입해 파싱·거리·bbox·길이 계산과 레시피 왕복을 테스트한다.
"""

from __future__ import annotations

from inframon.insar import osm_bridge
from inframon.insar.recipe import (
    BridgeTarget,
    SelectionCriteria,
    load_bridge_target,
    load_selection_criteria,
    save_bridge_target,
    save_selection_criteria,
)

# 정자교 부근을 흉내낸 canned Overpass 응답 (way 1개, 노드 3개)
CANNED = {
    "elements": [
        {
            "type": "way",
            "id": 123456,
            "tags": {"bridge": "yes", "name": "정자교", "name:ko": "정자교", "highway": "primary"},
            "geometry": [
                {"lat": 37.3300, "lon": 127.1100},
                {"lat": 37.3305, "lon": 127.1110},
                {"lat": 37.3310, "lon": 127.1120},
            ],
        }
    ]
}


def test_find_bridges_parses_response(monkeypatch):
    monkeypatch.setattr(osm_bridge, "_overpass_query", lambda ql, **k: CANNED)
    bridges = osm_bridge.find_bridges_near(37.3305, 127.1110, radius_m=200)

    assert len(bridges) == 1
    b = bridges[0]
    assert b.name == "정자교" and b.name_ko == "정자교"
    assert b.osm_type == "way" and b.osm_id == 123456
    # bbox = (min_lon, min_lat, max_lon, max_lat)
    assert b.bbox == (127.1100, 37.3300, 127.1120, 37.3310)
    assert b.length_m > 0
    assert b.distance_m >= 0
    assert b.osm_url.endswith("/way/123456")


_NOMINATIM = [
    {"display_name": "정자교, 분당구, 성남시", "type": "bridge", "class": "man_made",
     "osm_type": "way", "osm_id": 111, "lat": "37.3305", "lon": "127.1110", "extratags": {}},
    {"display_name": "한강대교, 용산구, 서울", "type": "primary", "class": "highway",
     "osm_type": "way", "osm_id": 222, "lat": "37.514", "lon": "126.956", "extratags": {}},
    {"display_name": "서울역", "type": "station", "class": "railway",   # 교량 아님 → 제외
     "osm_type": "node", "osm_id": 333, "lat": "37.55", "lon": "126.97"},
]


def test_find_bridges_by_name(monkeypatch):
    captured = {}

    def fake(q, **k):
        captured["q"] = q
        return _NOMINATIM

    monkeypatch.setattr(osm_bridge, "_nominatim_query", fake)
    out = osm_bridge.find_bridges_by_name("교", limit=10)
    names = [b.name for b in out]
    assert "정자교" in names and "한강대교" in names    # type=bridge + 이름에 '교'
    assert "서울역" not in names                        # 교량 아님 → 필터
    assert all(b.distance_m == 0.0 for b in out)
    assert captured["q"] == "교"


def test_find_bridges_by_name_empty_query():
    assert osm_bridge.find_bridges_by_name("") == []
    assert osm_bridge.find_bridges_by_name("   ") == []


def test_query_includes_bridge_filters(monkeypatch):
    captured = {}

    def fake(ql, **k):
        captured["ql"] = ql
        return {"elements": []}

    monkeypatch.setattr(osm_bridge, "_overpass_query", fake)
    osm_bridge.find_bridges_near(37.0, 127.0, radius_m=150)
    ql = captured["ql"]
    assert '["bridge"]' in ql and "around:150" in ql and "37.0,127.0" in ql


def test_confirm_bridge_none_when_empty(monkeypatch):
    monkeypatch.setattr(osm_bridge, "_overpass_query", lambda ql, **k: {"elements": []})
    assert osm_bridge.confirm_bridge(37.0, 127.0) is None


def test_confirm_bridge_returns_nearest(monkeypatch):
    monkeypatch.setattr(osm_bridge, "_overpass_query", lambda ql, **k: CANNED)
    b = osm_bridge.confirm_bridge(37.3305, 127.1110)
    assert b is not None and b.name == "정자교"


def test_bridge_target_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(osm_bridge, "_overpass_query", lambda ql, **k: CANNED)
    bridge = osm_bridge.find_bridges_near(37.3305, 127.1110)[0]
    target = BridgeTarget.from_bridge(bridge, selected_lat=37.3305, selected_lon=127.1110)

    assert target.confirmed and target.name == "정자교"
    # bbox 는 이제 교량 주변 buffer(점 추출 AOI); 원 extent 는 bridge_bbox 에 보존
    assert target.bridge_bbox == (127.1100, 37.3300, 127.1120, 37.3310)
    assert target.aoi_buffer_m == 200.0
    bmn_lon, bmn_lat, bmx_lon, bmx_lat = target.bbox
    # buffer 로 사방 확장됐는지 (AOI ⊃ 교량 extent)
    assert bmn_lon < 127.1100 and bmn_lat < 37.3300
    assert bmx_lon > 127.1120 and bmx_lat > 37.3310

    path = save_bridge_target(tmp_path / "recipe" / "bridge_target.json", target)
    assert path.exists()
    loaded = load_bridge_target(path)
    assert loaded.osm_id == 123456
    assert loaded.selected_lat == 37.3305
    assert loaded.tags["highway"] == "primary"


def test_selection_criteria_defaults():
    crit = SelectionCriteria()
    # 공간(수직) baseline 기본 상한 150 m, VV 편파, 시간 baseline 무제한
    assert crit.perp_baseline_max_m == 150.0
    assert crit.polarization == "VV"
    assert crit.temporal_baseline_max_days is None
    assert crit.prefer_most_data_track is True


def test_selection_criteria_roundtrip(tmp_path):
    crit = SelectionCriteria(perp_baseline_max_m=150.0, temporal_baseline_max_days=48.0,
                             orbit_direction="DESCENDING")
    path = save_selection_criteria(tmp_path / "recipe" / "selection_criteria.json", crit)
    loaded = load_selection_criteria(path)
    assert loaded.perp_baseline_max_m == 150.0
    assert loaded.temporal_baseline_max_days == 48.0
    assert loaded.orbit_direction == "DESCENDING"
    assert loaded.polarization == "VV"
