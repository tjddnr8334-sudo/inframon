"""처리 오프셋(B) 추정 — 지면 점이 OSM 도로선에서 계통적으로 밀려 있는가.

쉬프트에는 세 층이 있다:
  A. 기하 쉬프트  δh/tanθ — 데크 위 산란체가 DEM 지오코딩 때문에 밀림. `geolocation` 이 보정.
  B. 처리 오프셋  궤도·타이밍·DEM 오차 — **점군 전체**가 같은 벡터로 밀림. 대개 1화소 이내.
  C. 산란체 위치  점이 노면이 아니라 보도·난간에 있음 — 쉬프트가 아니라 실제 위치.

교량 위 점만 봐서는 B 와 C 를 못 가른다. **교량 밖 지면 점**은 δh=0 이라 A 가 없고 C 도
없으니 남는 것은 B 뿐이다. 지면 점을 OSM 차도 중심선에 투영해 수직 오프셋 벡터의 계통적
평균을 구하면 그것이 B 다. B 를 뺀 뒤에도 교량 위 점이 한쪽에 남으면 그것은 C 다.

한계: OSM 도로 중심선이 기준이므로 **상대** 검증이다. 도로선 자체가 몇 m 틀릴 수 있어
정밀도 하한은 3~5 m. 절대 기준은 코너리플렉터·GNSS 가 있어야 한다.

정자교(2026-09-08): 지면 점 1,025 → 평균 (0.0, +0.5) m, 도로 양쪽 대칭(E 379 / W 401).
→ B ≈ 0. 교면 위 점의 남측 +12 m 는 산란체 위치(C)다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROAD_HALF_WIDTH_M = 25.0      # 도로선에서 이 안의 점만 '도로 위 산란체 후보'로 본다
MIN_POINTS = 100              # 이보다 적으면 B 를 추정하지 않는다(σ 가 너무 크다)
SIGNIFICANT_M = 3.0           # |B| 가 이보다 작으면 0 으로 본다(OSM 정밀도 하한)
ROAD_TAGS = "primary|secondary|tertiary|residential|unclassified|trunk"


@dataclass
class GroundOffset:
    dx_m: float                   # 동(+)
    dy_m: float                   # 북(+)
    se_m: float                   # 벡터 평균의 표준오차(합성)
    n_points: int
    n_roads: int
    direction_counts: dict        # 8방위별 점수 — 대칭이면 B≈0
    significant: bool
    meta: dict = field(default_factory=dict)

    @property
    def norm_m(self) -> float:
        return float(math.hypot(self.dx_m, self.dy_m))

    def describe(self) -> str:
        if not self.significant:
            return (f"처리 오프셋 ≈ 0 (|B| {self.norm_m:.1f} m < {SIGNIFICANT_M:g} m · "
                    f"지면 점 {self.n_points} · 도로 양쪽 대칭)")
        return (f"처리 오프셋 (E {self.dx_m:+.1f}, N {self.dy_m:+.1f}) m ± {self.se_m:.1f} · "
                f"지면 점 {self.n_points} — 전 점군에서 뺀다")


def fetch_roads(lat: float, lon: float, radius_m: float = 500.0, *, cache: Path | None = None,
                retries: int = 3) -> list[list[tuple[float, float]]]:
    """OSM 차도(교량 제외) 폴리라인 [[(lat,lon),...],...]. 캐시가 있으면 그것."""
    if cache and Path(cache).exists():
        d = json.loads(Path(cache).read_text(encoding="utf-8"))
        return [[tuple(p) for p in r] for r in d["roads"]]
    import time

    from .osm_bridge import _overpass_query
    q = (f'[out:json][timeout:90];way(around:{radius_m:.0f},{lat},{lon})'
         f'["highway"~"^({ROAD_TAGS})$"]["bridge"!~"."];out geom;')
    last: Exception | None = None
    for i in range(retries):
        try:
            els = _overpass_query(q, timeout=120)["elements"]
            break
        except Exception as e:                   # noqa: BLE001 — Overpass 는 자주 504 다
            last = e
            time.sleep(8 * (i + 1))
    else:
        raise RuntimeError(f"Overpass 실패 {retries}회: {last}")
    roads = [[(g["lat"], g["lon"]) for g in e.get("geometry", []) if "lat" in g] for e in els]
    roads = [r for r in roads if len(r) >= 2]
    if cache:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        Path(cache).write_text(json.dumps({"lat": lat, "lon": lon, "radius_m": radius_m,
                                           "roads": roads}, ensure_ascii=False), encoding="utf-8")
    return roads


def estimate(lonlat: np.ndarray, roads: list, *, center_lat: float, center_lon: float,
             exclude_radius_m: float = 80.0, max_radius_m: float = 500.0) -> GroundOffset:
    """지면 점(교량 중심 exclude~max 반경) → 최근접 도로 세그먼트 수직 오프셋 벡터의 평균.

    lonlat [N,2] 는 **보정 전** 원 좌표여야 한다(B 는 처리 단계 오프셋이다).
    """
    k = math.cos(math.radians(center_lat)) * 111_320.0
    P = np.column_stack([(lonlat[:, 0] - center_lon) * k, (lonlat[:, 1] - center_lat) * 111_320.0])
    d0 = np.hypot(P[:, 0], P[:, 1])
    sel = (d0 > exclude_radius_m) & (d0 <= max_radius_m)
    Pn = P[sel]
    segs = []
    for r in roads:
        Q = np.column_stack([(np.array([p[1] for p in r]) - center_lon) * k,
                             (np.array([p[0] for p in r]) - center_lat) * 111_320.0])
        segs += list(zip(Q[:-1], Q[1:]))
    if len(Pn) < MIN_POINTS or not segs:
        return GroundOffset(0.0, 0.0, float("nan"), int(len(Pn)), len(roads), {},
                            significant=False,
                            meta={"reason": f"지면 점 {len(Pn)} 또는 도로 {len(roads)} 부족"})
    A = np.array([s[0] for s in segs]); B = np.array([s[1] for s in segs])
    D = B - A; L2 = (D ** 2).sum(1) + 1e-9
    best = np.full(len(Pn), np.inf); vec = np.zeros((len(Pn), 2))
    for j in range(len(A)):
        w = Pn - A[j]
        t = np.clip((w @ D[j]) / L2[j], 0.0, 1.0)
        v = Pn - (A[j] + t[:, None] * D[j])
        dd = np.hypot(v[:, 0], v[:, 1])
        m = dd < best
        best[m] = dd[m]; vec[m] = v[m]
    close = best <= ROAD_HALF_WIDTH_M
    V = vec[close]
    n = int(close.sum())
    if n < MIN_POINTS:
        return GroundOffset(0.0, 0.0, float("nan"), n, len(roads), {}, significant=False,
                            meta={"reason": f"도로 {ROAD_HALF_WIDTH_M:g} m 안 지면 점 {n} 부족"})
    dx, dy = float(V[:, 0].mean()), float(V[:, 1].mean())
    se = float(math.hypot(V[:, 0].std() / math.sqrt(n), V[:, 1].std() / math.sqrt(n)))
    ang = np.degrees(np.arctan2(V[:, 1], V[:, 0])) % 360
    h, _ = np.histogram(ang, bins=8, range=(0, 360))
    counts = dict(zip(("E", "NE", "N", "NW", "W", "SW", "S", "SE"), h.tolist()))
    norm = math.hypot(dx, dy)
    sig = norm >= SIGNIFICANT_M and norm > 2.0 * se
    return GroundOffset(dx, dy, se, n, len(roads), counts, significant=sig,
                        meta={"road_half_width_m": ROAD_HALF_WIDTH_M,
                              "median_abs_offset_m": float(np.median(best[close])),
                              "note": "OSM 도로선 기준 상대 검증 — 정밀도 하한 3~5 m"})


def apply(lonlat: np.ndarray, off: GroundOffset, *, center_lat: float) -> np.ndarray:
    """B 가 유의하면 전 점군에서 뺀다(되돌린다). 아니면 그대로."""
    if not off.significant:
        return np.asarray(lonlat, float)
    k = math.cos(math.radians(center_lat)) * 111_320.0
    out = np.asarray(lonlat, float).copy()
    out[:, 0] -= off.dx_m / k
    out[:, 1] -= off.dy_m / 111_320.0
    return out
