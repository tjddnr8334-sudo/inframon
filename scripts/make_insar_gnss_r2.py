#!/usr/bin/env python3
"""위성(inframon) ↔ 보고서 계측 — **선형추세와 R²**.

세 가지를 낸다.

  ① 위성 선형추세   교면 결합 측점 중앙값 LOS 를 직선으로 — 전 기간(2018-06~2025-12)과
                   보고서 창(2022~24 또는 2024) 두 가지로 따로 낸다.
  ② 보고서 선형추세 보고서 월별 변위(처짐·신축·GNSS)를 같은 창에서 직선으로.
  ③ 둘 사이 R²     같은 **달**끼리 짝지어(위성은 그 달 취득의 평균) 상관을 잰다.
                   R² 는 짝지은 값들의 피어슨 상관 제곱이다.

**주의 — R² 를 '정확도' 로 읽으면 안 된다.**
두 값은 같은 양이 아니다(보고서는 한 지점의 처짐·신축·GNSS 연직, 위성은 교면 결합
측점의 LOS 중앙값). 크기도 축도 다르다. 여기서 R² 가 말하는 것은 **같은 달에 같은
방향으로 움직이는가** 하나뿐이다. 부호는 r 로 따로 적는다.

    python scripts/make_insar_gnss_r2.py

산출: docs/img/위성_계측_선형_R2.png · docs/bridges/insar_gnss_r2.json
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
DIM = "#55636F"
NAVY = "#12314F"


def insar_series(folder: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """교면 결합 측점의 **중앙값** LOS 시계열 → (십진연도, mm)."""
    import h5py

    p = folder / "project.h5"
    if not p.exists():
        return None
    with h5py.File(p, "r") as f:
        if "insar" not in f:
            return None
        los = np.asarray(f["insar/los"][()], float)
        lab = [s.decode() if isinstance(s, bytes) else str(s)
               for s in f["insar/date_labels"][()]]
    med = np.median(los, axis=0)
    t = []
    for s in lab:
        y, m, d = int(s[:4]), int(s[4:6]), int(s[6:8])
        doy = date(y, m, d).timetuple().tm_yday
        t.append(y + (doy - 0.5) / (366.0 if y % 4 == 0 else 365.0))
    o = np.argsort(t)
    return np.asarray(t)[o], med[o]


def report_series(auto: dict, eye: dict, name: str
                  ) -> tuple[np.ndarray, np.ndarray, str, str] | None:
    """보고서 월별값 → (십진연도, mm, 무엇인가, 어떻게 읽었나)."""
    for c in auto.get("charts", []):
        if c.get("bridge") == name and "monthly" in c:
            t, v = [], []
            for ykey, arr in c["monthly"].items():
                for i, q in enumerate(arr):
                    if q is None:
                        continue
                    t.append(int(ykey) + (i + 0.5) / 12.0)
                    v.append(float(q))
            if len(t) >= 6:
                o = np.argsort(t)
                return (np.asarray(t)[o], np.asarray(v)[o],
                        f"GNSS {c['dir']}변위 ({c['sensor']})", "자동 판독")
    rec = eye.get(name) or {}
    mon = rec.get("monthly") or {}
    t, v = [], []
    for k, arr in mon.items():
        if not isinstance(arr, list):
            continue
        try:
            yr = int(str(k)[-4:])
        except ValueError:
            continue
        for i, q in enumerate(arr[:12]):
            if q is None:
                continue
            t.append(yr + (i + 0.5) / 12.0)
            v.append(float(q))
    if len(t) < 6:
        return None
    o = np.argsort(t)
    return (np.asarray(t)[o], np.asarray(v)[o],
            str(rec.get("quantity") or "변위"), "눈 판독")


def fit(t: np.ndarray, y: np.ndarray) -> dict:
    """직선 적합 — 기울기[mm/yr] · 95% CI · 그 직선의 R²."""
    if len(t) < 3:
        return {}
    A = np.vstack([np.ones_like(t), t]).T
    c, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ c
    res = y - pred
    ss_t = float(np.sum((y - y.mean()) ** 2))
    r2 = float(1 - np.sum(res ** 2) / ss_t) if ss_t > 0 else float("nan")
    sig = float(np.sqrt(np.sum(res ** 2) / max(len(t) - 2, 1)))
    ci = 1.96 * sig / (np.std(t) * np.sqrt(len(t))) if np.std(t) > 0 else float("nan")
    return {"slope": float(c[1]), "ci": float(ci), "r2": r2, "n": int(len(t)),
            "coef": [float(c[0]), float(c[1])]}


def monthly_mean(t: np.ndarray, v: np.ndarray) -> dict[tuple[int, int], float]:
    """(연, 월) → 평균. 위성은 한 달에 여러 시점이 있을 수 있다."""
    acc: dict[tuple[int, int], list[float]] = {}
    for a, b in zip(t, v):
        y = int(a)
        m = min(12, max(1, int((a - y) * 12) + 1))
        acc.setdefault((y, m), []).append(float(b))
    return {k: float(np.mean(q)) for k, q in acc.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--eye", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/위성_계측_선형_R2.png")
    ap.add_argument("--json-out", default="docs/bridges/insar_gnss_r2.json")
    a = ap.parse_args()

    auto = json.loads(Path(a.auto).read_text(encoding="utf-8")) \
        if Path(a.auto).exists() else {}
    eye = json.loads(Path(a.eye).read_text(encoding="utf-8")) \
        if Path(a.eye).exists() else {}

    names = ["가양대교", "원효대교", "올림픽대교", "천호대교", "암사대교",
             "샛강문화다리", "월드컵대교"]
    rows = []
    for n in names:
        ins = insar_series(Path(a.root) / n)
        rep = report_series(auto, eye, n)
        if ins is None or rep is None:
            continue
        ti, vi = ins
        tr, vr, what, how = rep
        lo, hi = float(tr.min()), float(tr.max())
        sel = (ti >= lo - 0.12) & (ti <= hi + 0.12)
        r = {"name": n, "what": what, "how": how,
             "insar_span": [round(float(ti.min()), 2), round(float(ti.max()), 2)],
             "report_span": [round(lo, 2), round(hi, 2)],
             "insar_full": fit(ti, vi),
             "insar_win": fit(ti[sel], vi[sel] - float(np.median(vi[sel])))
             if sel.sum() >= 3 else {},
             "report": fit(tr, vr)}
        # 창이 1.5년보다 짧으면 기울기는 숫자만 나올 뿐 의미가 없다 — 천호대교
        # (2024-07~11, 위성 3시점)에서 +80 mm/yr 이 나왔다. 그렇다고 적는다.
        r["short"] = bool((hi - lo) < 1.5 or int(sel.sum()) < 8)
        mi, mr = monthly_mean(ti[sel], vi[sel]), monthly_mean(tr, vr)
        keys = sorted(set(mi) & set(mr))
        if len(keys) >= 4:
            x = np.asarray([mi[k] for k in keys])
            y = np.asarray([mr[k] for k in keys])
            cc = float(np.corrcoef(x, y)[0, 1])
            r["pair"] = {"n": len(keys), "r": round(cc, 3),
                         "r2": round(cc * cc, 3),
                         "months": [f"{k[0]}-{k[1]:02d}" for k in keys],
                         "insar": [round(q, 2) for q in x],
                         "report": [round(q, 2) for q in y]}
        rows.append(r)

    n = len(rows)
    fig, axes = plt.subplots(n, 2, figsize=(15.4, 2.75 * n),
                             gridspec_kw={"width_ratios": [2.15, 1]})
    axes = np.atleast_2d(axes)
    for i, r in enumerate(rows):
        ax, bx = axes[i, 0], axes[i, 1]
        ins = insar_series(Path(a.root) / r["name"])
        rep = report_series(auto, eye, r["name"])
        ti, vi = ins
        tr, vr, _, _ = rep
        lo, hi = r["report_span"]
        sel = (ti >= lo - 0.12) & (ti <= hi + 0.12)

        ax.plot(tr, vr, "-o", ms=3.2, lw=1.5, color=RED, alpha=.9, label="보고서 계측")
        if r["report"]:
            c = r["report"]["coef"]
            xs = np.linspace(tr.min(), tr.max(), 50)
            ax.plot(xs, c[0] + c[1] * xs, "--", lw=2.2, color=RED, alpha=.85)
        ax.set_ylabel("보고서 [mm]", color=RED, fontsize=9)
        ax.tick_params(axis="y", colors=RED, labelsize=8.5)
        ax.tick_params(axis="x", labelsize=8.5)

        a2 = ax.twinx()
        vv = vi - float(np.median(vi[sel])) if sel.sum() else vi
        a2.plot(ti, vv, "-", lw=1.1, color=BLUE, alpha=.45)
        a2.plot(ti[sel], vv[sel], "-o", ms=3.0, lw=1.5, color=BLUE, alpha=.9)
        if r["insar_win"]:
            c = r["insar_win"]["coef"]
            xs = np.linspace(lo, hi, 50)
            a2.plot(xs, c[0] + c[1] * xs, "--", lw=2.2, color=BLUE, alpha=.9)
        a2.set_ylabel("위성 LOS [mm]", color=BLUE, fontsize=9)
        a2.tick_params(axis="y", colors=BLUE, labelsize=8.5)
        ax.axvspan(lo, hi, color="#F1F5F9", zorder=0)
        ax.grid(alpha=.2)

        sl_r = r["report"].get("slope")
        sl_i = r["insar_win"].get("slope")
        sl_f = r["insar_full"].get("slope")
        ci_r = r["report"].get("ci", float("nan"))
        ci_i = r["insar_win"].get("ci", float("nan"))
        ci_f = r["insar_full"].get("ci", float("nan"))
        y0, y1 = int(r["insar_span"][0]), int(r["insar_span"][1])
        warn = "   ⚠ 창이 짧아 기울기를 믿을 수 없다" if r["short"] else ""
        ax.set_title(
            f"{r['name']} — {r['what']} ({r['how']}){warn}\n"
            f"보고서 {sl_r:+.2f} ± {ci_r:.2f} mm/yr · "
            f"위성(같은 창 {hi - lo:.1f}년) {sl_i:+.2f} ± {ci_i:.2f} · "
            f"위성(전 기간 {y0}~{y1}) {sl_f:+.2f} ± {ci_f:.2f}",
            fontsize=9.6, color=(RED if r["short"] else NAVY), pad=6)

        p = r.get("pair")
        if p:
            bx.plot(p["insar"], p["report"], "o", ms=5.5, color=GREEN, alpha=.85)
            x = np.asarray(p["insar"], float)
            y = np.asarray(p["report"], float)
            if np.std(x) > 0:
                k = np.polyfit(x, y, 1)
                xs = np.linspace(x.min(), x.max(), 30)
                bx.plot(xs, np.polyval(k, xs), "-", lw=2.0, color=GREEN, alpha=.8)
            bx.set_title(f"같은 달 짝짓기 n={p['n']} · r={p['r']:+.2f} · "
                         f"R²={p['r2']:.2f}", fontsize=10, color=NAVY, pad=6)
            bx.set_xlabel("위성 LOS [mm]", fontsize=9, color=BLUE)
            bx.set_ylabel("보고서 [mm]", fontsize=9, color=RED)
            bx.grid(alpha=.22)
            bx.tick_params(labelsize=8.5)
        else:
            bx.axis("off")
            bx.text(.5, .5, "같은 달로 짝지을 게 4개 미만", ha="center", va="center",
                    fontsize=9.5, color=DIM)

    fig.suptitle("위성 InSAR ↔ 보고서 계측 — 선형추세와 R²\n"
                 "왼쪽: 각자의 직선(점선) · 회색 띠가 보고서 기간  |  "
                 "오른쪽: 같은 달끼리 짝지은 산점도",
                 fontsize=14.5, fontweight="bold", y=0.995)
    fig.text(0.006, 0.004,
             "※ R² 를 '정확도' 로 읽으면 안 된다. 두 값은 같은 양이 아니다(보고서는 한 "
             "지점의 처짐·신축·GNSS 연직, 위성은 교면 결합 측점의 LOS 중앙값). 여기서 R² 가 "
             "말하는 것은 같은 달에 같은 방향으로 움직이는가 하나뿐이고, 방향은 r 의 부호가 "
             "말한다. 위성 곡선은 보고서 창의 중앙값을 빼 0 에 맞춰 놓았다.\n"
             "※ 창이 1.5년보다 짧거나 위성 시점이 8개 미만이면 기울기를 붉게 적고 "
             "'믿을 수 없다' 고 표시한다 — 천호대교(2024-07~11 · 위성 3시점)는 "
             "+80 mm/yr 이 나온다. 숫자가 나온다고 값이 아니다.",
             fontsize=8.8, color=DIM)
    fig.tight_layout(rect=(0, 0.013, 1, 0.965))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=135)
    plt.close(fig)
    print("wrote", a.out)

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "위성 InSAR ↔ 보고서 계측의 선형추세와 상관",
         "_주의": "두 값은 같은 양이 아니다. R² 는 '같은 달에 같은 방향으로 움직이는가' "
                "만 말한다 — 정확도가 아니다.",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)

    print(f"\n{'교량':<12}{'보고서 기울기':>14}{'위성(같은창)':>14}{'위성(전기간)':>14}"
          f"{'n':>4}{'r':>7}{'R²':>7}")
    for r in rows:
        p = r.get("pair") or {}
        print(f"{r['name']:<12}{r['report'].get('slope', float('nan')):>+13.2f} "
              f"{r['insar_win'].get('slope', float('nan')):>+13.2f} "
              f"{r['insar_full'].get('slope', float('nan')):>+13.2f} "
              f"{p.get('n', 0):>3} {p.get('r', float('nan')):>+6.2f} "
              f"{p.get('r2', float('nan')):>6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
