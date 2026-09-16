#!/usr/bin/env python3
"""**추세가 같은가** — 보고서 계측의 기울기 ↔ PS 점 하나하나의 기울기.

앞의 `make_ps_match.py` 는 달마다의 오르내림(상관)을 봤다. 그건 연주기가 지배해서
추세 질문에는 답하지 못한다. 여기서는 **기울기만** 본다.

  1. 보고서 계측을 같은 창에서 직선으로 적합 → b_rep ± 95% CI
  2. **PS 점마다** 같은 창에서 직선으로 적합 → b_i ± 95% CI
  3. 교면 결합 측점 중앙값도 따로 → b_med ± CI
  4. 일치 판정 — 두 신뢰구간이 겹치면 '추세가 다르다고 말할 수 없다'

부호 — LOS 는 위성에서 **멀어질수록 음수**다. 보고서 처짐·신축은 양이 제각각이라
부호 규약이 서로 다를 수 있다. 그래서 LOS 를 입사각으로 **연직**으로 환산한 값도 같이
낸다(÷cos θ). 그래도 '처짐 -3 mm/yr' 와 '연직 -3 mm/yr' 가 같은 것을 뜻하는지는
계측기 종류가 정한다 — 그 판단까지 코드가 하지 않는다.

    python scripts/make_trend_agree.py

산출: docs/img/추세일치.png · docs/bridges/trend_agree.json
"""

from __future__ import annotations

import argparse
import json
from datetime import date
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
DIM = "#55636F"
NAVY = "#12314F"
MEMBER_KO = {0: "슬래브", 1: "교각", 2: "교대", 3: "받침"}


def dec_year(s: str) -> float:
    y, m, d = int(s[:4]), int(s[4:6]), int(s[6:8])
    doy = date(y, m, d).timetuple().tm_yday
    return y + (doy - 0.5) / (366.0 if y % 4 == 0 else 365.0)


def load_points(folder: Path):
    import h5py

    p = folder / "project.h5"
    if not p.exists():
        return None
    with h5py.File(p, "r") as f:
        if "insar" not in f:
            return None
        g = f["insar"]
        los = np.asarray(g["los"][()], float)
        lab = [s.decode() if isinstance(s, bytes) else str(s)
               for s in g["date_labels"][()]]
        st = np.asarray(g["deck_station"][()], float) if "deck_station" in g \
            else np.full(los.shape[0], np.nan)
        mem = np.asarray(g["member"][()]).astype(int) if "member" in g \
            else np.zeros(los.shape[0], int)
        inc = float(np.nanmedian(np.asarray(g["incidence_deg"][()], float))) \
            if "incidence_deg" in g else 39.0
    return los, np.asarray([dec_year(s) for s in lab]), st, mem, inc


def report_series(auto: dict, eye: dict, name: str):
    for c in auto.get("charts", []):
        if c.get("bridge") == name and "monthly" in c:
            t, v = [], []
            for ykey, arr in c["monthly"].items():
                for i, q in enumerate(arr):
                    if q is not None:
                        t.append(int(ykey) + (i + 0.5) / 12.0)
                        v.append(float(q))
            if len(t) >= 8:
                o = np.argsort(t)
                return (np.asarray(t)[o], np.asarray(v)[o],
                        f"GNSS {c['dir']}변위 ({c['sensor']})", "자동 판독")
    rec = eye.get(name) or {}
    t, v = [], []
    for k, arr in (rec.get("monthly") or {}).items():
        if not isinstance(arr, list):
            continue
        try:
            yr = int(str(k)[-4:])
        except ValueError:
            continue
        for i, q in enumerate(arr[:12]):
            if q is not None:
                t.append(yr + (i + 0.5) / 12.0)
                v.append(float(q))
    if len(t) < 8:
        return None
    o = np.argsort(t)
    return (np.asarray(t)[o], np.asarray(v)[o],
            str(rec.get("quantity") or "변위"), "눈 판독")


def slope_ci(t: np.ndarray, Y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """여러 계열의 기울기와 95% CI를 한 번에. Y 는 (계열 × 시점)."""
    A = np.vstack([np.ones_like(t), t]).T
    coef, *_ = np.linalg.lstsq(A, Y.T, rcond=None)          # 2 × 계열
    res = Y.T - A @ coef
    dof = max(len(t) - 2, 1)
    sig = np.sqrt((res ** 2).sum(0) / dof)
    denom = np.std(t) * np.sqrt(len(t))
    ci = 1.96 * sig / denom if denom > 0 else np.full(Y.shape[0], np.nan)
    return coef[1], ci


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--eye", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/추세일치.png")
    ap.add_argument("--json-out", default="docs/bridges/trend_agree.json")
    a = ap.parse_args()

    auto = json.loads(Path(a.auto).read_text(encoding="utf-8")) \
        if Path(a.auto).exists() else {}
    eye = json.loads(Path(a.eye).read_text(encoding="utf-8")) \
        if Path(a.eye).exists() else {}

    names = ["가양대교", "원효대교", "올림픽대교", "암사대교", "샛강문화다리",
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
        b_pt, ci_pt = slope_ci(ti[sel], los[:, sel])
        med = np.median(los[:, sel], axis=0)
        b_med, ci_med = slope_ci(ti[sel], med[None, :])
        b_med, ci_med = float(b_med[0]), float(ci_med[0])

        cosv = float(np.cos(np.radians(inc)))
        # 두 95% CI 가 겹치면 '추세가 다르다' 고 말할 수 없다.
        ok = np.isfinite(b_pt) & np.isfinite(ci_pt)
        overlap = ok & (np.abs(b_pt - b_rep) <= (ci_pt + ci_rep))
        r = {"name": nm, "what": what, "how": how,
             "window": [round(lo, 2), round(hi, 2)], "years": round(hi - lo, 2),
             "n_epochs_in_window": int(sel.sum()), "n_points": int(los.shape[0]),
             "incidence_deg": round(inc, 1),
             "report_slope": round(b_rep, 3), "report_ci": round(ci_rep, 3),
             "deck_median_slope": round(b_med, 3), "deck_median_ci": round(ci_med, 3),
             "deck_median_vert": round(b_med / cosv, 3),
             "point_slope_p05": round(float(np.nanpercentile(b_pt, 5)), 3),
             "point_slope_p50": round(float(np.nanmedian(b_pt)), 3),
             "point_slope_p95": round(float(np.nanpercentile(b_pt, 95)), 3),
             "n_overlap": int(overlap.sum()),
             "frac_overlap": round(float(overlap.mean()), 3),
             "median_overlaps": bool(abs(b_med - b_rep) <= (ci_med + ci_rep)),
             # 이 자료로 가릴 수 있는 **가장 작은 차이**. 이보다 작은 차이는 "같다" 도
             # "다르다" 도 말할 수 없다 — 겹친다는 말만으로는 아무것도 못 판정한다.
             "resolvable_mm_yr": round(float(ci_med + ci_rep), 2),
             "gap_mm_yr": round(float(abs(b_med - b_rep)), 2),
             "short": bool((hi - lo) < 1.5 or int(sel.sum()) < 8)}
        rows.append(r)
        keep.append((nm, st, b_pt, ci_pt, overlap, b_rep, ci_rep, b_med, ci_med,
                     what, how, r))

    n = len(keep)
    fig, axes = plt.subplots(n, 2, figsize=(15.8, 2.6 * n),
                             gridspec_kw={"width_ratios": [2.0, 1]})
    axes = np.atleast_2d(axes)
    for i, (nm, st, b_pt, ci_pt, ov, b_rep, ci_rep, b_med, ci_med, what, how,
            r) in enumerate(keep):
        ax, bx = axes[i]
        good = np.isfinite(b_pt) & np.isfinite(st)
        ax.axhspan(b_rep - ci_rep, b_rep + ci_rep, color="#FBE9E7", zorder=0)
        ax.axhline(b_rep, color=RED, lw=2.0, zorder=1)
        ax.errorbar(st[good & ~ov], b_pt[good & ~ov], yerr=ci_pt[good & ~ov],
                    fmt="o", ms=3.0, color="#9AA7B4", ecolor="#D3DAE1",
                    elinewidth=.8, capsize=0, alpha=.8, zorder=2)
        ax.errorbar(st[good & ov], b_pt[good & ov], yerr=ci_pt[good & ov],
                    fmt="o", ms=3.4, color=GREEN, ecolor="#AEDCC6",
                    elinewidth=.9, capsize=0, alpha=.9, zorder=3)
        ax.axhline(b_med, color=BLUE, lw=1.8, ls="--", zorder=4)
        ax.set_xlabel("교축 위치 [m]", fontsize=9)
        ax.set_ylabel("추세 [mm/yr]", fontsize=9)
        warn = "   ⚠ 창이 짧다" if r["short"] else ""
        ax.set_title(
            f"{nm} — {what} ({how}){warn}\n"
            f"보고서 {b_rep:+.2f} ± {ci_rep:.2f}  ·  교면 중앙값 {b_med:+.2f} ± "
            f"{ci_med:.2f}  ·  차이 {r['gap_mm_yr']:.2f} vs 분해능 "
            f"{r['resolvable_mm_yr']:.2f} mm/yr  ·  겹치는 점 "
            f"{r['n_overlap']}/{r['n_points']}",
            fontsize=9.6, color=NAVY, pad=6)
        ax.grid(alpha=.2)
        ax.tick_params(labelsize=8.5)

        bx.hist(b_pt[np.isfinite(b_pt)], bins=24, color="#C9D6E2",
                edgecolor="white")
        bx.axvspan(b_rep - ci_rep, b_rep + ci_rep, color="#FBE9E7", zorder=0)
        bx.axvline(b_rep, color=RED, lw=2.0, label="보고서")
        bx.axvline(b_med, color=BLUE, lw=1.8, ls="--", label="교면 중앙값")
        bx.set_xlabel("추세 [mm/yr]", fontsize=9)
        bx.set_ylabel("점 수", fontsize=9)
        verdict = ("판정 보류 — 분해능보다 작은 차이"
                   if r["median_overlaps"] else "추세가 다르다")
        bx.set_title("점 기울기 분포  ·  " + verdict,
                     fontsize=9.6,
                     color=(GREEN if r["median_overlaps"] else ORANGE), pad=6)
        bx.legend(fontsize=8, framealpha=.9)
        bx.grid(alpha=.2)
        bx.tick_params(labelsize=8.5)

    fig.suptitle("추세가 같은가 — 보고서 계측의 기울기 ↔ PS 점 하나하나의 기울기\n"
                 "붉은 선·띠 = 보고서 추세 ± 95% CI · 파란 점선 = 교면 중앙값 · "
                 "초록 점 = 보고서와 신뢰구간이 겹치는 점",
                 fontsize=14.5, fontweight="bold", y=0.995)
    fig.text(0.006, 0.004,
             "※ '겹친다' 는 두 95% 신뢰구간이 만난다는 뜻이고, 그건 '추세가 같다' 가 "
             "아니라 '다르다고 말할 수 없다' 이다 — 구간이 넓으면 무엇이든 겹친다. "
             "LOS 는 위성에서 멀어질수록 음수이고 보고서 계측은 양마다 부호 규약이 다르다. "
             "크기를 맞대려면 입사각으로 연직 환산이 필요하고(÷cosθ), 그 값도 JSON 에 "
             "같이 적었다.\n"
             "※ 그래서 '분해능' 을 같이 적는다 — 두 CI 를 더한 값이고, 이보다 작은 차이는 "
             "이 자료로 같다고도 다르다고도 말할 수 없다. 지금은 분해능이 ±2~±80 mm/yr 라 "
             "대부분의 판정이 '보류' 다.", fontsize=8.8, color=DIM)
    fig.tight_layout(rect=(0, 0.012, 1, 0.962))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=135)
    plt.close(fig)
    print("wrote", a.out)

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "보고서 계측 추세 ↔ PS 점별 추세의 일치 여부",
         "_판정": "두 95% CI 가 겹치면 '추세가 다르다고 말할 수 없다'. 같다는 증명이 "
                "아니다 — CI 가 넓으면 무엇이든 겹친다.",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)

    print(f"\n{'교량':<12}{'창':>5}{'보고서 추세':>16}{'교면 중앙값':>16}"
          f"{'점 5~95%':>18}{'겹침':>9}{'중앙값':>8}")
    for r in rows:
        print(f"{r['name']:<12}{r['years']:>5.1f}"
              f"{r['report_slope']:>+9.2f}±{r['report_ci']:<6.2f}"
              f"{r['deck_median_slope']:>+9.2f}±{r['deck_median_ci']:<6.2f}"
              f"{r['point_slope_p05']:>+8.2f}~{r['point_slope_p95']:<+8.2f}"
              f"{r['n_overlap']:>4}/{r['n_points']:<4}"
              f"{r['gap_mm_yr']:>7.2f}{r['resolvable_mm_yr']:>8.2f}"
              f"{'보류' if r['median_overlaps'] else '다름':>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
