#!/usr/bin/env python3
"""Track B/C (MintPy SBAS/QPS) 지오코딩 래스터 시계열 → inframon Track H5 (점 추출).

MintPy 산출은 지오코딩된 래스터 큐브다:
  - (geo_)timeseries.h5 : 'timeseries' [M,L,W] (m), 'date' [M], 그리고 격자 affine 속성
        X_FIRST,Y_FIRST,X_STEP,Y_STEP (좌표는 per-pixel 배열이 아니라 이 attrs 로 정의)
  - temporalCoherence.h5: 'temporalCoherence' [L,W]   (또는 maskTempCoh.h5 마스크)

각 픽셀의 lon/lat 를 affine 으로 계산하고, coherence ≥ 임계(MintPy 기본 ~0.7) + (선택)ROI
안의 픽셀만 점으로 뽑아 Track H5(pixel_lonlat/epochs/los_mm/coh)로 쓴다.

좌표가 레이더(geometryRadar.h5 lat/lon 배열)면 --geometry 를 주면 그걸 쓴다(Track D 와 동일).
점 구름(MiaplPy)은 52_miaplpy_to_inframon.py 사용.

사용(지오코딩):
  python3 54_mintpy_to_inframon.py \
    --timeseries SBAS/geo/geo_timeseries.h5 \
    --coherence  SBAS/geo/geo_temporalCoherence.h5 \
    --out track_b.h5 --coh-thresh 0.7 \
    --bbox 127.10058 37.35939 127.12098 37.37802
"""

from __future__ import annotations

import argparse

import h5py
import numpy as np


def _attr(obj, key):
    v = obj.attrs.get(key)
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode()
    return v


def _dates_to_yyyymmdd(raw) -> np.ndarray:
    out = []
    for v in np.asarray(raw).ravel():
        s = v.decode() if isinstance(v, bytes) else str(v)
        out.append(int(s.replace("-", "").strip()[:8]))
    return np.asarray(out, dtype=np.int32)


def _lonlat_from_affine(f, L, W, *, pixel_center=True):
    """MintPy 격자 attrs(X_FIRST/Y_FIRST/X_STEP/Y_STEP)로 픽셀별 lon/lat [L,W] 생성."""
    try:
        x0 = float(_attr(f, "X_FIRST")); y0 = float(_attr(f, "Y_FIRST"))
        dx = float(_attr(f, "X_STEP")); dy = float(_attr(f, "Y_STEP"))
    except (TypeError, ValueError):
        return None, None
    off = 0.5 if pixel_center else 0.0
    lon = x0 + (np.arange(W) + off) * dx           # [W]
    lat = y0 + (np.arange(L) + off) * dy           # [L]
    return np.tile(lon[None, :], (L, 1)), np.tile(lat[:, None], (1, W))


def convert(
    timeseries_h5: str, coherence_h5: str, out: str,
    *, geometry_h5=None, ts_key="timeseries", date_key="date",
    lat_key="latitude", lon_key="longitude", coh_key="temporalCoherence",
    coh_thresh=0.7, bbox=None, max_points=0, unit="m", pixel_center=True,
) -> tuple[int, int]:
    with h5py.File(timeseries_h5, "r") as f:
        ts = np.asarray(f[ts_key][()], dtype=np.float64)       # [M,L,W]
        epochs = _dates_to_yyyymmdd(f[date_key][()])
        if ts.ndim != 3:
            raise ValueError(f"timeseries 는 [M,L,W] 여야 합니다: {ts.shape}")
        M, L, W = ts.shape
        # 좌표 결정: geometry 우선, 없으면 affine attrs
        if geometry_h5:
            with h5py.File(geometry_h5, "r") as g:
                lat = np.asarray(g[lat_key][()], dtype=np.float64)
                lon = np.asarray(g[lon_key][()], dtype=np.float64)
        else:
            lon, lat = _lonlat_from_affine(f, L, W, pixel_center=pixel_center)
            if lon is None:
                raise ValueError("좌표를 얻지 못했습니다. 지오코딩 attrs(X_FIRST 등)도 없고 "
                                 "--geometry 도 안 줬습니다.")
    if lat.shape != (L, W) or lon.shape != (L, W):
        raise ValueError(f"좌표 형상 {lat.shape}/{lon.shape} 가 [{L},{W}] 와 안 맞습니다.")

    with h5py.File(coherence_h5, "r") as f:
        coh = np.asarray(f[coh_key][()], dtype=np.float64)     # [L,W]
    if coh.shape != (L, W):
        raise ValueError(f"coherence 형상 {coh.shape} 가 [{L},{W}] 와 안 맞습니다.")

    mask = (coh >= coh_thresh) & np.isfinite(lat) & np.isfinite(lon) & np.isfinite(coh)
    mask &= np.isfinite(ts).all(axis=0)
    # MintPy 무효 픽셀은 보통 0 → 전 시점 0인 점 제외
    mask &= ~(ts == 0).all(axis=0)
    if bbox:
        mn_lon, mn_lat, mx_lon, mx_lat = bbox
        mask &= (lon >= mn_lon) & (lon <= mx_lon) & (lat >= mn_lat) & (lat <= mx_lat)

    n = int(mask.sum())
    if n < 2:
        raise ValueError(f"임계 통과 점 {n}개(coh≥{coh_thresh}, bbox={bbox}). 임계/영역/좌표 확인.")

    lonlat = np.column_stack([lon[mask], lat[mask]]).astype(np.float64)
    los = ts[:, mask].T
    coh_pts = coh[mask].astype(np.float32)
    if max_points and n > max_points:
        keep = np.argsort(-coh_pts)[:max_points]
        lonlat, los, coh_pts, n = lonlat[keep], los[keep], coh_pts[keep], max_points

    los_mm = (los * 1000.0 if unit == "m" else los).astype(np.float32)
    with h5py.File(out, "w") as f:
        f.create_dataset("pixel_lonlat", data=lonlat)
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=los_mm)
        f.create_dataset("coh", data=coh_pts)
        f.attrs["FILE_TYPE"] = "mintpy_raster_points"
        f.attrs["unit"] = "mm"
        f.attrs["coh_thresh"] = coh_thresh
        f.attrs["source_timeseries"] = timeseries_h5
    return n, M


def main() -> None:
    p = argparse.ArgumentParser(description="Track B/C (MintPy 지오코딩 래스터) → inframon Track H5")
    p.add_argument("--timeseries", required=True)
    p.add_argument("--coherence", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--geometry", default=None, help="레이더 좌표면 geometryRadar.h5 (lat/lon 배열)")
    p.add_argument("--ts-key", default="timeseries")
    p.add_argument("--date-key", default="date")
    p.add_argument("--lat-key", default="latitude")
    p.add_argument("--lon-key", default="longitude")
    p.add_argument("--coh-key", default="temporalCoherence")
    p.add_argument("--coh-thresh", type=float, default=0.7)
    p.add_argument("--max-points", type=int, default=0)
    p.add_argument("--unit", default="m", choices=["m", "mm"])
    p.add_argument("--corner", action="store_true", help="픽셀 모서리 좌표(기본은 중심)")
    p.add_argument("--bbox", nargs=4, type=float, default=None,
                   metavar=("MINLON", "MINLAT", "MAXLON", "MAXLAT"))
    a = p.parse_args()
    n, m = convert(a.timeseries, a.coherence, a.out, geometry_h5=a.geometry,
                   ts_key=a.ts_key, date_key=a.date_key, lat_key=a.lat_key, lon_key=a.lon_key,
                   coh_key=a.coh_key, coh_thresh=a.coh_thresh, bbox=a.bbox,
                   max_points=a.max_points, unit=a.unit, pixel_center=not a.corner)
    print(f"변환 완료: {a.out}  (점 {n} · 시점 {m})")
    print(f"다음: python -m inframon --import-track-h5 {a.out} --out data/project.h5")


if __name__ == "__main__":
    main()
