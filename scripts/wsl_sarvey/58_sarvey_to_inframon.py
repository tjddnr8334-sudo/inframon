#!/usr/bin/env python3
"""SARvey p2 시계열 → inframon Track H5 (입사각·heading 포함 → asc+desc 연직분해 지원).

기존 50/52 어댑터와 달리 **incidence(입사각)·heading** 을 함께 기록해, 두 궤도(asc/desc)
Track H5 로 `insar/fusion.fuse_asc_desc` 연직·종축 분해가 가능하게 한다.

실 SARvey 산출(p2_*_ts.h5)은 `coord_xy`·`phase` 레이아웃이라 50_ 어댑터
(displacement/latitude/longitude)로는 열리지 않는다. `--engine sarvey` 는 이 파일의
`convert()` 를 쓴다.

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


def convert(sarvey_out: str, out: str, *, miaplpy_inputs: str | None = None,
            ts: str = "p2_coh80_ts.h5", utm_epsg: int = 32652,
            bbox=None, max_points: int = 0) -> tuple[int, int]:
    """SARvey outputs 폴더 → inframon Track H5. (N, M) 을 돌려준다.

    `bbox=(min_lon, min_lat, max_lon, max_lat)` 를 주면 그 안의 점만 남긴다 — 광역 PSI
    필드를 그대로 넘기면 하류(PINN·CRI)가 교량이 아니라 주변 지반을 본다(50/52/54/56
    어댑터와 같은 규약). `max_points` 는 coherence 상위 N 점만.
    """
    src_dir = sarvey_out
    inputs = miaplpy_inputs or _guess_miaplpy_inputs(src_dir)

    with h5py.File(os.path.join(src_dir, ts), "r") as f:
        coord_xy = f["coord_xy"][()]
        phase = f["phase"][()].astype(np.float64)
    with h5py.File(os.path.join(src_dir, "coordinates_utm.h5"), "r") as f:
        cu = f["coord_utm"][()]
    with h5py.File(os.path.join(src_dir, "temporal_coherence.h5"), "r") as f:
        tc = f["temp_coh"][()]
        attrs = {k: str(v) for k, v in f.attrs.items()}
    H, W = tc.shape
    n_points, n_dates = phase.shape
    wl = float(attrs.get("RADAR_WAVELENGTH", attrs.get("WAVELENGTH", 0.05546576)))
    heading = float(attrs.get("HEADING", attrs.get("ORBIT_HEADING", "nan")))

    # 날짜
    dates = None
    if inputs:
        try:
            with h5py.File(os.path.join(inputs, "slcStack.h5"), "r") as f:
                dates = [d.decode() if isinstance(d, bytes) else str(d) for d in f["date"][()]]
        except Exception:  # noqa: BLE001
            pass
    if not dates or len(dates) != n_dates:
        dates = [f"{i:08d}" for i in range(n_dates)]

    # 픽셀 인덱스
    a0, a1 = coord_xy[:, 0], coord_xy[:, 1]
    if a0.max() < H and a1.max() < W:
        rows, cols = a0, a1
    elif a0.max() < W and a1.max() < H:
        rows, cols = a1, a0
    else:
        rows, cols = np.clip(a0, 0, H - 1), np.clip(a1, 0, W - 1)
    rows = np.clip(rows, 0, H - 1).astype(int)
    cols = np.clip(cols, 0, W - 1).astype(int)

    ux, uy = cu[0, rows, cols], cu[1, rows, cols]
    coh = tc[rows, cols].astype(np.float32)

    # 입사각·고도(geometryRadar) — 점별 샘플
    incidence = None
    height = None                                # [N] 점별 고도(m) — DEM z 연계·구조분리·대기보정
    try:
        with h5py.File(os.path.join(inputs, "geometryRadar.h5"), "r") as f:
            if "incidenceAngle" in f:
                inc_grid = f["incidenceAngle"][()]
                if inc_grid.shape == (H, W):
                    incidence = inc_grid[rows, cols].astype(np.float32)
            if "height" in f:                    # MintPy/MiaplPy 지오메트리 고도(DEM in radar)
                h_grid = f["height"][()]
                if h_grid.shape == (H, W):
                    height = h_grid[rows, cols].astype(np.float32)
            if not math.isfinite(heading) and "HEADING" in f.attrs:
                heading = float(f.attrs["HEADING"])
    except Exception as e:  # noqa: BLE001
        print("geometryRadar 입사각/고도 읽기 경고:", e)

    # SARvey 점별 DEM error(잔차 지형, 상부구조 반영). 두 곳에 쓴다:
    #   ① 절대고도 = 기준고도 + dem_error (z 정밀화)
    #   ② dem_error 자체를 별도 저장 → inframon 의 지오로케이션 쉬프트 보정
    #      (--insar-geoloc-correct)이 쓴다. 상부구조가 밀린 위치를 δh/tanθ 로 되돌린다.
    dem_error_out = None
    try:
        with h5py.File(os.path.join(src_dir, ts), "r") as f:
            for key in ("dem_error", "demErr", "residual_height"):
                if key in f:
                    de = np.asarray(f[key][()], dtype=np.float32).ravel()
                    n_expect = (height.shape[0] if height is not None else n_points)
                    if de.shape[0] == n_expect:
                        dem_error_out = de
                        if height is not None:
                            height = (height + de).astype(np.float32)
                        print(f"  dem_error({key}) 반영 → 절대고도 정밀화 + 별도 저장")
                    break
    except Exception as e:  # noqa: BLE001
        print("dem_error 읽기 경고(무시):", e)

    # heading 은 MintPy/ISCE 에서 라디안으로 오는 경우가 많다(예: asc -0.23, desc -2.91).
    # fuse_asc_desc 는 도(°)를 기대하므로 여기서 통일해 저장한다.
    if math.isfinite(heading) and abs(heading) < 7.0:
        heading = math.degrees(heading)

    from pyproj import Transformer
    lon, lat = Transformer.from_crs(f"EPSG:{utm_epsg}", "EPSG:4326",
                                    always_xy=True).transform(ux, uy)
    lon = np.asarray(lon)
    lat = np.asarray(lat)
    disp_mm = (-wl / (4 * np.pi) * phase * 1000.0).astype(np.float32)
    epochs = np.array([int(d) if str(d).isdigit() else i for i, d in enumerate(dates)],
                      dtype=np.int32)

    # ── 교량 범위로 자르기(50/52/54/56 과 같은 규약) ──
    if bbox:
        mn_lon, mn_lat, mx_lon, mx_lat = bbox
        keep = (lon >= mn_lon) & (lon <= mx_lon) & (lat >= mn_lat) & (lat <= mx_lat)
        if not keep.any():
            raise ValueError(f"bbox {bbox} 안에 점이 0개입니다 — 좌표·산출물 범위를 확인하세요.")
        lon, lat, disp_mm, coh = lon[keep], lat[keep], disp_mm[keep], coh[keep]
        incidence = incidence[keep] if incidence is not None else None
        height = height[keep] if height is not None else None
        dem_error_out = dem_error_out[keep] if dem_error_out is not None else None
        n_points = int(keep.sum())
    if max_points and n_points > max_points:
        sel = np.argsort(-coh)[:max_points]
        lon, lat, disp_mm, coh = lon[sel], lat[sel], disp_mm[sel], coh[sel]
        incidence = incidence[sel] if incidence is not None else None
        height = height[sel] if height is not None else None
        dem_error_out = dem_error_out[sel] if dem_error_out is not None else None
        n_points = max_points

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with h5py.File(out, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack([lon, lat]).astype(np.float64))
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=disp_mm)
        f.create_dataset("coh", data=coh)
        if incidence is not None:
            f.create_dataset("incidenceAngle", data=incidence)   # read_track_h5 가 인식
        if height is not None:
            f.create_dataset("height", data=height)              # read_track_h5 가 z 로 사용
        if dem_error_out is not None:
            f.create_dataset("dem_error", data=dem_error_out)    # 지오로케이션 쉬프트 보정용
        if math.isfinite(heading):
            f.attrs["HEADING"] = heading                          # asc/desc 분해용
        # preflight 의 래핑 판정(λ/4)이 이 값을 쓴다 — 엔진별 λ 차이를 흡수한다.
        f.attrs["RADAR_WAVELENGTH"] = wl
        f.attrs["source"] = "SARvey p2 (incidence/height/heading 포함)"
        if bbox:
            f.attrs["bbox"] = np.asarray(bbox, dtype=np.float64)
    hs = (f"{float(np.nanmin(height)):.0f}~{float(np.nanmax(height)):.0f}m"
          if height is not None else "X")
    print(f"wrote {out}: N={n_points} M={n_dates} "
          f"incidence={'O' if incidence is not None else 'X'} "
          f"height={hs} heading={heading if math.isfinite(heading) else 'X'}")
    return int(n_points), int(n_dates)


def _guess_miaplpy_inputs(sarvey_out: str) -> str | None:
    """<run>/sarvey/outputs → <run>/miaplpy/inputs 를 관례로 찾는다(없으면 None)."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(sarvey_out)))
    cand = os.path.join(base, "miaplpy", "inputs")
    return cand if os.path.isdir(cand) else None


def main() -> None:
    p = argparse.ArgumentParser(description="SARvey → inframon Track H5 (incidence 포함)")
    p.add_argument("--sarvey-out", required=True, help="SARvey outputs 폴더")
    p.add_argument("--miaplpy-inputs", default=None,
                   help="MiaplPy inputs (geometryRadar/slcStack). 생략 시 관례 경로 추정")
    p.add_argument("--out", required=True, help="출력 Track H5")
    p.add_argument("--utm-epsg", type=int, default=32652, help="coord_utm 의 EPSG (기본 UTM52N)")
    p.add_argument("--ts", default="p2_coh80_ts.h5", help="시계열 파일명")
    p.add_argument("--bbox", nargs=4, type=float, default=None,
                   metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
                   help="교량 범위로 자르기 — 광역 PSI 필드를 그대로 넘기지 않는다")
    p.add_argument("--max-points", type=int, default=0, help="coherence 상위 N 점만")
    a = p.parse_args()
    convert(a.sarvey_out, a.out, miaplpy_inputs=a.miaplpy_inputs, ts=a.ts,
            utm_epsg=a.utm_epsg, bbox=a.bbox, max_points=a.max_points)


if __name__ == "__main__":
    main()
