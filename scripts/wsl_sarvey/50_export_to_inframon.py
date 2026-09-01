#!/usr/bin/env python3
"""SARvey 결과 HDF5 → inframon Track H5 변환 (F → G 다리).

SARvey/MiaplPy 시계열 산출(점별 변위·좌표·날짜·coherence)을 inframon 이 읽는 Track
H5 스키마로 변환한다:
  pixel_lonlat [N,2] (lon,lat) · epochs [M] YYYYMMDD int · los_mm [N,M] · coh [N]

SARvey 버전마다 데이터셋 이름이 다르므로 --*-key 로 덮어쓸 수 있다. 기본값은
MintPy/MiaplPy 계열 관례(displacement[m], latitude, longitude, date, temporalCoherence).

사용:
  python3 50_export_to_inframon.py --sarvey-h5 outputs/p2_coh80_ts.h5 --out track_jeongjagyo.h5
그 뒤 (Windows/inframon):
  python -m inframon --import-track-h5 track_jeongjagyo.h5 --out data/project.h5
"""

from __future__ import annotations

import argparse

import h5py
import numpy as np


def _to_yyyymmdd(raw: np.ndarray) -> np.ndarray:
    """날짜 배열을 YYYYMMDD int32 로 표준화 (bytes/str/'YYYY-MM-DD' 허용)."""
    out = []
    for v in np.asarray(raw).ravel():
        s = v.decode() if isinstance(v, bytes) else str(v)
        s = s.replace("-", "").replace("/", "").strip()[:8]
        out.append(int(s))
    return np.asarray(out, dtype=np.int32)


def convert(
    sarvey_h5: str,
    out: str,
    *,
    disp_key: str = "displacement",
    lat_key: str = "latitude",
    lon_key: str = "longitude",
    date_key: str = "date",
    coh_key: str = "temporalCoherence",
    unit: str = "m",
    bbox=None,
    max_points: int = 0,
) -> tuple[int, int]:
    """SARvey 결과 → inframon Track H5.

    `bbox=(min_lon, min_lat, max_lon, max_lat)` 를 주면 그 안의 점만 남긴다 —
    광역 PSI 필드(수만 점) 그대로 넘기면 하류가 교량이 아니라 주변 지반을 본다.
    `max_points` 는 coherence 상위 N 점만(52/54/56 어댑터와 같은 규약).
    """
    with h5py.File(sarvey_h5, "r") as f:
        for k in (disp_key, lat_key, lon_key, date_key):
            if k not in f:
                raise KeyError(f"'{k}' 데이터셋이 {sarvey_h5} 에 없습니다. --*-key 로 지정하세요. "
                               f"(가용: {list(f.keys())})")
        disp = np.asarray(f[disp_key][()], dtype=np.float64)
        lat = np.asarray(f[lat_key][()], dtype=np.float64).ravel()
        lon = np.asarray(f[lon_key][()], dtype=np.float64).ravel()
        epochs = _to_yyyymmdd(f[date_key][()])
        coh = (np.asarray(f[coh_key][()], dtype=np.float32).ravel()
               if coh_key in f else np.ones(lat.shape[0], dtype=np.float32))

    n_points, n_dates = lat.shape[0], epochs.shape[0]
    # 변위를 [N, M] 으로 정렬 (SARvey 가 [M,N] 으로 줄 수도 있음)
    if disp.shape == (n_dates, n_points):
        disp = disp.T
    elif disp.shape != (n_points, n_dates):
        raise ValueError(f"displacement {disp.shape} 가 점수 {n_points}·시점 {n_dates} 와 안 맞습니다.")

    los_mm = (disp * 1000.0 if unit == "m" else disp).astype(np.float32)
    lonlat = np.column_stack([lon, lat]).astype(np.float64)

    # ── 교량 범위로 자르기(52/54/56 어댑터와 같은 규약) ──
    if bbox:
        mn_lon, mn_lat, mx_lon, mx_lat = bbox
        keep = ((lon >= mn_lon) & (lon <= mx_lon) & (lat >= mn_lat) & (lat <= mx_lat))
        if not keep.any():
            raise ValueError(f"bbox {bbox} 안에 점이 0개입니다 — 좌표·산출물 범위를 확인하세요.")
        lonlat, los_mm, coh = lonlat[keep], los_mm[keep], coh[keep]
        n_points = int(keep.sum())
    if max_points and n_points > max_points:
        sel = np.argsort(-coh)[:max_points]
        lonlat, los_mm, coh = lonlat[sel], los_mm[sel], coh[sel]
        n_points = max_points

    with h5py.File(out, "w") as f:
        f.create_dataset("pixel_lonlat", data=lonlat)
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=los_mm)
        f.create_dataset("coh", data=coh)
        f.attrs["FILE_TYPE"] = "sarvey_export"
        f.attrs["source_h5"] = sarvey_h5
        f.attrs["unit"] = "mm"
        if bbox:
            f.attrs["bbox"] = np.asarray(bbox, dtype=np.float64)
    return n_points, n_dates


def main() -> None:
    p = argparse.ArgumentParser(description="SARvey 결과 → inframon Track H5")
    p.add_argument("--sarvey-h5", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--disp-key", default="displacement")
    p.add_argument("--lat-key", default="latitude")
    p.add_argument("--lon-key", default="longitude")
    p.add_argument("--date-key", default="date")
    p.add_argument("--coh-key", default="temporalCoherence")
    p.add_argument("--unit", default="m", choices=["m", "mm"])
    p.add_argument("--bbox", nargs=4, type=float, default=None,
                   metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
                   help="교량 범위로 자르기 — 광역 PSI 필드를 그대로 넘기지 않는다")
    p.add_argument("--max-points", type=int, default=0, help="coherence 상위 N 점만")
    a = p.parse_args()
    n, m = convert(a.sarvey_h5, a.out, disp_key=a.disp_key, lat_key=a.lat_key,
                   lon_key=a.lon_key, date_key=a.date_key, coh_key=a.coh_key, unit=a.unit,
                   bbox=a.bbox, max_points=a.max_points)
    print(f"변환 완료: {a.out}  (점 {n} · 시점 {m})")
    print(f"다음(Windows): python -m inframon --import-track-h5 {a.out} --out data/project.h5")


if __name__ == "__main__":
    main()
