#!/usr/bin/env python3
"""지역 교량에서 실제로 잡히는 점 수 — 정합된 스택 하나를 교량 묶음 구역으로 나눠 MiaplPy·SARvey 를 돌린다.

스택 전체(강릉 asc54: 4190 × 24728 화소)를 한 번에 위상연결하면 내곡교 구역의 약 300배라 너무 무겁다.
그래서 연장 기준 이상 교량만 골라 가까운 것끼리 한 구역(최대 --tile-deg)으로 묶고, 구역마다
`region_points.sh` 가 MiaplPy(load → phase_linking) → SARvey 를 돈다. 끝나면 `report` 가 교량마다
데크 안 점 수를 센다(세는 법은 sarvey_pl_report.py 와 같다 — 시점·종점 축선 + 반폭 6 m + 여유 5 m).

    python region_points.py plan   --bridges data/gangneung/bridges.csv --min-len 100 --out tiles.json
    python region_points.py report --tiles tiles.json --root ~/gangwon/out/gangneung_points --out points.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def plan(a) -> int:
    rows = list(csv.DictReader(open(a.bridges, encoding="utf-8-sig")))
    sel = []
    for r in rows:
        length = _f(r.get("length_m"))
        if length is None or length < a.min_len:
            continue
        s = (_f(r.get("교량시작점위도")) or _f(r["lat"]), _f(r.get("교량시작점경도")) or _f(r["lon"]))
        e = (_f(r.get("교량종료점위도")) or s[0], _f(r.get("교량종료점경도")) or s[1])
        sel.append({"name": r["교량명"].strip(), "sigungu": r.get("시군구명", ""), "length_m": length,
                    "facility_class": r.get("시설물종별등급구분", ""), "lat": _f(r["lat"]), "lon": _f(r["lon"]),
                    "geometry": [list(s), list(e)], "asc_bursts": r.get("asc_bursts", "")})
    inner = a.tile_deg - 2 * a.buffer_deg
    tiles: list[dict] = []
    for b in sorted(sel, key=lambda b: (b["lat"], b["lon"])):
        la = [p[0] for p in b["geometry"]]
        lo = [p[1] for p in b["geometry"]]
        for t in tiles:
            s, n = min(t["s"], *la), max(t["n"], *la)
            w, e = min(t["w"], *lo), max(t["e"], *lo)
            if n - s <= inner and e - w <= inner:
                t.update(s=s, n=n, w=w, e=e)
                t["bridges"].append(b)
                break
        else:
            tiles.append({"s": min(la), "n": max(la), "w": min(lo), "e": max(lo), "bridges": [b]})
    out = []
    for i, t in enumerate(tiles, 1):
        bb = a.buffer_deg
        out.append({"id": f"t{i:02d}", "n_bridges": len(t["bridges"]),
                    "snwe": [round(t["s"] - bb, 5), round(t["n"] + bb, 5), round(t["w"] - bb, 5), round(t["e"] + bb, 5)],
                    "bridges": t["bridges"]})
    json.dump({"min_len": a.min_len, "tile_deg": a.tile_deg, "buffer_deg": a.buffer_deg,
               "n_bridges": len(sel), "tiles": out}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"교량 {len(sel)}개 → 구역 {len(out)}개 (구역당 교량 {sorted(t['n_bridges'] for t in out)}) → {a.out}")
    return 0


def _deck(bridge: dict, buffer_m: float):
    from shapely.geometry import LineString, Point
    pts = [(lon, lat) for lat, lon in bridge["geometry"]]
    lat0 = sum(p[1] for p in pts) / len(pts)
    mx, my = 111_320.0 * math.cos(math.radians(lat0)), 111_320.0
    to_m = lambda x, y: ((x - pts[0][0]) * mx, (y - pts[0][1]) * my)  # noqa: E731
    p0, p1 = to_m(*pts[0]), to_m(*pts[-1])
    geom = (LineString([p0, p1]) if p0 != p1 else Point(p0)).buffer(buffer_m + 6.0)
    return geom, to_m


def report(a) -> int:
    import glob

    import h5py
    import numpy as np
    from shapely.geometry import Point
    from shapely.prepared import prep

    plan_ = json.load(open(a.tiles, encoding="utf-8"))
    rows, tiles_done = [], 0
    for t in plan_["tiles"]:
        d = os.path.join(a.root, t["id"])
        geo = os.path.join(d, "miaplpy", "inputs", "geometryRadar.h5")
        ts_files = sorted(glob.glob(os.path.join(d, "sarvey", "outputs", "p*_ts.h5")))
        state = "done" if os.path.exists(os.path.join(d, "DONE")) else ("failed" if os.path.exists(os.path.join(d, "FAILED")) else "pending")
        layers = {}
        if ts_files and os.path.exists(geo):
            with h5py.File(geo) as g:
                lat, lon = g["latitude"][:], g["longitude"][:]
            for f in ts_files:
                with h5py.File(f) as h:
                    xy = h["coord_xy"][:].astype(int)
                layers[os.path.basename(f).replace("_ts.h5", "")] = (lon[xy[:, 0], xy[:, 1]], lat[xy[:, 0], xy[:, 1]])
            tiles_done += state == "done"
        for b in t["bridges"]:
            # 공공데이터에 시점=종점(한 점)인 교량이 많다 — 그러면 데크가 반경 11 m 원뿐이라 점 수가 과소다
            row = {"tile": t["id"], "state": state, "name": b["name"], "length_m": b["length_m"],
                   "facility_class": b["facility_class"], "lat": b["lat"], "lon": b["lon"],
                   "axis_known": b["geometry"][0] != b["geometry"][-1]}
            if layers:
                geom, to_m = _deck(b, a.buffer_m)
                pg = prep(geom)
                n_cells = max(1, math.ceil(b["length_m"] / a.grid_m))
                x0, y0 = to_m(*b["geometry"][0][::-1])
                x1, y1 = to_m(*b["geometry"][-1][::-1])
                ux, uy = (x1 - x0, y1 - y0)
                L = math.hypot(ux, uy) or 1.0
                for name, (lo, la) in layers.items():
                    # 교량 근처만 먼저 거른다(±0.01°)
                    near = (np.abs(la - b["lat"]) < 0.01) & (np.abs(lo - b["lon"]) < 0.012)
                    inside, cells = 0, set()
                    for x, y in zip(lo[near], la[near]):
                        px, py = to_m(float(x), float(y))
                        if pg.contains(Point(px, py)):
                            inside += 1
                            s = ((px - x0) * ux + (py - y0) * uy) / L  # 축 따라 거리
                            cells.add(min(max(int(s // a.grid_m), 0), n_cells - 1))
                    row[f"{name}_deck"] = inside
                    row[f"{name}_fill"] = round(len(cells) / n_cells, 3)
            rows.append(row)
    p2 = next((k for k in sorted({k for r in rows for k in r}) if k.startswith("p2") and k.endswith("_deck")), None)
    done = [r for r in rows if p2 and p2 in r]
    summ = {"n_bridges": len(rows), "tiles": len(plan_["tiles"]), "tiles_done": tiles_done,
            "bridges_measured": len(done)}
    if done:
        dk = [r[p2] for r in done]
        summ.update({"layer": p2.replace("_deck", ""), "with_any_point": sum(x > 0 for x in dk),
                     "with_5plus": sum(x >= 5 for x in dk), "median_points": float(np.median(dk)),
                     "median_fill": float(np.median([r[p2.replace('_deck', '_fill')] for r in done]))})
    json.dump({"summary": summ, "bridges": rows}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.csv:
        keys = list(dict.fromkeys(k for r in rows for k in r))
        with open(a.csv, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, keys)
            w.writeheader()
            w.writerows(rows)
    print(json.dumps(summ, ensure_ascii=False))
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("plan")
    p.add_argument("--bridges", required=True)
    p.add_argument("--min-len", type=float, default=100.0)
    p.add_argument("--tile-deg", type=float, default=0.06, help="구역 한 변 최대(버퍼 포함)")
    p.add_argument("--buffer-deg", type=float, default=0.012, help="교량 밖 여유 — 기준점·대기 보정용 주변 점")
    p.add_argument("--out", required=True)
    r = sp.add_parser("report")
    r.add_argument("--tiles", required=True)
    r.add_argument("--root", required=True)
    r.add_argument("--buffer-m", type=float, default=5.0)
    r.add_argument("--grid-m", type=float, default=20.0)
    r.add_argument("--out", required=True)
    r.add_argument("--csv", default=None)
    a = ap.parse_args()
    return plan(a) if a.cmd == "plan" else report(a)


if __name__ == "__main__":
    sys.exit(main())
