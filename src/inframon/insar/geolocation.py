"""PS/DS 지오로케이션 쉬프트 보정 — 높이 오차가 만드는 수평 위치 밀림.

SAR 는 사각(side-looking)이라, 점이 지오코딩에 쓴 기준 DEM 보다 **δh 높으면** 그 점의
slant range 가 짧아져 지도상 위치가 **위성 지상궤적 쪽(near-range)으로 밀린다**. 밀림 크기는

    Δground = δh / tan(θ)        (θ = 입사각)

교량에서 이게 치명적이다 — 교각·거더는 하천·지면 DEM 보다 5~20m 높은데, 39° 입사각에서
δh=10m 면 **12m 밀린다**. 교량 폭이 10~30m 이므로, 보정 안 하면 데크 위 점이 지도에서
**교량 밖(강물·주변 건물)으로 나가** "데크 점이 없다"처럼 보인다.

보정: 밀린 만큼 **반대 방향(far-range)으로 되돌리고**, z 를 실제 높이(DEM+δh)로 둔다.
방향은 지상 LOS 수평투영(heading 으로 결정)이다.

필요 입력: 점별 **DEM 오차 δh**(SARvey `dem_error`/잔차 지형), **입사각 θ**, 위성 **heading**.
셋 다 없으면 보정 불가 — 그 사실을 알린다(조용히 넘어가지 않는다).
"""

from __future__ import annotations

import numpy as np

# δh 부호 규약: **양수 = 점이 기준 DEM 보다 높다**(교량 상부구조가 여기 해당).
# SARvey dem_error / residual height 표준 규약과 일치.


def los_ground_unit(heading_deg: float, look_side: str = "right") -> tuple[float, float]:
    """지상 LOS 수평투영 단위벡터 (E, N) — 점에서 위성 지상궤적을 향하는 방향.

    gnss_ngl.enu_to_los 와 같은 규약: 우측관측(Sentinel-1 기본)에서
    p_E ∝ −cos(α), p_N ∝ +sin(α) (α = heading, N 기준 시계).
    좌측관측이면 부호 반전.
    """
    al = np.radians(float(heading_deg))
    e, n = -np.cos(al), np.sin(al)
    if str(look_side).lower().startswith("l"):
        e, n = -e, -n
    norm = np.hypot(e, n) + 1e-12
    return e / norm, n / norm


def height_shift(dem_error: np.ndarray, incidence_deg: np.ndarray | float,
                 heading_deg: float, *, look_side: str = "right") -> dict:
    """높이 오차 → 지오코딩 수평 밀림 벡터 [N,2] (E,N, m) 와 크기.

    반환하는 것은 **현재 위치에 이미 들어간 밀림**(geocoded − true)이다.
    보정은 이 벡터를 **빼면** 된다(`apply_correction` 참조).

    Args:
        dem_error: [N] 점별 DEM 오차 δh[m] (양수=DEM 보다 높음)
        incidence_deg: [N] 또는 스칼라 입사각[deg]
        heading_deg: 위성 heading[deg]
    """
    dh = np.asarray(dem_error, dtype=np.float64).ravel()
    inc = np.asarray(incidence_deg, dtype=np.float64).ravel()
    if inc.size == 1:
        inc = np.full_like(dh, float(inc[0]))
    tan_t = np.tan(np.radians(inc))
    mag = dh / np.where(np.abs(tan_t) < 1e-6, np.nan, tan_t)   # δh/tanθ, [N]
    ue, un = los_ground_unit(heading_deg, look_side)
    # 높은 점은 위성쪽(near-range = +지상LOS수평)으로 밀린다 → geocoded = true + mag·(ue,un)
    shift = np.stack([mag * ue, mag * un], axis=1)             # [N,2] (E,N)
    return {"shift_en_m": shift, "magnitude_m": np.abs(mag),
            "los_ground_unit": (ue, un),
            "mean_abs_m": float(np.nanmean(np.abs(mag))),
            "p95_abs_m": float(np.nanpercentile(np.abs(mag), 95)),
            "max_abs_m": float(np.nanmax(np.abs(mag)))}


def _meters_per_degree(lat_deg: float) -> tuple[float, float]:
    """위도에서 경도 1°·위도 1° 의 미터 환산(경위도 좌표 보정용)."""
    m_per_lat = 111_320.0
    m_per_lon = 111_320.0 * np.cos(np.radians(lat_deg))
    return m_per_lon, m_per_lat


def apply_correction(
    xyz: np.ndarray,
    dem_error: np.ndarray,
    incidence_deg: np.ndarray | float,
    heading_deg: float,
    *,
    crs_is_lonlat: bool | None = None,
    look_side: str = "right",
    set_height: bool = True,
    base_height: np.ndarray | None = None,
) -> dict:
    """점 좌표 [N,3] (또는 [N,2]) 의 지오로케이션 쉬프트를 보정한다.

    Args:
        xyz: [N,2|3] 점 좌표. crs_is_lonlat=True 면 (lon,lat,[z]), 아니면 (E,N,[z]) 투영 미터.
        dem_error: [N] DEM 오차 δh[m]
        crs_is_lonlat: None 이면 좌표 범위로 자동 판정(|x|≤180,|y|≤90 → 경위도).
        set_height: True 면 z 를 base_height+δh(있으면) 또는 기존 z 에 δh 더해 실제 높이로.
        base_height: [N] 기준 DEM 고도. 주면 z=base+δh, 없으면 z 기존값+δh.

    Returns: {"xyz": 보정좌표, "shift_m": 크기[N], "meta": 통계·방향}
    """
    P = np.atleast_2d(np.asarray(xyz, dtype=np.float64)).copy()
    n = P.shape[0]
    hs = height_shift(dem_error, incidence_deg, heading_deg, look_side=look_side)
    shift = hs["shift_en_m"]                                   # [N,2] geocoded−true (E,N, m)

    xy = P[:, :2]
    if crs_is_lonlat is None:
        crs_is_lonlat = bool(np.abs(xy[:, 0]).max() <= 180.0 and np.abs(xy[:, 1]).max() <= 90.0)

    if crs_is_lonlat:
        lat0 = float(np.median(xy[:, 1]))
        m_lon, m_lat = _meters_per_degree(lat0)
        # 보정 = geocoded − shift (밀린 만큼 되돌림). 미터 → 도.
        xy[:, 0] -= shift[:, 0] / m_lon
        xy[:, 1] -= shift[:, 1] / m_lat
    else:
        xy -= shift                                           # 투영 미터면 그대로 뺀다

    P[:, :2] = xy
    if set_height and P.shape[1] >= 3:
        dh = np.asarray(dem_error, dtype=np.float64).ravel()
        if base_height is not None:
            P[:, 2] = np.asarray(base_height, dtype=np.float64).ravel() + dh
        else:
            P[:, 2] = P[:, 2] + dh

    return {
        "xyz": P,
        "shift_m": hs["magnitude_m"],
        "meta": {
            "n_points": n,
            "crs": "lonlat" if crs_is_lonlat else "projected_m",
            "heading_deg": float(heading_deg),
            "look_side": look_side,
            "shift_mean_abs_m": round(hs["mean_abs_m"], 3),
            "shift_p95_abs_m": round(hs["p95_abs_m"], 3),
            "shift_max_abs_m": round(hs["max_abs_m"], 3),
            "dem_error_mean_m": round(float(np.nanmean(np.abs(dem_error))), 3),
            "note": ("δh 부호 규약: 양수=DEM 보다 높음(교량 상부구조). 보정은 밀림(δh/tanθ)을 "
                     "지상 LOS 수평투영 반대 방향으로 되돌리고 z=DEM+δh 로 둔다."),
        },
    }


def diagnose(dem_error: np.ndarray, incidence_deg: np.ndarray | float,
             heading_deg: float, *, deck_width_m: float = 20.0) -> dict:
    """점들이 얼마나 밀렸는지 진단 — 보정 전에 '쉬프트가 있는가'를 판단.

    교량 폭 대비 밀림이 크면(데크 밖으로 나갈 정도) 보정이 필요하다고 알린다.
    """
    hs = height_shift(dem_error, incidence_deg, heading_deg)
    mag = hs["magnitude_m"]
    beyond = float(np.mean(mag > deck_width_m / 2.0))         # 데크 반폭 넘는 점 비율
    dh = np.asarray(dem_error, dtype=np.float64).ravel()
    return {
        "shift_mean_abs_m": round(hs["mean_abs_m"], 2),
        "shift_p95_abs_m": round(hs["p95_abs_m"], 2),
        "shift_max_abs_m": round(hs["max_abs_m"], 2),
        "dem_error_p95_m": round(float(np.nanpercentile(np.abs(dh), 95)), 2),
        "fraction_beyond_half_deck": round(beyond, 3),
        "deck_width_m": deck_width_m,
        "needs_correction": bool(hs["p95_abs_m"] > deck_width_m / 4.0),
        "verdict": ("쉬프트가 데크 폭에 비해 큼 — 보정 권장(점이 교량 밖으로 밀렸을 수 있음)"
                    if hs["p95_abs_m"] > deck_width_m / 4.0 else
                    "쉬프트가 작음 — 보정 영향 미미"),
    }
