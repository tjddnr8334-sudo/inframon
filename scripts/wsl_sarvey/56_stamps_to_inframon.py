#!/usr/bin/env python3
"""Track A (StaMPS PS-InSAR) MATLAB .mat → inframon Track H5.

StaMPS `ps_plot('v-do','ts')` 등이 내보내는 .mat 에는 보통:
  - lonlat : [N,2] (lon,lat)        점 좌표
  - ph_mm  : [N,M] (mm)             LOS 변위 시계열 (PS 점별)
  - day    : [M]   MATLAB datenum   취득일
  - coh_ps : [N]   (선택) PS 품질/coherence
가 들어있다. 이를 inframon Track H5(pixel_lonlat/epochs/los_mm/coh)로 변환한다.

.mat 버전: v5 이하는 scipy.io.loadmat, v7.3(HDF5)은 h5py 로 자동 처리.
변수명이 다르면 --*-key 로 지정. 날짜는 datenum/YYYYMMDD 자동 판별.

사용:
  python3 56_stamps_to_inframon.py --mat ps_plot_ts_v-do.mat --out track_a.h5 \
    --bbox 127.10058 37.35939 127.12098 37.37802
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

import numpy as np


def _load_mat(path: str) -> dict:
    """v5/v7.3 .mat 모두 dict[str, ndarray] 로 로드."""
    try:
        from scipy.io import loadmat
        m = loadmat(path, squeeze_me=True, struct_as_record=False)
        return {k: v for k, v in m.items() if not k.startswith("__")}
    except NotImplementedError:
        # v7.3 = HDF5
        import h5py
        out = {}
        with h5py.File(path, "r") as f:
            for k in f.keys():
                arr = np.asarray(f[k][()])
                # MATLAB(h5py)은 열우선 → 전치
                out[k] = arr.T if arr.ndim >= 2 else arr.squeeze()
        return out


def _days_to_yyyymmdd(day: np.ndarray) -> np.ndarray:
    day = np.asarray(day, dtype=np.float64).ravel()
    mx = np.nanmax(day)
    if mx > 1e7:                          # 이미 YYYYMMDD
        return day.astype(np.int64).astype(np.int32)
    if mx > 1e5:                          # MATLAB datenum
        out = []
        for dn in day:
            d = datetime.fromordinal(int(dn) - 366) + timedelta(days=float(dn) % 1)
            out.append(int(d.strftime("%Y%m%d")))
        return np.asarray(out, dtype=np.int32)
    raise ValueError(f"day 값이 datenum/YYYYMMDD 로 안 보입니다 (max={mx}).")


def _orient(disp: np.ndarray, n: int, m: int) -> np.ndarray:
    """변위를 [N,M] 으로 맞춘다."""
    if disp.shape == (n, m):
        return disp
    if disp.shape == (m, n):
        return disp.T
    raise ValueError(f"ph_mm {disp.shape} 가 점 {n}·시점 {m} 와 안 맞습니다.")


def convert(
    mat_path: str, out: str, *, lonlat_key="lonlat", disp_key="ph_mm",
    day_key="day", coh_key="coh_ps", coh_thresh=0.0, bbox=None, max_points=0, unit="mm",
) -> tuple[int, int]:
    m = _load_mat(mat_path)
    for k in (lonlat_key, disp_key, day_key):
        if k not in m:
            raise KeyError(f"'{k}' 가 .mat 에 없습니다. --*-key 로 지정. (가용: {sorted(m)})")
    lonlat = np.asarray(m[lonlat_key], dtype=np.float64)
    if lonlat.ndim != 2 or 2 not in lonlat.shape:
        raise ValueError(f"lonlat 는 [N,2] 여야: {lonlat.shape}")
    if lonlat.shape[1] != 2:
        lonlat = lonlat.T                       # [2,N] → [N,2]
    n = lonlat.shape[0]
    epochs = _days_to_yyyymmdd(m[day_key])
    mdates = epochs.shape[0]
    disp = _orient(np.asarray(m[disp_key], dtype=np.float64), n, mdates)
    coh = (np.asarray(m[coh_key], dtype=np.float64).ravel()
           if coh_key in m else np.ones(n)).astype(np.float32)
    if coh.shape[0] != n:
        coh = np.ones(n, dtype=np.float32)

    mask = np.isfinite(lonlat).all(axis=1) & np.isfinite(disp).all(axis=1)
    mask &= coh >= coh_thresh
    if bbox:
        mn_lon, mn_lat, mx_lon, mx_lat = bbox
        mask &= (lonlat[:, 0] >= mn_lon) & (lonlat[:, 0] <= mx_lon) \
            & (lonlat[:, 1] >= mn_lat) & (lonlat[:, 1] <= mx_lat)

    k = int(mask.sum())
    if k < 2:
        raise ValueError(f"통과 점 {k}개(coh≥{coh_thresh}, bbox={bbox}). 임계/영역/키 확인.")

    lonlat, disp, coh = lonlat[mask], disp[mask], coh[mask]
    if max_points and k > max_points:
        keep = np.argsort(-coh)[:max_points]
        lonlat, disp, coh, k = lonlat[keep], disp[keep], coh[keep], max_points

    los_mm = (disp * 1000.0 if unit == "m" else disp).astype(np.float32)
    import h5py
    with h5py.File(out, "w") as f:
        f.create_dataset("pixel_lonlat", data=lonlat.astype(np.float64))
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=los_mm)
        f.create_dataset("coh", data=coh.astype(np.float32))
        f.attrs["FILE_TYPE"] = "stamps_track_a"
        f.attrs["unit"] = "mm"
        f.attrs["source_mat"] = mat_path
    return k, mdates


def main() -> None:
    p = argparse.ArgumentParser(description="Track A (StaMPS .mat) → inframon Track H5")
    p.add_argument("--mat", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--lonlat-key", default="lonlat")
    p.add_argument("--disp-key", default="ph_mm")
    p.add_argument("--day-key", default="day")
    p.add_argument("--coh-key", default="coh_ps")
    p.add_argument("--coh-thresh", type=float, default=0.0)
    p.add_argument("--max-points", type=int, default=0)
    p.add_argument("--unit", default="mm", choices=["m", "mm"])
    p.add_argument("--bbox", nargs=4, type=float, default=None,
                   metavar=("MINLON", "MINLAT", "MAXLON", "MAXLAT"))
    a = p.parse_args()
    n, mm = convert(a.mat, a.out, lonlat_key=a.lonlat_key, disp_key=a.disp_key,
                    day_key=a.day_key, coh_key=a.coh_key, coh_thresh=a.coh_thresh,
                    bbox=a.bbox, max_points=a.max_points, unit=a.unit)
    print(f"변환 완료: {a.out}  (점 {n} · 시점 {mm})")
    print(f"다음: python -m inframon --import-track-h5 {a.out} --out data/project.h5")


if __name__ == "__main__":
    main()
