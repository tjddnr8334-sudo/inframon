#!/usr/bin/env python3
"""래핑 산출 재처리 배치 — 같은 SLC 로 **위상 언래핑까지** 다시 만든다.

기존 track 은 언래핑 없이 만들어져 LOS 가 ±λ/4(13.87mm)에 갇혀 있었다(감사표 '보고 불가').
이 스크립트는 스택 단위로 재처리하고, 끝나는 대로 preflight 로 "정말 풀렸는지"를 확인해
보고한다. 쌍마다 독립이라 중간에 끊겨도 다시 돌리면 이어서 간다(skip_existing).

**어느 컴퓨터에서든** 돌게 경로를 박아두지 않는다. 세 가지 방법 중 하나로 대상을 준다:

  1) 직접 지정 — 다른 PC 에서 가장 확실하다
     python scripts/rerun_unwrap_batch.py --slc /data/SLC --out data/rerun --target 37.3685,127.1090

  2) 스택 설정 파일 — 여러 교량을 반복해 돌릴 때
     python scripts/rerun_unwrap_batch.py --stacks my_stacks.json --stack all
     # my_stacks.json: {"f120": {"slc": "...", "out": "...", "lat": 37.36, "lon": 127.10,
     #                            "name": "정자교"}}

  3) 레시피 폴더 — inframon 이 이미 만든 것을 그대로
     python scripts/rerun_unwrap_batch.py --recipe data/recipe_jeongja --slc /data/SLC

사전 조건(없으면 시작 전에 알려준다):
  · SNAP gpt         — `python -m inframon --doctor` 또는 SNAP 설치
  · snaphu           — `python -m inframon --insar-tools` 가 유무·설치법을 알려준다
                       (Windows 는 WSL 안의 snaphu 를 자동으로 건너가 쓴다)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:                 # 설치 없이도 돌게(다른 PC 배려)
    sys.path.insert(0, str(REPO / "src"))

DEFAULT_STACKS = REPO / "configs" / "rerun_stacks.json"


def _load_stacks(path: Path | None) -> dict:
    p = path or DEFAULT_STACKS
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _target_from_recipe(recipe: Path) -> tuple[float, float] | None:
    f = recipe / "bridge_target.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    lat = d.get("selected_lat", d.get("lat"))
    lon = d.get("selected_lon", d.get("lon"))
    return (float(lat), float(lon)) if lat is not None and lon is not None else None


def preflight_env(unwrap: bool) -> None:
    """도구가 없으면 **처리 전에** 멈춘다 — 몇 시간 돌리고 나서 알면 늦다."""
    from inframon.insar.snap_backend import SnapError, find_gpt
    from inframon.insar.snap_unwrap import install_hint, is_available

    try:
        gpt = find_gpt()
    except SnapError as e:
        raise SystemExit(f"⛔ SNAP gpt 를 찾지 못했습니다 — {e}")
    print(f"  gpt    : {gpt}")
    if unwrap:
        ok, msg = is_available()
        print(f"  snaphu : {msg}")
        if not ok:
            raise SystemExit("⛔ " + install_hint())


def run_stack(name: str, slc_dir: str, out_dir: str, target: tuple[float, float], *,
              unwrap: bool = True, half_km: float = 2.0, count: int | None = None) -> dict:
    from inframon.insar.snap_backend import run as snap_run
    from inframon.insar.track_preflight import preflight_track_h5

    scenes = sorted(str(p) for p in Path(slc_dir).glob("*.zip"))
    if count:
        scenes = scenes[:count]
    if not scenes:
        raise SystemExit(f"⛔ SLC(.zip)를 찾지 못했습니다: {slc_dir}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    h5 = out / f"track_{Path(out_dir).name}_{'unw' if unwrap else 'wrap'}.h5"
    lat, lon = target
    # 대상 좌표를 산출물 곁에 남긴다 — 이게 없으면 나중에 감사가 "어느 교량인지 모름"
    # 으로 판정을 낮춘다(실제로 첫 재처리에서 그랬다).
    (out / "bridge_target.json").write_text(
        json.dumps({"name": name, "selected_lat": lat, "selected_lon": lon,
                    "slc_dir": str(slc_dir), "unwrap": unwrap,
                    "unwrap_half_km": half_km}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n{'=' * 60}\n  {name} — SLC {len(scenes)}장 → "
          f"{'언래핑 ' if unwrap else ''}재처리\n{'=' * 60}", flush=True)
    t0 = time.time()
    res = snap_run(scenes, lat, lon, out_dir=str(out), out_h5=str(h5),
                   era5_master=True, unwrap=unwrap, unwrap_half_km=half_km)
    ok = sum(1 for p in res.pairs if p.ok)
    print(f"  쌍 {ok}/{len(res.pairs)} 성공 · N={res.n_points} · {time.time() - t0:.0f}s",
          flush=True)
    for p in res.pairs:
        if not p.ok:
            print(f"    ✗ {p.ref_date}_{p.sec_date}: {p.note[:110]}", flush=True)

    pf = preflight_track_h5(h5, target=(lat, lon))
    print(f"  preflight: ready={pf.is_ready} · |LOS|max={pf.los_abs_max} · "
          f"래핑={pf.looks_wrapped} · 30m내 {pf.n_within_deck}/{pf.n_points}", flush=True)
    for e in pf.errors:
        print(f"    ❌ {e[:120]}", flush=True)
    return {"stack": name, "track_h5": str(h5), "n_points": res.n_points,
            "pairs_ok": ok, "pairs": len(res.pairs), "ready": pf.is_ready,
            "los_abs_max": pf.los_abs_max, "wrapped": pf.looks_wrapped,
            "n_within_deck": pf.n_within_deck, "seconds": round(time.time() - t0)}


def main() -> None:
    ap = argparse.ArgumentParser(description="언래핑 재처리 배치 (어느 PC 에서든)")
    ap.add_argument("--slc", help="SLC(.zip) 폴더")
    ap.add_argument("--out", default="data/rerun", help="산출 폴더(기본 data/rerun)")
    ap.add_argument("--target", help="교량 좌표 LAT,LON")
    ap.add_argument("--recipe", help="레시피 폴더 — bridge_target.json 에서 좌표를 읽는다")
    ap.add_argument("--stacks", help="스택 설정 JSON (기본 configs/rerun_stacks.json)")
    ap.add_argument("--stack", default="all", help="설정 파일에서 돌릴 스택 이름 또는 all")
    ap.add_argument("--count", type=int, default=None, help="앞에서 N 장만(시험용)")
    ap.add_argument("--half-km", type=float, default=2.0, help="언래핑 범위(반경 km)")
    ap.add_argument("--no-unwrap", action="store_true", help="언래핑 없이(비교용)")
    a = ap.parse_args()

    unwrap = not a.no_unwrap
    print("=" * 60 + "\n  사전 점검\n" + "=" * 60)
    preflight_env(unwrap)

    jobs = []
    if a.slc:                                   # 1) 직접 지정
        tgt = None
        if a.target:
            tgt = tuple(float(v) for v in a.target.split(","))
        elif a.recipe:
            tgt = _target_from_recipe(Path(a.recipe))
        if not tgt:
            raise SystemExit("⛔ --target LAT,LON 또는 --recipe 가 필요합니다")
        jobs.append((Path(a.out).name, a.slc, a.out, tgt))
    else:                                       # 2) 설정 파일
        stacks = _load_stacks(Path(a.stacks) if a.stacks else None)
        if not stacks:
            raise SystemExit(
                "⛔ 대상이 없습니다. --slc 와 --target 을 주거나, 스택 설정 파일을 만드세요"
                f"(기본 위치 {DEFAULT_STACKS}). --help 에 예시가 있습니다.")
        names = list(stacks) if a.stack == "all" else [a.stack]
        for n in names:
            if n not in stacks:
                raise SystemExit(f"⛔ 설정에 '{n}' 스택이 없습니다 — 가능: {list(stacks)}")
            c = stacks[n]
            jobs.append((c.get("name", n), c["slc"], c["out"],
                         (float(c["lat"]), float(c["lon"]))))

    results = []
    for name, slc, out, tgt in jobs:
        try:
            results.append(run_stack(name, slc, out, tgt, unwrap=unwrap,
                                     half_km=a.half_km, count=a.count))
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 — 한 스택 실패가 다음을 막지 않는다
            print(f"  ⛔ {name} 실패: {str(e)[:200]}", flush=True)
            results.append({"stack": name, "error": str(e)[:200]})

    print(f"\n{'=' * 60}\n  배치 요약\n{'=' * 60}", flush=True)
    for r in results:
        print(f"  {r}", flush=True)
    print("\n  다음: python -m inframon --audit-artifacts <project.h5> 로 보고 가능 여부 확인",
          flush=True)


if __name__ == "__main__":
    main()
