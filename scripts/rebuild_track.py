#!/usr/bin/env python3
"""언래핑된 간섭도는 그대로 두고 **기간만 잘라** 트랙을 다시 만든다.

왜 필요한가 — 월드컵대교
------------------------
월드컵대교 주경간교는 **2021년 준공**이다. 그런데 스택은 2018-06 ~ 2025-12 이고,
`build_track_h5` 는 **첫 쌍의 coherence** 로 점을 고른다. 첫 쌍이 2022-03-30 ↔ 2018-06-19
라, 다리가 없던 2018년과의 결맞음을 보고 교면 화소를 전부 버렸다. 그래서 데크 ±30 m 안
PS 가 20000점 중 **1점**이었고 트윈·PINN 이 통째로 불가였다.

SLC 도 SNAP 도 다시 돌릴 필요가 없다 — `unw_<기준일>_<보조일>.tif` 는 이미 다 있다.
**준공 이후 날짜만** 골라 트랙을 다시 쌓으면 교면이 살아난다(1점 → 54점).

준공이 스택 한가운데인 교량은 전부 같은 함정에 빠진다. 재가설·확장 구간도 마찬가지다.

    python scripts/rebuild_track.py --proc F:/SLC/seoul_work/월드컵대교 \\
        --out F:/SLC/seoul_work/track_월드컵대교_2021.h5 \\
        --lat 37.556493 --lon 126.885528 --since 2021-09-01 \\
        --like F:/SLC/seoul_work/track_월드컵대교.h5

`--like` 는 기존 트랙에서 HEADING·파장 같은 궤도 메타를 그대로 가져온다(간섭도 tif 에는
없다). `--deck` 를 주면 그 선 기준 ±30 m 안 점 수를 전/후로 찍어 준다 — 잘라서 나아졌는지
바로 보이라고.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from inframon.insar.snap_backend import SnapPairResult, build_track_h5

TIF = re.compile(r"^unw_(\d{8})_(\d{8})\.tif$")


def find_pairs(proc: Path) -> list[tuple[str, str, Path]]:
    """`unw_<기준일>_<보조일>.tif` 를 (기준일, 보조일, 경로) 로."""
    out = []
    for p in sorted(proc.glob("unw_*.tif")):
        m = TIF.match(p.name)
        if m:
            out.append((m.group(1), m.group(2), p))
    return out


def deck_hits(h5: Path, deck: list[list[float]], half_m: float) -> tuple[int, int, float]:
    """데크선 ±half_m 안 점 수 · 전체 점 수 · 최소 거리[m]."""
    import h5py

    with h5py.File(h5, "r") as f:
        ll = f["pixel_lonlat"][()]
    G = np.asarray(deck, float)
    lat0 = float(G[:, 0].mean())
    c = math.cos(math.radians(lat0))

    def to_m(la, lo):
        return np.stack([(lo - G[0, 1]) * 111_320 * c, (la - G[0, 0]) * 110_540], -1)

    P, Q = to_m(ll[:, 1], ll[:, 0]), to_m(G[:, 0], G[:, 1])
    best = np.full(len(P), np.inf)
    for i in range(len(Q) - 1):
        a, b = Q[i], Q[i + 1]
        d = b - a
        L2 = float(d @ d)
        if L2 < 1e-9:
            continue
        t = np.clip(((P - a) @ d) / L2, 0.0, 1.0)
        best = np.minimum(best, np.hypot(*(P - (a + t[:, None] * d)).T))
    return int((best <= half_m).sum()), len(P), float(best.min())


def meta_from(like: Path | None) -> dict:
    """기존 트랙에서 궤도 메타(HEADING) 를 가져온다 — 간섭도 tif 에는 없다."""
    if not like or not Path(like).exists():
        return {}
    import h5py

    with h5py.File(like, "r") as f:
        a = dict(f.attrs)
    out = {}
    if "HEADING" in a:
        out["heading"] = float(a["HEADING"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proc", required=True, help="unw_*.tif 가 있는 처리 폴더")
    ap.add_argument("--out", required=True, help="새 트랙 H5 경로")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                    help="이 날짜 이후 보조일만 쓴다(준공일을 넣으면 된다)")
    ap.add_argument("--until", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--like", default=None, help="궤도 메타(HEADING)를 가져올 기존 트랙 H5")
    ap.add_argument("--heading", type=float, default=None, help="--like 대신 직접")
    ap.add_argument("--coh-min", type=float, default=0.3)
    ap.add_argument("--radius-km", type=float, default=3.0)
    ap.add_argument("--max-points", type=int, default=20000)
    ap.add_argument("--deck", default=None,
                    help="데크선 JSON([[lat,lon],...] 또는 bridge.json) — 전/후 점 수를 찍는다")
    ap.add_argument("--half-m", type=float, default=30.0)
    a = ap.parse_args()

    proc = Path(a.proc)
    allp = find_pairs(proc)
    if not allp:
        print(f"unw_*.tif 가 없습니다: {proc}", file=sys.stderr)
        return 1
    refs = {r for r, _, _ in allp}
    if len(refs) != 1:
        print(f"기준일이 여럿입니다: {sorted(refs)} — 폴더를 나눠 주세요", file=sys.stderr)
        return 1
    ref = refs.pop()

    lo = a.since.replace("-", "") if a.since else "00000000"
    hi = a.until.replace("-", "") if a.until else "99999999"
    keep = [(s, p) for _, s, p in allp if lo <= s <= hi]
    if len(keep) < 5:
        print(f"기간 안 간섭도가 {len(keep)}장뿐입니다 — 속도 추정에 모자랍니다",
              file=sys.stderr)
        return 1

    deck = None
    if a.deck:
        d = json.loads(Path(a.deck).read_text(encoding="utf-8"))
        deck = d.get("geometry") if isinstance(d, dict) else d
    before = None
    like = Path(a.like) if a.like else None
    if deck and like and like.exists():
        before = deck_hits(like, deck, a.half_m)

    kw = meta_from(like)
    if a.heading is not None:
        kw["heading"] = a.heading
    print(f"기준일 {ref} · 전체 {len(allp)}장 → 기간 안 {len(keep)}장 "
          f"({keep[0][0]} ~ {keep[-1][0]})")
    if "heading" not in kw:
        print("  경고 — HEADING 을 못 찾았습니다(--like 나 --heading). "
              "쉬프트 보정이 방위를 모른 채 돕니다.")

    pairs = [SnapPairResult(ref_date=ref, sec_date=s, product=str(p), ok=True)
             for s, p in keep]
    out = Path(a.out)
    n = build_track_h5(pairs, ref, out, lat=a.lat, lon=a.lon,
                       coh_min=a.coh_min, radius_km=a.radius_km,
                       max_points=a.max_points, unwrapped=True, **kw)
    print(f"  점 {n} · 시점 {len(keep) + 1} → {out}")

    if deck:
        after = deck_hits(out, deck, a.half_m)
        if before:
            print(f"\n데크 ±{a.half_m:.0f} m 안 점 — 기존 {before[0]}/{before[1]} "
                  f"(최소 {before[2]:.1f} m) → 신규 {after[0]}/{after[1]} "
                  f"(최소 {after[2]:.1f} m)")
        else:
            print(f"\n데크 ±{a.half_m:.0f} m 안 점 {after[0]}/{after[1]} "
                  f"(최소 {after[2]:.1f} m)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
