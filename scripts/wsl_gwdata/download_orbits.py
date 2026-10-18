#!/usr/bin/env python3
"""버스트 ID 로 취득 날짜·위성을 조회해 정밀 궤도(POEORB, 없으면 RESORB) EOF 를 받는다 (sentineleof).

    python scripts/wsl_gwdata/download_orbits.py --bursts 054_114060_IW2 --start 2025-01-01 \
        --save-dir E:/InSAR_DATA/gangwon/orbits

같은 궤도의 버스트는 날짜·위성이 같으므로 하나만 줘도 된다. 자격증명은 ~/.netrc (ASF) 를 쓴다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import asf_search as asf


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bursts", nargs="+", required=True)
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--save-dir", required=True)
    a = ap.parse_args()

    from eof.download import download_eofs  # sentineleof

    # sceneName 은 'S1_114060_...' 라 위성 문자가 없다 → granule url 의 상위 SLC 이름(S1A_IW_SLC__...) 에서 가져온다
    pairs: set[tuple[str, str]] = set()
    for b in a.bursts:
        for r in asf.search(dataset=asf.DATASET.SLC_BURST, fullBurstID=b, polarization="VV", start=a.start, end=a.end):
            p = r.properties
            parent = p["url"].split("/")[3]  # S1A_IW_SLC__1SDV_20250108T092353_..._9B0C
            pairs.add((p["startTime"], parent[:3]))
    dts = sorted(pairs)
    print(f"{len(dts)} acquisitions: {dts[0][0][:10]} .. {dts[-1][0][:10]}  missions={sorted({m for _, m in dts})}")
    save = Path(a.save_dir); save.mkdir(parents=True, exist_ok=True)
    got = download_eofs(orbit_dts=[datetime.fromisoformat(t.replace("Z", "")) for t, _ in dts],
                        missions=[m for _, m in dts], save_dir=str(save), orbit_type="precise")
    print(f"downloaded/present: {len(got)} EOF → {save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
