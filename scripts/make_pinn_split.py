#!/usr/bin/env python3
"""**PINN 이 변위를 무엇으로 나누는지** 한 장으로 — 4쪽 하단에 넣을 그림.

4쪽 하단 오른쪽의 수치 타일 넷(6단계 · 1개 · 12일 · 0개)은 전부 다른 쪽에 또 있다.
6단계는 바로 위 파이프라인 띠가 보여 주고, 12일·0개는 2쪽 큰 숫자에 있고, '좌표
하나' 는 왼쪽 칸과 3쪽 카드에 있다. 같은 말을 네 번 더 하는 자리다.

그 자리에 **지금 자료 어디에도 안 보이는 것**을 넣는다 — PINN 이 하는 일이다.
보통의 InSAR 는 "이 점이 이만큼 움직였다" 에서 끝난다. Inframon 이 한 걸음 더 가는
지점이 여기인데, 자료에서는 ④ 칸에 글 세 줄로만 있다.

  관측된 변위(뭉뚱그려진 값) → 열 · 하중 · 침하 · 이상 네 성분

**과장하지 않는다.** 이것은 모형이 나눈 것이지 넷을 따로 잰 것이 아니다. 그 말을
그림 안에 적는다.

⚠ **그런데 이 그림은 지금 발표자료에 넣으면 안 된다.** 그리고 나서야 보였다.

  · **열 성분이 사실상 0 이다** — 진폭 0.21 mm. 같은 교량에서 MT-InSAR 로 따로 낸
    열팽창은 −0.217 mm/°C 이고, 서울 연교차 30°C 면 6.5 mm 가 나와야 한다.
    **31배 차이**다. 우리 두 산출이 서로 안 맞는다.
  · **침하·이상이 지나치게 매끄럽다** — 이웃 시점 사이 변화의 표준편차가 각각
    0.057 mm · 0.027 mm 다. 관측 LOS 는 시점마다 수십 mm 씩 튀는데, 거기서 나온
    성분이 이만큼 매끄러울 수 없다. 낮은 차수 기저로 추세를 나눠 가진 모양이다.
  · **하중이 2023년에 부푸는 혹**(+2 ~ +31 mm)이다. 차량 하중이 그렇게 움직일
    까닭이 없다 — 나머지 저주파를 하중이 흡수한 것으로 보인다.

그래서 이 스크립트는 지금 **발표용이 아니라 진단용**이다. 성분 분해를 고치고 나서,
열 성분이 MT-InSAR 열팽창과 맞고 이상 성분이 매끄럽지 않게 되면 그때 자료에 넣는다.

    python scripts/make_pinn_split.py [--bridge 올림픽대교]

산출: docs/img/proposal/PINN_성분분해.png
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import h5py
import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kaia_theme import MPL, use_mpl_style                             # noqa: E402

use_mpl_style()

# (데이터셋, 보여 줄 이름, 무엇인지 한 줄, 색)
PARTS = [
    ("comp_thermal", "열",   "기온 따라 늘고 주는 몫", MPL["orange"]),
    ("comp_load",    "하중", "차량·자중이 누르는 몫",  MPL["blue"]),
    ("comp_settle",  "침하", "받침·기초가 내려앉는 몫", MPL["slate"]),
    ("comp_anomaly", "이상", "위 셋으로 설명이 안 되는 몫", MPL["red"]),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="올림픽대교")
    ap.add_argument("--out", default=str(ROOT / "docs" / "img" / "proposal"
                                        / "PINN_성분분해.png"))
    a = ap.parse_args()

    proj = ROOT / "docs" / "bridges" / a.bridge / "project.h5"
    with h5py.File(proj, "r") as h:
        g = h["pinn"]
        comps = {k: np.nanmedian(np.asarray(g[k]), axis=0) for k, *_ in PARTS}
        days = np.asarray(h["insar/dates"], float)
    # dates 는 첫 취득일로부터의 날수다 — 실제 날짜로 되돌린다.
    lbl = None
    with h5py.File(proj, "r") as h:
        if "insar/date_labels" in h:
            lbl = [s.decode() if isinstance(s, bytes) else str(s)
                   for s in h["insar/date_labels"][:]]
    if lbl:
        xs = [date(int(s[:4]), int(s[4:6]), int(s[6:8])) for s in lbl]
    else:
        d0 = date(2018, 6, 19)
        xs = [d0.fromordinal(d0.toordinal() + int(v)) for v in days]

    fig, axes = plt.subplots(len(PARTS), 1, figsize=(6.6, 3.5), sharex=True,
                             gridspec_kw={"hspace": 0.34})
    for ax, (key, name, what, col) in zip(axes, PARTS):
        y = comps[key]
        ax.fill_between(xs, 0, y, color=col, alpha=.22, lw=0)
        ax.plot(xs, y, color=col, lw=1.35)
        ax.axhline(0, color=MPL["gray"], lw=.7, ls=":")
        ax.set_ylabel(name, fontsize=11, fontweight="bold", color=col,
                      rotation=0, ha="right", va="center", labelpad=10)
        ax.text(0.012, 0.86, f"{what}   ({np.nanmin(y):+.0f} ~ {np.nanmax(y):+.0f} mm)",
                transform=ax.transAxes, fontsize=8.6, color=MPL["gray"], va="top")
        ax.tick_params(labelsize=8.6)
        ax.grid(alpha=.18)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    axes[-1].xaxis.set_major_locator(mdates.YearLocator())
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.suptitle(f"{a.bridge} — 관측된 변위를 네 가지로 나눈다",
                 fontsize=12.5, fontweight="bold", color=MPL["ink"], y=0.985)
    fig.text(0.008, 0.008,
             "※ 모형(PINN)이 물리식을 지키며 나눈 몫이다 — 넷을 따로 잰 것이 아니다. "
             "교면 측점의 중앙값.",
             fontsize=8.2, color=MPL["gray"])
    fig.tight_layout(rect=(0.02, 0.045, 1, 0.945))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=180)
    plt.close(fig)
    print("wrote", out)
    for key, name, *_ in PARTS:
        y = comps[key]
        print(f"   {name}  {np.nanmin(y):+7.2f} ~ {np.nanmax(y):+7.2f} mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
