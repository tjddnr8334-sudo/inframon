#!/usr/bin/env python3
"""SARvey 실행 결과(p1 · p2 점)를 교량 데크 폴리곤 안에서 세어 두 실행(위상연결 끔/켬)을 비교한다.

    python sarvey_pl_report.py --runs pl_off=/path/pl_off pl_on=/path/pl_on \
        --geometry /path/geometryRadar.h5 --bridge bridge_target.json [--buffer-m 5] [--grid-m 20]

각 실행 폴더의 outputs/p1_ts.h5, outputs/p2_coh*_ts.h5 에서 coord_xy(행,열)를 읽고,
geometryRadar.h5 의 latitude/longitude 로 경위도로 바꿔 OSM 교량 폴리곤(또는 축선 버퍼) 안의 점을 센다.
데크를 따라 grid-m 간격 격자를 놓고 "점이 하나라도 있는 격자 비율" 도 낸다 — 등간격 목표의 척도.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys

import h5py
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import transform


def deck_polygon(bridge: dict, buffer_m: float):
    pts = [(lon, lat) for lat, lon in bridge["geometry"]]
    lat0 = sum(p[1] for p in pts) / len(pts)
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    to_m = lambda x, y: ((x - pts[0][0]) * m_per_deg_lon, (y - pts[0][1]) * m_per_deg_lat)  # noqa: E731
    if len(pts) >= 4 and pts[0] == pts[-1]:
        geom_m = transform(to_m, Polygon(pts))
        axis_m = None
    else:
        axis_m = transform(to_m, LineString(pts))
        geom_m = axis_m.buffer(buffer_m + 6.0)  # 축선이면 데크 반폭(≈6 m)+여유
    if buffer_m and geom_m.geom_type == "Polygon" and axis_m is None:
        geom_m = geom_m.buffer(buffer_m)
    return geom_m, to_m, axis_m


def load_points(run_dir: str, geometry: str):
    with h5py.File(geometry) as g:
        lat, lon = g["latitude"][:], g["longitude"][:]
    out = {}
    for f in sorted(glob.glob(os.path.join(run_dir, "outputs", "p*_ts.h5"))):
        name = os.path.basename(f).replace("_ts.h5", "")
        with h5py.File(f) as h:
            xy = h["coord_xy"][:].astype(int)
        r, c = xy[:, 0], xy[:, 1]
        out[name] = (lon[r, c], lat[r, c])
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", nargs="+", required=True, help="name=dir ...")
    ap.add_argument("--geometry", required=True)
    ap.add_argument("--bridge", required=True)
    ap.add_argument("--buffer-m", type=float, default=5.0)
    ap.add_argument("--grid-m", type=float, default=20.0)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    bridge = json.load(open(a.bridge, encoding="utf-8"))
    deck_m, to_m, axis_m = deck_polygon(bridge, a.buffer_m)
    # 격자: 데크 폴리곤의 긴 축을 따라 grid-m 간격 셀
    minx, miny, maxx, maxy = deck_m.bounds
    long_axis_x = (maxx - minx) >= (maxy - miny)
    n_cells = max(1, int(math.ceil(((maxx - minx) if long_axis_x else (maxy - miny)) / a.grid_m)))
    print(f"교량 {bridge.get('name')}  연장 {bridge.get('length_m')} m  데크 면적 {deck_m.area:.0f} m²  격자 {n_cells}칸 × {a.grid_m:.0f} m")
    report = {"bridge": bridge.get("name"), "grid_m": a.grid_m, "n_cells": n_cells, "runs": {}}
    header = f"{'run':8} {'layer':12} {'AOI 전체':>9} {'데크 안':>8} {'격자 채움':>9}"
    print(header)
    for spec in a.runs:
        name, d = spec.split("=", 1)
        pts = load_points(d, a.geometry)
        report["runs"][name] = {}
        for layer, (lon, lat) in pts.items():
            inside = []
            cells = set()
            for x, y in zip(lon, lat):
                p = Point(*to_m(float(x), float(y)))
                if deck_m.contains(p):
                    inside.append((p.x, p.y))
                    k = int(((p.x - minx) if long_axis_x else (p.y - miny)) // a.grid_m)
                    cells.add(min(k, n_cells - 1))
            fill = len(cells) / n_cells
            report["runs"][name][layer] = dict(total=int(len(lon)), deck=len(inside), grid_fill=round(fill, 3))
            print(f"{name:8} {layer:12} {len(lon):9d} {len(inside):8d} {fill:9.0%}")
    if a.json_out:
        json.dump(report, open(a.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("→", a.json_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
