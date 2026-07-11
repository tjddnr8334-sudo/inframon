"""⑪ 교량 메타 확장 — 등급(1/2/3종)·종류 세분(PSC box·라멘)·폭·지형(산지/평지/해상).

기존 bridge_profile(형식·water_context) 위에 시설물안전법 등급, PSC box/라멘 세분,
폭(OSM width/lanes), 지형(DEM 기복 → 산지/평지, water_context → 해상)을 얹어 형식별
PINN·조건게이팅에 더 정확한 제원을 준다. 표고는 Open-Meteo elevation(키 불필요),
네트워크는 elev_fn 주입으로 격리(테스트).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

# 종류 세분(bridge_profile 형식에 추가)
BOX_GIRDER = "box_girder"       # PSC box 형교
RAHMEN = "rahmen"               # 라멘(강결)교
_STRUCTURE_KO = {
    "girder": "거더교", "box_girder": "PSC박스교", "rahmen": "라멘교",
    "cable_stayed": "사장교", "suspension": "현수교", "arch": "아치교",
    "truss": "트러스교", "special": "특수교",
}

OPEN_METEO_ELEV = "https://api.open-meteo.com/v1/elevation"


@dataclass
class BridgeMeta:
    grade: str                  # 1종 | 2종 | 3종 | 기타
    structure: str              # girder|box_girder|rahmen|cable_stayed|...
    structure_ko: str
    width_m: float | None
    length_m: float | None
    max_span_m: float | None
    terrain: str                # 산지 | 평지 | 해상
    relief_m: float | None      # 주변 표고 기복(산지 판정 근거)
    lat: float
    lon: float

    def as_dict(self) -> dict:
        return {"grade": self.grade, "structure": self.structure,
                "structure_ko": self.structure_ko, "width_m": self.width_m,
                "length_m": self.length_m, "max_span_m": self.max_span_m,
                "terrain": self.terrain, "relief_m": self.relief_m,
                "latlon": [self.lat, self.lon]}


def classify_structure(tags: dict, base_class: str) -> str:
    """OSM 태그로 PSC box/라멘 세분 — 없으면 base_class(bridge_profile 형식) 유지."""
    s = (tags.get("bridge:structure") or "").lower()
    b = " ".join(str(v).lower() for v in tags.values())
    if "box" in s or "box" in b:
        return BOX_GIRDER
    if "rahmen" in s or "frame" in s or "라멘" in b or "rigid" in b:
        return RAHMEN
    return base_class


def max_span_estimate(structure: str, length_m: float | None,
                      n_spans: int | None = None) -> float | None:
    """최대경간 추정 — 경간수 있으면 length/n, 없으면 형식별 대표 비율."""
    if length_m is None:
        return None
    if n_spans and n_spans > 0:
        return round(length_m / n_spans, 1)
    ratio = {"suspension": 0.75, "cable_stayed": 0.55, "arch": 0.5, "truss": 0.4,
             "box_girder": 0.25, "girder": 0.18, "rahmen": 0.15}.get(structure, 0.2)
    # 단경간형(거더·box·라멘·트러스)은 장대교라도 다경간이라 최대경간을 현실범위로 캡.
    cap = {"girder": 60.0, "box_girder": 90.0, "rahmen": 45.0, "truss": 120.0}.get(structure)
    span = length_m * ratio
    return round(min(span, cap) if cap else span, 1)


def bridge_grade(length_m: float | None, max_span_m: float | None = None) -> str:
    """시설물안전법 도로교량 등급(heuristic).

    1종: 연장≥500m 또는 최대경간≥50m. 2종: 연장≥100m. 3종: 연장≥20m. 그 외 기타.
    (정확 판정은 폭·경간수·공용연수 필요 — 여기선 연장·경간 근사.)
    """
    if length_m is not None and length_m >= 500:
        return "1종"
    if max_span_m is not None and max_span_m >= 50:
        return "1종"
    if length_m is not None and length_m >= 100:
        return "2종"
    if length_m is not None and length_m >= 20:
        return "3종"
    return "기타"


def bridge_width_m(tags: dict) -> float | None:
    """OSM width(우선) 또는 lanes×3.5m(+갓길)로 폭 추정."""
    w = tags.get("width")
    if w is not None:
        try:
            return round(float(str(w).split()[0]), 1)
        except (ValueError, IndexError):
            pass
    lanes = tags.get("lanes")
    if lanes is not None:
        try:
            return round(int(lanes) * 3.5 + 1.0, 1)      # 차로 3.5m + 갓길 여유
        except ValueError:
            pass
    return None


def _fetch_elevation(lats: list[float], lons: list[float], *, timeout: float = 20.0) -> list[float]:
    """Open-Meteo elevation — 여러 점 표고[m] (네트워크 격리 지점)."""
    q = urllib.parse.urlencode({
        "latitude": ",".join(f"{x:.5f}" for x in lats),
        "longitude": ",".join(f"{x:.5f}" for x in lons)})
    req = urllib.request.Request(f"{OPEN_METEO_ELEV}?{q}",
                                 headers={"User-Agent": "inframon-insar/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode()).get("elevation", [])


def terrain_class(lat: float, lon: float, water_context: str, *,
                  relief_km: float = 1.5, mountain_relief_m: float = 150.0,
                  elev_fn=_fetch_elevation) -> tuple[str, float | None]:
    """지형 분류 → (terrain, relief_m). marine 수계면 해상, 아니면 주변 표고 기복으로 산지/평지."""
    if water_context == "marine":
        return "해상", None
    import math
    d = relief_km / 111.0
    dlon = relief_km / (111.0 * math.cos(math.radians(lat)))
    lats = [lat, lat + d, lat - d, lat, lat, lat + d, lat - d, lat + d, lat - d]
    lons = [lon, lon, lon, lon + dlon, lon - dlon, lon + dlon, lon - dlon, lon - dlon, lon + dlon]
    try:
        elev = elev_fn(lats, lons)
    except Exception:  # noqa: BLE001 — 표고 실패 시 판정 보류
        return "평지", None
    elev = [e for e in elev if e is not None]
    if not elev:
        return "평지", None
    relief = max(elev) - min(elev)
    return ("산지" if relief >= mountain_relief_m else "평지"), round(relief, 1)


def build_bridge_meta(lat: float, lon: float, tags: dict, base_class: str,
                      length_m: float | None, water_context: str, *,
                      n_spans: int | None = None, elev_fn=_fetch_elevation) -> BridgeMeta:
    """교량 확장 메타 종합(⑪)."""
    structure = classify_structure(tags, base_class)
    span = max_span_estimate(structure, length_m, n_spans)
    grade = bridge_grade(length_m, span)
    width = bridge_width_m(tags)
    terrain, relief = terrain_class(lat, lon, water_context, elev_fn=elev_fn)
    return BridgeMeta(grade=grade, structure=structure,
                      structure_ko=_STRUCTURE_KO.get(structure, structure),
                      width_m=width, length_m=length_m, max_span_m=span,
                      terrain=terrain, relief_m=relief, lat=lat, lon=lon)
