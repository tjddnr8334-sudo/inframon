"""지지부(교각·교대) ZONE 모니터링 — 교량 중심선에서 지지부 위치를 잡고 근처 InSAR 점 추출.

매끈한 데크는 InSAR 점이 없지만, 교각·교대·접속부(거친 콘크리트·수직면)는 자연 PS/DS 가
생긴다. 이 모듈은 교량 선형(OSM 절점)으로 지지부 위치를 산정하고, buffer 내 점을 모아
침하·변위 추세 감시 대상으로 삼는다(데크는 PINN 경계추론).
"""
from __future__ import annotations

import math

import numpy as np


def support_positions(nodes, n_piers: int = 3) -> list[tuple[float, float, str]]:
    """중심선 절점[(lat,lon), ...] → 교대(양끝) + 교각(내부 등간격 n_piers) 위치.

    반환: [(lat, lon, kind)] · kind ∈ {"abutment", "pier"}.
    """
    nd = np.asarray(nodes, dtype=float)
    a, b = nd[0], nd[-1]
    pos = [(float(a[0]), float(a[1]), "abutment"), (float(b[0]), float(b[1]), "abutment")]
    for i in range(1, max(0, n_piers) + 1):
        t = i / (n_piers + 1)
        p = a + t * (b - a)
        pos.append((float(p[0]), float(p[1]), "pier"))
    return pos


def _m_per_deg(lat0: float) -> tuple[float, float]:
    return 111320.0, 111320.0 * math.cos(math.radians(lat0))


def support_zone(lonlat, nodes, n_piers: int = 3, buffer_m: float = 30.0) -> dict:
    """지지부(교대+교각) buffer_m 내 InSAR 점 식별.

    lonlat: [N,2] (lon,lat). nodes: 교량 중심선 [(lat,lon),...].
    반환: mask[N] · dmin[N] · supports(지지부별 개수·최근접) · n_support_points.
    """
    ll = np.asarray(lonlat, dtype=float)
    lon, lat = ll[:, 0], ll[:, 1]
    pos = support_positions(nodes, n_piers)
    clat = float(np.mean([p[0] for p in pos]))
    mlat, mlon = _m_per_deg(clat)
    dmin = np.full(len(lon), np.inf)
    supports = []
    for plat, plon, kind in pos:
        d = np.sqrt(((lat - plat) * mlat) ** 2 + ((lon - plon) * mlon) ** 2)
        dmin = np.minimum(dmin, d)
        supports.append({"kind": kind, "lat": plat, "lon": plon,
                         "n": int((d <= buffer_m).sum()), "nearest_m": float(d.min())})
    mask = dmin <= buffer_m
    return {"mask": mask, "dmin": dmin, "positions": pos, "supports": supports,
            "n_support_points": int(mask.sum()), "buffer_m": float(buffer_m)}


def support_velocity(los, days, mask) -> dict:
    """지지부 점들의 LOS 속도(mm/yr) 요약 — 침하/변위 추세."""
    los = np.asarray(los, dtype=float)[mask]
    if los.shape[0] == 0:
        return {"n": 0, "mean_mm_yr": float("nan"), "min_mm_yr": float("nan"),
                "max_mm_yr": float("nan")}
    t = np.asarray(days, dtype=float) / 365.25
    A = np.vstack([t, np.ones_like(t)]).T
    vel = np.linalg.lstsq(A, los.T, rcond=None)[0][0]
    return {"n": int(los.shape[0]), "mean_mm_yr": float(np.mean(vel)),
            "min_mm_yr": float(np.min(vel)), "max_mm_yr": float(np.max(vel))}
