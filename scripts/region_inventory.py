#!/usr/bin/env python3
"""지역(시도·시군구) 단위 교량 전수 목록 + Sentinel-1 버스트 배정.

교량 하나씩 AOI 를 잡던 방식으로는 강원 4,255 교량을 못 돌린다. 스택은 **버스트 단위**로
한 번만 만들고, 그 버스트 안의 교량은 전부 같은 스택에서 뽑는다. 이 스크립트가 그 배정표를 만든다.

  ① 전국교량표준데이터(data/national_bridge_standard_*.csv) 에서 시도(·시군구) 필터
  ② ASF 검색 — 지역 bbox 를 지나는 S1 SLC 전 아카이브 → 궤도(path)별 취득 날짜·연도 분포
  ③ ASF 버스트 검색(최근 8주) — 버스트 풋프린트 목록
  ④ 교량 좌표 → 포함 버스트(상승/하강) 배정
  ⑤ 연장 ≥ min_len 교량을 전부 덮는 최소 버스트 집합(방향별, 탐욕)

    python scripts/region_inventory.py --sido 강원 --out data/gangwon
    python scripts/region_inventory.py --sido 강원 --sigungu 강릉 --out data/gangneung
    python scripts/region_inventory.py --sido 경기 --min-len 200 --out data/gyeonggi

산출: bridges.csv(교량+버스트), bursts.csv(버스트+교량 수), paths.csv(궤도별 날짜), summary.json.
ASF 응답은 out/_asf_*.json 에 캐시한다(--refresh 로 다시 받음).
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ASF = "https://api.daac.asf.alaska.edu/services/search/param?"


def asf(params: dict, cache: Path, refresh: bool, timeout: int = 600) -> list[dict]:
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))
    url = ASF + urllib.parse.urlencode({**params, "output": "jsonlite"})
    with urllib.request.urlopen(url, timeout=timeout) as r:
        res = json.load(r)["results"]
    cache.write_text(json.dumps(res), encoding="utf-8")
    return res


def load_national(path: str | None) -> pd.DataFrame:
    if path is None:
        cands = sorted(glob.glob("data/national_bridge_standard_*.csv"))
        if not cands:
            sys.exit("전국교량표준데이터 CSV 가 없다: data/national_bridge_standard_*.csv")
        path = cands[-1]
    df = None
    for enc in ("utf-8-sig", "cp949"):
        try:
            df = pd.read_csv(path, encoding=enc, low_memory=False)
            break
        except UnicodeDecodeError:
            continue
    if df is None:
        sys.exit(f"CSV 인코딩을 못 읽는다: {path}")
    df["_src"] = path
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sido", required=True, help="시도명 부분 문자열 (예: 강원)")
    ap.add_argument("--sigungu", default=None, help="시군구명 부분 문자열 (예: 강릉)")
    ap.add_argument("--national-csv", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-len", type=float, default=100.0, help="최소 버스트 집합 계산에 쓰는 교량 연장 하한 [m]")
    ap.add_argument("--burst-weeks", type=int, default=8, help="버스트 풋프린트 수집 창(최근 N주)")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    try:  # Windows 콘솔(cp949) 에서 한글 출력이 깨지지 않게
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    try:
        from shapely import wkt as W
        from shapely.geometry import Point
    except ImportError:
        sys.exit("shapely 가 필요하다: pip install shapely")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    df = load_national(a.national_csv)
    g = df[df["시도명"].astype(str).str.contains(a.sido)].copy()
    if a.sigungu:
        g = g[g["시군구명"].astype(str).str.contains(a.sigungu)]
    if g.empty:
        sys.exit(f"교량 0건: 시도={a.sido} 시군구={a.sigungu}")
    g["lat"] = pd.to_numeric(g["교량시작점위도"], errors="coerce")
    g["lon"] = pd.to_numeric(g["교량시작점경도"], errors="coerce")
    g["length_m"] = pd.to_numeric(g["교량연장"], errors="coerce")
    g = g.dropna(subset=["lat", "lon"])
    lat0, lat1 = g.lat.min() - 0.05, g.lat.max() + 0.05
    lon0, lon1 = g.lon.min() - 0.05, g.lon.max() + 0.05
    poly = f"POLYGON(({lon0} {lat0},{lon1} {lat0},{lon1} {lat1},{lon0} {lat1},{lon0} {lat0}))"
    print(f"교량 {len(g)}건  bbox lat {lat0:.3f}-{lat1:.3f} lon {lon0:.3f}-{lon1:.3f}")

    # ② SLC 전 아카이브 — 궤도별 날짜
    today = date.today().isoformat()
    scenes = asf({"platform": "S1", "processingLevel": "SLC", "beamMode": "IW", "intersectsWith": poly,
                  "start": "2014-10-01T00:00:00Z", "end": f"{today}T00:00:00Z", "maxResults": 50000},
                 out / "_asf_slc_all.json", a.refresh)
    dates: dict = collections.defaultdict(set)
    plat: dict = collections.defaultdict(collections.Counter)
    for r in scenes:
        k = (r["flightDirection"][:3], r["path"])
        dates[k].add(r["startTime"][:10])
        plat[k][r["granuleName"][:3]] += 1
    prow = []
    for k, ds in sorted(dates.items()):
        byy = collections.Counter(d[:4] for d in ds)
        prow.append(dict(dir=k[0], path=k[1], n_dates=len(ds), first=min(ds), last=max(ds),
                         n_dates_since_2025=sum(1 for d in ds if d >= "2025-01-01"),
                         platforms=json.dumps(dict(plat[k])),
                         dates_by_year=json.dumps(dict(sorted(byy.items())))))
    paths = pd.DataFrame(prow)
    paths.to_csv(out / "paths.csv", index=False, encoding="utf-8-sig")
    print(paths[["dir", "path", "n_dates", "first", "last", "n_dates_since_2025"]].to_string(index=False))

    # ③ 버스트 풋프린트
    start = (date.today() - timedelta(weeks=a.burst_weeks)).isoformat()
    bp = asf({"dataset": "SLC-BURST", "intersectsWith": poly, "start": f"{start}T00:00:00Z",
              "end": f"{today}T00:00:00Z", "maxResults": 50000}, out / "_asf_bursts.json", a.refresh)
    bursts: dict[str, dict] = {}
    for r in bp:
        b = r["burst"]
        k = b["fullBurstID"]
        v = bursts.setdefault(k, dict(dir=r["flightDirection"][:3], path=r["path"], subswath=b["subswath"],
                                      poly=W.loads(r["wkt"]), n_recent=0, size_mb=r["sizeMB"]))
        v["n_recent"] += 1
    print(f"버스트 {len(bursts)}개 (최근 {a.burst_weeks}주 제품 {len(bp)}건)")

    # ④ 배정
    members: dict = collections.defaultdict(list)
    asc_col: list[str] = []
    des_col: list[str] = []
    for i, b in g.iterrows():
        p = Point(float(b.lon), float(b.lat))
        hit_a: list[str] = []
        hit_d: list[str] = []
        for k, v in bursts.items():
            if v["poly"].contains(p):
                (hit_a if v["dir"] == "ASC" else hit_d).append(k)
                members[k].append(i)
        asc_col.append(";".join(sorted(hit_a)))
        des_col.append(";".join(sorted(hit_d)))
    g["asc_bursts"] = asc_col
    g["desc_bursts"] = des_col

    def _paths(s: str) -> str:
        return "+".join(sorted({x.split("_")[0].lstrip("0") for x in s.split(";") if x}))

    g["asc_paths"] = g.asc_bursts.map(_paths)
    g["desc_paths"] = g.desc_bursts.map(_paths)
    g.drop(columns=["_src"]).to_csv(out / "bridges.csv", index=False, encoding="utf-8-sig")

    brow = []
    for k, v in bursts.items():
        sub = g.loc[members.get(k, [])]
        brow.append(dict(burst=k, dir=v["dir"], path=v["path"], subswath=v["subswath"], n_bridges=len(sub),
                         n_ge_minlen=int((sub.length_m >= a.min_len).sum()),
                         n_ge200=int((sub.length_m >= 200).sum()),
                         top_sigungu=",".join(sub["시군구명"].value_counts().head(3).index) if len(sub) else "",
                         size_mb=round(v["size_mb"], 1), wkt=v["poly"].wkt))
    bt = pd.DataFrame(brow).sort_values(["dir", "path", "burst"])
    bt.to_csv(out / "bursts.csv", index=False, encoding="utf-8-sig")

    # ⑤ 최소 버스트 집합
    minimal = {}
    for dr in ("ASC", "DES"):
        need = set(g.index[g.length_m >= a.min_len])
        chosen = []
        cand = {k: set(v) for k, v in members.items() if bursts[k]["dir"] == dr}
        while need and cand:
            best = max(cand, key=lambda k: len(cand[k] & need))
            got = cand[best] & need
            if not got:
                break
            chosen.append(dict(burst=best, covers=len(got)))
            need -= got
        minimal[dr] = dict(bursts=chosen, n_bursts=len(chosen), uncovered=len(need))
        print(f"{dr}: 연장≥{a.min_len:.0f} m 교량 전부 덮는 최소 버스트 {len(chosen)}개, 미커버 {len(need)}")

    used = bt[bt.n_bridges > 0]
    summary = dict(
        sido=a.sido, sigungu=a.sigungu, national_csv=df["_src"].iloc[0], n_bridges=int(len(g)),
        n_ge_minlen=int((g.length_m >= a.min_len).sum()), min_len=a.min_len,
        by_sigungu=g["시군구명"].value_counts().to_dict(),
        length_tiers={f"{lo}-{hi}": int(((g.length_m >= lo) & (g.length_m < hi)).sum())
                      for lo, hi in ((0, 50), (50, 100), (100, 200), (200, 500), (500, 99999))},
        n_bursts=len(bursts), n_bursts_with_bridges=int(len(used)),
        bursts_by_dir_path={f"{k[0]}-{k[1]}": int(v) for k, v in used.groupby(["dir", "path"]).size().items()},
        no_asc=int((g.asc_bursts == "").sum()), no_desc=int((g.desc_bursts == "").sum()),
        burst_size_mb_median=float(bt.size_mb.median()), minimal=minimal, paths=prow, generated=today,
    )
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {out}/bridges.csv · bursts.csv · paths.csv · summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
