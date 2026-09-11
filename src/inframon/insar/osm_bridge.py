"""OSM(OpenStreetMap)으로 교량 조회·확인 — InSAR 데이터 선별 A·B 단계.

한국 지도에서 고른 위치 주변의 교량을 Overpass API 로 찾아, 그 위치가 실제
'교량'인지 확인하고 풋프린트(bbox)·이름·종류를 돌려준다. 이 풋프린트가 이후
Sentinel-1 SLC 검색(C 단계)의 검색 영역이 된다.

네트워크는 `_overpass_query` 한 곳으로 격리(테스트에서 monkeypatch). 표준 라이브러리
(urllib)만 쓰므로 추가 의존성이 없고 API 키도 필요 없다.

Overpass 공용 서버는 504 Gateway Timeout 이 잦다. `_overpass_query` 는 504·429·연결실패를
**미러 서버 순환 + 백오프**로 재시도하고, 그래도 안 되면 `OverpassError` 하나로 알린다.

좌표 반경 안에는 교량 way 가 여럿이다(상하행 차도 2개·보도 2개·철교·이름 없는
`man_made=bridge` 외곽선). 가장 가까운 절점 하나로 고르면 엉뚱한 것이 잡히므로
`rank_key`(차도 우선 → 이름 일치 → 연장 일치 → 거리) 로 순위를 매긴다.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# 504 가 나면 이 순서로 다른 서버를 돈다(전부 같은 OSM 데이터, 키 불필요).
OVERPASS_MIRRORS: tuple[str, ...] = (
    OVERPASS_URL,
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    OVERPASS_URL,
)
_RETRY_HTTP = frozenset({429, 500, 502, 503, 504})   # 일시 오류 — 다시 시도할 가치가 있는 코드
_BACKOFF_S = (2.0, 4.0, 8.0)                         # 시도 사이 대기(초)
_sleep = time.sleep                                  # 테스트에서 바꿔치기


class OverpassError(RuntimeError):
    """Overpass 조회가 재시도·미러까지 전부 실패했을 때. 마지막 원인을 `last` 에 담는다."""

    def __init__(self, msg: str, last: BaseException | None = None):
        super().__init__(msg)
        self.last = last


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


def _overpass_once(url: str, ql: str, *, timeout: float) -> dict:
    """서버 1곳에 1번 질의(순수 네트워크)."""
    data = urllib.parse.urlencode({"data": ql}).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "inframon-insar/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 공개 Overpass
        return json.loads(resp.read().decode())


def _short_err(e: BaseException | None) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        return f"연결 실패({e.reason})"
    return f"{type(e).__name__}: {e}"[:60]


def _overpass_query(ql: str, *, timeout: float = 30.0, retries: int = 4,
                    urls: tuple[str, ...] | None = None) -> dict:
    """Overpass API 에 QL 질의를 보내고 JSON 을 반환한다(네트워크 격리 지점).

    504·429·503, 연결 실패, 깨진 JSON(과부하 서버의 HTML 응답)은 **미러를 바꿔 가며**
    최대 `retries` 회 시도하고 사이에 2·4·8초 쉰다. 400(질의 오류)처럼 다시 해도
    같은 실패는 즉시 올린다. 전부 실패하면 `OverpassError`.
    """
    servers = tuple(urls or OVERPASS_MIRRORS)
    last: BaseException | None = None
    tried: list[str] = []
    for attempt in range(max(1, retries)):
        url = servers[attempt % len(servers)]
        tried.append(url.split("//", 1)[-1].split("/", 1)[0])
        try:
            return _overpass_once(url, ql, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in _RETRY_HTTP:
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as e:
            last = e                                     # ValueError = JSON 아님(과부하 HTML)
        if attempt < retries - 1:
            _sleep(_BACKOFF_S[min(attempt, len(_BACKOFF_S) - 1)])
    raise OverpassError(
        f"OSM(Overpass) {len(tried)}회 실패 — 마지막 {_short_err(last)} · "
        f"시도 서버 {', '.join(dict.fromkeys(tried))}", last)


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
    # 도로 way 는 name 이 도로명(용구대로)이고 교량명은 bridge:name 에 있는 경우가 많다.
    name = (tags.get("bridge:name:ko") or tags.get("bridge:name") or tags.get("name:ko")
            or tags.get("name") or f"{el['type']}/{el['id']}")
    distance = min(_haversine_m(lat, lon, p[0], p[1]) for p in geom)
    length = sum(
        _haversine_m(geom[i][0], geom[i][1], geom[i + 1][0], geom[i + 1][1])
        for i in range(len(geom) - 1)
    )
    return Bridge(
        osm_type=el["type"], osm_id=int(el["id"]), name=name,
        name_ko=tags.get("bridge:name:ko") or tags.get("name:ko"), tags=tags,
        geometry=geom, bbox=bbox,
        distance_m=round(distance, 1), length_m=round(length, 1),
    )


_WALK_HIGHWAY = frozenset({"footway", "path", "cycleway", "steps", "pedestrian", "bridleway"})
_MINOR_HIGHWAY = frozenset({"service", "track", "living_street", "driveway"})   # 진입로·농로


def _kind(b: Bridge) -> int:
    """후보 종류 순위 — 0 차도 · 1 진입로(service/track) · 2 보도/자전거 · 3 이름 없는 외곽선 · 4 철교 · 5 기타.

    실제 37.3219,127.1083 응답: service 교량(23 m)이 독정교 primary(59 m)보다 가깝다.
    진입로를 차도보다 뒤로 두어야 CSV 힌트가 없어도 도로교량이 잡힌다.
    """
    t = b.tags
    if "railway" in t:
        return 4
    hw = t.get("highway")
    if hw:
        if hw in _WALK_HIGHWAY:
            return 2
        return 1 if hw in _MINOR_HIGHWAY else 0
    if t.get("man_made") == "bridge":
        return 3
    return 5


def _names_of(b: Bridge) -> list[str]:
    t = b.tags
    return [str(v).replace(" ", "") for v in
            (b.name, t.get("bridge:name"), t.get("bridge:name:ko"), t.get("name"), t.get("name:ko"))
            if v and not str(v).startswith(("way/", "relation/"))]


def name_matches(b: Bridge, name: str | None) -> bool:
    """찾는 교량명(예 '독정교')이 way 의 name/bridge:name 과 부분일치하는가."""
    want = str(name or "").replace(" ", "").strip()
    if not want:
        return False
    return any(want in n or n in want for n in _names_of(b))


def rank_key(b: Bridge, *, name: str | None = None, length_m: float | None = None,
             length_tol: float = 0.25) -> tuple:
    """후보 정렬 키 — 작을수록 좋다. (종류, 이름 불일치, 연장 불일치, 거리).

    · 종류: 차도 way 가 진입로·보도·외곽선·철교보다 먼저(우리 대상은 도로교량).
    · 이름: 전국교량표준데이터 이름을 알면 일치하는 way 먼저.
    · 연장: CSV 연장을 알면 ±25% 안에 드는 way 먼저 — 같은 이름의 접속 고가부
      (내곡교 401 m)가 실교량(162 m)을 밀어내는 것을 막는다.
    · 마지막으로 거리.
    """
    name_miss = 0 if name_matches(b, name) else 1
    if length_m and length_m > 0 and b.length_m > 0:
        len_miss = 0 if abs(b.length_m - length_m) <= length_tol * length_m else 1
    else:
        len_miss = 0
    return (_kind(b), name_miss, len_miss, b.distance_m)


def find_bridges_near(lat: float, lon: float, radius_m: float = 200.0, *,
                      name: str | None = None, length_m: float | None = None) -> list[Bridge]:
    """위치 주변의 교량들을 **좋은 순**(rank_key)으로 반환한다(없으면 빈 리스트).

    `name`·`length_m` 을 주면(전국교량표준데이터 등) 그에 맞는 way 가 앞에 온다.
    안 주면 차도 → 거리 순이라, 예전처럼 이름 없는 외곽선·철교가 먼저 오지 않는다.
    """
    result = _overpass_query(_build_query(lat, lon, radius_m))
    bridges = [b for el in result.get("elements", []) if (b := _parse_element(el, lat, lon))]
    bridges.sort(key=lambda b: rank_key(b, name=name, length_m=length_m))
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
        # OSM 태그로 교량 확정(man_made=bridge → type/class=bridge) vs 이름만 일치(도로 가능성)
        tag_confirmed = typ == "bridge" or cls == "bridge"
        if not (tag_confirmed or any(h in name.lower() for h in _BRIDGE_HINTS)):
            continue                                    # 교량도 도로도 아님 → 제외
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (KeyError, ValueError):
            continue
        tags = {str(k): str(v) for k, v in (r.get("extratags") or {}).items()}
        tags["osm_feature"] = f"{cls}/{typ}"            # 예 man_made/bridge, highway/primary
        tags["bridge_confirmed"] = "yes" if tag_confirmed else "name_only"
        out.append(Bridge(
            osm_type=str(r.get("osm_type", "node")), osm_id=int(r.get("osm_id", 0)),
            name=name, name_ko=None, tags=tags,
            geometry=[(lat, lon)], bbox=(lon, lat, lon, lat), distance_m=0.0, length_m=0.0))
    return out


def confirm_bridge(lat: float, lon: float, radius_m: float = 150.0, *,
                   name: str | None = None, length_m: float | None = None) -> Bridge | None:
    """위치가 교량인지 확인 — 반경 안에서 rank_key 최상위 교량(없으면 None).

    가장 가까운 절점 하나로 고르지 않는다: 37.3219,127.1083 에서는 20 m 거리의 이름 없는
    650 m 외곽선(man_made=bridge)이 60 m 거리의 실제 도로교 독정교(123 m)를 밀어냈다.
    """
    bridges = find_bridges_near(lat, lon, radius_m, name=name, length_m=length_m)
    return bridges[0] if bridges else None
