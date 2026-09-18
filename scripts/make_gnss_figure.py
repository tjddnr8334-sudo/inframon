#!/usr/bin/env python3
"""보고서에 **인쇄된 GNSS 수치 전부**를 한 그림으로.

원본 쪽을 뽑아 대조하는 것(`make_gnss_evidence.py`)과는 다른 용도다. 이건 흩어져 있는
GNSS 수치를 한 판에 모아 "보고서가 GNSS 로 말하는 것이 이것뿐" 을 한눈에 보이게 한다.

보고서의 GNSS 실체
  · 계측항목에 GNSS O 인 교량 4개소(가양·월드컵·서강·샛강)
  · 그중 **수치가 인쇄된 곳은 2개소**뿐 — 월드컵(p30) · 샛강문화다리(p51)
  · 가양(p27)·서강(p33)은 항목에만 있고 그래프·표가 없다

    python scripts/make_gnss_figure.py

산출: docs/img/보고서_GNSS_수치.png
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
from matplotlib.patches import Patch

for _f in ("Malgun Gothic", "맑은 고딕", "NanumGothic"):
    if any(_f in f.name for f in font_manager.fontManager.ttflist):
        rcParams["font.family"] = _f
        break
rcParams["axes.unicode_minus"] = False

NAVY = "#12314F"
BLUE = "#2E6FB7"
SKY = "#8FB8DE"
RED = "#C8443C"
GREEN = "#2E9E6B"
ORANGE = "#D98324"
DIM = "#55636F"
GRAY = "#B9C4CE"

YEAR_C = {2022: "#9FC0DE", 2023: "#4E86BC", 2024: "#1B4F80"}


def panel_worldcup(ax, rec: dict) -> None:
    """월드컵대교 p30 — 센서 6개의 Min~Max 구간. 2024-01~11 한 해치."""
    bars = []
    for tb in rec.get("tables") or []:
        if not tb.get("rows"):
            continue
        d = "교축" if "교축방향" in tb["direction_in_report"] else "교축직각"
        for r in tb["rows"]:
            bars.append((f"{r['sensor']}", d, r["min"], r["max"], r["band"]))
    bars.reverse()
    y = np.arange(len(bars))
    for i, (sen, d, lo, hi, band) in enumerate(bars):
        c = BLUE if d == "교축" else ORANGE
        ax.plot([lo, hi], [i, i], lw=9, color=c, solid_capstyle="butt", alpha=.85)
        ax.plot(lo, i, "|", ms=14, mew=2.2, color=NAVY)
        ax.plot(hi, i, "|", ms=14, mew=2.2, color=RED)
        ax.text(hi + 1.6, i, f"변동폭 {band:.2f}", va="center", fontsize=9, color=DIM)
        ax.text(lo - 1.6, i, f"{lo:.2f}", va="center", ha="right", fontsize=8.6,
                color=NAVY)
    ax.set_yticks(y)
    ax.set_yticklabels([f"{s}  ({d})" for s, d, *_ in bars], fontsize=9.5)
    ax.axvline(0, color="#444", lw=1.0)
    ax.set_xlabel("변위량 [mm] — 막대의 좌·우 끝이 보고서의 Min·Max", fontsize=10)
    ax.set_title("월드컵대교 — 보고서 p30 · GNSS 교축·교축직각 변위량 (2024-01~11)",
                 fontsize=12.5, color=NAVY, pad=8)
    ax.grid(axis="x", alpha=.25)
    ax.set_xlim(-45, 88)
    ax.legend(handles=[Patch(color=BLUE, label="교축방향"),
                       Patch(color=ORANGE, label="교축직각방향")],
              fontsize=9.5, loc="lower right", framealpha=.95)


def panel_saetgang(ax, rec: dict, idx: int, title: str) -> None:
    """샛강문화다리 p51 — 위치×연도별 Min~Max. 2022·2023·2024 세 해치.

    표를 제목 문자열로 고르면 안 된다 — 'GNSS 교축, 교축직각방향 변위량 비교' 가
    '교축직각방향 변위량' 을 품고 있어 첫 표가 두 번 잡힌다(그래서 두 판이 똑같이
    그려졌다). 순서로 집는다.
    """
    cand = [t for t in rec.get("tables") or [] if t.get("groups")]
    tb = cand[idx] if idx < len(cand) else None
    if tb is None:
        ax.axis("off")
        return
    labels, i = [], 0
    for g in tb["groups"]:
        for r in g["rows"]:
            lo, hi = float(r["min"]), float(r["max"])
            if lo > hi:
                lo, hi = hi, lo
            c = YEAR_C.get(int(r["year"]), BLUE)
            ax.plot([lo, hi], [i, i], lw=8, color=c, solid_capstyle="butt")
            ax.plot(lo, i, "|", ms=12, mew=2.0, color=NAVY)
            ax.plot(hi, i, "|", ms=12, mew=2.0, color=RED)
            ax.text(hi + 0.5, i, f"{r['band']:.2f}", va="center", fontsize=8.4,
                    color=DIM)
            labels.append(f"{g['position']} · {r['year']}")
            i += 1
        i += 0.6
    ax.set_yticks(range(len(labels)) if len(labels) == i else
                  [k for k in range(int(i)) if k < len(labels)])
    yt, yl, k = [], [], 0
    for g in tb["groups"]:
        for r in g["rows"]:
            yt.append(k); yl.append(f"{g['position']} · {r['year']}")
            k += 1
        k += 0.6
    ax.set_yticks(yt)
    ax.set_yticklabels(yl, fontsize=9)
    ax.invert_yaxis()
    ax.axvline(0, color="#444", lw=1.0)
    ax.grid(axis="x", alpha=.25)
    ax.set_xlabel("변위량 [mm] · 막대 오른쪽 숫자 = 보고서의 변동폭", fontsize=9.5)
    ax.set_title(title, fontsize=12, color=NAVY, pad=8)
    ax.legend(handles=[Patch(color=YEAR_C[y], label=str(y)) for y in (2022, 2023, 2024)],
              fontsize=9, loc="lower right", ncol=3, framealpha=.95)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gnss", default="docs/bridges/hangang_gnss_2024.json")
    ap.add_argument("--out", default="docs/img/보고서_GNSS_수치.png")
    a = ap.parse_args()

    g = json.loads(Path(a.gnss).read_text(encoding="utf-8"))

    fig = plt.figure(figsize=(16.4, 11.2))
    gs = fig.add_gridspec(3, 2, height_ratios=[1.05, 1.25, 0.62],
                          hspace=0.42, wspace=0.30,
                          left=0.115, right=0.975, top=0.905, bottom=0.055)

    panel_worldcup(fig.add_subplot(gs[0, :]), g.get("월드컵대교") or {})
    panel_saetgang(fig.add_subplot(gs[1, 0]), g.get("샛강문화다리") or {}, 0,
                   "샛강문화다리 — p51 상단 · GNSS 교축방향 (2022~2024)")
    panel_saetgang(fig.add_subplot(gs[1, 1]), g.get("샛강문화다리") or {}, 1,
                   "샛강문화다리 — p51 하단 · GNSS 교축직각방향 (2022~2024)")

    # 아래 — 보고서의 GNSS 실체를 표로
    ax = fig.add_subplot(gs[2, :]); ax.axis("off")
    rows = [("가양대교", "p27", "감시항목 1순위 O", "그래프·수치표 **없음**", DIM),
            ("월드컵대교", "p29 · p30", "감시항목 2순위 O",
             "p30 에 Min/Max/변동폭 6칸 — 위 그림", GREEN),
            ("서강대교", "p33", "항목 O", "본문은 케이블장력뿐 · 수치표 없음", DIM),
            ("샛강문화다리", "p50 · p51", "항목 O",
             "p51 에 위치×연도 Min/Max/변동폭 — 위 그림", GREEN)]
    ax.text(0.0, 1.02, "보고서가 GNSS 로 말하는 것은 이것뿐 — 16개소 중 4개소에 설치,"
                       " 그중 수치가 인쇄된 곳은 2개소",
            fontsize=13, color=NAVY, fontweight="bold", va="top")
    xs = [0.0, 0.135, 0.235, 0.40]
    for j, h in enumerate(["교량", "쪽", "계측항목 표", "본문에 실린 GNSS"]):
        ax.text(xs[j], 0.80, h, fontsize=10, color="white", va="center",
                bbox=dict(fc=NAVY, ec="none", pad=3.5))
    for i, (nm, pg, item, body, col) in enumerate(rows):
        y = 0.62 - i * 0.175
        for j, v in enumerate([nm, pg, item, body.replace("**", "")]):
            ax.text(xs[j], y, v, fontsize=10.5, color=(col if j == 3 else NAVY),
                    fontweight=("bold" if j in (0, 3) else "normal"), va="center")
    ax.text(0.0, -0.10,
            "※ p16 에 'GNSS 데이터 연계(행주대교 2개소 등 17개소)' 가 따로 있다 — "
            "계측항목 표의 설치(O)와는 다른 층위로 보여 수치로 쓰지 않았다(확인 필요).",
            fontsize=9.6, color=ORANGE, va="center")
    ax.set_xlim(0, 1); ax.set_ylim(-0.2, 1.1)

    fig.suptitle("2024 한강교량 최종보고 — 보고서에 인쇄된 GNSS 수치 전부\n"
                 "막대 = Min~Max 구간 · 좌 끝(남색)=Min · 우 끝(붉은색)=Max · "
                 "숫자는 보고서가 적은 변동폭",
                 fontsize=15.5, fontweight="bold", y=0.978)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=140)
    plt.close(fig)
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
