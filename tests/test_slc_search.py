"""ASF SLC 검색 + 트랙 선별 (InSAR C·D 단계) — 네트워크 없이 검증.

ASF geo_search 응답(properties dict)을 monkeypatch 로 주입해 파싱·편파 필터·트랙
집계/선별·레시피 저장을 테스트한다.
"""

from __future__ import annotations

from inframon.insar import slc_search
from inframon.insar.recipe import TrackSelection, load_track_selection, save_track_selection

# path 100/frame 600 (ASCENDING) VV 3장 + path 200/frame 700 (DESCENDING) VV 2장 + VH 전용 1장
CANNED = [
    {"sceneName": "S1A_100a", "flightDirection": "ASCENDING", "pathNumber": 100,
     "frameNumber": 600, "polarization": "VV+VH", "startTime": "2020-01-01T00:00:00Z"},
    {"sceneName": "S1A_100b", "flightDirection": "ASCENDING", "pathNumber": 100,
     "frameNumber": 600, "polarization": "VV", "startTime": "2020-01-13T00:00:00Z"},
    {"sceneName": "S1A_100c", "flightDirection": "ASCENDING", "pathNumber": 100,
     "frameNumber": 600, "polarization": "VV", "startTime": "2020-01-25T00:00:00Z"},
    {"sceneName": "S1A_200a", "flightDirection": "DESCENDING", "pathNumber": 200,
     "frameNumber": 700, "polarization": "VV+VH", "startTime": "2020-02-01T00:00:00Z"},
    {"sceneName": "S1A_200b", "flightDirection": "DESCENDING", "pathNumber": 200,
     "frameNumber": 700, "polarization": "VV+VH", "startTime": "2020-02-13T00:00:00Z"},
    {"sceneName": "S1A_vh", "flightDirection": "ASCENDING", "pathNumber": 100,
     "frameNumber": 600, "polarization": "VH", "startTime": "2020-03-01T00:00:00Z"},
]
BBOX = (127.110, 37.330, 127.112, 37.331)


def test_bbox_to_wkt():
    wkt = slc_search.bbox_to_wkt(BBOX)
    assert wkt.startswith("POLYGON((")
    assert "127.11 37.33" in wkt


def test_search_filters_polarization(monkeypatch):
    monkeypatch.setattr(slc_search, "_asf_geo_search", lambda wkt, **k: CANNED)
    scenes = slc_search.search_slc(BBOX, polarization="VV")
    # VH 전용 1장은 제외 → 5장
    assert len(scenes) == 5
    assert all("VV" in s.polarization for s in scenes)
    assert scenes[0].date == "20200101"


def test_select_track_picks_most_data(monkeypatch):
    monkeypatch.setattr(slc_search, "_asf_geo_search", lambda wkt, **k: CANNED)
    scenes = slc_search.search_slc(BBOX, polarization="VV")
    best, chosen, groups = slc_search.select_track(scenes)

    # 취득 최다 = ASCENDING path100/frame600 (VV 3장)
    assert best.flight_direction == "ASCENDING"
    assert (best.path, best.frame) == (100, 600)
    assert best.n_scenes == 3
    assert [s.date for s in chosen] == ["20200101", "20200113", "20200125"]
    assert len(groups) == 2  # 두 트랙


def test_select_track_respects_orbit_direction(monkeypatch):
    monkeypatch.setattr(slc_search, "_asf_geo_search", lambda wkt, **k: CANNED)
    scenes = slc_search.search_slc(BBOX, polarization="VV")
    best, chosen, _ = slc_search.select_track(scenes, orbit_direction="DESCENDING")

    assert best.flight_direction == "DESCENDING"
    assert (best.path, best.frame) == (200, 700)
    assert best.n_scenes == 2


def test_track_selection_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(slc_search, "_asf_geo_search", lambda wkt, **k: CANNED)
    scenes = slc_search.search_slc(BBOX, polarization="VV")
    best, chosen, _ = slc_search.select_track(scenes)
    sel = TrackSelection.from_selection(best, chosen, polarization="VV")

    path = save_track_selection(tmp_path / "recipe" / "track_selection.json", sel)
    loaded = load_track_selection(path)
    assert loaded.path == 100 and loaded.frame == 600
    assert loaded.n_scenes == 3
    assert loaded.scene_dates == ["20200101", "20200113", "20200125"]
    assert loaded.polarization == "VV"


def test_select_track_empty(monkeypatch):
    monkeypatch.setattr(slc_search, "_asf_geo_search", lambda wkt, **k: [])
    scenes = slc_search.search_slc(BBOX)
    best, chosen, groups = slc_search.select_track(scenes)
    assert best is None and chosen == [] and groups == []
