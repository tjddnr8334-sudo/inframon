#!/usr/bin/env python3
"""한강교량 현장계측(2024 온라인 안전감시) ↔ 위성 InSAR — 교량 전부를 같은 자로 잰다.

무엇을 검증하는가
------------------
2024년 한강교량 온라인 안전감시 최종보고는 대상 16개소 **전부** 를 "관리기준 이내 ·
거동추세 양호" 로 판정했다. inframon 이 같은 교량을 위성으로만 보고 같은 결론에 오는가 —
그게 이 그림이 답하는 질문이다. 위성이 어느 한 곳에서 "유의한 침하" 라고 말하면 둘 중
하나가 틀린 것이고, 전부 "유의한 변위 없음" 이면 서로를 지지한다.

GNSS 에 대해 정직하게
----------------------
보고서에서 **GNSS 변위계가 달린 교량은 4개소뿐** 이다(가양·월드컵·서강·샛강문화다리).
그중 수치표가 인쇄된 곳은 월드컵대교(2024년 교축·교축직각)와 샛강문화다리(2022~24년)
둘뿐이고, 나머지 둘은 계측항목 표에 ◯ 만 있고 그래프도 없다. 게다가

  · 월드컵대교 — 데크 ±30 m 안 PS 0점(트윈·시계열 불가)
  · 샛강문화다리 — OSM·표준데이터에 좌표 없음(InSAR 대상 밖)

이라, **GNSS 수치와 InSAR 시계열이 둘 다 있는 한강교량은 현재 0개소** 다. 그래서 이
그림은 GNSS 를 억지로 끼워 맞추지 않는다. 대신

  (지표 1) 16개소 전부 — 보고서 판정 ↔ InSAR 판정 일치표
  (지표 2) 교량별 추세선 — 데크 중앙값 LOS 시계열에 직선 / 직선+연주기를 얹는다
  (지표 3) GNSS 수치가 있는 2개소 — 보고서 변동폭 ↔ 같은 양(InSAR 변동폭) 나란히

를 낸다. 추세는 **어떻게 뽑느냐가 결론을 바꾼다** — 1년치에 직선만 맞추면 계절 주기가
통째로 기울기로 샌다. 그래서 (지표 2) 는 두 적합을 같이 그린다.

    python scripts/make_hangang_gnss_insar.py

산출:
    docs/bridges/hangang_gnss_insar.json
    docs/img/hangang_지표_종합.png
    docs/img/hangang_추세선.png
    docs/img/hangang_gnss_변동폭.png
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from inframon.insar.chainage import _use_korean_font

GREEN, ORANGE, RED, BLUE, NAVY, GRAY = (
    "#2E7D32", "#E06C2C", "#C03028", "#1F6FB2", "#123A5E", "#8A8F96")


# ── 적합 ──────────────────────────────────────────────────────────────────────
def fit(t_yr: np.ndarray, y: np.ndarray, *, annual: bool) -> dict:
    """선형(+연주기) 최소제곱 → 기울기·95% CI 반폭·잔차σ·연주기 진폭."""
    cols = [np.ones_like(t_yr), t_yr]
    if annual:
        cols += [np.sin(2 * np.pi * t_yr), np.cos(2 * np.pi * t_yr)]
    A = np.vstack(cols).T
    c, *_ = np.linalg.lstsq(A, y, rcond=None)
    r = y - A @ c
    dof = max(len(t_yr) - A.shape[1], 1)
    sig = float(np.sqrt(np.sum(r ** 2) / dof))
    se = sig * float(np.sqrt(np.linalg.inv(A.T @ A)[1, 1]))
    return {"v": float(c[1]), "ci": 1.96 * se, "sigma": sig,
            "amp": float(np.hypot(c[2], c[3])) if annual else 0.0,
            "coef": [float(x) for x in c]}


def model(t_yr: np.ndarray, coef: list[float]) -> np.ndarray:
    cols = [np.ones_like(t_yr), t_yr]
    if len(coef) == 4:
        cols += [np.sin(2 * np.pi * t_yr), np.cos(2 * np.pi * t_yr)]
    return np.vstack(cols).T @ np.asarray(coef)


# ── 교량 하나 읽기 ────────────────────────────────────────────────────────────
def read_bridge(folder: Path) -> dict | None:
    """project.h5 → 교량 하나의 InSAR 지표. 점이 없으면 None."""
    import h5py

    p5 = folder / "project.h5"
    if not p5.exists():
        return None
    with h5py.File(p5, "r") as f:
        if "insar/los" not in f:
            return None
        los = np.asarray(f["insar/los"][()], float)          # (점, 시점)
        st = np.asarray(f["insar/deck_station"][()], float)
        days = np.asarray(f["insar/dates"][()], float)
        labels = [b.decode() for b in f["insar/date_labels"][()]]
        inc = float(np.median(np.asarray(f["insar/incidence_deg"][()], float)))
    if los.size == 0 or los.shape[0] == 0:
        return None

    t = days / 365.25
    t = t - t[0]
    med = np.median(los, axis=0)                              # 데크 중앙값 시계열

    # 점별 적합 → 중앙값 (교량 대표값; 한 점의 튐에 끌려가지 않는다)
    A = np.vstack([np.ones_like(t), t]).T
    c, *_ = np.linalg.lstsq(A, los.T, rcond=None)
    r = los.T - A @ c
    sig = np.sqrt(np.sum(r ** 2, axis=0) / max(len(t) - 2, 1))
    ci_pt = 1.96 * sig / (np.std(t) * np.sqrt(len(t)))

    lin = fit(t, med, annual=False)
    ann = fit(t, med, annual=True)
    # 교축 구간별 추세 — **중앙값은 교량 전체의 요약이지, 어느 구간도 안 움직인다는
    # 뜻이 아니다.** 구간마다 부호가 다르면 중앙값에서 서로 지워진다(연주기에서는
    # 가양대교 진폭이 1/10 로 죽었다). 추세는 그만큼은 아니지만, 중앙값이 '유의차 없음'
    # 인데 한 구간만 유의한 경우가 있어 그것을 놓치면 안 된다.
    sections = []
    if st.size == los.shape[0] and np.ptp(st) > 1.0:
        edges = np.linspace(float(st.min()), float(st.max()), 7)
        for i in range(6):
            sel = (st >= edges[i]) & ((st < edges[i + 1]) if i < 5
                                      else (st <= edges[i + 1]))
            if sel.sum() < 3:
                sections.append({"s0": float(edges[i]), "s1": float(edges[i + 1]),
                                 "n": int(sel.sum()), "v": None, "ci": None})
                continue
            q = fit(t, np.median(los[sel], axis=0), annual=True)
            sections.append({"s0": float(edges[i]), "s1": float(edges[i + 1]),
                             "n": int(sel.sum()), "v": q["v"], "ci": q["ci"],
                             "significant": bool(abs(q["v"]) >= q["ci"])})
    cos_th = float(np.cos(np.radians(inc)))
    return {
        "n_points": int(los.shape[0]), "n_epochs": int(los.shape[1]),
        "span": [labels[0], labels[-1]], "incidence_deg": inc,
        "labels": labels,
        "t_yr": t.tolist(), "median_mm": med.tolist(),
        "v_pt_med": float(np.median(c[1])), "ci_pt_med": float(np.median(ci_pt)),
        "lin": lin, "ann": ann,
        "v_vert_mm_yr": ann["v"] / cos_th, "ci_vert_mm_yr": ann["ci"] / cos_th,
        "p2p_mm": float(med.max() - med.min()),
        "annual_p2p_mm": 2 * ann["amp"],
        "significant": bool(abs(ann["v"]) >= ann["ci"]),
        "sections": sections,
        "section_only": bool(abs(ann["v"]) < ann["ci"]
                             and any(q.get("significant") for q in sections)),
    }


def window(d: dict, lo: str, hi: str) -> dict | None:
    """같은 시계열을 **보고서가 덮는 기간만** 잘라 다시 적합한다.

    보고서가 보는 것은 2024년 한 해(월드컵대교 GNSS)나 2022~24년 3년(샛강문화다리)이다.
    위성의 2018~2025 8년 추세와 그것을 맞대면 애초에 같은 것을 재고 있지 않다 —
    기간을 맞춰야 "같은 결론인가"를 물을 수 있다. 시점이 6개 미만이면 적합하지 않는다.
    """
    if not d:
        return None
    lab = d.get("labels") or []
    t = np.asarray(d["t_yr"], float)
    y = np.asarray(d["median_mm"], float)
    m = np.array([lo <= s <= hi for s in lab], bool)
    if m.sum() < 6:
        return None
    tw, yw = t[m] - t[m][0], y[m]
    ann = fit(tw, yw, annual=True) if m.sum() >= 8 else fit(tw, yw, annual=False)
    return {"lo": lo, "hi": hi, "n_epochs": int(m.sum()),
            "span": [lab[int(np.argmax(m))], [s for s in lab if lo <= s <= hi][-1]],
            "t_yr": tw.tolist(), "median_mm": yw.tolist(),
            "lin": fit(tw, yw, annual=False), "ann": ann,
            "annual_fit": bool(m.sum() >= 8),
            "significant": bool(abs(ann["v"]) >= ann["ci"])}


def agree(sig_moving: bool, report_ok: bool) -> str:
    if report_ok and not sig_moving:
        return "일치 — 둘 다 '유의한 변위 없음'"
    if report_ok and sig_moving:
        return "불일치 — 보고서 양호, 위성은 유의한 변위"
    if not report_ok and sig_moving:
        return "일치 — 둘 다 변위 있음"
    return "불일치 — 보고서 관찰필요, 위성은 유의차 없음"


def _wrap(s: str, width: int, lines: int = 2) -> str:
    """구분자(·) 단위로 접는다. 넘치면 잘라 내고 … 를 붙인다 — 칸을 넘지 않게."""
    if "·" not in s and len(s) > width:          # 한 덩어리면 글자 수로 접는다
        import textwrap
        return "\n".join(textwrap.wrap(s, width)[:lines])
    parts, cur, out = [p.strip() for p in s.split("·") if p.strip()], "", []
    for p in parts:
        cand = f"{cur} · {p}" if cur else p
        if len(cand) <= width:
            cur = cand
            continue
        out.append(cur)
        cur = p
        if len(out) == lines:
            break
    if cur and len(out) < lines:
        out.append(cur)
    if len(out) == lines and (len(parts) > sum(o.count("·") + 1 for o in out)):
        out[-1] += " …"
    return "\n".join(out)


# ── (지표 1) 종합 ─────────────────────────────────────────────────────────────
def fig_summary(rows: list[dict], out: Path) -> None:
    n = len(rows)
    fig = plt.figure(figsize=(17.2, max(7.4, 0.50 * n + 3.4)))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.28], wspace=0.05)

    y = np.arange(n)[::-1]

    # 좌: LOS 변위속도 ± 95% CI
    a = fig.add_subplot(gs[0])
    for yi, r in zip(y, rows):
        d = r["insar"]
        if d is None:
            a.axhspan(yi - 0.40, yi + 0.40, color=RED, alpha=.06, zorder=0)
            a.text(0.5, yi, "InSAR 산출 없음", transform=a.get_yaxis_transform(),
                   fontsize=9, color=RED, va="center", ha="center", style="italic")
            continue
        # 구간별 추세 범위 — 중앙값 하나로는 "어느 구간도 안 움직인다" 를 말할 수 없다
        sec = [q for q in (d.get("sections") or []) if q.get("v") is not None]
        if sec:
            vs = [q["v"] for q in sec]
            a.plot([min(vs), max(vs)], [yi + 0.30, yi + 0.30], "-", lw=5.0,
                   color="#DCE6EE", solid_capstyle="butt", zorder=2)
            for q in sec:
                a.plot(q["v"], yi + 0.30, "|", ms=8, mew=1.4,
                       color=(RED if q.get("significant") else "#7F93A6"), zorder=3)
        c = ORANGE if d["significant"] else GREEN
        a.errorbar(d["ann"]["v"], yi, xerr=d["ann"]["ci"], fmt="o", ms=6.5, color=c,
                   ecolor=c, elinewidth=2.1, capsize=3.5, zorder=4)
        a.errorbar(d["lin"]["v"], yi + 0.27, xerr=d["lin"]["ci"], fmt="s", ms=4.2,
                   color=GRAY, ecolor=GRAY, elinewidth=1.2, capsize=2.5, zorder=3)
        a.text(0.012, yi - 0.31, f"{d['n_points']}점 · {d['n_epochs']}시점",
               transform=a.get_yaxis_transform(), fontsize=7.4, color=GRAY, va="center")
    a.axvline(0, color="k", lw=1.2)
    a.axvspan(-0.5, 0.5, color=GREEN, alpha=.07)
    a.set_yticks(y)
    a.set_yticklabels([r["name"] + ("  ◆GNSS" if r["gnss"] else "")
                       + (" ▲" if (r["insar"] or {}).get("section_only") else "")
                       for r in rows], fontsize=10)
    a.set_ylim(-0.6, n - 0.4)
    a.set_xlabel("LOS 변위속도 [mm/yr] · 오차막대 = 95% 신뢰구간", fontsize=10.5)
    a.set_title("(a) 위성 InSAR — 교량별 변위속도\n"
                "◆ = 보고서상 GNSS 변위계 설치 교량", fontsize=12, pad=9)
    a.grid(axis="x", alpha=.25)
    a.legend(handles=[Patch(color=GREEN, label="교면 중앙값 — 95% CI 가 0 을 포함(유의차 없음)"),
                      Patch(color=ORANGE, label="교면 중앙값 — 0 을 벗어남"),
                      Patch(color=GRAY, label="직선만 적합(비교용)"),
                      Patch(color="#DCE6EE", label="교축 6구간별 추세 범위 "
                                                   "(| 붉은 것 = 그 구간은 유의)"),
                      Patch(color="white", ec="white",
                            label="▲ = 중앙값은 유의차 없는데 한 구간은 유의")],
             fontsize=8.6, loc="upper left", bbox_to_anchor=(1.08, -0.035),
             ncol=1, framealpha=.93)

    # 우: 판정 대조표
    b = fig.add_subplot(gs[1])
    b.axis("off")
    xs = [0.000, 0.115, 0.515, 0.680, 0.885]
    heads = ["교량", "보고서가 말하는 추세(2024)", "보고서 판정",
             "위성이 본 추세", "대조"]
    top = 1.0
    row_h = 1.0 / (n + 1.8)
    for x, h in zip(xs, heads):
        b.text(x, top, h, fontsize=9.5, fontweight="bold", color="white", va="center",
               bbox=dict(boxstyle="square,pad=0.32", fc=NAVY, ec="none"))
    for i, r in enumerate(rows):
        yy = top - (i + 1.30) * row_h
        d = r["insar"]
        if i % 2 == 0:
            b.axhspan(yy - row_h * 0.48, yy + row_h * 0.48, color="#F2F5F8", zorder=0)
        b.text(xs[0], yy, r["name"] + ("◆" if r["gnss"] else ""), fontsize=9, va="center")
        # 보고서가 **추세에 대해 뭐라고 했는지** 를 그대로 옮긴다 — 위성 mm/yr 와
        # 나란히 놓아야 "어떻게 다른가" 가 읽힌다. 감시항목은 ④ 슬라이드에 있다.
        b.text(xs[1], yy, _wrap(r["report_trend"] or "추세 서술 없음", 38),
               fontsize=7.2, va="center", color="#333", linespacing=1.25)
        b.text(xs[2], yy, _wrap(r["verdict_short"], 12), fontsize=8.2, va="center",
               color=GREEN, linespacing=1.25)
        if d is None:
            b.text(xs[3], yy, "산출 없음", fontsize=8.5, va="center", color=RED)
            b.text(xs[4], yy, "대조 불가", fontsize=9, fontweight="bold",
                   va="center", color=RED)
        else:
            col = ORANGE if d["significant"] else GREEN
            b.text(xs[3], yy, f"{d['ann']['v']:+.2f} ± {d['ann']['ci']:.2f} mm/yr",
                   fontsize=8.6, va="center", color=col)
            ok = r["agree"].startswith("일치")
            b.text(xs[4], yy, "○ 일치" if ok else "△ 불일치", fontsize=9.5,
                   fontweight="bold", va="center", color=GREEN if ok else ORANGE)
    b.set_xlim(0, 1); b.set_ylim(0, 1.06)
    b.set_title("(b) 보고서가 말하는 추세 ↔ 위성이 본 추세\n"
                "위성 = 데크 중앙값 시계열에 직선+연주기 · 95% CI 가 0 을 포함하면 "
                "'유의한 변위 없음'", fontsize=12, pad=9)

    miss = [r for r in rows if r["insar"] is None]
    if miss:
        b.text(0.0, -0.012 - 0.5 * row_h,
               "대조 불가 — " + " ／ ".join(f"{m['name']}: {m['why']}" for m in miss),
               fontsize=8.2, color=RED, va="top", wrap=True,
               transform=b.transAxes)

    ok = sum(1 for r in rows if r["agree"].startswith("일치"))
    na = sum(1 for r in rows if r["insar"] is None)
    fig.suptitle(f"한강교량 현장 안전감시(2024) ↔ 위성 InSAR — {len(rows)}개소 전수 대조 "
                 f"· 일치 {ok} · 대조불가 {na}",
                 fontsize=14.5, fontweight="bold", y=0.988)
    so = [r["name"] for r in rows if (r["insar"] or {}).get("section_only")]
    nsec = sum(1 for r in rows
               for q in ((r["insar"] or {}).get("sections") or [])
               if q.get("v") is not None)
    nsig = sum(1 for r in rows
               for q in ((r["insar"] or {}).get("sections") or [])
               if q.get("significant"))
    cav = ("▲ 교면 전체 중앙값은 '유의차 없음' 인데 한 국간은 유의한 교량 — "
           + (" ／ ".join(so) if so else "해당 없음")
           + f"  ({len(so)}개소).\n"
           + "　 중앙값은 교량 전체의 요약일 뿐, ‘어느 국간도 안 움직인다’ 는 뜻이 아니다."
           + f"   ※ 국간 검정 {nsec}회 · 유의 {nsig}개 — 유의수준 5% 이므로 "
             f"{0.05 * nsec:.1f}개 가량은 우연으로 나온다(다중비교 보정 전)."
           )
    fig.text(0.030, 0.020, cav, fontsize=9.0, color="#334155", va="bottom",
             linespacing=1.55)
    fig.subplots_adjust(left=0.092, right=0.997, top=0.862, bottom=0.175)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140); plt.close(fig)
    print("wrote", out)


# ── (지표 2) 추세선 ───────────────────────────────────────────────────────────
def fig_trends(rows: list[dict], out: Path) -> None:
    have = [r for r in rows if r["insar"]]
    ncol = 4
    nrow = int(np.ceil(len(have) / ncol))
    fig_h = 2.9 * nrow + 1.15
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.35 * ncol, fig_h), squeeze=False)
    for k, r in enumerate(have):
        ax = axes[k // ncol][k % ncol]
        d = r["insar"]
        t = np.asarray(d["t_yr"]); y = np.asarray(d["median_mm"])
        ax.plot(t, y, "o", ms=2.8, color="#9FB6C8", zorder=2)
        ax.plot(t, y, "-", lw=0.7, color="#C8D6E0", zorder=1)
        tt = np.linspace(t.min(), t.max(), 400)
        ax.plot(tt, model(tt, d["lin"]["coef"]), "--", lw=1.6, color=GRAY, zorder=4,
                label=f"직선만 {d['lin']['v']:+.2f}±{d['lin']['ci']:.2f}")
        c = ORANGE if d["significant"] else GREEN
        ax.plot(tt, model(tt, d["ann"]["coef"]), "-", lw=2.0, color=c, zorder=5,
                label=f"연주기포함 {d['ann']['v']:+.2f}±{d['ann']['ci']:.2f}")
        ax.axhline(0, color="k", lw=0.8, alpha=.5)
        mark = "◆GNSS" if r["gnss"] else ""
        ax.set_title(f"{r['name']} {mark}   {d['n_points']}점 · {d['n_epochs']}시점",
                     fontsize=10.5, pad=4)
        ax.legend(fontsize=7.4, loc="upper left", framealpha=.9, handlelength=1.6)
        ax.grid(alpha=.22)
        ax.tick_params(labelsize=8)
        if k % ncol == 0:
            ax.set_ylabel("LOS 변위 [mm]", fontsize=9)
        if k // ncol == nrow - 1:
            ax.set_xlabel("경과 [년]", fontsize=9)
    for k in range(len(have), nrow * ncol):
        axes[k // ncol][k % ncol].axis("off")
    sp = have[0]["insar"]["span"]
    fig.suptitle("한강교량 추세선 — 데크 중앙값 LOS 시계열에 직선 / 직선+연주기를 얹는다  "
                 f"({sp[0]} ~ {sp[1]})\n"
                 "같은 데이터라도 직선만 맞추면 계절 주기가 기울기로 샌다 — 두 선의 차이가 그 크기다",
                 fontsize=14, fontweight="bold", y=1 - 0.16 / fig_h)
    fig.subplots_adjust(left=0.052, right=0.992, top=1 - 1.05 / fig_h,
                        bottom=0.75 / fig_h, hspace=0.44, wspace=0.20)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=135); plt.close(fig)
    print("wrote", out)


# ── (지표 3) GNSS 변동폭 ──────────────────────────────────────────────────────
def fig_gnss(gn: dict, rows: list[dict], out: Path) -> None:
    fig = plt.figure(figsize=(16.0, 7.2))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.15, 1.05], wspace=0.28)

    # (a) 월드컵대교 2024 방향별 변동폭
    a = fig.add_subplot(gs[0])
    w = gn["월드컵대교"]
    labs, vals, cols = [], [], []
    for tb in w["tables"]:
        if not tb.get("rows"):
            continue
        d = "교축" if "교축방향" in tb["direction_in_report"] else "교축직각"
        for r in tb["rows"]:
            labs.append(f"{r['sensor'].replace('GP_','')}\n{d}")
            vals.append(r["band"])
            cols.append(BLUE if d == "교축" else "#6FA8D6")
    a.bar(range(len(vals)), vals, color=cols)
    for i, v in enumerate(vals):
        a.text(i, v + 0.8, f"{v:.1f}", ha="center", fontsize=8.5)
    a.set_xticks(range(len(labs))); a.set_xticklabels(labs, fontsize=7.6)
    a.set_ylabel("변동폭 [mm]", fontsize=10)
    a.set_title("(a) 월드컵대교 GNSS 변동폭 (2024-01~11)\n"
                "보고서 p30 인쇄값 · 연직은 표가 없어 그래프뿐", fontsize=11.5, pad=8)
    a.grid(axis="y", alpha=.25)

    # (b) 샛강문화다리 연도별 변동폭
    b = fig.add_subplot(gs[1])
    s = gn["샛강문화다리"]
    yrs = [2022, 2023, 2024]
    x = np.arange(len(yrs))
    off, wdt = -0.315, 0.105
    blues, greens = ["#1F6FB2", "#4A90C8", "#8FBEDD"], ["#3E6B2A", "#6E9E52", "#A6C48A"]
    for tb in s["tables"]:
        horiz = "수평" if "상단" in tb["band_position"] else "연직(화살표)"
        pal = blues if horiz == "수평" else greens
        for j, g in enumerate(tb["groups"]):
            band = [next(r["band"] for r in g["rows"] if r["year"] == yy) for yy in yrs]
            b.bar(x + off, band, wdt * 0.94, label=f"{g['position']} · {horiz}",
                  color=pal[j], edgecolor="white", linewidth=0.6)
            off += wdt
    b.set_xticks(x); b.set_xticklabels([str(y) for y in yrs], fontsize=10)
    b.set_ylabel("변동폭 [mm]", fontsize=10)
    b.set_title("(b) 샛강문화다리 GNSS 변동폭 (일평균 기준)\n"
                "3년치가 있어 '해마다 커지는가' 를 볼 수 있는 유일한 곳", fontsize=11.5, pad=8)
    b.legend(fontsize=7.2, ncol=2, framealpha=.92)
    b.grid(axis="y", alpha=.25)

    # (c) 같은 양으로 본 InSAR 변동폭
    c = fig.add_subplot(gs[2])
    have = [r for r in rows if r["insar"]]
    have = sorted(have, key=lambda r: r["insar"]["p2p_mm"])
    yy = np.arange(len(have))
    c.barh(yy, [r["insar"]["p2p_mm"] for r in have], color="#B9C7D2",
           label="전체 변동폭(시계열 최대-최소)")
    c.barh(yy, [r["insar"]["annual_p2p_mm"] for r in have], height=0.5, color=GREEN,
           label="연주기 진폭×2(계절 성분)")
    c.set_yticks(yy)
    c.set_yticklabels([r["name"] + ("◆" if r["gnss"] else "") for r in have], fontsize=9)
    c.set_xlabel("변동폭 [mm] · LOS", fontsize=10)
    c.set_title("(c) 같은 양을 위성으로 — 교량별 LOS 변동폭\n"
                "GNSS 변동폭과 나란히 읽으라고 같은 단위로 낸다", fontsize=11.5, pad=8)
    c.legend(fontsize=8.2, loc="lower right", framealpha=.92)
    c.grid(axis="x", alpha=.25)

    fig.suptitle("GNSS ↔ InSAR — 보고서에 GNSS 수치가 있는 교량은 2개소뿐이고, "
                 "그 2개소는 InSAR 산출이 없다",
                 fontsize=14, fontweight="bold", y=0.985)
    fig.subplots_adjust(left=0.058, right=0.995, top=0.815, bottom=0.175)
    for ax, note in ((a, "InSAR 대조 불가 — 데크 ±30 m 안 PS 0/20000점"),
                     (b, "InSAR 대조 불가 — OSM·표준데이터에 좌표 없음(대상 밖)")):
        bb = ax.get_position()
        fig.text(bb.x0 + bb.width / 2, 0.048, note, ha="center", fontsize=9.5,
                 color=RED, fontweight="bold")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140); plt.close(fig)
    print("wrote", out)


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--shm", default="docs/bridges/hangang_shm_2024.json")
    ap.add_argument("--gnss", default="docs/bridges/hangang_gnss_2024.json")
    ap.add_argument("--json-out", default="docs/bridges/hangang_gnss_insar.json")
    ap.add_argument("--out-summary", default="docs/img/hangang_지표_종합.png")
    ap.add_argument("--out-trend", default="docs/img/hangang_추세선.png")
    ap.add_argument("--out-gnss", default="docs/img/hangang_gnss_변동폭.png")
    a = ap.parse_args()
    _use_korean_font(plt)

    root = Path(a.root)
    shm = {k: v for k, v in json.loads(Path(a.shm).read_text(encoding="utf-8")).items()
           if not k.startswith("_")}
    gn = {k: v for k, v in json.loads(Path(a.gnss).read_text(encoding="utf-8")).items()
          if not k.startswith("_")}

    rows = []
    for name, v in shm.items():
        items = v.get("items") or []
        has_gnss = any("GNSS" in it for it, _ in items)
        d = read_bridge(root / name)
        why = ""
        if d is None:
            why = (gn.get(name, {}).get("insar")
                   or v.get("insar")
                   or "project.h5 에 InSAR 산출 없음")
        verdict = v.get("verdict", "")
        report_ok = "지속관찰" not in verdict
        rows.append({
            "name": name, "page": v.get("page"), "gnss": has_gnss,
            "items": " · ".join(f"{it}{'' if st == 'O' else f'({st})'}" for it, st in items),
            "verdict": verdict,
            "verdict_short": verdict.replace("관리기준 이내", "기준 이내"),
            "report_trend": v.get("trend", ""),
            "insar": d, "why": why,
            "agree": "대조 불가" if d is None else agree(d["significant"], report_ok),
        })

    # 그림은 인쇄 순서(보고서 목차)대로
    fig_summary(rows, Path(a.out_summary))
    fig_trends(rows, Path(a.out_trend))
    fig_gnss(gn, rows, Path(a.out_gnss))

    slim = []
    for r in rows:
        d = r["insar"]
        slim.append({k: r[k] for k in
                     ("name", "page", "gnss", "items", "verdict", "report_trend",
                      "agree", "why")} |
                    ({} if d is None else {
                        "insar": {k: d[k] for k in
                                  ("n_points", "n_epochs", "span", "incidence_deg",
                                   "v_pt_med", "ci_pt_med", "lin", "ann",
                                   "v_vert_mm_yr", "ci_vert_mm_yr",
                                   "p2p_mm", "annual_p2p_mm", "significant",
                                   "sections", "section_only")}}))
    Path(a.json_out).write_text(json.dumps(
        {"_설명": "2024 한강교량 온라인 안전감시 보고 ↔ inframon InSAR 전수 대조",
         "_판정기준": "연주기 포함 적합의 95% CI 가 0 을 포함하면 '유의한 변위 없음'",
         "bridges": slim}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)

    print(f"\n{'교량':<10}{'점':>6}{'시점':>5}  {'LOS 연주기포함':>18}  {'연직환산':>16}  판정")
    for r in rows:
        d = r["insar"]
        if d is None:
            print(f"{r['name']:<10}{'—':>6}{'—':>5}  {'산출 없음':>18}  {'—':>16}  {r['why'][:44]}")
            continue
        s = "유의" if d["significant"] else "유의차 없음"
        print(f"{r['name']:<10}{d['n_points']:>6}{d['n_epochs']:>5}  "
              f"{d['ann']['v']:+8.2f} ± {d['ann']['ci']:5.2f}  "
              f"{d['v_vert_mm_yr']:+7.2f} ± {d['ci_vert_mm_yr']:5.2f}  {s} · {r['agree']}")
    ok = sum(1 for r in rows if r["agree"].startswith("일치"))
    print(f"\n대조 {len(rows)}개소 — 일치 {ok} · 대조불가 "
          f"{sum(1 for r in rows if r['insar'] is None)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
