#!/usr/bin/env python3
"""40단계 — StaMPS 처리폴더 → inframon Track H5.

`ps_plot` 으로 따로 내보낼 필요가 없다. 처리폴더의 ps2/phuw2/bp2/la2/pm2/hgt2/parms
를 그대로 읽는다(`inframon.insar.stamps_io`). 그래야 **수직기선 B⊥ 와 입사각**이
같이 나오고, 그 둘이 있어야 점별 잔차고도 Δh 와 열팽창을 같이 풀 수 있다
(`scripts/run_mtinsar.py`). `ps_plot` 내보내기에는 그 둘이 없다.

부호 규약은 리포 전체와 같다 — `d[mm] = −λ/(4π)·φ·1000`, **양수 = 위성 접근**.

사용(WSL):
  python3 40_to_inframon.py --stamps ~/stamps/INSAR_20220330 --out track_stamps.h5
  python3 40_to_inframon.py --stamps ~/stamps/proc --patch PATCH_2 --out t.h5

처리폴더를 그대로 MT-InSAR 에 물릴 수도 있다(이 단계를 건너뛴다):
  python scripts/run_mtinsar.py --only 가양대교 --stamps ~/stamps/가양대교
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent.parent.parent / "src"))

from inframon.insar.stamps_io import (  # noqa: E402
    StampsError, read_stamps, write_track_h5,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stamps", required=True, help="StaMPS 처리폴더(INSAR_<마스터>)")
    ap.add_argument("--out", required=True, help="쓸 Track H5 경로")
    ap.add_argument("--patch", default=None, help="패치 하위폴더(PATCH_1 …)")
    ap.add_argument("--wavelength", type=float, default=None,
                    help="파장[m] — 주면 parms.mat 보다 우선한다")
    a = ap.parse_args()

    try:
        st = read_stamps(a.stamps, wavelength_m=a.wavelength, patch=a.patch)
    except StampsError as exc:
        print("!!", exc, file=sys.stderr)
        return 2

    p = write_track_h5(st, a.out)
    print(f"wrote {p}")
    print(f"   점 {st.n_points} · 시점 {st.n_epochs} · 마스터 {st.master}")
    print(f"   {st.epochs[0]} ~ {st.epochs[-1]} · λ {st.wavelength_m:.5f} m")
    print(f"   입사각 중앙 {float(__import__('numpy').nanmedian(st.incidence_deg)):.2f}°"
          + (f" · 슬랜트거리 {st.slant_range_m:,.0f} m" if st.slant_range_m else ""))
    for k, v in st.sources.items():
        print(f"   · {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
