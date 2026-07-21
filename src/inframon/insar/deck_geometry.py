"""곡선 교량 데크의 **호길이 station(along-deck arc length)** 산출.

교량은 직선이라는 보장이 없다(곡선·램프·S자). 그런데 FRAM 공간전파 항이나 구조축
거리는 '데크를 따라가는 순서/거리'가 필요하다 — X좌표만으로 정렬하면 곡선에서 순서가
뒤엉킨다(같은 X 에 데크 위치가 여럿). 여기서는 데크를 1차원 곡선으로 보고 각 점의
**호길이 station**(한쪽 끝에서 데크를 따라 잰 거리)을 구한다:

  · 데크 폴리라인(OSM geometry)이 있으면 각 점을 폴리라인에 **투영**해 station·수직오프셋.
  · 없으면 점군 자체에서 **주곡선**을 추정(PCA 시드 + 최근접 체인)해 station.

두 방식 모두 곡선·직선을 함께 처리한다. 좌표는 lon/lat 이면 위도 보정으로 국소 미터화.
"""

from __future__ import annotations

import numpy as np

_M_PER_DEG = 111_000.0


def _to_local_m(pts: np.ndarray, lat0: float | None = None) -> np.ndarray:
    """(lon,lat) → 국소 평면 미터. 이미 미터면 lat0=None 로 그대로. lat0 주면 lon 보정."""
    p = np.asarray(pts, float)[:, :2].copy()
    if lat0 is None:
        lat0 = float(np.median(p[:, 1]))
    looks_lonlat = np.abs(p[:, 0]).max() <= 360.0 and np.abs(p[:, 1]).max() <= 90.0
    if looks_lonlat:
        p[:, 0] = p[:, 0] * np.cos(np.radians(lat0)) * _M_PER_DEG
        p[:, 1] = p[:, 1] * _M_PER_DEG
    return p


def project_to_polyline(pts_lonlat: np.ndarray, poly_latlon) -> tuple[np.ndarray, np.ndarray]:
    """각 점을 데크 폴리라인에 투영 → (station_m, offset_m).

    poly_latlon: [[lat,lon],...] 데크 중심선(OSM geometry 규약). station 은 폴리라인
    시작점에서 데크를 따라 잰 호길이(곡선 반영), offset 은 데크 중심선과의 수직 거리.
    """
    poly = np.asarray(poly_latlon, float)
    poly_lonlat = np.column_stack([poly[:, 1], poly[:, 0]])           # [lat,lon]→[lon,lat]
    lat0 = float(np.median(poly[:, 0]))
    P = _to_local_m(pts_lonlat, lat0)                                 # [N,2] m
    V = _to_local_m(poly_lonlat, lat0)                               # [K,2] m
    seg = np.diff(V, axis=0)                                          # [K-1,2]
    seg_len = np.linalg.norm(seg, axis=1)                            # [K-1]
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])                # [K] 정점 누적 호길이
    N = len(P)
    station = np.zeros(N)
    offset = np.zeros(N)
    best = np.full(N, np.inf)
    for j in range(len(seg)):
        L2 = seg_len[j] ** 2 + 1e-12
        w = P - V[j]                                                  # [N,2]
        tt = np.clip((w @ seg[j]) / L2, 0.0, 1.0)                    # 세그먼트 내 위치[0,1]
        proj = V[j] + tt[:, None] * seg[j]                           # [N,2] 투영점
        d = np.linalg.norm(P - proj, axis=1)                        # 수직거리
        better = d < best
        best[better] = d[better]
        station[better] = cum[j] + tt[better] * seg_len[j]           # 호길이 station
        offset[better] = d[better]
    return station, offset


def principal_curve_station(pts: np.ndarray, lat0: float | None = None) -> np.ndarray:
    """폴리라인 없이 점군에서 **주곡선 호길이 station** 추정(곡선 데크 대응).

    PCA 제1축의 극단점에서 출발해 **최근접 이웃 체인**으로 데크를 따라가며 순서를 만들고,
    체인 누적거리를 station 으로 되돌린다. 직선·완만곡선·S자 모두 X정렬보다 견고하다.
    점<3 이면 좌표 순서 그대로.
    """
    P = _to_local_m(pts, lat0)
    n = len(P)
    if n < 3:
        return np.arange(n, dtype=float)
    c = P - P.mean(axis=0)
    # 제1주성분: 최대분산 축 → 데크 장축 근사
    _, _, vt = np.linalg.svd(c, full_matrices=False)
    proj = c @ vt[0]
    start = int(np.argmin(proj))                                     # 한쪽 끝에서 출발
    order = [start]
    used = np.zeros(n, bool)
    used[start] = True
    for _ in range(n - 1):
        last = order[-1]
        d = np.linalg.norm(P - P[last], axis=1)
        d[used] = np.inf
        nxt = int(np.argmin(d))
        order.append(nxt)
        used[nxt] = True
    order = np.asarray(order)
    seg = np.linalg.norm(np.diff(P[order], axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    station = np.empty(n)
    station[order] = cum
    return station


def deck_station(pts_lonlat: np.ndarray, geometry_latlon=None) -> np.ndarray:
    """데크 호길이 station[N] — 폴리라인 있으면 투영, 없으면 주곡선 추정. 곡선 교량 대응."""
    if geometry_latlon is not None and len(np.asarray(geometry_latlon)) >= 2:
        return project_to_polyline(pts_lonlat, geometry_latlon)[0]
    return principal_curve_station(pts_lonlat)
