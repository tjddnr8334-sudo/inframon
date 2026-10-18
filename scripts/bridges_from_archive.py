#!/usr/bin/env python3
"""로컬 SLC 아카이브로 **교량 여러 개를 한 번에** — 다운로드 없이 InSAR 트랙까지.

기관이 이미 받아 둔 Sentinel-1 아카이브(수백 GB~TB)가 있을 때, 다시 내려받지 않고
그 자리에서 교량별 Track H5 를 만든다. 아카이브는 보통 zip 이 아니라 **풀어 둔
`.SAFE` 디렉터리**로 쌓여 있으므로 둘 다 읽는다(`snap_backend.annotation_root`).

    python scripts/bridges_from_archive.py \
        --slc-dir "F:/SLC/서울-인천SLC/SLC" --bridges docs/bridges/seoul.json \
        --out F:/SLC/seoul_work --every 2 --workers 6 --unwrap

한 일:
  ① 아카이브 장면 나열 → `--every N` 으로 솎고 `--start/--end` 로 자른다
  ② 기준(master) = 기간 한가운데 장면(명시하면 그 값) — 스타 네트워크 기선을 줄인다
  ③ 교량을 덮는 subswath·burst 판별 → 교량별 코레지+간섭도+**언래핑**+지오코딩
  ④ 교량별 Track H5(pixel_lonlat/epochs/los_mm/coh/incidenceAngle)

그다음은 좌표 하나짜리 기존 경로 그대로:
    python scripts/bridge_run.py --name 성수대교 --lat .. --lon .. \
        --track <out>/track_성수대교.h5 --proc <out>/성수대교 --master <YYYYMMDD>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

for _s in (sys.stdout, sys.stderr):          # 한국어 Windows 콘솔(cp949) 에서 죽지 않게
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from inframon.insar import snap_backend as sb


def scene_day(p: Path) -> date:
    m = re.search(r"_(\d{8})T", p.name)
    if not m:
        raise ValueError(f"장면 이름에서 날짜를 못 읽음: {p.name}")
    s = m.group(1)
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def list_scenes(slc_dir: Path) -> list[Path]:
    """아카이브의 SLC — 풀어 둔 .SAFE 디렉터리와 zip 을 모두 받는다."""
    safes = [p for p in sorted(slc_dir.glob("*.SAFE")) if sb.is_safe_dir(p)]
    zips = sorted(slc_dir.glob("*.zip"))
    return safes + zips


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slc-dir", required=True, help="SLC 아카이브 폴더(.SAFE 또는 .zip)")
    ap.add_argument("--bridges", required=True, help="[{name,lat,lon}, ...] JSON")
    ap.add_argument("--out", required=True, help="처리 산출 폴더")
    ap.add_argument("--every", type=int, default=1, metavar="N", help="N 장마다 1장만 쓴다")
    ap.add_argument("--start", help="YYYY-MM-DD 이후 장면만")
    ap.add_argument("--end", help="YYYY-MM-DD 이전 장면만")
    ap.add_argument("--master", help="기준일 YYYYMMDD(기본: 기간 한가운데)")
    ap.add_argument("--workers", type=int, default=1, help="동시에 처리할 쌍 수")
    ap.add_argument("--unwrap", action="store_true",
                    help="snaphu 위상 언래핑까지(실산출은 반드시 이쪽 — 래핑 LOS 는 ±λ/4 에 갇힌다)")
    ap.add_argument("--half-km", type=float, default=2.0, help="언래핑할 교량 주변 반폭[km]")
    ap.add_argument("--radius-km", type=float, default=3.0, help="Track 에 담을 교량 반경[km]")
    ap.add_argument("--coh-min", type=float, default=0.3)
    ap.add_argument("--max-temporal-days", type=float, default=4000.0,
                    help="기준일로부터 허용 시간기선[일] (스타 네트워크는 길어질 수밖에 없다)")
    ap.add_argument("--max-perp-m", type=float, default=400.0)
    args = ap.parse_args()

    slc_dir, out = Path(args.slc_dir), Path(args.out)
    scenes = list_scenes(slc_dir)
    if not scenes:
        print(f"✗ SLC 를 못 찾았습니다: {slc_dir}")
        return 2
    if args.start:
        scenes = [s for s in scenes if scene_day(s) >= date.fromisoformat(args.start)]
    if args.end:
        scenes = [s for s in scenes if scene_day(s) <= date.fromisoformat(args.end)]
    if args.every > 1:
        scenes = scenes[:: args.every]
    if len(scenes) < 2:
        print(f"✗ 조건에 맞는 장면이 {len(scenes)}장 — 최소 2장 필요")
        return 2

    days = [scene_day(s) for s in scenes]
    if args.master:
        ref = next((s for s in scenes if args.master in s.name), None)
        if ref is None:
            print(f"✗ 기준일 {args.master} 장면이 목록에 없습니다")
            return 2
    else:
        mid = days[0] + (days[-1] - days[0]) / 2
        ref = min(scenes, key=lambda s: abs((scene_day(s) - mid).days))

    bridges = json.loads(Path(args.bridges).read_text(encoding="utf-8"))
    print(f"장면 {len(scenes)}장 · {days[0]} ~ {days[-1]} · 기준 {sb.scene_date(ref)}")
    print(f"교량 {len(bridges)}개 · 동시 {args.workers} · "
          f"{'언래핑' if args.unwrap else '래핑(연구용)'}", flush=True)

    t0 = time.time()

    def progress(lane, done, total, pair):
        el = time.time() - t0
        eta = el / max(done, 1) * (total - done)
        mark = "✅" if pair.ok else "✗"
        print(f"  [{lane}] {done}/{total} {mark} {pair.sec_date} {pair.detail[:40]} "
              f"· 경과 {el/60:.0f}분 · 이 레인 잔여 ~{eta/60:.0f}분", flush=True)

    res = sb.run_batch(scenes, bridges, out, reference=ref,
                       unwrap=args.unwrap, unwrap_half_km=args.half_km,
                       coh_min=args.coh_min, radius_km=args.radius_km,
                       max_temporal_days=args.max_temporal_days,
                       max_perp_m=args.max_perp_m,
                       workers=args.workers, progress=progress)

    print("=" * 60)
    summary = []
    for r in res:
        print(f"  {'✅' if r.track_h5 else '✗'} {r.name} — {r.n_points}점 · "
              f"{r.burst.subswath if r.burst else '?'}#{r.burst.burst_index if r.burst else '?'} "
              f"· {r.track_h5 or r.error}")
        summary.append(r.as_dict())
    (out / "batch_result.json").write_text(
        json.dumps({"reference": sb.scene_date(ref), "n_scenes": len(scenes),
                    "date_range": [str(days[0]), str(days[-1])],
                    "unwrapped": bool(args.unwrap), "bridges": summary},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"기록: {out / 'batch_result.json'} · 총 {(time.time()-t0)/60:.0f}분")
    return 0 if any(r.track_h5 for r in res) else 1


if __name__ == "__main__":
    raise SystemExit(main())
