#!/usr/bin/env python3
"""SARvey p2 시계열 → inframon Track H5 (입사각·heading 포함 → asc+desc 연직분해 지원).

기존 50/52 어댑터와 달리 **incidence(입사각)·heading** 을 함께 기록해, 두 궤도(asc/desc)
Track H5 로 `insar/fusion.fuse_asc_desc` 연직·종축 분해가 가능하게 한다.

사용(WSL, sarvey/isce2_mintpy env):
  python3 58_sarvey_to_inframon.py --sarvey-out ~/isce_run/sarvey/outputs \
      --miaplpy-inputs ~/isce_run/miaplpy/inputs --out /mnt/d/프로그램/data/track_asc.h5 \
      --utm-epsg 32652
"""
from __future__ import annotations

import argparse
import math
import os

import h5py
import numpy as np


def main() -> None:
    p = argparse.ArgumentParser(description="SARvey → inframon Track H5 (incidence 포함)")
    p.add_argument("--sarvey-out", required=True, help="SARvey outputs 폴더")
    p.add_argument("--miaplpy-inputs", required=True, help="MiaplPy inputs (geometryRadar/slcStack)")
    p.add_argument("--out", required=True, help="출력 Track H5")
    p.add_argument("--utm-epsg", type=int, default=32652, help="coord_utm 의 EPSG (기본 UTM52N)")
    p.add_argument("--ts", default="p2_coh80_ts.h5", help="시계열 파일명")
    a = p.parse_args()
    O, I = a.sarvey_out, a.miaplpy_inputs

    with h5py.File(os.path.join(O, a.ts), "r") as f:
        coord_xy = f["coord_xy"][()]; phase = f["phase"][()].astype(np.float64)
    with h5py.File(os.path.join(O, "coordinates_utm.h5"), "r") as f:
        cu = f["coord_utm"][()]
    with h5py.File(os.path.join(O, "temporal_coherence.h5"), "r") as f:
        tc = f["temp_coh"][()]; attrs = {k: str(v) for k, v in f.attrs.items()}
    H, W = tc.shape; N, M = phase.shape
    wl = float(attrs.get("RADAR_WAVELENGTH", attrs.get("WAVELENGTH", 0.05546576)))
    heading = float(attrs.get("HEADING", attrs.get("ORBIT_HEADING", "nan")))

    # 날짜
    dates = None
    try:
        with h5py.File(os.path.join(I, "slcStack.h5"), "r") as f:
            dates = [d.decode() if isinstance(d, bytes) else str(d) for d in f["date"][()]]
    except Exception:  # noqa: BLE001
        pass
    if not dates or len(dates) != M:
        dates = [f"{i:08d}" for i in range(M)]

    # 픽셀 인덱스
    a0, a1 = coord_xy[:, 0], coord_xy[:, 1]
    if a0.max() < H and a1.max() < W:
        rows, cols = a0, a1
    elif a0.max() < W and a1.max() < H:
        rows, cols = a1, a0
    else:
        rows, cols = np.clip(a0, 0, H - 1), np.clip(a1, 0, W - 1)
    rows = np.clip(rows, 0, H - 1).astype(int); cols = np.clip(cols, 0, W - 1).astype(int)

    ux, uy = cu[0, rows, cols], cu[1, rows, cols]
    coh = tc[rows, cols].astype(np.float32)

    # 입사각(geometryRadar) — 점별 샘플
    incidence = None
    try:
        with h5py.File(os.path.join(I, "geometryRadar.h5"), "r") as f:
            if "incidenceAngle" in f:
                inc_grid = f["incidenceAngle"][()]
                if inc_grid.shape == (H, W):
                    incidence = inc_grid[rows, cols].astype(np.float32)
            if not math.isfinite(heading) and "HEADING" in f.attrs:
                heading = float(f.attrs["HEADING"])
    except Exception as e:  # noqa: BLE001
        print("geometryRadar 입사각 읽기 경고:", e)

    # heading 은 MintPy/ISCE 에서 라디안으로 오는 경우가 많다(예: asc -0.23, desc -2.91).
    # fuse_asc_desc 는 도(°)를 기대하므로 여기서 통일해 저장한다.
    if math.isfinite(heading) and abs(heading) < 7.0:
        heading = math.degrees(heading)

    from pyproj import Transformer
    lon, lat = Transformer.from_crs(f"EPSG:{a.utm_epsg}", "EPSG:4326",
                                    always_xy=True).transform(ux, uy)
    disp_mm = (-wl / (4 * np.pi) * phase * 1000.0).astype(np.float32)
    epochs = np.array([int(d) if str(d).isdigit() else i for i, d in enumerate(dates)], dtype=np.int32)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with h5py.File(a.out, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack([lon, lat]).astype(np.float64))
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=disp_mm)
        f.create_dataset("coh", data=coh)
        if incidence is not None:
            f.create_dataset("incidenceAngle", data=incidence)   # read_track_h5 가 인식
        if math.isfinite(heading):
            f.attrs["HEADING"] = heading                          # asc/desc 분해용
        f.attrs["source"] = "SARvey p2 (incidence/heading 포함)"
    print(f"wrote {a.out}: N={N} M={M} incidence={'O' if incidence is not None else 'X'} "
          f"heading={heading if math.isfinite(heading) else 'X'}")


if __name__ == "__main__":
    main()
