"""OSM 조회 — 504 재시도·미러 폴백, 후보 순위(차도 > 외곽선·철교, 이름·연장 일치) — 네트워크 없음.

승민 님 보고(타 PC): ① 좌표 주변 교량이 여럿이라 엉뚱한 것이 매칭, ② Overpass 504 로 계획 단계 실패.
두 문제를 여기서 고정한다.
"""

from __future__ import annotations

import io
import urllib.error

import pytest

from inframon.insar import osm_bridge as ob


def _http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b""))


# ── 재시도·미러 ──────────────────────────────────────────────────────────
def test_504_then_mirror_succeeds(monkeypatch):
    calls: list[str] = []
    slept: list[float] = []

    def once(url, ql, *, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise _http(504)
        return {"elements": []}

    monkeypatch.setattr(ob, "_overpass_once", once)
    monkeypatch.setattr(ob, "_sleep", slept.append)
    out = ob._overpass_query("[out:json];", urls=("https://a/api", "https://b/api"))
    assert out == {"elements": []}
    assert calls == ["https://a/api", "https://b/api"]      # 두 번째 시도는 다른 서버
    assert slept == [2.0]                                     # 사이에 백오프


def test_all_fail_raises_overpass_error_with_last_cause(monkeypatch):
    monkeypatch.setattr(ob, "_overpass_once", lambda url, ql, *, timeout: (_ for _ in ()).throw(_http(504)))
    monkeypatch.setattr(ob, "_sleep", lambda s: None)
    with pytest.raises(ob.OverpassError) as ei:
        ob._overpass_query("q", retries=3, urls=("https://a/api", "https://b/api"))
    msg = str(ei.value)
    assert "3회 실패" in msg and "HTTP 504" in msg and "a" in msg and "b" in msg
    assert isinstance(ei.value.last, urllib.error.HTTPError)


def test_non_retryable_http_raises_immediately(monkeypatch):
    n = {"c": 0}

    def once(url, ql, *, timeout):
        n["c"] += 1
        raise _http(400)                      # 질의 문법 오류 — 다시 해도 같다

    monkeypatch.setattr(ob, "_overpass_once", once)
    monkeypatch.setattr(ob, "_sleep", lambda s: pytest.fail("400 에는 대기·재시도가 없어야 한다"))
    with pytest.raises(urllib.error.HTTPError):
        ob._overpass_query("q")
    assert n["c"] == 1


def test_connection_error_and_bad_json_are_retried(monkeypatch):
    seq = [urllib.error.URLError("dns"), ValueError("not json"), {"elements": [1]}]

    def once(url, ql, *, timeout):
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(ob, "_overpass_once", once)
    monkeypatch.setattr(ob, "_sleep", lambda s: None)
    assert ob._overpass_query("q")["elements"] == [1]


# ── 후보 순위 ──────────────────────────────────────────────────────────
def _way(id_, tags, pts, ):
    return {"type": "way", "id": id_, "tags": tags,
            "geometry": [{"lat": a, "lon": b} for a, b in pts]}


LAT, LON = 37.3219, 127.1083

# 실제 37.3219,127.1083 (독정교) Overpass 응답 축약 — 이름 없는 650 m 외곽선이 20 m 로 가장 가깝고,
# 실제 도로교 독정교(highway=primary, bridge:name) 는 60 m, 철교 분당기지선은 73 m.
REAL_LIKE = {"elements": [
    _way(840845311, {"man_made": "bridge", "layer": "1"},
         [(37.32008, 127.10621), (37.32100, 127.10720), (37.32208, 127.10827)]),   # 외곽선 ~250m
    _way(1025439836, {"bridge": "yes", "highway": "service"},
         [(37.32170, 127.10830), (37.32200, 127.10940)]),
    _way(1195151851, {"bridge": "yes", "highway": "primary", "bridge:name": "독정교",
                      "name:en": "Yonggu-daero"},
         [(37.32140, 127.10850), (37.32200, 127.10950)]),                           # ~123m
    _way(840845297, {"bridge": "yes", "railway": "rail", "name": "분당기지선"},
         [(37.32130, 127.10770), (37.32220, 127.10790)]),
]}


def test_nearest_node_no_longer_wins_road_first(monkeypatch):
    monkeypatch.setattr(ob, "_overpass_query", lambda ql, **k: REAL_LIKE)
    b = ob.confirm_bridge(LAT, LON)
    assert b is not None
    assert b.osm_id == 1195151851 and b.name == "독정교"  # 20 m 외곽선·23 m service 가 아니라 59 m 도로교
    assert b.tags.get("man_made") != "bridge"           # 이름 없는 외곽선은 뒤로
    assert "railway" not in b.tags                       # 철교도 뒤로


def test_bridge_name_tag_becomes_name(monkeypatch):
    monkeypatch.setattr(ob, "_overpass_query", lambda ql, **k: REAL_LIKE)
    names = {b.osm_id: b.name for b in ob.find_bridges_near(LAT, LON)}
    assert names[1195151851] == "독정교"                  # name 이 아니라 bridge:name 에 있어도
    assert names[840845311] == "way/840845311"           # 이름 없으면 예전처럼 id


def test_csv_name_and_length_pick_matching_way(monkeypatch):
    # 같은 이름의 접속 고가부(401 m)와 실교량(162 m)이 나란히 — CSV 연장 162 를 주면 실교량
    data = {"elements": [
        _way(1, {"bridge": "yes", "highway": "primary", "name": "내곡교"},
             [(37.4600, 127.0700), (37.4636, 127.0700)]),        # ~400m, 더 가까움
        _way(2, {"bridge": "yes", "highway": "primary", "name": "내곡교"},
             [(37.4640, 127.0700), (37.46545, 127.0700)]),       # ~161m
    ]}
    monkeypatch.setattr(ob, "_overpass_query", lambda ql, **k: data)
    near = ob.confirm_bridge(37.4610, 127.0700)
    assert near.osm_id == 1                                       # 힌트 없으면 거리
    fit = ob.confirm_bridge(37.4610, 127.0700, name="내곡교", length_m=162.0)
    assert fit.osm_id == 2                                        # 연장이 맞는 것


def test_name_hint_beats_distance(monkeypatch):
    data = {"elements": [
        _way(1, {"bridge": "yes", "highway": "secondary", "name": "옆교"},
             [(37.0000, 127.0000), (37.0005, 127.0000)]),
        _way(2, {"bridge": "yes", "highway": "primary", "bridge:name": "진위교"},
             [(37.0010, 127.0000), (37.0020, 127.0000)]),
    ]}
    monkeypatch.setattr(ob, "_overpass_query", lambda ql, **k: data)
    assert ob.confirm_bridge(37.0, 127.0).osm_id == 1
    assert ob.confirm_bridge(37.0, 127.0, name="진위교").osm_id == 2


def test_rank_key_kinds():
    mk = lambda tags, d: ob.Bridge("way", 0, "x", None, tags, [], (0, 0, 0, 0), distance_m=d)  # noqa: E731
    road = mk({"highway": "primary"}, 90)
    svc = mk({"highway": "service", "bridge": "yes"}, 20)
    walk = mk({"highway": "footway", "bridge": "yes"}, 10)
    outline = mk({"man_made": "bridge"}, 5)
    rail = mk({"railway": "rail", "bridge": "yes"}, 1)
    order = sorted([rail, outline, walk, svc, road], key=ob.rank_key)
    assert order == [road, svc, walk, outline, rail]
