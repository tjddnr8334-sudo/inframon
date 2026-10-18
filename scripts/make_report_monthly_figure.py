#!/usr/bin/env python3
"""보고서의 **월별 변위 그래프를 전부** 다시 그린다 — 1M~12M · 연도별 막대.

보고서 곳곳에 흩어진 '월별 변위' 그래프(x축 1M~12M, 2022·23·24년 막대 3개)를 한 판에
모은다. 값의 출처는 둘이고 그림에 그대로 표시한다.

  · 자동 판독 — 그림에서 격자선·막대를 재서 읽는다(`read_gnss_charts.py`).
                샛강문화다리 3센서 · 월드컵대교 3센서.
  · 눈 판독   — 사람이 눈금을 보고 옮겨 적은 값(`hangang_displacement_2024.json`).
                가양·원효·올림픽·천호·암사. 자동 판독이 무너지는 그림들이다.

어느 쪽으로 읽었는지 판마다 적는다 — 섞어 놓고 출처를 감추면 나중에 못 가린다.

    python scripts/make_report_monthly_figure.py

산출: docs/img/보고서_월별변위_전부.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams

for _f in ("Malgun Gothic", "맑은 고딕", "NanumGothic"):
    if any(_f in f.name for f in font_manager.fontManager.ttflist):
        rcParams["font.family"] = _f
        break
rcParams["axes.unicode_minus"] = False

NAVY = "#12314F"
DIM = "#55636F"
AUTO = "#2E6FB7"
EYE = "#B8860B"
YEAR_C = {2022: "#B7BEC6", 2023: "#6E7C8A", 2024: "#E8A33D"}


def from_auto(p: Path) -> list[dict]:
    if not p.exists():
        return []
    j = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for c in j.get("charts", []):
        if "monthly" not in c:
            continue
        out.append({"bridge": c["bridge"], "label": f"{c['sensor']} ({c['pos']})",
                    "page": c["page"], "quantity": f"GNSS {c['dir']}변위",
                    "series": {int(y): v for y, v in c["monthly"].items()},
                    "how": "자동 판독"})
    return out


def from_eye(p: Path) -> list[dict]:
    if not p.exists():
        return []
    j = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for name, rec in j.items():
        if name.startswith("_"):
            continue
        mon = rec.get("monthly") or {}
        ser: dict[int, list] = {}
        for k, arr in mon.items():
            if not isinstance(arr, list):
                continue
            try:
                yr = int(str(k)[-4:])
            except ValueError:
                continue
            ser.setdefault(yr, [None] * 12)
            for i, v in enumerate(arr[:12]):
                if v is not None:
                    ser[yr][i] = float(v)
        if not ser:
            continue
        out.append({"bridge": name, "label": rec.get("chart", "")[:28] or "본선",
                    "page": rec.get("page"), "quantity": rec.get("quantity") or "변위",
                    "series": ser, "how": "눈 판독",
                    "warn": rec.get("status") == "read_unreliable"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--eye", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--out", default="docs/img/보고서_월별변위_전부.png")
    a = ap.parse_args()

    rows = from_eye(Path(a.eye)) + from_auto(Path(a.auto))
    if not rows:
        print("그릴 것이 없다")
        return 2

    n = len(rows)
    ncol = 2
    nrow = (n + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(16.6, 2.55 * nrow))
    axes = np.atleast_1d(axes).ravel()
    mons = np.arange(1, 13)

    for ax, r in zip(axes, rows):
        ys = sorted(r["series"])
        wd = 0.82 / max(len(ys), 1)
        for j, y in enumerate(ys):
            v = [np.nan if q is None else q for q in r["series"][y]]
            ax.bar(mons + (j - (len(ys) - 1) / 2) * wd, v, width=wd * 0.9,
                   color=YEAR_C.get(y, "#888"), label=f"{y}", edgecolor="white", lw=.4)
        ax.axhline(0, color="#333", lw=1.0)
        ax.grid(axis="y", alpha=.22)
        ax.set_xticks(mons)
        ax.set_xticklabels([f"{m}M" for m in mons], fontsize=8.5)
        ax.tick_params(axis="y", labelsize=8.5)
        ax.set_ylabel("변위 [mm]", fontsize=9)
        c = AUTO if r["how"] == "자동 판독" else EYE
        # 같은 교량이 여러 판이면(샛강 3센서·월드컵 3센서) 어느 센서인지 안 적으면
        # 판이 구분되지 않는다.
        lab = r.get("label") or ""
        ttl = (f"{r['bridge']} · {r['quantity']} — p{r['page']}"
               + (f"  [{lab}]" if r["how"] == "자동 판독" and lab else ""))
        if r.get("warn"):
            ttl += "  (판독 신뢰도 낮음)"
        ax.set_title(ttl, fontsize=10.5, color=NAVY, pad=5)
        ax.text(0.995, 0.04, r["how"], transform=ax.transAxes, ha="right",
                fontsize=8.4, color=c,
                bbox=dict(fc="white", ec=c, lw=.8, alpha=.85, pad=2))
        ax.legend(fontsize=8, ncol=len(ys), loc="upper left", framealpha=.9)
    for ax in axes[n:]:
        ax.axis("off")

    n_auto = sum(1 for r in rows if r["how"] == "자동 판독")
    fig.suptitle("2024 한강교량 최종보고 — 보고서에 실린 월별 변위 그래프를 전부 다시 그린 것\n"
                 f"x축 1M~12M · 막대는 연도(2022·2023·2024) · "
                 f"자동 판독 {n_auto}판 · 눈 판독 {n - n_auto}판",
                 fontsize=14.5, fontweight="bold", y=0.995)
    fig.text(0.008, 0.004,
             "※ 값의 출처를 판마다 적었다(파란 = 그림에서 자동 판독, 갈색 = 사람이 눈금을 "
             "보고 옮겨 적음). 막대가 없는 달은 보고서에도 없다. "
             "표로 인쇄된 Min/Max 가 있는 교량은 그 표가 더 확실하다(월드컵 p30 · 샛강 p51).",
             fontsize=9, color=DIM)
    fig.tight_layout(rect=(0, 0.014, 1, 0.972))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=135)
    plt.close(fig)
    print("wrote", a.out)
    for r in rows:
        got = sum(1 for y in r["series"] for q in r["series"][y] if q is not None)
        print(f"  {r['bridge']:<12} p{str(r['page']):<4} {r['how']:<8} "
              f"{len(r['series'])}개년 · 값 {got}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
