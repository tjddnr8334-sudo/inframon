"""오름/내림(asc+desc) 궤도 융합 — 단일 LOS 를 연직+종축 변위로 분해.

단일 궤도는 LOS 1성분만 측정하므로 연직(처짐·침하)과 수평(열팽창)을 분리할 수 없다.
서로 다른 방향에서 본 두 LOS(asc, desc)가 있으면 점·시점마다 2×2 역산으로
**연직(U)** 과 **교량 종축 수평(H)** 을 분리한다.

  los_track = U·cosθ + H·sinθ·cos(A − λ)
    θ = 입사각, λ = LOS 지상투영 방위각(= heading + 90°, 우측 관측), A = 교량 종축 방위각.
  두 궤도 → [U, H] 를 풀 수 있다.

asc/desc 는 취득일이 달라 **시간 보간**(desc → asc 타임라인)으로 공통 시점을 만들고,
공간은 **최근접 정합**(데크 위 같은 지점)으로 짝짓는다.

융합 불가 조건(→ `FusionError`, 호출 측은 단일 궤도로 폴백):
  - 두 궤도 중 하나라도 입사각/heading 이 없음
  - 시간 겹침 구간이 없거나 공통 시점 < 2
  - 최근접 정합점이 부족(매칭 < 2)
  - 기하가 특이(asc/desc 시선이 거의 평행)해 역산 불안정
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .track_reader import TrackData

_EPOCH = datetime(1970, 1, 1)

# 우측 관측(Sentinel-1) 기본. 역산 안정 한계(det)·정합 거리 기본값.
LOOK_SIDE_DEG = 90.0          # heading → LOS 지상투영 방위각(우측 +90°)
_DEFAULT_MAX_PAIR_M = 60.0    # asc↔desc 최근접 정합 허용 거리(데크 폭 규모)
_DEFAULT_MIN_DET = 0.05       # 2×2 행렬식 하한(이하면 시선 평행 → 분해 불안정)


class FusionError(Exception):
    """asc+desc 융합 불가 — 호출 측은 단일 궤도 처리로 폴백한다."""


@dataclass
class FusionResult:
    track: TrackData            # 융합된(asc 기준) 점·LOS·공통시점
    longitudinal: np.ndarray    # [N,Mc] 종축 수평 변위 H
    vertical: np.ndarray        # [N,Mc] 연직 변위 U
    meta: dict = field(default_factory=dict)


def look_azimuth(heading_deg: float, side_deg: float = LOOK_SIDE_DEG) -> float:
    """위성 heading → LOS 지상투영 방위각 λ(북 기준 시계방향). 우측 관측이면 +90°."""
    return (float(heading_deg) + side_deg) % 360.0


def los_sensitivity(inc_deg, look_az_deg: float, axis_az_deg: float):
    """(연직 민감도 cosθ, 종축 민감도 sinθ·cos(A−λ)). inc_deg 스칼라/[N]."""
    th = np.deg2rad(np.asarray(inc_deg, dtype=np.float64))
    dlt = np.deg2rad(float(axis_az_deg) - float(look_az_deg))
    return np.cos(th), np.sin(th) * np.cos(dlt)


def los_from_components(U, H, inc_deg, look_az_deg, axis_az_deg):
    """순방향 모델 los = U·cosθ + H·sinθ·cos(A−λ) (테스트·검증용)."""
    su, sh = los_sensitivity(inc_deg, look_az_deg, axis_az_deg)
    return np.asarray(U) * su + np.asarray(H) * sh


def _to_local_m(lonlat: np.ndarray, lon0: float, lat0: float) -> np.ndarray:
    """지리좌표(도)면 lon0/lat0 기준 국소 ENU 미터로 변환. 투영좌표면 그대로."""
    geographic = (np.abs(lonlat[:, 0]).max() <= 180.0) and (np.abs(lonlat[:, 1]).max() <= 90.0)
    if not geographic:
        return lonlat.astype(np.float64)
    e = (lonlat[:, 0] - lon0) * 111320.0 * np.cos(np.deg2rad(lat0))
    n = (lonlat[:, 1] - lat0) * 110540.0
    return np.column_stack([e, n])


def _match_nearest(p_xy: np.ndarray, q_xy: np.ndarray, max_dist: float):
    """p 각 점에 대해 q 최근접 인덱스 + max_dist 이내 마스크(블록 처리로 메모리 안전)."""
    n = p_xy.shape[0]
    idx = np.zeros(n, dtype=np.int64)
    dmin = np.full(n, np.inf)
    block = 256
    for s in range(0, n, block):
        pb = p_xy[s:s + block]
        d2 = ((pb[:, None, :] - q_xy[None, :, :]) ** 2).sum(axis=2)
        j = np.argmin(d2, axis=1)
        idx[s:s + block] = j
        dmin[s:s + block] = np.sqrt(d2[np.arange(pb.shape[0]), j])
    return idx, dmin <= max_dist


def _axis_azimuth_deg(lonlat: np.ndarray) -> float:
    """점군 주축(PC1)의 방위각[deg, 북 기준 시계]. 지리좌표는 국소 ENU 로 환산 후 PCA."""
    lon0, lat0 = float(np.mean(lonlat[:, 0])), float(np.mean(lonlat[:, 1]))
    en = _to_local_m(lonlat, lon0, lat0)
    c = en - en.mean(axis=0)
    _, vecs = np.linalg.eigh(c.T @ c)
    e, nth = vecs[0, -1], vecs[1, -1]      # PC1 (E, N)
    return float(np.rad2deg(np.arctan2(e, nth)) % 360.0)


def _abs_days(date_labels) -> np.ndarray:
    """date_labels(YYYYMMDD) → 절대 일수(1970 기준). asc/desc 시간축을 공통 기준으로."""
    labels = np.asarray(date_labels).astype(str)
    return np.array([(datetime.strptime(s, "%Y%m%d") - _EPOCH).days for s in labels],
                    dtype=np.float64)


def _common_timeline(asc: TrackData, desc: TrackData):
    """asc 타임라인 중 desc 시간 범위와 겹치는 시점 인덱스 + asc/desc 절대 일수.

    asc.dates·desc.dates 는 각자 첫 취득일 기준 상대일수라 직접 비교 불가 →
    date_labels(절대일)로 환산해 겹치는 구간을 찾는다.
    """
    a_abs = _abs_days(asc.date_labels)
    d_abs = _abs_days(desc.date_labels)
    lo, hi = max(a_abs.min(), d_abs.min()), min(a_abs.max(), d_abs.max())
    sel = np.where((a_abs >= lo) & (a_abs <= hi))[0]
    return sel, a_abs, d_abs


def fuse_asc_desc(
    asc: TrackData,
    desc: TrackData,
    *,
    max_pair_dist_m: float = _DEFAULT_MAX_PAIR_M,
    min_det: float = _DEFAULT_MIN_DET,
    look_side_deg: float = LOOK_SIDE_DEG,
) -> FusionResult:
    """asc(기준)·desc Track → 연직+종축 분해. 불가 시 FusionError."""
    if asc.incidence is None or desc.incidence is None:
        raise FusionError("입사각 없음 — 두 궤도 모두 incidence 필요(단일 폴백).")
    if asc.heading is None or desc.heading is None:
        raise FusionError("heading 없음 — 두 궤도 모두 위성 heading 필요(단일 폴백).")

    # 1) 공통 시간(asc 타임라인) + desc 시간보간 (절대일 기준)
    sel, a_abs, d_abs = _common_timeline(asc, desc)
    if sel.size < 2:
        raise FusionError(f"공통 시점 부족({sel.size}<2) — 시간 겹침이 없습니다(단일 폴백).")
    abs_days = a_abs[sel]                                            # 공통 시점 절대일
    a_los = np.asarray(asc.los, dtype=np.float64)[:, sel]            # [Na, Mc]

    # 2) 공간 최근접 정합(데크 위 동일 지점)
    lon0, lat0 = float(np.mean(asc.lonlat[:, 0])), float(np.mean(asc.lonlat[:, 1]))
    pa = _to_local_m(asc.lonlat, lon0, lat0)
    pd = _to_local_m(desc.lonlat, lon0, lat0)
    j, within = _match_nearest(pa, pd, max_pair_dist_m)
    if int(within.sum()) < 2:
        raise FusionError(f"정합점 부족({int(within.sum())}<2) — asc/desc 가 같은 지점에 없습니다(단일 폴백).")

    keep = np.where(within)[0]
    j_keep = j[keep]
    # desc LOS 를 매칭점별로 asc 공통시점(절대일)에 선형 시간보간
    d_los_full = np.asarray(desc.los, dtype=np.float64)
    d_los = np.vstack([np.interp(abs_days, d_abs, d_los_full[jk]) for jk in j_keep])  # [Nk, Mc]

    # 3) 기하: 종축 방위 A(매칭점 PCA) + 궤도별 LOS 방위 λ
    axis_az = _axis_azimuth_deg(asc.lonlat[keep])
    lam_a = look_azimuth(asc.heading, look_side_deg)
    lam_d = look_azimuth(desc.heading, look_side_deg)
    su_a, sh_a = los_sensitivity(asc.incidence[keep], lam_a, axis_az)            # [Nk]
    su_d, sh_d = los_sensitivity(desc.incidence[j_keep], lam_d, axis_az)         # [Nk]

    # 4) 점별 2×2 역산:  [los_a; los_d] = [[su_a,sh_a],[su_d,sh_d]] [U; H]
    det = su_a * sh_d - sh_a * su_d
    good = np.abs(det) >= min_det
    if int(good.sum()) < 2:
        raise FusionError("기하 특이 — asc/desc 시선이 거의 평행해 분해 불안정(단일 폴백).")

    keep2 = keep[good]
    j2 = j_keep[good]
    det_g = det[good][:, None]
    a_g = a_los[keep][good]      # [Ng, Mc]
    d_g = d_los[good]            # [Ng, Mc]
    su_a_g, sh_a_g = su_a[good][:, None], sh_a[good][:, None]
    su_d_g, sh_d_g = su_d[good][:, None], sh_d[good][:, None]
    U = (sh_d_g * a_g - sh_a_g * d_g) / det_g
    H = (-su_d_g * a_g + su_a_g * d_g) / det_g

    # 5) 융합된(asc 기준) TrackData 구성
    coh = np.minimum(asc.coherence[keep2], desc.coherence[j2]).astype(np.float32)
    height = None if asc.height is None else asc.height[keep2]
    fused = TrackData(
        lonlat=asc.lonlat[keep2],
        los=a_los[keep2].astype(np.float32),         # 기준(asc) LOS 보존
        dates=(abs_days - abs_days[0]).astype(np.float64),  # 첫 공통시점 기준 상대일
        date_labels=np.asarray(asc.date_labels)[sel],
        coherence=coh,
        height=height,
        incidence=asc.incidence[keep2],
        heading=asc.heading,
        attrs={**asc.attrs, "fused": "asc+desc"},
    )
    meta = {
        "ok": True, "method": "asc_desc_fusion",
        "n_asc": int(asc.los.shape[0]), "n_desc": int(desc.los.shape[0]),
        "n_matched": int(within.sum()), "n_fused": int(keep2.size),
        "n_common_dates": int(sel.size),
        "axis_azimuth_deg": round(axis_az, 2),
        "look_az_asc_deg": round(lam_a, 2), "look_az_desc_deg": round(lam_d, 2),
        "max_pair_dist_m": max_pair_dist_m,
    }
    return FusionResult(track=fused, longitudinal=H.astype(np.float32),
                        vertical=U.astype(np.float32), meta=meta)
