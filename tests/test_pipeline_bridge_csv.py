"""① 교량선정 — 전국교량표준데이터(CSV) 우선, OSM 504 시 CSV 폴백, 캐시. 네트워크 없음."""

from __future__ import annotations

import json

import pytest

from inframon import pipeline_bridge as pb
from inframon.insar.osm_bridge import OverpassError
from inframon.insar.roi_selection import RoiResult
from inframon.structure import BridgeProfile


class _Frame:
    n_scenes = 40; centrality_km = 8.4
    def label(self):
        return "ASC path127 frame120"


def _patch_rest(monkeypatch):
    monkeypatch.setattr("inframon.insar.roi_selection.select_roi",
                        lambda lat, lon, **k: RoiResult((127.09, 37.31, 127.11, 37.33),
                                                        2.0, (37.32, 127.10), 1224, 306.0, True))
    monkeypatch.setattr("inframon.insar.snap_acquire.search_frames",
                        lambda lat, lon, **k: [_Frame()])


def _csv_profile():
    return BridgeProfile(name="독정교", bridge_type="girder", material="concrete",
                         length_m=125.0, width_m=21.0, section_depth_m=1.2,
                         load_per_len=1.0e4, boundary="simply_supported",
                         source="data_go_kr:전국교량표준데이터",
                         extra={"lat": 37.3214, "lon": 127.1085, "lat_end": 37.3224, "lon_end": 127.1081,
                                "match_by": "distance", "match_dist_m": 12.0, "grade": "2",
                                "max_span_m": 30.0, "lanes": 4})


def _patch_csv(monkeypatch, prof):
    monkeypatch.setattr("inframon.public_data.find_bridge_csv", lambda *d: "fake.csv")
    seen = {}
    def nearest(csv, lat, lon, *, max_km=1.0, name=None, name_max_km=5.0):
        seen["name"] = name
        return prof
    monkeypatch.setattr("inframon.public_data.nearest_bridge_profile", nearest)
    return seen


class _Way:
    def __init__(self, name="way/999", length=650.0, tags=None):
        self.name = name; self.osm_id = 999; self.osm_url = "http://osm/way/999"
        self.length_m = length; self.tags = tags or {"man_made": "bridge"}
        self.geometry = [(37.32, 127.10), (37.321, 127.101)]; self.distance_m = 20.0


def test_osm_504_falls_back_to_csv(monkeypatch, tmp_path):
    _patch_rest(monkeypatch)
    _patch_csv(monkeypatch, _csv_profile())
    def boom(lat, lon, **k):
        raise OverpassError("OSM(Overpass) 4회 실패 — 마지막 HTTP 504")
    monkeypatch.setattr("inframon.insar.osm_bridge.confirm_bridge", boom)
    rep = pb.run_bridge_pipeline(37.3219, 127.1083, out_dir=tmp_path, mode="plan")
    s1 = rep.stages[0]
    assert s1.step.startswith("①") and s1.status == "partial"          # ❌ 가 아니라 ◐
    assert "독정교" in s1.detail and "HTTP 504" in s1.detail and "전국교량표준데이터" in s1.detail
    b = rep.context["bridge"]
    assert b["name"] == "독정교" and b["length_m"] == 125 and b["source"] == "csv"
    assert b["geometry"] == [[37.3214, 127.1085], [37.3224, 127.1081]]  # 시점·종점 → 데크선
    assert rep.context["bridge_csv"]["name"] == "독정교"
    # ⑪ 교량메타도 CSV 제원으로 돌았다
    assert any(s.step.startswith("⑪") and s.status != "error" for s in rep.stages)


def test_csv_name_and_length_are_passed_to_osm(monkeypatch, tmp_path):
    _patch_rest(monkeypatch)
    _patch_csv(monkeypatch, _csv_profile())
    got = {}
    def confirm(lat, lon, **k):
        got.update(k)
        return _Way("독정교", 123.0, {"bridge": "yes", "highway": "primary", "bridge:name": "독정교"})
    monkeypatch.setattr("inframon.insar.osm_bridge.confirm_bridge", confirm)
    rep = pb.run_bridge_pipeline(37.3219, 127.1083, out_dir=tmp_path, mode="plan")
    assert got == {"name": "독정교", "length_m": 125.0}
    s1 = rep.stages[0]
    assert s1.status == "done" and "독정교 · 123m" in s1.detail and "CSV 독정교 125m" in s1.detail
    assert "⚠️" not in s1.detail
    assert rep.context["bridge"]["source"] == "osm"


def test_user_bridge_name_hint_overrides(monkeypatch, tmp_path):
    _patch_rest(monkeypatch)
    seen = _patch_csv(monkeypatch, _csv_profile())
    got = {}
    monkeypatch.setattr("inframon.insar.osm_bridge.confirm_bridge",
                        lambda lat, lon, **k: got.update(k) or _Way("진위교", 120.0, {"highway": "primary"}))
    pb.run_bridge_pipeline(37.1009, 127.0640, out_dir=tmp_path, mode="plan", bridge_name="진위교")
    assert seen["name"] == "진위교" and got["name"] == "진위교"


@pytest.mark.parametrize("junk", ["37.3219,127.1083", "현재 교량", "way/840845311", "  "])
def test_coordinate_or_placeholder_name_is_not_a_hint(monkeypatch, tmp_path, junk):
    # 대시보드는 교량을 안 고르면 이름 자리에 좌표를 넣는다 — 그걸 교량명으로 매칭하면 안 된다
    _patch_rest(monkeypatch)
    seen = _patch_csv(monkeypatch, _csv_profile())
    got = {}
    monkeypatch.setattr("inframon.insar.osm_bridge.confirm_bridge",
                        lambda lat, lon, **k: got.update(k) or _Way("way/999", 125.0, {"highway": "primary"}))
    rep = pb.run_bridge_pipeline(37.3219, 127.1083, out_dir=tmp_path, mode="plan", bridge_name=junk)
    assert seen["name"] is None and got["name"] == "독정교"      # CSV 이름이 힌트
    assert rep.context["bridge"]["name"] == "독정교"


def test_osm_csv_mismatch_is_flagged(monkeypatch, tmp_path):
    _patch_rest(monkeypatch)
    _patch_csv(monkeypatch, _csv_profile())
    monkeypatch.setattr("inframon.insar.osm_bridge.confirm_bridge",
                        lambda lat, lon, **k: _Way("way/840845311", 650.0))
    rep = pb.run_bridge_pipeline(37.3219, 127.1083, out_dir=tmp_path, mode="plan")
    s1 = rep.stages[0]
    assert s1.status == "done" and "⚠️" in s1.detail and "불일치" in s1.detail
    assert rep.context["bridge"]["name"] == "독정교"       # OSM 무명 way 는 CSV 이름으로


def test_osm_result_cached_and_reused(monkeypatch, tmp_path):
    _patch_rest(monkeypatch)
    monkeypatch.setattr("inframon.public_data.find_bridge_csv", lambda *d: None)
    n = {"c": 0}
    def confirm(lat, lon, **k):
        n["c"] += 1
        return _Way("독정교", 123.0, {"highway": "primary"})
    monkeypatch.setattr("inframon.insar.osm_bridge.confirm_bridge", confirm)
    pb.run_bridge_pipeline(37.3219, 127.1083, out_dir=tmp_path, mode="plan")
    cache = json.loads((tmp_path / "osm_bridge_cache.json").read_text(encoding="utf-8"))
    assert len(cache) == 1 and next(iter(cache.values()))["name"] == "독정교"
    # 두 번째 실행 — 네트워크(OSM) 를 타지 않고 캐시
    def boom(lat, lon, **k):
        raise OverpassError("HTTP 504")
    monkeypatch.setattr("inframon.insar.osm_bridge.confirm_bridge", boom)
    rep = pb.run_bridge_pipeline(37.3219, 127.1083, out_dir=tmp_path, mode="plan")
    assert rep.stages[0].status == "done" and "(캐시)" in rep.stages[0].detail
    assert n["c"] == 1


def test_no_osm_no_csv_is_error_with_hint(monkeypatch, tmp_path):
    _patch_rest(monkeypatch)
    monkeypatch.setattr("inframon.public_data.find_bridge_csv", lambda *d: None)
    def boom(lat, lon, **k):
        raise OverpassError("OSM(Overpass) 4회 실패 — 마지막 HTTP 504")
    monkeypatch.setattr("inframon.insar.osm_bridge.confirm_bridge", boom)
    rep = pb.run_bridge_pipeline(37.3219, 127.1083, out_dir=tmp_path, mode="plan")
    s1 = rep.stages[0]
    assert s1.status == "error" and "HTTP 504" in s1.detail and "national_bridge_standard" in s1.detail


def test_real_csv_has_start_end_coords():
    """리포의 전국교량표준데이터(35,593건)에서 좌표 근처 교량이 시점·종점과 함께 나온다."""
    from inframon.public_data import find_bridge_csv, nearest_bridge_profile
    csv = find_bridge_csv("data")
    if not csv:
        pytest.skip("data/national_bridge_standard*.csv 없음")
    prof = nearest_bridge_profile(csv, 37.100936, 127.064073, max_km=0.3)   # 진위교(첫 행)
    assert prof is not None and prof.name == "진위교"
    x = prof.extra
    assert x["lat"] == pytest.approx(37.100936) and x["lon_end"] == pytest.approx(127.063808)
