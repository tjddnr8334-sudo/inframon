#!/usr/bin/env python3
"""추세가 **몇 % 비슷한가** — 신뢰구간 말고 값끼리의 거리로.

앞의 `make_trend_match_points.py` 는 통계적 판정(신뢰구간)으로 걸러 0개가 나왔다.
그건 "같다고 **말할 수 있느냐**" 의 답이고, "값이 **얼마나 가깝냐**" 는 다른 질문이다.
여기서는 후자를 센다.

  유사도 = 1 − |b_점 − b_보고서| / |b_보고서|

  · 1.00 이면 값이 똑같다. 0.60 이면 보고서 값의 40 % 안쪽으로 들어온다.
  · 보고서보다 반대 부호로 크게 벗어나면 음수가 되고, 0 아래는 0 으로 자른다.

**이 숫자를 '검증' 으로 읽으면 안 된다.** 신뢰구간을 보지 않으므로, 점 추세가
±9 mm/yr 로 흔들려도 우연히 가까우면 90 % 가 나온다. 그래서 같은 그림에 **우연히
그만큼 가까울 점이 몇 개나 되는지**(보고서 값을 뒤집거나 섞었을 때)도 같이 낸다.

    python scripts/make_trend_similarity.py

산출: docs/img/추세유사도.png · docs/bridges/trend_similarity.json
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
from make_trend_agree import load_points, report_series, slope_ci   # noqa: E402

for _f in ("Malgun Gothic", "맑은 고딕", "NanumGothic"):
    if any(_f in f.name for f in font_manager.fontManager.ttflist):
        rcParams["font.family"] = _f
        break
rcParams["axes.unicode_minus"] = False

RED = "#C8443C"
BLUE = "#2E6FB7"
GREEN = "#2E9E6B"
ORANGE = "#D98324"
GRAY = "#B7C1CB"
DIM = "#55636F"
NAVY = "#12314F"
NOT_VERTICAL = {"신축변위"}
LEVELS = (0.5, 0.6, 0.7, 0.8, 0.9)


def similarity(b_pt: np.ndarray, b_rep: float) -> np.ndarray:
    """1 − 상대오차. 0 아래는 0 으로 자른다(반대 부호로 크게 벗어난 경우)."""
    if abs(b_rep) < 1e-9:
        return np.full_like(b_pt, np.nan)
    return np.clip(1.0 - np.abs(b_pt - b_rep) / abs(b_rep), 0.0, 1.0)


def line_figure(keep, out: Path) -> None:
    """**추세선끼리** 겹쳐 본다 — 막대 개수 말고 선이 얼마나 나란한가.

    같은 창에서 각 직선을 창 가운데 기준으로 0 에 맞춰 그린다. 기울기만 남으므로
    선이 나란하면 추세가 같은 것이고, 벌어지면 다른 것이다.
    """
    n = len(keep)
    ncol = 2
    nrow = (n + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(15.6, 3.1 * nrow))
    axes = np.atleast_1d(axes).ravel()
    for ax, (nm, st, b_pt, ci_pt, sim, sim_flip, b_rep, ci_rep, what, how, r,
             tt, LL, cosv) in zip(axes, keep):
        t0, tm = float(tt.min()), float(tt.mean())
        xs = np.linspace(tt.min(), tt.max(), 40)
        good = np.isfinite(b_pt) & np.isfinite(sim)
        for j in np.where(good & (sim < 0.6))[0]:
            ax.plot(xs, b_pt[j] * (xs - tm), "-", lw=.7, color=GRAY, alpha=.35)
        order = np.where(good & (sim >= 0.6))[0]
        order = order[np.argsort(-sim[order])]
        for k, j in enumerate(order[:8]):
            lbl = (f"교축 {st[j]:.0f} m · {sim[j]:.0%}"
                   if np.isfinite(st[j]) else f"{sim[j]:.0%}")
            ax.plot(xs, b_pt[j] * (xs - tm), "-", lw=1.9, color=GREEN,
                    alpha=.9 - .06 * k, label=lbl if k < 4 else None)
        ax.plot(xs, b_rep * (xs - tm), "-", lw=3.4, color=RED,
                label=f"보고서 {b_rep:+.2f} mm/yr", zorder=6)
        ax.axhline(0, color="#999", lw=.8)
        ax.grid(alpha=.2)
        ax.set_xlabel("연", fontsize=9)
        ax.set_ylabel(("연직 변위 [mm]" if r["vertical"] else "LOS 변위 [mm]"),
                      fontsize=9)
        ax.set_title(f"{nm} — 추세선 {len(order)}개가 60% 이상 나란하다"
                     + ("   ※ 창이 짧다" if r["short"] else ""),
                     fontsize=10, color=NAVY, pad=6)
        if len(order):
            ax.legend(fontsize=7.8, framealpha=.9, loc="best")
        else:
            ax.legend(fontsize=8, framealpha=.9, loc="best")
        ax.tick_params(labelsize=8.5)
    for ax in axes[len(keep):]:
        ax.axis("off")
    fig.suptitle("추세선끼리 겹쳐 보기 — 얼마나 나란한가\n"
                 "붉은 굵은 선 = 보고서 추세 · 초록 = 60% 이상 비슷한 점 · "
                 "회색 = 나머지 점 (모두 창 가운데에서 0 으로 맞춤)",
                 fontsize=14.5, fontweight="bold", y=0.995)
    fig.text(0.006, 0.004,
             "※ 기울기만 보려고 각 직선을 창 가운데에서 0 으로 맞췄다. 선이 나란하면 추세가 "
             "같은 것이고 벌어지면 다르다. 회색 다발이 붉은 선을 넓게 감싸고 있으면, "
             "초록 선이 나란한 것은 '점 추세 분포가 넓어서' 일 수 있다.",
             fontsize=8.8, color=DIM)
    fig.tight_layout(rect=(0, 0.014, 1, 0.955))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=135)
    plt.close(fig)
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--eye", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/추세유사도.png")
    ap.add_argument("--json-out", default="docs/bridges/trend_similarity.json")
    ap.add_argument("--line-out", default="docs/img/추세선_비교.png")
    a = ap.parse_args()

    auto = json.loads(Path(a.auto).read_text(encoding="utf-8")) \
        if Path(a.auto).exists() else {}
    eye = json.loads(Path(a.eye).read_text(encoding="utf-8")) \
        if Path(a.eye).exists() else {}

    names = ["가양대교", "원효대교", "올림픽대교", "샛강문화다리", "암사대교",
             "천호대교", "월드컵대교"]
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
        if sel.sum() < 5:
            continue
        b_rep, ci_rep = slope_ci(tr, vr[None, :])
        b_rep, ci_rep = float(b_rep[0]), float(ci_rep[0])
        b_los, ci_los = slope_ci(ti[sel], los[:, sel])
        vertical = not any(k in (what or "") for k in NOT_VERTICAL)
        cosv = float(np.cos(np.radians(inc)))
        b_pt = b_los / cosv if vertical else b_los
        ci_pt = ci_los / cosv if vertical else ci_los

        sim = similarity(b_pt, b_rep)
        ok = np.isfinite(sim)
        cnt = {f"{int(L * 100)}%": int((ok & (sim >= L)).sum()) for L in LEVELS}
        # 우연 기준선 — 보고서 추세의 **부호를 뒤집어** 같은 셈을 한다. 값의 분포는
        # 그대로인데 맞을 이유가 없어지므로, 여기서 비슷하게 나오면 그 유사도는
        # '가깝다' 가 아니라 '분포가 넓다' 는 뜻이다.
        sim_flip = similarity(b_pt, -b_rep)
        cnt_flip = {f"{int(L * 100)}%": int((np.isfinite(sim_flip)
                                             & (sim_flip >= L)).sum())
                    for L in LEVELS}
        i60 = np.where(ok & (sim >= 0.6))[0]
        order = i60[np.argsort(-sim[i60])]
        rows.append({
            "name": nm, "what": what, "how": how, "vertical": vertical,
            "years": round(hi - lo, 2), "n_epochs": int(sel.sum()),
            "n_points": int(los.shape[0]),
            "report_slope": round(b_rep, 3), "report_ci": round(ci_rep, 3),
            "count_at": cnt, "count_at_flipped": cnt_flip,
            "best_similarity": (None if not ok.any()
                                else round(float(np.nanmax(sim)), 3)),
            "top60": [{"station_m": (None if not np.isfinite(st[j])
                                     else round(float(st[j]), 1)),
                       "slope": round(float(b_pt[j]), 2),
                       "ci": round(float(ci_pt[j]), 2),
                       "similarity": round(float(sim[j]), 3)}
                      for j in order[:12]],
            "short": bool((hi - lo) < 1.5 or int(sel.sum()) < 8)})
        keep.append((nm, st, b_pt, ci_pt, sim, sim_flip, b_rep, ci_rep, what, how,
                     rows[-1], ti[sel], los[:, sel], cosv))

    n = len(keep)
    fig, axes = plt.subplots(n, 2, figsize=(15.6, 2.75 * n),
                             gridspec_kw={"width_ratios": [1.5, 1]})
    axes = np.atleast_2d(axes)
    for i, (nm, st, b_pt, ci_pt, sim, sim_flip, b_rep, ci_rep, what, how,
            r, _tt, _LL, _cos) in enumerate(keep):
        ax, bx = axes[i]
        good = np.isfinite(b_pt) & np.isfinite(st) & np.isfinite(sim)
        hit = good & (sim >= 0.6)
        for frac, col in ((0.4, "#FDECEA"), (0.2, "#F8D7D3")):
            ax.axhspan(b_rep - frac * abs(b_rep), b_rep + frac * abs(b_rep),
                       color=col, zorder=0)
        ax.axhline(b_rep, color=RED, lw=2.0, zorder=1)
        ax.plot(st[good & ~hit], b_pt[good & ~hit], "o", ms=3.0, color=GRAY,
                alpha=.55, zorder=2)
        ax.plot(st[hit], b_pt[hit], "o", ms=5.2, color=GREEN, alpha=.95, zorder=3)
        ax.set_xlabel("교축 위치 [m]", fontsize=9)
        ax.set_ylabel(("연직 추세 [mm/yr]" if r["vertical"] else "LOS 추세 [mm/yr]"),
                      fontsize=9)
        c60, f60 = r["count_at"]["60%"], r["count_at_flipped"]["60%"]
        ax.set_title(f"{nm} — 60% 이상 비슷한 점 {c60}개 / {r['n_points']}개"
                     + ("   ※ 창이 짧다" if r["short"] else "") + "\n"
                     f"보고서 {b_rep:+.2f} ± {ci_rep:.2f} mm/yr · "
                     f"진한 띠 ±20% · 연한 띠 ±40% · 부호를 뒤집으면 {f60}개",
                     fontsize=9.4, color=NAVY, pad=6)
        ax.grid(alpha=.2)
        ax.tick_params(labelsize=8.5)

        xs = np.asarray(LEVELS)
        bx.bar(xs - 0.012, [r["count_at"][f"{int(L*100)}%"] for L in LEVELS],
               width=0.024, color=GREEN, label="보고서와")
        bx.bar(xs + 0.012, [r["count_at_flipped"][f"{int(L*100)}%"] for L in LEVELS],
               width=0.024, color=GRAY, label="부호 뒤집은 값과(우연 기준)")
        bx.set_xticks(xs)
        bx.set_xticklabels([f"{int(L*100)}%" for L in LEVELS], fontsize=8.5)
        bx.set_xlabel("유사도 기준", fontsize=9)
        bx.set_ylabel("점 수", fontsize=9)
        bx.set_title(f"기준별 점 수 · 최고 유사도 {r['best_similarity']:.0%}"
                     if r["best_similarity"] is not None else "기준별 점 수",
                     fontsize=9.4, color=NAVY, pad=6)
        bx.legend(fontsize=8, framealpha=.9)
        bx.grid(axis="y", alpha=.2)
        bx.tick_params(labelsize=8.5)

    fig.suptitle("추세가 몇 % 비슷한가 — 신뢰구간이 아니라 값끼리의 거리로\n"
                 "유사도 = 1 − |점 추세 − 보고서 추세| / |보고서 추세| · "
                 "초록 점 = 60% 이상",
                 fontsize=14.5, fontweight="bold", y=0.995)
    fig.text(0.006, 0.004,
             "※ 이 숫자를 '검증' 으로 읽으면 안 된다. 신뢰구간을 보지 않으므로 점 추세가 "
             "±9 mm/yr 로 흔들려도 우연히 가까우면 90% 가 나온다. 그래서 보고서 추세의 "
             "부호를 뒤집어 같은 셈을 한 값(회색 막대)을 나란히 뒀다 — 초록이 회색보다 "
             "뚜렷이 많지 않으면 그 유사도는 '가깝다' 가 아니라 '점 추세 분포가 넓다' 는 뜻이다.",
             fontsize=8.8, color=DIM)
    fig.tight_layout(rect=(0, 0.012, 1, 0.962))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=135)
    plt.close(fig)
    print("wrote", a.out)

    line_figure(keep, Path(a.line_out))

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "보고서 추세와 PS 점 추세의 상대 유사도(1 − 상대오차)",
         "_주의": "신뢰구간을 보지 않는 값끼리의 거리다. 부호를 뒤집은 기준선"
                "(count_at_flipped)보다 뚜렷이 많아야 의미가 있다.",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)

    hdr = "".join(f"{int(L*100)}%".rjust(7) for L in LEVELS)
    print(f"\n{'교량':<12}{'점':>5}{'최고':>7}{hdr}   (부호뒤집음 60%)")
    for r in rows:
        c = "".join(str(r["count_at"][f"{int(L*100)}%"]).rjust(7) for L in LEVELS)
        best = "—" if r["best_similarity"] is None else f"{r['best_similarity']:.0%}"
        print(f"{r['name']:<12}{r['n_points']:>5}{best:>7}{c}"
              f"        {r['count_at_flipped']['60%']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
