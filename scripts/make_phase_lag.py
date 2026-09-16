#!/usr/bin/env python3
"""연주기 성분의 **위상차**를 잰다 — "계측과 위성이 완전히 반대다" 를 숫자로.

연주기 성분 그림에서 가양·원효·올림픽·샛강은 두 곡선이 거울처럼 뒤집혀 있다.
R² 는 |r| 을 쓰기 때문에 반대여도 높게 나온다 — 그래서 R² 만으로는 이걸 못 잡는다.
위상차를 월 단위로 재면 잡힌다.

    y = c0 + c1·t + A·sin(2πt) + B·cos(2πt)
    위상 = atan2(A, B) · 두 위상의 차를 −6~+6 개월로 접는다.

0 개월이면 같이 오르내리고, ±6 개월이면 정반대다. 반대라는 사실 자체가 오류는
아니다 — 계측량의 부호 규약(처짐은 아래가 양수인 경우가 많다)과 위성 LOS(위성
쪽으로 오면 양수)가 반대이면 **반대로 나오는 것이 정상**이다. 다만 여섯 교량이
한 방향으로 반대가 아니라 넷은 반대, 둘은 같은 위상이므로 규약 하나로는 설명이
안 된다. 그래서 여기서는 재서 보여 주기만 하고 결론을 내지 않는다.

    python scripts/make_phase_lag.py

산출: docs/img/value/연주기_위상차.png · docs/bridges/phase_lag.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from kaia_theme import MPL, use_mpl_style

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_r2_methods import ym                                         # noqa: E402
from make_trend_agree import load_points, report_series                # noqa: E402

# 발표자료(KAIA 톤)와 같은 서체·색을 쓴다 — 한 장에 붙였을 때 따로 놀지 않게.
use_mpl_style()

NAVY = MPL["ink"]
DIM = MPL["gray"]
RED = MPL["red"]
GREEN = MPL["green"]


def fit(t: np.ndarray, v: np.ndarray) -> np.ndarray:
    A = np.vstack([np.ones_like(t), t, np.sin(2 * np.pi * t),
                   np.cos(2 * np.pi * t)]).T
    c, *_ = np.linalg.lstsq(A, v, rcond=None)
    return c


def point_rs(auto: dict, eye: dict, root: Path, nm: str):
    """교량 위 **모든** 점에 대해 연주기 성분의 부호 있는 상관계수를 낸다.

    "가장 닮은 점" 하나만 보면 그 점이 왜 뽑혔는지 알 수 없다. 점들이 어떤 값을
    갖는지 전부 펼쳐 놓으면, 고르기가 무엇을 하고 있는지가 한눈에 보인다.
    """
    rp = report_series(auto, eye, nm)
    pts = load_points(root / nm)
    if rp is None or pts is None:
        return None
    tr, vr, _what, _how = rp
    los, ti, _st, _mem, _inc = pts
    lo, hi = float(tr.min()), float(tr.max())
    sel = (ti >= lo - 0.12) & (ti <= hi + 0.12)
    kr, ki = ym(tr), ym(ti[sel])
    common = sorted(set(kr) & set(ki))
    if len(common) < 6:
        return None
    ridx = {k: i for i, k in enumerate(kr)}
    X = np.vstack([los[:, sel][:, [i for i, k in enumerate(ki) if k == c]
                               ].mean(axis=1) for c in common]).T
    y = np.asarray([vr[ridx[c]] for c in common], float)
    t = np.asarray([c[0] + (c[1] - .5) / 12 for c in common])
    A = np.vstack([np.ones_like(t), t, np.sin(2 * np.pi * t),
                   np.cos(2 * np.pi * t)]).T

    def ann(v):
        c, *_ = np.linalg.lstsq(A, v, rcond=None)
        return A @ c

    ya = ann(y)
    out = []
    for k in range(X.shape[0]):
        xa = ann(X[k])
        if np.std(xa) > 1e-9 and np.std(ya) > 1e-9:
            out.append(float(np.corrcoef(xa, ya)[0, 1]))
    return np.asarray(out)


def spread_figure(rows: list, out: Path, auto: dict, eye: dict, root: Path) -> None:
    """점마다의 상관계수를 전부 펼쳐 놓는다 — 고르기가 무엇을 만들어 내는지.

    한 교량 위에 r 이 +1 에 가까운 점과 −1 에 가까운 점이 **둘 다** 있으면,
    "가장 닮은 점" 은 자료가 말해 주는 것이 아니라 고르는 사람이 정하는 것이다.
    """
    rng = np.random.default_rng(3)
    fig, ax = plt.subplots(figsize=(11.6, 5.9))
    names = [d["name"] for d in rows]
    for i, nm in enumerate(names):
        rs = point_rs(auto, eye, root, nm)
        if rs is None or not len(rs):
            continue
        ax.scatter(rs, i + rng.uniform(-0.17, 0.17, len(rs)), s=13,
                   color=MPL["blue"], edgecolors="none", alpha=0.75, zorder=2)
        med = float(np.median(rs))
        ax.plot([med, med], [i - 0.28, i + 0.28], color=MPL["slate"], lw=2.2, zorder=4)
        k = int(np.argmax(np.abs(rs)))
        ax.scatter([rs[k]], [i], s=155, marker="*", color=RED,
                   edgecolors=MPL["red"], linewidths=0.6, zorder=5)
        kp = int(np.argmax(rs))
        ax.scatter([rs[kp]], [i], s=145, marker="*", color=GREEN,
                   edgecolors=MPL["green"], linewidths=0.6, zorder=5)
        ax.text(1.06, i, f"점 {len(rs)}개 · 중앙 {med:+.2f}", fontsize=9.4,
                va="center", color=NAVY)
    ax.axvline(0, color=MPL["slate"], lw=1.0)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=10.5)
    ax.set_xlim(-1.12, 1.12)
    ax.set_xlabel("연주기 성분의 상관계수 r  ·  현장 계측 ↔ 그 PS 점", fontsize=11,
                  color=NAVY)
    ax.grid(axis="x", alpha=.25)
    # 맑은 고딕에 U+2248(≈)이 없다 — 네모로 찍히므로 말로 쓴다.
    ax.set_title("한 교량 안에 r 이 +1 에 가까운 점도, -1 에 가까운 점도 있다 — "
                 "'가장 닮은 점'은 자료가 아니라 고르기가 정한다",
                 fontsize=13.5, fontweight="bold", color=NAVY, pad=10)
    ax.scatter([], [], s=145, marker="*", color=RED, label="|r| 이 가장 큰 점(지금까지 쓰던 방식)")
    ax.scatter([], [], s=145, marker="*", color=GREEN, label="r 이 가장 큰 점(같은 방향)")
    ax.plot([], [], color=MPL["slate"], lw=2.2, label="점들의 중앙값")
    # 범례를 그림 안에 두면 점을 덮는다 — 축 아래로 내린다.
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.135), ncol=3,
              fontsize=9.4, frameon=False)
    fig.text(0.006, 0.012,
             "※ 회색 점 하나가 교량 위 PS 점 하나다. 중앙값이 0 근처라는 것은 "
             "'평균적으로는 아무 관계가 없다'는 뜻이다.\n"
             "   그런데 점이 45~237개나 되고 맞출 대상은 12개월짜리 매끈한 곡선 "
             "하나뿐이라, 그중에는 우연히 꼭 맞는 점도, 꼭 반대인 점도 반드시 있다.\n"
             "   앞 장에서 '정반대'로 보인 것은 교량이 반대로 움직여서가 아니라 "
             "R² 가 |r| 을 쓰는 바람에 반대인 점이 뽑혔기 때문이다.",
             fontsize=9.2, color=DIM)
    fig.tight_layout(rect=(0, 0.155, 1, 1))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--eye", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--r2", default="docs/bridges/r2_methods.json")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/value/연주기_위상차.png")
    ap.add_argument("--json-out", default="docs/bridges/phase_lag.json")
    a = ap.parse_args()

    auto = json.loads(Path(a.auto).read_text(encoding="utf-8"))
    eye = json.loads(Path(a.eye).read_text(encoding="utf-8"))
    J = json.loads(Path(a.r2).read_text(encoding="utf-8"))

    rows = []
    for r in J["bridges"]:
        nm = r["name"]
        j = r["methods"]["연주기 성분"]["best_point"]
        rp = report_series(auto, eye, nm)
        pts = load_points(Path(a.root) / nm)
        if rp is None or pts is None or j < 0:
            continue
        tr, vr, what, _how = rp
        los, ti, _st, _mem, _inc = pts
        lo, hi = float(tr.min()), float(tr.max())
        sel = (ti >= lo - 0.12) & (ti <= hi + 0.12)
        kr, ki = ym(tr), ym(ti[sel])
        common = sorted(set(kr) & set(ki))
        if len(common) < 6:
            continue
        ridx = {k: i for i, k in enumerate(kr)}
        X = np.vstack([los[:, sel][:, [i for i, k in enumerate(ki) if k == c]
                                   ].mean(axis=1) for c in common]).T
        y = np.asarray([vr[ridx[c]] for c in common], float)
        t = np.asarray([c[0] + (c[1] - .5) / 12 for c in common])
        cr, ci = fit(t, y), fit(t, X[j])
        A = np.vstack([np.ones_like(t), t, np.sin(2 * np.pi * t),
                       np.cos(2 * np.pi * t)]).T
        lag = (((np.arctan2(cr[2], cr[3]) - np.arctan2(ci[2], ci[3]))
                / (2 * np.pi)) * 12 + 6) % 12 - 6
        rows.append({
            "name": nm, "what": what, "n_months": len(common), "point": int(j),
            "amp_meas": round(float(np.hypot(cr[2], cr[3])), 2),
            "amp_insar": round(float(np.hypot(ci[2], ci[3])), 2),
            "lag_months": round(float(lag), 2),
            "r_signed": round(float(np.corrcoef(A @ cr, A @ ci)[0, 1]), 3)})

    rows.sort(key=lambda d: abs(d["lag_months"]))
    spread_figure(rows, Path(a.out).with_name("연주기_점별상관_분포.png"),
                  auto, eye, Path(a.root))
    fig, ax = plt.subplots(figsize=(11.2, 5.1))
    k = np.arange(len(rows))
    lag = [d["lag_months"] for d in rows]
    col = [GREEN if abs(v) <= 2.0 else RED for v in lag]
    ax.barh(k, lag, color=col, height=0.58, edgecolor=MPL["slate"], linewidth=0.5)
    ax.axvline(0, color=MPL["slate"], lw=1.1)
    for s in (-6, 6):
        ax.axvline(s, color=RED, lw=1.2, ls="--")
    ax.set_ylim(-0.75, len(rows) - 0.1)
    ax.text(6, -0.55, " 정반대", color=RED, fontsize=10, va="center")
    ax.text(0.12, -0.55, " 같은 위상", color=GREEN, fontsize=10, va="center")
    ax.set_yticks(k)
    ax.set_yticklabels([f"{d['name']}\n{d['what'][:16]}" for d in rows], fontsize=9.6)
    ax.set_xlim(-7.2, 7.2)
    ax.set_xticks(range(-6, 7, 2))
    # 맑은 고딕에 U+2212(−)가 없다 — 빼기 기호는 ASCII 하이픈으로 쓴다.
    ax.set_xlabel("연주기 위상차 [개월]  ·  현장 계측 - InSAR", fontsize=11, color=NAVY)
    ax.grid(axis="x", alpha=.25)
    for i, d in enumerate(rows):
        x = d["lag_months"]
        ax.text(x + (0.25 if x >= 0 else -0.25), i,
                f"{x:+.1f}개월 · r {d['r_signed']:+.2f}",
                fontsize=9.3, va="center",
                ha="left" if x >= 0 else "right", color=NAVY)
    ax.set_title("연주기 성분은 얼마나 어긋나 있나 — 넷은 정반대, 둘은 같은 위상",
                 fontsize=14, fontweight="bold", color=NAVY, pad=10)
    fig.text(0.006, 0.012,
             "※ R² 는 상관계수의 제곱이라 부호를 버린다 — 정반대로 움직여도 1.00 이 나온다."
             " 위상차를 재야 이게 잡힌다.\n"
             "   반대라는 사실 자체가 오류는 아니다. 처짐은 아래로 휘는 쪽을 양수로 적는 일이"
             " 많고, 위성 LOS 는 위성 쪽으로 오는 쪽이 양수다 — 규약이 반대면 반대로 나오는"
             " 것이 정상이다.\n"
             "   다만 여섯이 한 방향으로 반대가 아니라 넷만 반대이므로 규약 하나로는 설명되지"
             " 않는다. 계측 부호 규약 확인이 먼저다.",
             fontsize=9, color=DIM)
    fig.tight_layout(rect=(0, 0.125, 1, 1))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("wrote", out)

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "연주기 성분의 위상차(개월) — 0 이면 같은 위상, ±6 이면 정반대",
         "_주의": "R² 는 부호를 버리므로 정반대도 높게 나온다. 위상차를 같이 봐야 한다",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    for d in rows:
        print(f"{d['name']:<12}{d['lag_months']:+7.2f}개월  r {d['r_signed']:+.2f}  "
              f"진폭 계측 {d['amp_meas']:.1f} / InSAR {d['amp_insar']:.1f} mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
