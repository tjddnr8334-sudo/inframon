#!/usr/bin/env python3
"""Track D (MiaplPy/MintPy) 시계열 → inframon Track H5 변환.

MiaplPy phase-linking 산출(MintPy 형식)을 점 기반 inframon 계약으로 변환한다:
  - timeseries.h5      : 'timeseries' [M,L,W] (m), 'date' [M] (YYYYMMDD)
  - geometryRadar.h5   : 'latitude' [L,W], 'longitude' [L,W]
  - temporalCoherence.h5: 'temporalCoherence' [L,W]  (γ_t)

temporalCoherence ≥ 임계(기본 0.6, Track D 규약) + (선택)ROI bbox 안의 픽셀만 점으로 뽑아
Track H5(pixel_lonlat/epochs/los_mm/coh)로 쓴다. 그 뒤 inframon --import-track-h5 로 인제스트.

사용:
  python3 52_miaplpy_to_inframon.py \
    --timeseries miaplpy/network_delaunay/timeseries.h5 \
    --geometry   miaplpy/inputs/geometryRadar.h5 \
    --coherence  miaplpy/network_delaunay/temporalCoherence.h5 \
    --out track_d_jeongjagyo.h5 --coh-thresh 0.6 \
    --bbox 127.10058 37.35939 127.12098 37.37802
"""

from __future__ import annotations

import argparse

import h5py
import numpy as np


def _dates_to_yyyymmdd(raw) -> np.ndarray:
    out = []
    for v in np.asarray(raw).ravel():
        s = v.decode() if isinstance(v, bytes) else str(v)
        out.append(int(s.replace("-", "").strip()[:8]))
    return np.asarray(out, dtype=np.int32)


def convert(
    timeseries_h5: str, geometry_h5: str, coherence_h5: str, out: str,
    *, ts_key="timeseries", date_key="date", lat_key="latitude", lon_key="longitude",
    coh_key="temporalCoherence", coh_thresh=0.6, bbox=None, max_points=0, unit="m",
) -> tuple[int, int]:
    with h5py.File(timeseries_h5, "r") as f:
        ts = np.asarray(f[ts_key][()], dtype=np.float64)        # [M,L,W]
        epochs = _dates_to_yyyymmdd(f[date_key][()])
    with h5py.File(geometry_h5, "r") as f:
        lat = np.asarray(f[lat_key][()], dtype=np.float64)      # [L,W]
        lon = np.asarray(f[lon_key][()], dtype=np.float64)
    with h5py.File(coherence_h5, "r") as f:
        coh = np.asarray(f[coh_key][()], dtype=np.float64)      # [L,W]

    if ts.ndim != 3:
        raise ValueError(f"timeseries 는 [M,L,W] 여야 합니다: {ts.shape}")
    M, L, W = ts.shape
    if lat.shape != (L, W) or lon.shape != (L, W) or coh.shape != (L, W):
        raise ValueError(f"geometry/coherence 형상이 [{L},{W}] 와 안 맞습니다: "
                         f"lat{lat.shape} lon{lon.shape} coh{coh.shape}")

    # 점 선택: coherence 임계 + 유한 + (선택)ROI bbox + 시계열 유효
    mask = (coh >= coh_thresh) & np.isfinite(lat) & np.isfinite(lon) & np.isfinite(coh)
    mask &= np.isfinite(ts).all(axis=0)
    if bbox:
        mn_lon, mn_lat, mx_lon, mx_lat = bbox
        mask &= (lon >= mn_lon) & (lon <= mx_lon) & (lat >= mn_lat) & (lat <= mx_lat)

    n = int(mask.sum())
    if n < 2:
        raise ValueError(f"임계 통과 점이 {n}개뿐입니다(coh≥{coh_thresh}, bbox={bbox}). 임계/영역 확인.")

    lonlat = np.column_stack([lon[mask], lat[mask]]).astype(np.float64)   # [N,2]
    los = ts[:, mask].T                                                   # [N,M]
    coh_pts = coh[mask].astype(np.float32)

    # 너무 많으면 coherence 상위 max_points 만
    if max_points and n > max_points:
        keep = np.argsort(-coh_pts)[:max_points]
        lonlat, los, coh_pts = lonlat[keep], los[keep], coh_pts[keep]
        n = max_points

    los_mm = (los * 1000.0 if unit == "m" else los).astype(np.float32)

    with h5py.File(out, "w") as f:
        f.create_dataset("pixel_lonlat", data=lonlat)
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=los_mm)
        f.create_dataset("coh", data=coh_pts)
        f.attrs["FILE_TYPE"] = "miaplpy_track_d"
        f.attrs["unit"] = "mm"
        f.attrs["coh_thresh"] = coh_thresh
        f.attrs["source_timeseries"] = timeseries_h5
    return n, M


def main() -> None:
    p = argparse.ArgumentParser(description="Track D (MiaplPy/MintPy) → inframon Track H5")
    p.add_argument("--timeseries", required=True)
    p.add_argument("--geometry", required=True)
    p.add_argument("--coherence", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--ts-key", default="timeseries")
    p.add_argument("--date-key", default="date")
    p.add_argument("--lat-key", default="latitude")
    p.add_argument("--lon-key", default="longitude")
    p.add_argument("--coh-key", default="temporalCoherence")
    p.add_argument("--coh-thresh", type=float, default=0.6)
    p.add_argument("--max-points", type=int, default=0, help="0=무제한, >0이면 coherence 상위 N")
    p.add_argument("--unit", default="m", choices=["m", "mm"])
    p.add_argument("--bbox", nargs=4, type=float, default=None,
                   metavar=("MINLON", "MINLAT", "MAXLON", "MAXLAT"))
    a = p.parse_args()
    n, m = convert(a.timeseries, a.geometry, a.coherence, a.out,
                   ts_key=a.ts_key, date_key=a.date_key, lat_key=a.lat_key, lon_key=a.lon_key,
                   coh_key=a.coh_key, coh_thresh=a.coh_thresh, bbox=a.bbox,
                   max_points=a.max_points, unit=a.unit)
    print(f"변환 완료: {a.out}  (점 {n} · 시점 {m})")
    print(f"다음: python -m inframon --import-track-h5 {a.out} --out data/project.h5")


if __name__ == "__main__":
    main()
