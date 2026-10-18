#!/usr/bin/env python3
"""내려받은 ASF 버스트(.tiff/.xml) 를 날짜별로 묶어 ESA SAFE 로 조립한다 (burst2safe, Windows conda env b2s).

    python scripts/wsl_gwdata/build_safes.py --bursts-dir E:/InSAR_DATA/gangwon/bursts \
        --bursts 054_114060_IW2 054_114061_IW2 054_114062_IW2 --out E:/InSAR_DATA/gangwon/safe/asc54_gangneung

절차: 각 버스트의 manifest.json 에서 같은 절대궤도(=같은 날짜)의 그래뉼을 모아, 날짜 작업 폴더에
tiff/xml 을 **하드링크**(복사 없음)로 넣고 burst2safe 를 돌린다. burst2safe 는 현재 폴더에 파일이
있으면 다시 받지 않는다. 결과: out/<date>/S1?_IW_SLC__1SSV_*.SAFE. 이미 SAFE 가 있으면 건너뛴다.
ISCE topsStack 은 out/<date>/*.SAFE 를 zip 없이 폴더째 읽는다(-s 에 SAFE 들이 든 폴더).

자격증명: burst2safe 는 Windows 에서 ~/_netrc 를 찾는다. 여기서는 ~/.netrc 를 읽어 환경변수로 넘긴다.
"""
from __future__ import annotations

import argparse
import json
import netrc
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

URS = "urs.earthdata.nasa.gov"


def b2s_exe() -> str:
    for c in (Path.home() / "anaconda3/envs/b2s/Scripts/burst2safe.exe",
              Path.home() / "miniforge3/envs/b2s/bin/burst2safe",
              Path.home() / "miniconda3/envs/b2s/Scripts/burst2safe.exe"):
        if c.exists():
            return str(c)
    w = shutil.which("burst2safe")
    if w:
        return w
    sys.exit("burst2safe 를 못 찾는다: conda create -n b2s -c conda-forge python=3.11 gdal burst2safe")


def creds_env() -> dict:
    env = dict(os.environ)
    if env.get("EARTHDATA_TOKEN") or (env.get("EARTHDATA_USERNAME") and env.get("EARTHDATA_PASSWORD")):
        return env
    auth = netrc.netrc().authenticators(URS)
    if not auth:
        sys.exit(f"~/.netrc 에 {URS} 항목이 없다")
    env["EARTHDATA_USERNAME"], env["EARTHDATA_PASSWORD"] = auth[0], auth[2]
    return env


def link_or_copy(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bursts-dir", required=True)
    ap.add_argument("--bursts", nargs="+", required=True, help="fullBurstID 목록 (같은 궤도·같은 서브스왓 권장)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--require-all", action="store_true", help="모든 버스트가 있는 날짜만 조립 (기본: 있는 것만으로 조립)")
    ap.add_argument("--dates", nargs="*", default=None, help="이 날짜(YYYY-MM-DD)만")
    a = ap.parse_args()

    bdir = Path(a.bursts_dir)
    by_orbit: dict[tuple, list[dict]] = defaultdict(list)
    for b in a.bursts:
        mf = bdir / b / "manifest.json"
        if not mf.exists():
            print(f"[{b}] manifest.json 없음 — download_bursts.py 먼저", file=sys.stderr)
            continue
        for e in json.loads(mf.read_text(encoding="utf-8")):
            tiff, xml = Path(e["tiff"]), Path(e["xml"])
            if not (tiff.exists() and xml.exists()):
                continue
            by_orbit[(e["date"], e["orbit"])].append(dict(e, burst=b))

    exe, env = b2s_exe(), creds_env()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    n_done = n_skip = n_fail = 0
    for (date, orbit), items in sorted(by_orbit.items()):
        if a.dates and date not in a.dates:
            continue
        if a.require_all and len(items) < len(a.bursts):
            print(f"{date} 버스트 {len(items)}/{len(a.bursts)} — 건너뜀(--require-all)")
            continue
        wd = out / date
        if any(wd.glob("*.SAFE/manifest.safe")):
            n_skip += 1
            continue
        wd.mkdir(parents=True, exist_ok=True)
        for e in items:
            for k in ("tiff", "xml"):
                link_or_copy(Path(e[k]), wd / Path(e[k]).name)
        granules = [e["granule"] for e in items]
        r = subprocess.run([exe, "--output-dir", ".", *granules], cwd=wd, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok = r.returncode == 0 and any(wd.glob("*.SAFE/manifest.safe"))
        for e in items:  # 링크 정리 (SAFE 안에 데이터가 들어갔으므로)
            for k in ("tiff", "xml"):
                (wd / Path(e[k]).name).unlink(missing_ok=True)
        for stray in wd.glob("*_VV.xml"):
            stray.unlink()
        if ok:
            n_done += 1
            safe = next(wd.glob("*.SAFE")).name
            print(f"{date} orbit {orbit} bursts={len(items)} → {safe}", flush=True)
        else:
            n_fail += 1
            (wd / "burst2safe_error.log").write_text(r.stdout + "\n" + r.stderr, encoding="utf-8")
            print(f"{date} FAIL rc={r.returncode} (see {wd / 'burst2safe_error.log'})", flush=True)
    print(f"done built={n_done} skip={n_skip} fail={n_fail} → {out}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
