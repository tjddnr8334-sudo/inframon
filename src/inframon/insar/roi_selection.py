"""교량 주변 **도심지 가중 ROI 선정** — OSM built-up 밀도로 5→2km 창 최적화.

InSAR PS/DS 는 도심(건물·인공구조물)에서 산란체가 풍부하다. 교량을 반드시 중심에 두지
않고, 5km~2km 폭 후보 중 **built-up 밀도가 가장 높은(교량 포함) ROI** 를 고른다. 이렇게
잡은 ROI 를 SLC 검색 AOI·SNAP 처리 창으로 쓰면 조밀한 점군을 얻는다.

OSM(Overpass)로 건물(building)·시가지 landuse(residential/commercial/industrial/retail)를
가져와 후보 ROI 별 건물수·밀도를 계산. 네트워크는 osm_bridge._overpass_query 재사용(격리).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .osm_bridge import _overpass_query


@dataclass
class RoiResult:
    """선정된 도심지 ROI."""

    bbox: tuple[float, float, float, float]   # (min_lon, min_lat, max_lon, max_lat)
    size_km: float
    center: tuple[float, float]               # (lat, lon)
    n_buildings: int
    density_per_km2: float                    # 건물/km²(도심지수)
    contains_bridge: bool

    def wkt(self) -> str:
        w, s, e, n = self.bbox
        return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"

    def as_dict(self) -> dict:
        return {"bbox": list(self.bbox), "size_km": self.size_km,
                "center_latlon": list(self.center), "n_buildings": self.n_buildings,
                "density_per_km2": round(self.density_per_km2, 1),
                "contains_bridge": self.contains_bridge}


def _builtup_query(lat: float, lon: float, radius_m: float) -> str:
    return (
        "[out:json][timeout:60];("
        f'way(around:{radius_m},{lat},{lon})["building"];'
        f'way(around:{radius_m},{lat},{lon})["landuse"~"residential|commercial|industrial|retail"];'
        ");out center;"
    )


def _elem_lonlat(el: dict):
    """way(center) / node → (lon, lat). 없으면 None."""
    if "center" in el:
        return el["center"]["lon"], el["center"]["lat"]
    if "lon" in el and "lat" in el:
        return el["lon"], el["lat"]
    return None


def fetch_builtup(lat: float, lon: float, radius_m: float, *, query_fn=_overpass_query,
                  retries: int = 3):
    """교량 주변 built-up 요소 중심점 목록 [(lon,lat), ...]. Overpass 504 등 일시오류 재시도."""
    ql = _builtup_query(lat, lon, radius_m)
    data = None
    for attempt in range(retries):
        try:
            data = query_fn(ql)
            break
        except Exception:  # noqa: BLE001 — 504/timeout 등 → 재시도(마지막이면 전파)
            if attempt == retries - 1:
                raise
    pts = []
    for el in data.get("elements", []):
        p = _elem_lonlat(el)
        if p:
            pts.append((float(p[0]), float(p[1])))
    return pts


def _km_to_deg(km: float, lat: float) -> tuple[float, float]:
    return km / 111.0, km / (111.0 * math.cos(math.radians(lat)))   # (dlat, dlon)


def select_roi(lat: float, lon: float, *, sizes_km=(2.0, 3.0, 4.0, 5.0),
               grid: int = 7, query_fn=_overpass_query,
               builtup: list | None = None) -> RoiResult:
    """교량(lat,lon) 을 포함하며 built-up 밀도가 최대인 ROI(5→2km 후보) 선정.

    각 크기 s 마다 교량이 ROI 안에 남는 범위에서 중심을 grid×grid 로 슬라이드하며 건물수
    최대 위치를 찾고, 크기 간에는 **밀도(건물/km²)** 로 비교(도심 집중). 동률(±10%)이면
    큰 ROI 선호(산란체·커버리지↑). builtup 을 주면 조회 생략(테스트).
    """
    if builtup is None:
        builtup = fetch_builtup(lat, lon, radius_m=max(sizes_km) * 1000.0 * 0.8, query_fn=query_fn)
    bx = [p[0] for p in builtup]; by = [p[1] for p in builtup]

    best: RoiResult | None = None
    for s in sorted(sizes_km):
        dlat, dlon = _km_to_deg(s, lat)
        half_lat, half_lon = dlat / 2, dlon / 2
        # 교량이 ROI 안에 있으려면 중심은 교량에서 ±half 이내
        best_s = None
        for i in range(grid):
            for j in range(grid):
                clat = lat + (i / (grid - 1) - 0.5) * dlat      # ±half_lat
                clon = lon + (j / (grid - 1) - 0.5) * dlon
                w, e = clon - half_lon, clon + half_lon
                so, no = clat - half_lat, clat + half_lat
                n = sum(1 for k in range(len(bx)) if w <= bx[k] <= e and so <= by[k] <= no)
                dens = n / (s * s)
                cand = RoiResult((w, so, e, no), s, (clat, clon), n, dens,
                                 w <= lon <= e and so <= lat <= no)
                if best_s is None or cand.n_buildings > best_s.n_buildings:
                    best_s = cand
        if best_s is None:
            continue
        if best is None or best_s.density_per_km2 > best.density_per_km2 * 1.10:
            best = best_s
        elif best_s.density_per_km2 >= best.density_per_km2 * 0.90 and best_s.size_km > best.size_km:
            best = best_s          # 밀도 비슷하면 큰 ROI
    if best is None:
        raise ValueError("ROI 후보를 만들 수 없습니다(built-up 데이터 없음).")
    return best
