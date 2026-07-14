"""OSM(OpenStreetMap)으로 교량 조회·확인 — InSAR 데이터 선별 A·B 단계.

한국 지도에서 고른 위치 주변의 교량을 Overpass API 로 찾아, 그 위치가 실제
'교량'인지 확인하고 풋프린트(bbox)·이름·종류를 돌려준다. 이 풋프린트가 이후
Sentinel-1 SLC 검색(C 단계)의 검색 영역이 된다.

네트워크는 `_overpass_query` 한 곳으로 격리(테스트에서 monkeypatch). 표준 라이브러리
(urllib)만 쓰므로 추가 의존성이 없고 API 키도 필요 없다.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


@dataclass
class Bridge:
    """OSM 교량 요소 1개."""

    osm_type: str                       # "way" | "relation"
    osm_id: int
    name: str
    name_ko: str | None
    tags: dict[str, str]
    geometry: list[tuple[float, float]]  # [(lat, lon), ...]
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    distance_m: float = 0.0             # 선택 지점에서 가장 가까운 노드까지 거리
    length_m: float = 0.0               # 지오메트리 총 길이(교량 길이 추정)

    @property
    def osm_url(self) -> str:
        return f"https://www.openstreetmap.org/{self.osm_type}/{self.osm_id}"


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _overpass_query(ql: str, *, timeout: float = 30.0) -> dict:
    """Overpass API 에 QL 질의를 보내고 JSON 을 반환한다(네트워크 격리 지점)."""
    data = urllib.parse.urlencode({"data": ql}).encode()
    req = urllib.request.Request(OVERPASS_URL, data=data,
                                 headers={"User-Agent": "inframon-insar/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _build_query(lat: float, lon: float, radius_m: float) -> str:
    return (
        "[out:json][timeout:25];"
        "("
        f'way(around:{radius_m},{lat},{lon})["bridge"];'
        f'way(around:{radius_m},{lat},{lon})["man_made"="bridge"];'
        f'relation(around:{radius_m},{lat},{lon})["bridge"];'
        ");"
        "out tags geom;"
    )


def _parse_element(el: dict, lat: float, lon: float) -> Bridge | None:
    geom = [(g["lat"], g["lon"]) for g in el.get("geometry", []) if "lat" in g and "lon" in g]
    if not geom:
        return None
    lats = [p[0] for p in geom]
    lons = [p[1] for p in geom]
    bbox = (min(lons), min(lats), max(lons), max(lats))
    tags = {str(k): str(v) for k, v in el.get("tags", {}).items()}
    name = tags.get("name") or tags.get("name:ko") or f"{el['type']}/{el['id']}"
    distance = min(_haversine_m(lat, lon, p[0], p[1]) for p in geom)
    length = sum(
        _haversine_m(geom[i][0], geom[i][1], geom[i + 1][0], geom[i + 1][1])
        for i in range(len(geom) - 1)
    )
    return Bridge(
        osm_type=el["type"], osm_id=int(el["id"]), name=name,
        name_ko=tags.get("name:ko"), tags=tags, geometry=geom, bbox=bbox,
        distance_m=round(distance, 1), length_m=round(length, 1),
    )


def find_bridges_near(lat: float, lon: float, radius_m: float = 200.0) -> list[Bridge]:
    """위치 주변의 교량들을 가까운 순으로 반환한다(없으면 빈 리스트)."""
    result = _overpass_query(_build_query(lat, lon, radius_m))
    bridges = [b for el in result.get("elements", []) if (b := _parse_element(el, lat, lon))]
    bridges.sort(key=lambda b: b.distance_m)
    return bridges


# 이름 검색은 Overpass 전국 정규식이 매우 느려(수십초) Nominatim(이름 인덱스)을 쓴다.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_BRIDGE_HINTS = ("교", "대교", "육교", "bridge")


def _nominatim_query(query: str, *, country: str = "kr", limit: int = 20,
                     timeout: float = 15.0) -> list:
    """Nominatim 이름검색(네트워크 격리 지점). 국가 한정·JSON."""
    params = urllib.parse.urlencode({"q": query, "countrycodes": country,
                                     "format": "json", "limit": limit, "extratags": 1})
    req = urllib.request.Request(NOMINATIM_URL + "?" + params,
                                 headers={"User-Agent": "inframon-insar/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 공개 Nominatim
        return json.loads(resp.read().decode())


def find_bridges_by_name(query: str, *, limit: int = 20) -> list[Bridge]:
    """교량명으로 한국 내 OSM 교량 검색(Nominatim, 빠름). 교량 후보만 필터."""
    if not str(query or "").strip():
        return []
    out: list[Bridge] = []
    for r in _nominatim_query(query.strip(), limit=limit):
        name = str(r.get("display_name", "")).split(",")[0].strip() or "?"
        typ, cls = str(r.get("type") or ""), str(r.get("class") or "")
        is_bridge = (typ == "bridge" or cls == "bridge"
                     or any(h in name.lower() for h in _BRIDGE_HINTS))
        if not is_bridge:
            continue
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (KeyError, ValueError):
            continue
        out.append(Bridge(
            osm_type=str(r.get("osm_type", "node")), osm_id=int(r.get("osm_id", 0)),
            name=name, name_ko=None, tags={str(k): str(v) for k, v in (r.get("extratags") or {}).items()},
            geometry=[(lat, lon)], bbox=(lon, lat, lon, lat), distance_m=0.0, length_m=0.0))
    return out


def confirm_bridge(lat: float, lon: float, radius_m: float = 150.0) -> Bridge | None:
    """위치가 교량인지 확인 — 반경 안에서 가장 가까운 교량(없으면 None)."""
    bridges = find_bridges_near(lat, lon, radius_m)
    return bridges[0] if bridges else None
