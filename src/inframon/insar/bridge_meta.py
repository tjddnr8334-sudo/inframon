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


# 주경간지배형(케이블·아치·트러스): 주경간/연장 비 — 하나의 큰 주경간이 지배
_MAIN_SPAN_RATIO = {"suspension": 0.75, "cable_stayed": 0.55, "arch": 0.5, "truss": 0.4}
# 반복경간형(거더·박스·라멘): 한국 실무 대표 경간[m] — 연장을 이 값 근처로 균등분할
_TYPICAL_SPAN_M = {"girder": 35.0, "box_girder": 50.0, "rahmen": 15.0}


def max_span_estimate(structure: str, length_m: float | None,
                      n_spans: int | None = None) -> float | None:
    """최대경간 추정[m].

    · 경간수(n_spans) 주어지면 연장/n (최우선).
    · **주경간지배형**(현수·사장·아치·트러스): 연장×주경간비(하나의 큰 주경간).
    · **반복경간형**(거더·박스·라멘): 형식별 대표경간 근처로 **균등분할**한 실제 단일경간.
      짧으면(≲1.3×대표) 단경간=연장. 기존 length×작은비율(예 girder 0.18)이 경간을
      과소평가(108m→19m→모달 22Hz)하던 것을 대표경간 기준으로 정밀화(108m→36m→~5Hz).
    """
    if length_m is None:
        return None
    if n_spans and n_spans > 0:
        return round(length_m / n_spans, 1)
    if structure in _MAIN_SPAN_RATIO:                     # 주경간지배형
        return round(length_m * _MAIN_SPAN_RATIO[structure], 1)
    typ = _TYPICAL_SPAN_M.get(structure, 40.0)            # 반복경간형: 대표경간 균등분할
    if length_m <= 1.3 * typ:
        return round(length_m, 1)                         # 단경간
    n = max(1, round(length_m / typ))
    return round(length_m / n, 1)


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
