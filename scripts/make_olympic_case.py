#!/usr/bin/env python3
"""**올림픽대교 한 곳만** 골라 예시안으로 끝까지 펴 본다.

왜 이 교량인가. 계측 곡선이 있는 5개소 중 교면 PS 가 계측과 가장 잘 맞은 곳이다 —
r = +0.893, **R² 0.798**, 우연 기준선(20점 중 최대 |r|, 달을 섞어 2만 회) R² 0.607.
우연을 넘는다. 전 교량 자료에 적혀 있던 0.546 은 2천 회로만 돌려 낮게 잡힌 값이다 —
2천 회에서는 0.55~0.61 사이를 오간다. 예시안은 2만 회로 고정한다.
관측 시점도 101장으로 가장 길다(2018-06 ~ 2025-12, 사장교).

앞서 전 교량 자료에서는 R² 0.80 을 판정선으로 놓아 이 교량이 **0.002 차이로** 아래에
떨어졌다. 그 선은 내가 정한 것이지 물리에서 나온 것이 아니다. 우연 기준선을 넘는지가
더 곧은 잣대이고, 그 잣대로는 넘는다. 그래서 여기 한 곳만 펴서 **파이프라인이 끝까지
가면 무엇이 나오는지**를 보인다.

동시에 **과장하지 않는다.** 이 예시안은 다음도 같이 적는다.
  · 교면 PS 가 20점뿐이고 그중 우연을 넘은 점은 **1개**다
  · 상위 3점이 한자리에 뭉치지 않는다(측점 폭 182 m · p=0.195) — 한 점이 우연히
    맞았을 가능성을 배제하지 못한다
  · 가장 잘 맞은 점은 주탑(766·904 m) 바깥이고 경간 중앙(835 m)이 아니다.
    계측기가 보는 자리와 위성이 보는 자리가 다르다

    python scripts/make_olympic_case.py

산출: docs/bridges/올림픽대교/예시안.png · 예시안.json · 예시안.md
     docs/img/value/예시안_올림픽대교.png (발표용 같은 그림)
"""

from __future__ import annotations

import argparse
import json
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
sys.path.insert(0, str(ROOT / "src"))

from insar_series import dec_year, report_series                      # noqa: E402
from kaia_theme import MPL, use_mpl_style                             # noqa: E402
from make_deck_point_match import climatology, station                # noqa: E402

use_mpl_style()
NAME = "올림픽대교"
TOP_K = 3
MONTHS = ["1월", "2월", "3월", "4월", "5월", "6월",
          "7월", "8월", "9월", "10월", "11월", "12월"]


def compute(folder: Path, root: Path, n_perm: int) -> dict:
    """점마다 계측과 맞대고, 우연 기준선과 뭉침까지 같이 낸다."""
    auto = json.loads((root / "hangang_gnss_monthly.json").read_text(encoding="utf-8"))
    eye = json.loads((root / "hangang_displacement_2024.json").read_text(
        encoding="utf-8"))
    tr, vr, what, how = report_series(auto, eye, NAME)

    with h5py.File(folder / "track_deck_mt.h5", "r") as h:
        L = np.asarray(h["los_mm"][()], float)
        ll = np.asarray(h["pixel_lonlat"][()], float)
        ep = [s.decode() if isinstance(s, bytes) else str(s) for s in h["epochs"][()]]
        coh = np.asarray(h["coh"][()], float)
        gam = np.asarray(h["temporal_coherence"][()], float)

    t = np.asarray([dec_year(e) for e in ep])
    C = climatology(t, L)
    g = climatology(np.asarray(tr), np.asarray(vr))[0]
    ok = np.isfinite(g) & np.all(np.isfinite(C), axis=0)
    st = station(ll)
    live = np.std(C[:, ok], axis=1) > 1e-9
    X, y = C[live][:, ok], g[ok]
    idx = np.flatnonzero(live)
    r = np.asarray([float(np.corrcoef(x, y)[0, 1]) for x in X])

    rng = np.random.default_rng(9)
    null = [float(np.max(np.abs([np.corrcoef(x, y[rng.permutation(len(y))])[0, 1]
                                 for x in X]))) for _ in range(n_perm)]
    chance = float(np.percentile(null, 95))

    top = np.argsort(-np.abs(r))[:TOP_K]
    spread = float(np.ptp(st[idx][top]))
    nullsp = [float(np.ptp(st[idx][rng.permutation(len(r))[:TOP_K]]))
              for _ in range(20000)]
    p_cluster = float(np.mean(np.asarray(nullsp) <= spread))

    best = int(top[0])
    dates = np.asarray([date(int(e[:4]), int(e[4:6]), int(e[6:8])) for e in ep])
    o = np.argsort(dates)
    return {
        "name": NAME, "quantity": what, "read": how,
        "n_epochs": len(ep), "first": ep[int(o[0])], "last": ep[int(o[-1])],
        "n_points": int(len(r)), "n_months": int(ok.sum()),
        "deck_span_m": round(float(np.ptp(st)), 1),
        "r_best": round(float(r[best]), 3), "r2_best": round(float(r[best] ** 2), 3),
        "r_median": round(float(np.median(r)), 3),
        "r2_chance95": round(chance ** 2, 3), "chance95_abs_r": round(chance, 3),
        "beats_chance": bool(abs(r[best]) > chance),
        "n_above_chance": int(np.sum(np.abs(r) > chance)),
        "top_spread_m": round(spread, 1), "cluster_p": round(p_cluster, 4),
        "clustered": bool(p_cluster < 0.05),
        "best_station_m": round(float(st[idx][best]), 1),
        "best_coh": round(float(coh[idx][best]), 3),
        "best_gamma": round(float(gam[idx][best]), 3),
        "gamma_median": round(float(np.nanmedian(gam)), 3),
        "_r": r, "_st": st[idx], "_X": X, "_y": y, "_ok": ok, "_null": np.asarray(null),
        "_chance": chance, "_best": best, "_top": top,
        "_dates": dates[o], "_los_best": L[idx][best][o],
        "_tr": np.asarray(tr), "_vr": np.asarray(vr),
    }


def figure(rec: dict, mt: dict, rb: dict, out: Path) -> None:
    fig = plt.figure(figsize=(13.4, 8.6))
    gs = fig.add_gridspec(3, 3, height_ratios=(1.0, 1.0, 0.92),
                          hspace=0.52, wspace=0.30)
    r, st, best = rec["_r"], rec["_st"], rec["_best"]

    # ── ① 교축 위에 점이 어디 있나 ────────────────────────────────────────
    ax = fig.add_subplot(gs[0, :])
    sc = ax.scatter(st, r, c=np.abs(r), cmap="viridis", vmin=0, vmax=1,
                    s=54, edgecolor=MPL["slate"], linewidth=.5, zorder=3)
    ax.scatter([st[best]], [r[best]], s=230, facecolor="none",
               edgecolor=MPL["red"], linewidth=2.2, zorder=4)
    ax.annotate(f"r {r[best]:+.3f} · R² {r[best] ** 2:.3f}",
                (st[best], r[best]), textcoords="offset points", xytext=(-14, 6),
                ha="right", fontsize=10, color=MPL["red"], fontweight="bold")
    ax.axhline(0, color=MPL["gray"], lw=.8, ls=":")
    for s_ in (rec["_chance"], -rec["_chance"]):
        ax.axhline(s_, color=MPL["red"], lw=1.1, ls="--")
    ax.text(st.min(), rec["_chance"], f" 우연 기준선 |r| {rec['_chance']:.3f}",
            fontsize=9, color=MPL["red"], va="bottom")
    ax.set_xlabel("교축 측점 [m] — 데크를 따라간 거리", fontsize=10)
    ax.set_ylabel("계측과의 상관 r", fontsize=10)
    ax.set_ylim(-1.05, 1.05)
    ax.grid(alpha=.22)
    ax.set_title(f"① 교면 PS {rec['n_points']}점을 한 점씩 계측과 맞댄다 — "
                 f"우연을 넘은 점은 {rec['n_above_chance']}개",
                 fontsize=11, color=MPL["ink"], pad=6)
    cb = fig.colorbar(sc, ax=ax, pad=.012, fraction=.022)
    cb.set_label("|r|", fontsize=9, rotation=0, labelpad=10)

    # ── ② 그 점의 월별 기후값 대조 ────────────────────────────────────────
    ax = fig.add_subplot(gs[1, 0])
    mo = np.flatnonzero(rec["_ok"])
    x = rec["_X"][best]
    ax.plot(mo + 1, (x - x.mean()) / x.std(), color=MPL["blue"], marker="o",
            ms=4, lw=1.6, label="InSAR(그 점)")
    y = rec["_y"]
    ax.plot(mo + 1, (y - y.mean()) / y.std(), color=MPL["orange"], marker="s",
            ms=4, lw=1.6, label=f"계측 — {rec['quantity']}")
    ax.set_xticks(range(1, 13))
    ax.set_xticklabels([m[:-1] for m in MONTHS], fontsize=8)
    ax.set_xlabel("달 (해마다 같은 달의 평균)", fontsize=9.5)
    ax.set_ylabel("표준화 값", fontsize=9.5)
    ax.legend(fontsize=8.0, loc="lower left", framealpha=.94)
    ax.grid(alpha=.22)
    ax.set_title(f"② 모양이 같은가 — R² {rec['r2_best']:.3f}",
                 fontsize=10.5, color=MPL["ink"], pad=5)

    # ── ③ 우연 기준선 ────────────────────────────────────────────────────
    ax = fig.add_subplot(gs[1, 1])
    ax.hist(rec["_null"], bins=40, color=MPL["slate"], alpha=.55,
            edgecolor="none", label="달을 섞었을 때")
    ax.axvline(rec["_chance"], color=MPL["red"], lw=1.6, ls="--",
               label=f"95% {rec['_chance']:.3f}")
    ax.axvline(abs(r[best]), color=MPL["green"], lw=2.0,
               label=f"관측 {abs(r[best]):.3f}")
    ax.set_xlabel(f"{rec['n_points']}점 중 최대 |r|", fontsize=9.5)
    ax.set_ylabel("횟수", fontsize=9.5)
    ax.legend(fontsize=8.2, framealpha=.94)
    ax.grid(alpha=.22)
    ax.set_title("③ 골라서 좋아 보이는 것은 아닌가", fontsize=10.5,
                 color=MPL["ink"], pad=5)

    # ── ④ 기선망 ─────────────────────────────────────────────────────────
    ax = fig.add_subplot(gs[1, 2])
    bl = mt.get("baseline", {})
    rows = rb
    ax.scatter(rows["days"], rows["bperp"], s=22, color=MPL["blue"],
               edgecolor=MPL["slate"], linewidth=.4)
    ax.axhline(0, color=MPL["gray"], lw=.8, ls=":")
    ax.set_xlabel("시간기선 [일]", fontsize=9.5)
    ax.set_ylabel("수직기선 B⊥ [m]", fontsize=9.5)
    ax.grid(alpha=.22)
    ax.set_title(f"④ 기선망 — B⊥ 폭 {bl.get('bperp_span_m', float('nan')):.0f} m · "
                 f"쌍 {mt.get('network', {}).get('n_pairs', 0)}개",
                 fontsize=10.5, color=MPL["ink"], pad=5)

    # ── ⑤ 전체 시계열 위에 계측 겹치기 ────────────────────────────────────
    ax = fig.add_subplot(gs[2, :])
    d, v = rec["_dates"], rec["_los_best"]
    ax.plot(d, v - np.nanmean(v), color=MPL["blue"], lw=1.2, marker="o", ms=2.6,
            label=f"InSAR 그 점 (LOS · {rec['n_epochs']}시점)")
    ax.axhline(0, color=MPL["gray"], lw=.8, ls=":")
    ax.set_ylabel("LOS [mm]", color=MPL["blue"], fontsize=9.5)
    ax.tick_params(axis="y", labelcolor=MPL["blue"])
    ax.set_xlim(date(2018, 1, 1), date(2026, 3, 1))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.grid(alpha=.22)
    ax2 = ax.twinx()
    td = [date(int(tt), max(1, min(12, int((tt % 1) * 12) + 1)), 15)
          for tt in rec["_tr"]]
    o = np.argsort(rec["_tr"])
    ax2.plot([td[i] for i in o], rec["_vr"][o], color=MPL["orange"], lw=1.5,
             marker="s", ms=3.2, label=f"계측 — {rec['quantity']}")
    ax2.set_ylabel("계측 [mm]", color=MPL["orange"], fontsize=9.5)
    ax2.tick_params(axis="y", labelcolor=MPL["orange"])
    ax.axvspan(min(td), max(td), color=MPL["orange"], alpha=.05, lw=0, zorder=0)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8.6, ncol=2, loc="upper left",
              framealpha=.94)
    ax.set_title(f"⑤ 같은 점의 전체 관측 {rec['first'][:4]}–{rec['last'][:4]} — "
                 "겹치는 기간(음영)만 맞댈 수 있다",
                 fontsize=10.5, color=MPL["ink"], pad=5)

    fig.suptitle(f"{NAME} 예시안 — 교면 MT-InSAR 가 계측과 맞은 한 사례",
                 fontsize=15, fontweight="bold", color=MPL["ink"], y=0.985)
    fig.text(0.006, 0.008,
             f"※ 우연 기준선 = 달을 섞어 2만 회 돌렸을 때 {rec['n_points']}점 중 최대 |r| "
             f"의 95 백분위. 관측 {abs(r[best]):.3f} 은 이를 넘는다.\n"
             f"   다만 넘은 점은 {rec['n_above_chance']}개뿐이고 상위 3점이 한자리에 "
             f"뭉치지도 않는다(폭 {rec['top_spread_m']:.0f} m · p={rec['cluster_p']:.3f}) — "
             "한 점이 우연히 맞았을 가능성을 배제하지 못한다. 계측값은 보고서 그래프 판독값이다.",
             fontsize=8.6, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.035, 1, 0.965))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("wrote", out)


def markdown(rec: dict, mt: dict, rb2: dict) -> str:
    n = chr(10)
    bl = mt.get("baseline", {})
    net = mt.get("network", {})
    L = [
        f"# {NAME} 예시안 — 한 교량을 끝까지 펴 본다", "",
        "계측 곡선이 있는 5개소 중 **교면 PS 가 계측과 가장 잘 맞은 곳**이다. 관측 시점도",
        f"{rec['n_epochs']}장으로 가장 길다({rec['first'][:4]}-{rec['first'][4:6]} ~ "
        f"{rec['last'][:4]}-{rec['last'][4:6]}).", "",
        "전 교량 자료에서는 R² 0.80 을 판정선으로 놓아 이 교량이 **0.002 차이로** 아래에",
        "떨어졌다. 그 선은 물리에서 나온 것이 아니라 내가 정한 것이다. **우연 기준선을**",
        "**넘는가**가 더 곧은 잣대이고, 그 잣대로는 넘는다.", "",
        "## 계측과 맞은 정도", "",
        "| 항목 | 값 |", "|---|---|",
        f"| 계측 | {rec['quantity']} ({rec['read']}) |",
        f"| 가장 잘 맞은 점 | r **{rec['r_best']:+.3f}** · "
        f"R² **{rec['r2_best']:.3f}** (양의 방향 — 계측과 같은 쪽으로 움직인다) |",
        f"| 우연 기준선 | \\|r\\| {rec['chance95_abs_r']:.3f} · "
        f"R² {rec['r2_chance95']:.3f} → "
        + ("**넘는다**" if rec["beats_chance"] else "못 넘는다") + " |",
        f"| 맞댄 달 | {rec['n_months']}개 (해마다 같은 달의 평균끼리) |",
        f"| 그 점의 품질 | 코히런스 {rec['best_coh']:.3f} · "
        f"시간결맞음 γ {rec['best_gamma']:.3f} |", "",
        "## 그대로 믿으면 안 되는 이유 — 같이 적는다", "",
        f"- 교면 PS 가 **{rec['n_points']}점뿐**이고, 그중 우연 기준선을 넘은 점은 "
        f"**{rec['n_above_chance']}개**다.",
        f"- 상위 3점이 한자리에 **뭉치지 않는다**(측점 폭 {rec['top_spread_m']:.0f} m · "
        f"p={rec['cluster_p']:.3f}). 같은 거동을 여러 점이 같이 봤다면 뭉쳐야 한다 —",
        "  한 점이 우연히 맞았을 가능성을 배제하지 못한다.",
        f"- 점별 상관의 중앙값은 {rec['r_median']:+.3f} 이다. 대부분의 점은 계측과 무관하다.",
        "- 계측값은 보고서 그래프를 **눈으로 읽은 값**이다(원자료도, 인쇄된 표도 아니다).",
        "", "## 같은 자료에서 함께 나오는 것들", "",
        "| 항목 | 값 |", "|---|---|",
        f"| 수직기선 B⊥ | {bl.get('bperp_min_m', float('nan')):+.1f} ~ "
        f"{bl.get('bperp_max_m', float('nan')):+.1f} m "
        f"(폭 {bl.get('bperp_span_m', float('nan')):.0f} m) |",
        f"| 기선망 | 쌍 {net.get('n_pairs', 0)}개 · "
        + ("연결됨" if net.get("connected") else "끊김") + " |",
        f"| γ 중앙값(교면) | {rec['gamma_median']:.3f} |",
        f"| 잔차고도 σΔh | {mt.get('sigma_dh_median_m', float('nan')):.1f} m |",
        f"| 열팽창(교면) | {rb2.get('thermal_deck_mm_per_C', float('nan')):+.3f} mm/°C |",
        f"| 속도 중앙값 | {rb2.get('velocity_median_mm_yr', float('nan')):+.2f} mm/년 |",
        f"| CRI | 점별 중앙 {rb2.get('cri_point_median', float('nan')):.3f} · "
        f"전역 {rb2.get('cri', float('nan')):.3f} (등급 기준은 잠정) |",
        f"| IFC 트윈 | 부재 {rb2.get('elements', 0)} · 결합 {rb2.get('bound', 0)} |",
        "", "![예시안](예시안.png)", "",
        "3D: `twin.viewer.html`(속도) · `twin_cri.viewer.html`(CRI) — 더블클릭", "",
        "## 이 예시안이 말하는 것", "",
        "한 교량에서 **위성만으로 계측과 같은 계절 거동을 집어낸 점이 실제로 있었다**.",
        "그러나 20점 중 1점이고 뭉치지도 않았다. 지금 부족한 것은 방법이 아니라 **교면",
        "측점의 수**다. Sentinel-1 IW 는 지상 5×20 m 이고 데크 폭은 1~2 화소라 교면에",
        "점이 몇 개 안 잡힌다. 주경간에 코너리플렉터를 놓거나 고해상도 SAR 을 쓰면",
        "이 칸이 채워지고, 그때 비로소 '맞는다/안 맞는다' 를 말할 수 있다.", "",
    ]
    return n.join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--perm", type=int, default=20000)
    ap.add_argument("--slide-out", default="docs/img/value/예시안_올림픽대교.png")
    a = ap.parse_args()

    root = Path(a.root)
    folder = root / NAME
    rec = compute(folder, root, a.perm)
    mt = json.loads((folder / "mtinsar.json").read_text(encoding="utf-8"))
    rb2 = json.loads((folder / "재도출.json").read_text(encoding="utf-8"))

    import csv
    with (folder / "mtinsar_baselines.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    rb = {"days": [float(r["btemp_days"]) for r in rows],
          "bperp": [float(r["bperp_m"]) for r in rows]}

    out = folder / "예시안.png"
    figure(rec, mt, rb, out)
    import shutil
    Path(a.slide_out).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out, a.slide_out)
    print("wrote", a.slide_out)

    (folder / "예시안.md").write_text(markdown(rec, mt, rb2), encoding="utf-8")
    print("wrote", folder / "예시안.md")
    (folder / "예시안.json").write_text(json.dumps(
        {k: v for k, v in rec.items() if not k.startswith("_")},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", folder / "예시안.json")

    print(f"{NAME}: r {rec['r_best']:+.3f} · R² {rec['r2_best']:.3f} · "
          f"우연 {rec['r2_chance95']:.3f} → "
          + ("넘는다" if rec["beats_chance"] else "못 넘는다")
          + f" · 넘은 점 {rec['n_above_chance']}/{rec['n_points']} · "
          f"상위3 폭 {rec['top_spread_m']:.0f} m (p={rec['cluster_p']:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
