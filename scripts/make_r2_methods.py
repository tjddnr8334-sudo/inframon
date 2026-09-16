#!/usr/bin/env python3
"""R² 를 올리는 방법들과 **각각의 우연 기준선** — 0.6 을 넘는 게 무슨 뜻인지.

"0.6 이상 나오는 게 없냐" 는 물음에 답하려면 두 가지를 같이 내야 한다.
  ① 그 방법으로 실제 얼마가 나오는가
  ② **아무 상관이 없어도** 그 방법으로 얼마가 나오는가(우연 기준선)

②를 빼면 숫자를 올리는 것은 얼마든지 가능하다. 평활하면 오르고, 누적하면 오르고,
연주기만 남기면 더 오른다 — 신호가 매끄러워질수록 두 곡선은 닮아 보이기 때문이다.
그건 일치가 아니라 **지표의 성질**이다.

네 가지를 같은 자료에 돌려 나란히 놓는다.
  원본        월값 그대로
  3개월 평활  이동평균 — 잡음을 줄인다
  연주기 성분 직선+사인/코사인을 맞춘 뒤 그 곡선끼리
  누적        월값을 누적합

우연 기준선은 **위성 쪽 월값을 무작위로 섞어** 같은 계산을 200회 돌려 얻는다(95 백분위).
섞으면 시간 구조가 사라지므로, 거기서 나오는 값이 그 방법의 '공짜 R²' 다.

    python scripts/make_r2_methods.py

산출: docs/img/R2_방법별_우연기준선.png · docs/bridges/r2_methods.json
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
from matplotlib import font_manager, rcParams

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_trend_agree import load_points, report_series                 # noqa: E402

for _f in ("Malgun Gothic", "맑은 고딕", "NanumGothic"):
    if any(_f in f.name for f in font_manager.fontManager.ttflist):
        rcParams["font.family"] = _f
        break
rcParams["axes.unicode_minus"] = False

NAVY = "#12314F"
DIM = "#55636F"
GREEN = "#2E9E6B"
RED = "#C8443C"
GRAY = "#B7C1CB"
METHODS = ["원본", "3개월 평활", "연주기 성분", "누적"]


def ym(t):
    y = np.floor(t).astype(int)
    return list(zip(y, np.clip(((t - y) * 12).astype(int) + 1, 1, 12)))


def annual(t, y):
    A = np.vstack([np.ones_like(t), t, np.sin(2 * np.pi * t),
                   np.cos(2 * np.pi * t)]).T
    c, *_ = np.linalg.lstsq(A, y, rcond=None)
    return A @ c


def transform(name: str, t: np.ndarray, v: np.ndarray) -> np.ndarray:
    if name == "원본":
        return v
    if name == "3개월 평활":
        return np.convolve(v, np.ones(3) / 3, mode="same")
    if name == "연주기 성분":
        return annual(t, v)
    return np.cumsum(v - v.mean())


def best_r2(X: np.ndarray, y: np.ndarray, t: np.ndarray, name: str,
            want_idx: bool = False):
    """점들 중 최대 R² — 지금까지 써 온 방식 그대로. want_idx 면 그 점 번호까지."""
    yy = transform(name, t, y)
    if np.std(yy) < 1e-9:
        return (float("nan"), -1) if want_idx else float("nan")
    out, jbest = 0.0, -1
    for j in range(X.shape[0]):
        xx = transform(name, t, X[j])
        if np.std(xx) < 1e-9:
            continue
        r = abs(float(np.corrcoef(xx, yy)[0, 1]))
        if r > out:
            out, jbest = r, j
    return (out ** 2, jbest) if want_idx else out ** 2


def method_figure(name: str, data: list, out: Path) -> None:
    """방법 하나를 교량마다 한 칸씩 — 변환한 두 곡선을 겹쳐 본다.

    숫자만 놓으면 '왜 오르는지' 가 안 보인다. 평활·연주기·누적이 신호를 어떻게
    매끄럽게 만드는지 눈으로 보이면, 우연 기준선이 왜 같이 오르는지도 보인다.
    """
    n = len(data)
    ncol = 3
    nrow = (n + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(15.6, 3.2 * nrow))
    axes = np.atleast_1d(axes).ravel()
    for ax, (nm, what, t, X, y, rec) in zip(axes, data):
        m = rec["methods"][name]
        j = m.get("best_point", -1)
        yy = transform(name, t, y)
        xx = transform(name, t, X[j]) if j >= 0 else None
        # (c%1)*12 = m-0.5 이므로 floor 로 내려야 m 이 나온다. round 를 쓰면
        # 은행가 반올림 때문에 8월·9월이 같은 이름으로 찍힌다.
        lab = [f"{int(c)}-{int(np.floor((c % 1) * 12)) + 1:02d}" for c in t]
        k = np.arange(len(t))
        ax.plot(k, yy, "-o", ms=3.4, lw=1.8, color=RED, label="현장 계측")
        ax.set_ylabel("계측", color=RED, fontsize=9)
        ax.tick_params(axis="y", colors=RED, labelsize=8.2)
        a2 = ax.twinx()
        if xx is not None:
            a2.plot(k, xx, "-o", ms=3.4, lw=1.8, color="#2E6FB7",
                    label=f"InSAR P{j:02d}")
        a2.set_ylabel("InSAR", color="#2E6FB7", fontsize=9)
        a2.tick_params(axis="y", colors="#2E6FB7", labelsize=8.2)
        step = max(1, len(k) // 8)
        ax.set_xticks(k[::step])
        ax.set_xticklabels(lab[::step], fontsize=7.6, rotation=45)
        ax.grid(alpha=.2)
        beat = m["beats_chance"]
        ax.set_title(f"{nm} — {what[:18]}\n"
                     f"R² {m['observed_r2']:.2f}  ·  우연 {m['chance_r2_p95']:.2f}  ·  "
                     + ("우연을 넘는다" if beat else "우연 안"),
                     fontsize=9.6, color=(GREEN if beat else RED), pad=6)
    for ax in axes[n:]:
        ax.axis("off")
    fig.suptitle(f"{name} — 변환한 두 곡선을 겹쳐 본다\n"
                 "붉은색 = 현장 계측 · 파란색 = 가장 닮은 PS 점 (각자 축)",
                 fontsize=14.5, fontweight="bold", y=0.995)
    fig.text(0.006, 0.006,
             "※ 제목의 '우연' 은 위성 월값을 무작위로 섞어 같은 계산을 200회 돌린 값의 "
             "95 백분위다. 신호가 매끄러울수록 R² 도 우연도 함께 오른다 — 그래서 R² 만 "
             "보고 좋다 나쁘다 말할 수 없다.", fontsize=9, color=DIM)
    fig.tight_layout(rect=(0, 0.018, 1, 0.955))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--eye", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/R2_방법별_우연기준선.png")
    ap.add_argument("--json-out", default="docs/bridges/r2_methods.json")
    ap.add_argument("--perm", type=int, default=200)
    a = ap.parse_args()

    auto = json.loads(Path(a.auto).read_text(encoding="utf-8")) \
        if Path(a.auto).exists() else {}
    eye = json.loads(Path(a.eye).read_text(encoding="utf-8")) \
        if Path(a.eye).exists() else {}
    rng = np.random.default_rng(11)

    names = ["가양대교", "원효대교", "올림픽대교", "샛강문화다리", "암사대교",
             "월드컵대교"]
    rows, keep = [], []
    for nm in names:
        rp = report_series(auto, eye, nm)
        pts = load_points(Path(a.root) / nm)
        if rp is None or pts is None:
            continue
        tr, vr, what, how = rp
        los, ti, st, mem, inc = pts
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

        rec = {"name": nm, "what": what, "n_months": len(common),
               "n_points": int(X.shape[0]), "methods": {}}
        for mname in METHODS:
            obs, jb = best_r2(X, y, t, mname, want_idx=True)
            null = []
            for _ in range(a.perm):
                Xp = X[:, rng.permutation(len(common))]
                null.append(best_r2(Xp, y, t, mname))
            n95 = float(np.nanpercentile(null, 95))
            rec["methods"][mname] = {
                "observed_r2": round(float(obs), 3),
                "chance_r2_p95": round(n95, 3),
                "beats_chance": bool(obs > n95), "best_point": int(jb)}
        rows.append(rec)
        keep.append((nm, what, t, X, y, rec))
        print(f"{nm:<12} " + "  ".join(
            f"{m}: {rec['methods'][m]['observed_r2']:.2f}"
            f"/{rec['methods'][m]['chance_r2_p95']:.2f}" for m in METHODS))

    n = len(rows)
    fig, axes = plt.subplots(1, n, figsize=(3.3 * n, 4.9), sharey=True)
    axes = np.atleast_1d(axes)
    xs = np.arange(len(METHODS))
    for ax, r in zip(axes, rows):
        obs = [r["methods"][m]["observed_r2"] for m in METHODS]
        chc = [r["methods"][m]["chance_r2_p95"] for m in METHODS]
        ax.bar(xs - 0.19, obs, width=0.36, color=GREEN, label="실제")
        ax.bar(xs + 0.19, chc, width=0.36, color=GRAY, label="우연 95%")
        ax.axhline(0.6, color=RED, lw=1.8, ls="--")
        ax.set_xticks(xs)
        ax.set_xticklabels(METHODS, fontsize=8.6, rotation=20)
        ax.set_ylim(0, 1.05)
        ax.grid(axis="y", alpha=.22)
        ax.set_title(f"{r['name']}\n{r['what'][:14]} · 짝 {r['n_months']}개월",
                     fontsize=10, color=NAVY, pad=6)
        ax.tick_params(labelsize=8.5)
    axes[0].set_ylabel("R²", fontsize=11)
    axes[0].legend(fontsize=8.5, framealpha=.9, loc="upper left")
    fig.suptitle("R² 를 올리는 방법들과 우연 기준선 — 붉은 점선이 0.6\n"
                 "초록(실제)이 회색(우연)보다 높아야 의미가 있다",
                 fontsize=14.5, fontweight="bold", y=0.985)
    fig.text(0.006, 0.006,
             "※ 우연 기준선 = 위성 월값을 무작위로 섞어 같은 계산을 200회 돌린 값의 95 백분위. "
             "신호를 매끄럽게 할수록(평활·연주기·누적) 두 곡선은 닮아 보여 R² 가 오르는데, "
             "우연 기준선도 같이 오른다 — 그래서 '0.6 을 넘었다' 만으로는 아무 말도 못 한다.",
             fontsize=9, color=DIM)
    fig.tight_layout(rect=(0, 0.02, 1, 0.93))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=140)
    plt.close(fig)
    print("wrote", a.out)

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "R² 를 올리는 방법별 실제값과 우연 기준선",
         "_우연기준선": "위성 월값을 무작위로 섞어 같은 계산을 200회 — 95 백분위",
         "_경고": "평활·연주기·누적은 R² 를 올리지만 우연 기준선도 같이 올린다. "
                "'0.6 을 넘겼다' 는 것만으로는 일치의 근거가 되지 않는다.",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    for mname in METHODS:
        method_figure(mname, keep,
                      Path(a.out).parent / f"R2_방법_{mname.replace(' ', '')}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
