#!/usr/bin/env python3
"""**추세가 같은 점만** 골라 그린다 — 교면 전체가 아니라 한 점씩 판정해서.

교면 중앙값으로 맞대면 구간이 서로 지워져 아무것도 안 보인다. 그래서 점을 하나씩
본다. 점마다 보고서와 같은 창에서 직선을 맞추고, **보고서 추세가 그 점의 95% 신뢰구간
안에 들어오는가**를 묻는다. 들어오는 점만 남겨 그린다.

판정(엄격) — 두 가지를 **모두** 만족해야 '같다' 고 한다.
  ① |b_점 − b_보고서| ≤ CI_점   보고서 값이 그 점의 신뢰구간 안에 있다
  ② |b_점| ≥ CI_점              그 점의 추세 자체가 0 과 구분된다
  ③ CI_점 ≤ |b_보고서| + CI_보고서  그 점의 구간이 보고서 규모보다 좁다

②가 없으면 "아무 추세도 없는 점"이 전부 통과한다 — 신뢰구간이 넓으면 무엇이든
품기 때문이다. 앞서 '겹치는 점 167/167' 이 나왔던 게 그 경우다.

단위 — 보고서 처짐·GNSS 연직은 **연직** 값이고 LOS 는 시선 방향이다. 비교하려면
입사각으로 환산해야 한다(÷cos θ). 신축변위(암사대교)는 연직량이 아니므로 환산이
성립하지 않는다 — 그렇다고 표시하고 판정에서 뺀다.

    python scripts/make_trend_match_points.py

산출: docs/img/추세일치_점만.png · docs/bridges/trend_match_points.json
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

RED = "#C8443C"
BLUE = "#2E6FB7"
GREEN = "#2E9E6B"
ORANGE = "#D98324"
GRAY = "#B7C1CB"
DIM = "#55636F"
NAVY = "#12314F"

# 연직량이 아닌 계측 — LOS→연직 환산이 성립하지 않는다.
NOT_VERTICAL = {"신축변위"}


def main() -> int:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from make_trend_agree import load_points, report_series, slope_ci

    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--eye", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/추세일치_점만.png")
    ap.add_argument("--json-out", default="docs/bridges/trend_match_points.json")
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

        ok = np.isfinite(b_pt) & np.isfinite(ci_pt) & (ci_pt > 0)
        near = ok & (np.abs(b_pt - b_rep) <= ci_pt)        # ① 보고서가 CI 안
        firm = ok & (np.abs(b_pt) >= ci_pt)                # ② 그 점의 추세가 유의
        # ③ 점의 신뢰구간이 보고서 규모보다 넓으면 ①은 공허하다 — 구간이 넓어서
        # 품은 것이지 값이 같아서가 아니다. 올림픽대교에서 점 추세 -9~-13 mm/yr 이
        # 보고서 -3.58 과 '일치' 로 나왔던 게 그 경우다.
        bound = abs(b_rep) + ci_rep
        tight = ok & (ci_pt <= bound)
        hit = near & firm & tight

        r = {"name": nm, "what": what, "how": how, "vertical": vertical,
             "window": [round(lo, 2), round(hi, 2)], "years": round(hi - lo, 2),
             "n_epochs": int(sel.sum()), "n_points": int(los.shape[0]),
             "incidence_deg": round(inc, 1),
             "report_slope": round(b_rep, 3), "report_ci": round(ci_rep, 3),
             "n_near": int(near.sum()), "n_firm": int(firm.sum()),
             "n_tight": int(tight.sum()), "tight_bound": round(float(bound), 2),
             "n_match": int(hit.sum()),
             "match_stations_m": [round(float(q), 1) for q in st[hit]][:40],
             "match_slopes": [round(float(q), 2) for q in b_pt[hit]][:40],
             "short": bool((hi - lo) < 1.5 or int(sel.sum()) < 8)}
        rows.append(r)
        keep.append((nm, st, b_pt, ci_pt, hit, near, firm, b_rep, ci_rep,
                     ti[sel], los[:, sel], cosv, vertical, what, how, r))

    show = [k for k in keep if k[-1]["n_match"] > 0]
    if not show:
        # 한 곳도 없으면 빈 그림을 내지 않는다 — **어디서 걸렸는지**를 보인다.
        print("추세가 같다고 할 점이 한 곳도 없다 — 조건별로 어디서 걸렸는지 그린다")
        show = keep
    n = max(len(show), 1)
    fig, axes = plt.subplots(n, 2, figsize=(15.8, 2.9 * n),
                             gridspec_kw={"width_ratios": [1.25, 1.9]})
    axes = np.atleast_2d(axes)
    for i, (nm, st, b_pt, ci_pt, hit, near, firm, b_rep, ci_rep, tt, LL, cosv,
            vertical, what, how, r) in enumerate(show):
        ax, bx = axes[i]
        good = np.isfinite(b_pt) & np.isfinite(st)
        ax.axhspan(b_rep - ci_rep, b_rep + ci_rep, color="#FBE9E7", zorder=0)
        ax.axhline(b_rep, color=RED, lw=2.0, zorder=1)
        ax.plot(st[good & ~hit], b_pt[good & ~hit], "o", ms=3.0, color=GRAY,
                alpha=.55, zorder=2)
        ax.errorbar(st[hit], b_pt[hit], yerr=ci_pt[hit], fmt="o", ms=5.0,
                    color=GREEN, ecolor="#9ED4BA", elinewidth=1.1, capsize=2,
                    zorder=4)
        ax.set_xlabel("교축 위치 [m]", fontsize=9)
        ax.set_ylabel(("연직 추세 [mm/yr]" if vertical else "LOS 추세 [mm/yr]"),
                      fontsize=9)
        ax.errorbar(st[near & firm & ~hit], b_pt[near & firm & ~hit],
                    yerr=ci_pt[near & firm & ~hit], fmt="o", ms=4.2,
                    color=ORANGE, ecolor="#EBC79A", elinewidth=.9, capsize=2,
                    zorder=3)
        ax.set_title(f"{nm} — 일치 {r['n_match']}개 / {r['n_points']}개"
                     + ("  (한 곳도 없다)" if r["n_match"] == 0 else "") + "\n"
                     f"보고서 {b_rep:+.2f} ± {ci_rep:.2f} · 통과 ①{r['n_near']} "
                     f"②{r['n_firm']} ③{r['n_tight']} — 셋을 다 만족해야 한다",
                     fontsize=9.4, color=(NAVY if r["n_match"] else RED), pad=6)
        ax.grid(alpha=.2)
        ax.tick_params(labelsize=8.5)

        # 오른쪽 — 일치한 점들의 시계열과 각자의 추세선, 그리고 보고서 추세선
        t0 = float(tt.min())
        # 일치한 점이 없으면 **가장 가까운 점** 몇 개라도 보인다 — 빈 판을 내면
        # 어디까지 갔는지 알 수 없다.
        if hit.any():
            draw = np.where(hit)[0]
        else:
            cand = np.where(np.isfinite(b_pt))[0]
            draw = cand[np.argsort(np.abs(b_pt[cand] - b_rep))][:8]
        for j in draw[:12]:
            y = LL[j] - float(np.median(LL[j]))
            y = y / cosv if vertical else y
            bx.plot(tt, y, "-", lw=.9, color=GREEN, alpha=.35)
            bb = b_pt[j]
            bx.plot(tt, bb * (tt - t0) - bb * (tt.mean() - t0), "-", lw=1.4,
                    color=GREEN, alpha=.75)
        xs = np.linspace(tt.min(), tt.max(), 20)
        bx.plot(xs, b_rep * (xs - t0) - b_rep * (tt.mean() - t0), "-", lw=3.0,
                color=RED, label=f"보고서 {b_rep:+.2f} mm/yr")
        bx.axhline(0, color="#999", lw=.8)
        bx.set_xlabel("연", fontsize=9)
        bx.set_ylabel(("연직 [mm]" if vertical else "LOS [mm]"), fontsize=9)
        bx.set_title(("일치한 점" if hit.any() else "가장 가까운 점 8개")
                     + "의 시계열(연한 선)과 추세선(진한 선) · 붉은 선 = 보고서 추세",
                     fontsize=9.4, color=(NAVY if hit.any() else ORANGE), pad=6)
        bx.legend(fontsize=8.5, framealpha=.9)
        bx.grid(alpha=.2)
        bx.tick_params(labelsize=8.5)

    fig.suptitle("추세가 같은 점만 — 한 점씩 판정해서 남은 것\n"
                 "판정: ① 보고서 추세가 그 점의 95% CI 안 · ② 그 점의 추세가 0 과 "
                 "구분됨 · ③ 그 점의 구간이 보고서 규모보다 좁음 — 셋 다",
                 fontsize=14.5, fontweight="bold", y=0.995)
    fig.text(0.006, 0.004,
             "※ ②가 없으면 '아무 추세도 없는 점' 이 전부 통과한다 — 신뢰구간이 넓으면 "
             "무엇이든 품기 때문이다(앞서 167/167 이 나왔던 게 그 경우다). LOS 는 시선 "
             "방향이라 연직 계측과 맞대려면 입사각으로 환산했다(÷cosθ). 신축변위(암사대교)는 "
             "연직량이 아니라 환산이 성립하지 않아 판정에서 뺀다.",
             fontsize=8.8, color=DIM)
    fig.tight_layout(rect=(0, 0.012, 1, 0.958))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=135)
    plt.close(fig)
    print("wrote", a.out)

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "보고서 추세와 일치하는 PS 점만 골라낸 결과",
         "_판정": "① 보고서 추세가 점의 95% CI 안 ② 점의 추세가 0 과 구분됨 — 둘 다.",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)

    print(f"\n{'교량':<12}{'창':>5}{'점':>5}{'①안':>6}{'②유의':>7}{'일치':>6}"
          f"{'보고서 추세':>15}  위치[m]")
    for r in rows:
        pos = ", ".join(f"{q:.0f}" for q in r["match_stations_m"][:8])
        print(f"{r['name']:<12}{r['years']:>5.1f}{r['n_points']:>5}"
              f"{r['n_near']:>6}{r['n_firm']:>7}{r['n_tight']:>7}{r['n_match']:>6}"
              f"{r['report_slope']:>+9.2f}±{r['report_ci']:<5.2f}  {pos}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
