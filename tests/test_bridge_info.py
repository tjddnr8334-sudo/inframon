"""공공 교량정보 → BridgeProfile — OSM 매핑·data.go.kr 파싱·폴백."""

from __future__ import annotations

import json
from types import SimpleNamespace

from inframon.bridge_info import (
    fetch_bridge_profile,
    fetch_from_data_go_kr,
    profile_from_osm,
)


def _osm(tags, length_m=0.0, name="테스트교"):
    return SimpleNamespace(tags=tags, length_m=length_m, name=name,
                           osm_url="https://osm/way/1")


def test_profile_from_osm_maps_structure_and_material():
    b = _osm({"bridge:structure": "cable-stayed", "material": "steel", "width": "25 m"},
             length_m=410.0)
    p = profile_from_osm(b)
    assert p.bridge_type == "cable_stayed"          # OSM 구조 태그 매핑
    assert p.material == "steel"
    assert p.length_m == 410.0 and p.width_m == 25.0
    assert p.source == "osm" and p.name == "테스트교"


def test_profile_from_osm_defaults_when_untagged():
    p = profile_from_osm(_osm({}, length_m=120.0))
    assert p.bridge_type == "girder" and p.material == "steel"   # 기본
    assert p.length_m == 120.0


def test_profile_from_osm_length_from_tag_when_geom_zero():
    p = profile_from_osm(_osm({"length": "88"}, length_m=0.0))
    assert p.length_m == 88.0


def test_fetch_data_go_kr_parses(monkeypatch):
    import urllib.request

    payload = {"response": {"body": {"items": {"item": [
        {"brdgTypeNm": "arch", "brdgLt": "150.5", "brdgWdth": "12", "matName": "콘크리트"}]}}}}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())
    prof = fetch_from_data_go_kr(
        "KEY", endpoint="https://apis.data.go.kr/x",
        field_map={"brdgTypeNm": "bridge_type", "brdgLt": "length_m",
                   "brdgWdth": "width_m", "matName": "material"})
    assert prof is not None
    assert prof.bridge_type == "arch" and prof.length_m == 150.5
    assert prof.width_m == 12.0 and prof.material == "concrete"
    assert prof.source == "data_go_kr"


def test_fetch_data_go_kr_network_fail_returns_none(monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise OSError("network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert fetch_from_data_go_kr("KEY", endpoint="https://x") is None


def test_fetch_bridge_profile_falls_back_to_default(monkeypatch):
    # 키 없음 + OSM 없음 → 기본값 프로파일
    import inframon.insar.osm_bridge as osm
    monkeypatch.setattr(osm, "find_bridges_near", lambda *a, **k: [])
    p = fetch_bridge_profile(37.36, 127.11, name="X")
    assert p.source == "default" and p.bridge_type == "girder"


def test_fetch_bridge_profile_uses_osm(monkeypatch):
    import inframon.insar.osm_bridge as osm
    monkeypatch.setattr(osm, "find_bridges_near",
                        lambda *a, **k: [_osm({"bridge:structure": "truss"}, length_m=60.0)])
    p = fetch_bridge_profile(37.36, 127.11)
    assert p.source == "osm" and p.bridge_type == "truss" and p.length_m == 60.0
