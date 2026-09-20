#!/usr/bin/env python3
"""**내부 참고용** — 교면 InSAR 전체 시계열(2018~2025) 위에 보고서 계측을 겹쳐 둔다.

자료에는 싣지 않는다. 지금 값으로는 계측 대조가 아무것도 주장하지 못해
(`strip_gnss_from_bridges.py`) 결과물에서 걷어냈지만, 누가 "그래서 계측이랑 어떻게
되는데?" 하고 물으면 바로 꺼낼 그림 한 장은 있어야 한다. 그래서 각 교량 폴더에
`참고_계측겹침.png` 만 두고 `결과.md` 에서는 가리키지 않는다.

그리는 것
  · 왼쪽 축 — 교면 PS(MT-InSAR, `track_deck_mt.h5`)의 **LOS 중앙값** 시계열. 전체
    기간을 다 그린다(2018~2025). 옅은 띠는 점들의 MAD.
  · 오른쪽 축 — 보고서 계측. 연도별 월별 값이라 **실제 연·월 자리에** 찍어 잇는다.
    GNSS 연직(샛강·월드컵)과 처짐(가양·원효·올림픽·천호)을 제목에 구분해 적는다.

**두 축은 같은 양이 아니다.** InSAR 는 위성 시선(LOS) 방향이고 계측은 연직이다.
게다가 계측값은 보고서 그래프를 눈으로 읽은 값이다(`_method`). 모양만 본다.

    python scripts/make_gnss_overlay.py

산출: docs/bridges/<교량>/참고_계측겹침.png
"""

from __future__ import annotations

import argparse
import json
import sys
import re
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
BR = ROOT / "docs" / "bridges"
SKIP_STATUS = ("not_applicable", "readable_not_read", "partial")


def insar_series(folder: Path):
    """교면 PS 의 LOS 중앙값 시계열 — 날짜순, 평균 0."""
    f = folder / "track_deck_mt.h5"
    if not f.exists():
        return None
    with h5py.File(f, "r") as h:
        ep = [e.decode() if isinstance(e, bytes) else str(e) for e in h["epochs"][:]]
        los = np.asarray(h["los_mm"][:], float)
        n_pts = los.shape[0]
    d = np.array([date(int(e[:4]), int(e[4:6]), int(e[6:8])) for e in ep])
    o = np.argsort(d)
    d, los = d[o], los[:, o]
    med = np.nanmedian(los, axis=0)
    mad = np.nanmedian(np.abs(los - med), axis=0) * 1.4826
    return d, med - np.nanmean(med), mad, n_pts


def report_curves(name: str, gnss: dict, disp: dict):
    """보고서 계측 곡선 — [(센서, [날짜], [값]), ...] 과 양의 이름.

    연도마다 따로 그리면 해가 바뀔 때마다 선이 끊겨 톱니처럼 보인다. 실제로는 같은
    센서의 이어진 기록이므로 **센서별로 연도를 이어 붙여** 한 줄로 그린다.
    """
    by_sensor: dict = {}
    quantity = None

    def add(sensor, yr, vals):
        for m, v in enumerate(vals):
            if v is not None:
                by_sensor.setdefault(sensor, []).append((date(yr, m + 1, 15), float(v)))

    for c in gnss.get("charts", []):
        if c.get("bridge") != name:
            continue
        quantity = f"GNSS 연직 ({c.get('dir', '연직')})"
        for y, vals in c.get("monthly", {}).items():
            add(c.get("sensor", "GNSS"), int(y), vals)

    if not by_sensor:
        b = disp.get(name)
        if not b or b.get("status") in SKIP_STATUS or not b.get("monthly"):
            return [], None
        quantity = b.get("quantity", "계측")
        for y, vals in b["monthly"].items():
            key = str(y)
            m = re.search(r"(\d{4})$", key)
            yr = int(m.group(1)) if m else int(key[:4])
            # 'DP_1_2_P10P11_2024' 처럼 이름에 연도가 붙은 것은 떼어 센서로 묶는다.
            sensor = re.sub(r"_?\d{4}$", "", key) or quantity
            add(sensor, yr, vals)

    out = []
    for sensor, pts in by_sensor.items():
        pts.sort()
        if len(pts) >= 3:
            out.append((sensor, [d for d, _ in pts], [v for _, v in pts]))
    return sorted(out), quantity


def figure(name: str, ins, curves, quantity: str, out: Path) -> None:
    d, med, mad, n_pts = ins
    fig, ax = plt.subplots(figsize=(12.0, 4.6))
    ax.fill_between(d, med - mad, med + mad, color=MPL["blue"], alpha=.14, lw=0,
                    label=f"교면 PS {n_pts}점 산포(MAD)")
    ax.plot(d, med, color=MPL["blue"], lw=1.5, marker="o", ms=3.0,
            label="InSAR 교면 LOS 중앙값")
    ax.axhline(0, color=MPL["gray"], lw=.8, ls=":")
    ax.set_ylabel("InSAR 교면 LOS [mm] (평균 0)", color=MPL["blue"], fontsize=10)
    ax.tick_params(axis="y", labelcolor=MPL["blue"])
    ax.set_xlim(date(2018, 1, 1), date(2025, 12, 31))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.grid(alpha=.22)

    ax2 = ax.twinx()
    warm = [MPL["orange"], MPL["red"], MPL["green"], MPL["slate"], MPL["ink"]]
    lo = min(min(dd) for _, dd, _ in curves)
    hi = max(max(dd) for _, dd, _ in curves)
    ax.axvspan(lo, hi, color=MPL["orange"], alpha=.055, lw=0, zorder=0)
    for i, (lab, dd, vv) in enumerate(curves):
        ax2.plot(dd, vv, color=warm[i % len(warm)], lw=1.6, marker="s", ms=3.4,
                 alpha=.92, label=lab)
    ax2.set_ylabel(f"보고서 계측 — {quantity} [mm]", color=MPL["orange"], fontsize=10)
    ax2.tick_params(axis="y", labelcolor=MPL["orange"])

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8.6, ncol=3, loc="upper left",
              framealpha=.94)
    ax.set_title(f"{name} — 교면 InSAR 전체 시계열 위에 보고서 계측을 겹침 "
                 "(내부 참고 · 자료에는 싣지 않는다)",
                 fontsize=12.5, fontweight="bold", color=MPL["ink"], pad=8)
    fig.text(0.006, 0.012,
             "※ 두 축은 같은 양이 아니다 — InSAR 는 위성 시선(LOS), 계측은 연직이다. "
             "계측값은 보고서 그래프를 눈으로 읽은 값이라 눈금 1/5 정도 오차를 봐야 한다.\n"
             "   겹친 기간(음영)만 비교할 수 있고, 그 밖의 InSAR 구간은 맞댈 상대가 없다. "
             "이 그림으로 일치·불일치를 주장하지 않는다.",
             fontsize=8.6, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.085, 1, 1))
    fig.savefig(out, dpi=150)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(BR))
    a = ap.parse_args()
    root = Path(a.root)
    gnss = json.loads((root / "hangang_gnss_monthly.json").read_text(encoding="utf-8"))
    disp = json.loads((root / "hangang_displacement_2024.json").read_text(
        encoding="utf-8"))

    made, skipped = [], []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        name = folder.name
        curves, quantity = report_curves(name, gnss, disp)
        if not curves:
            continue
        ins = insar_series(folder)
        if ins is None:
            skipped.append(f"{name}: track_deck_mt.h5 없음")
            continue
        out = folder / "참고_계측겹침.png"
        figure(name, ins, curves, quantity, out)
        made.append(f"{name:<10} {quantity:<22} 센서 {len(curves)}개 · "
                    f"InSAR {ins[3]}점 {len(ins[0])}시점")
    for m in made:
        print("  ", m)
    for s in skipped:
        print("   ⚠", s)
    print(f"{len(made)}개 교량에 참고_계측겹침.png 를 넣었다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
