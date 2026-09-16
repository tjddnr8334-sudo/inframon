#!/usr/bin/env python3
"""보고서 계측과 **가장 닮은 PS 점**을 찾는다 — 교면 중앙값 말고, 점 하나씩.

앞선 대조는 교면 결합 측점의 **중앙값**을 보고서와 맞댔다. 그런데 보고서 계측기는
교량 위 **한 지점**에 붙어 있다. 중앙값은 교축 구간이 서로 지워진 값이라(가양대교
연주기가 1/10 로 죽었다) 애초에 같은 것을 재고 있지 않다.

그래서 여기서는 점을 **하나씩** 본다.
  1. 점마다 그 달의 취득을 평균해 월값을 만든다.
  2. 보고서 월값과 같은 달끼리 짝지어 상관 r 을 잰다.
  3. 교축 위치(deck_station)에 따라 r 이 어떻게 변하는지 그린다.
  4. 가장 닮은 점의 시계열을 보고서와 겹쳐 그린다.

**고른 점의 r 을 그대로 믿으면 안 된다.**
점이 수십~수백 개인데 그중 최댓값을 고르면, 아무 상관이 없어도 꽤 큰 r 이 나온다.
그래서 보고서 값을 무작위로 섞어 같은 탐색을 200번 돌려 **우연히 나올 수 있는 최댓값**
(95 백분위)을 함께 낸다. 그 선을 넘지 못하면 "찾았다" 고 말할 수 없다.

    python scripts/make_ps_match.py

산출: docs/img/PS점_계측_닮은점.png · docs/bridges/ps_match.json
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


def load_points(folder: Path):
    """점별 LOS 시계열 · 취득일 · 교축 위치 · 부재."""
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
    ym = []
    for s in lab:
        ym.append((int(s[:4]), int(s[4:6])))
    return los, ym, st, mem


def report_monthly(auto: dict, eye: dict, name: str):
    """보고서 월값 → ({(연,월): 값}, 무엇인가, 어떻게 읽었나)."""
    for c in auto.get("charts", []):
        if c.get("bridge") == name and "monthly" in c:
            d = {}
            for ykey, arr in c["monthly"].items():
                for i, q in enumerate(arr):
                    if q is not None:
                        d[(int(ykey), i + 1)] = float(q)
            if len(d) >= 8:
                return d, f"GNSS {c['dir']}변위 ({c['sensor']})", "자동 판독"
    rec = eye.get(name) or {}
    d = {}
    for k, arr in (rec.get("monthly") or {}).items():
        if not isinstance(arr, list):
            continue
        try:
            yr = int(str(k)[-4:])
        except ValueError:
            continue
        for i, q in enumerate(arr[:12]):
            if q is not None:
                d[(yr, i + 1)] = float(q)
    if len(d) < 8:
        return None
    return d, str(rec.get("quantity") or "변위"), "눈 판독"


def match(folder: Path, rep: dict, n_perm: int = 200, seed: int = 7):
    """점마다 보고서와의 r — 그리고 **우연히 나올 수 있는 최댓값**까지."""
    got = load_points(folder)
    if got is None:
        return None
    los, ym, st, mem = got
    keys = sorted(set(ym) & set(rep))
    if len(keys) < 6:
        return None
    idx = {k: [i for i, q in enumerate(ym) if q == k] for k in keys}
    X = np.vstack([los[:, idx[k]].mean(axis=1) for k in keys]).T   # 점 × 달
    y = np.asarray([rep[k] for k in keys], float)

    Xc = X - X.mean(1, keepdims=True)
    sx = Xc.std(1)
    yc = y - y.mean()
    sy = float(yc.std())
    ok = (sx > 1e-9) & np.isfinite(sx)
    r = np.full(X.shape[0], np.nan)
    if sy > 1e-9:
        r[ok] = (Xc[ok] @ yc) / (len(keys) * sx[ok] * sy)

    rng = np.random.default_rng(seed)
    null = []
    for _ in range(n_perm):
        yp = rng.permutation(yc)
        rp = (Xc[ok] @ yp) / (len(keys) * sx[ok] * float(yp.std()))
        null.append(float(np.nanmax(np.abs(rp))))
    thr = float(np.percentile(null, 95)) if null else float("nan")

    j = int(np.nanargmax(np.abs(r)))
    return {"keys": keys, "X": X, "y": y, "r": r, "best": j, "null95": thr,
            "st": st, "mem": mem, "n_pairs": len(keys)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--eye", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/PS점_계측_닮은점.png")
    ap.add_argument("--json-out", default="docs/bridges/ps_match.json")
    a = ap.parse_args()

    auto = json.loads(Path(a.auto).read_text(encoding="utf-8")) \
        if Path(a.auto).exists() else {}
    eye = json.loads(Path(a.eye).read_text(encoding="utf-8")) \
        if Path(a.eye).exists() else {}

    names = ["가양대교", "원효대교", "올림픽대교", "암사대교", "샛강문화다리",
             "천호대교", "월드컵대교"]
    rows, keep = [], []
    for n in names:
        rp = report_monthly(auto, eye, n)
        if rp is None:
            continue
        rep, what, how = rp
        m = match(Path(a.root) / n, rep)
        if m is None:
            continue
        j, r = m["best"], m["r"]
        rows.append({
            "name": n, "what": what, "how": how, "n_pairs": m["n_pairs"],
            "n_points": int(len(r)),
            "best_r": round(float(r[j]), 3), "best_r2": round(float(r[j]) ** 2, 3),
            "best_station_m": (None if not np.isfinite(m["st"][j])
                               else round(float(m["st"][j]), 1)),
            "best_member": MEMBER_KO.get(int(m["mem"][j]), str(int(m["mem"][j]))),
            "null95_max_abs_r": round(m["null95"], 3),
            "beats_null": bool(abs(float(r[j])) > m["null95"]),
            "median_abs_r": round(float(np.nanmedian(np.abs(r))), 3),
        })
        keep.append((n, m, what, how))

    n = len(keep)
    fig, axes = plt.subplots(n, 3, figsize=(16.6, 2.7 * n),
                             gridspec_kw={"width_ratios": [1.15, 1.9, 1]})
    axes = np.atleast_2d(axes)
    for i, (nm, m, what, how) in enumerate(keep):
        ax, bx, cx = axes[i]
        r, st, j = m["r"], m["st"], m["best"]
        good = np.isfinite(r) & np.isfinite(st)

        ax.axhspan(-m["null95"], m["null95"], color="#EEF2F6", zorder=0)
        ax.axhline(0, color="#888", lw=.8)
        ax.plot(st[good], r[good], "o", ms=3.4, color=BLUE, alpha=.65)
        ax.plot(st[j], r[j], "*", ms=15, color=RED, zorder=5)
        ax.set_xlabel("교축 위치 [m]", fontsize=9)
        ax.set_ylabel("보고서와의 r", fontsize=9)
        ax.set_title(f"{nm} — 점마다의 r ({len(r)}점)\n"
                     f"회색 띠 = 우연히 나올 수 있는 범위(|r|<{m['null95']:.2f})",
                     fontsize=9.6, color=NAVY, pad=6)
        ax.grid(alpha=.2)
        ax.tick_params(labelsize=8.5)

        t = np.arange(len(m["keys"]))
        lab = [f"{k[0]%100:02d}-{k[1]:02d}" for k in m["keys"]]
        bx.plot(t, m["y"], "-o", ms=3.4, lw=1.6, color=RED, label="보고서 계측")
        bx.set_ylabel("보고서 [mm]", color=RED, fontsize=9)
        bx.tick_params(axis="y", colors=RED, labelsize=8.5)
        b2 = bx.twinx()
        b2.plot(t, m["X"][j], "-o", ms=3.4, lw=1.6, color=BLUE,
                label="가장 닮은 PS 점")
        b2.set_ylabel("PS 점 LOS [mm]", color=BLUE, fontsize=9)
        b2.tick_params(axis="y", colors=BLUE, labelsize=8.5)
        step = max(1, len(t) // 12)
        bx.set_xticks(t[::step])
        bx.set_xticklabels(lab[::step], fontsize=8, rotation=45)
        bx.grid(alpha=.2)
        sm = "" if m["st"][j] != m["st"][j] else f" · 교축 {m['st'][j]:.0f} m"
        bx.set_title(f"{nm} — {what} ({how}) ↔ 가장 닮은 점{sm} "
                     f"({MEMBER_KO.get(int(m['mem'][j]), '')})",
                     fontsize=9.6, color=NAVY, pad=6)

        cx.plot(m["X"][j], m["y"], "o", ms=5.5, color=GREEN, alpha=.85)
        if np.std(m["X"][j]) > 0:
            k = np.polyfit(m["X"][j], m["y"], 1)
            xs = np.linspace(m["X"][j].min(), m["X"][j].max(), 30)
            cx.plot(xs, np.polyval(k, xs), "-", lw=2.0, color=GREEN, alpha=.8)
        beats = abs(r[j]) > m["null95"]
        cx.set_title(f"n={m['n_pairs']} · r={r[j]:+.2f} · R²={r[j]**2:.2f}\n"
                     + ("우연 범위를 넘는다" if beats else "우연 범위 안 — 못 찾았다"),
                     fontsize=9.6, color=(GREEN if beats else RED), pad=6)
        cx.set_xlabel("PS 점 LOS [mm]", fontsize=9, color=BLUE)
        cx.set_ylabel("보고서 [mm]", fontsize=9, color=RED)
        cx.grid(alpha=.22)
        cx.tick_params(labelsize=8.5)

    fig.suptitle("보고서 계측과 가장 닮은 PS 점 — 교면 중앙값이 아니라 점 하나씩\n"
                 "왼쪽: 교축 위치별 r  |  가운데: 그 점의 시계열과 보고서  |  "
                 "오른쪽: 짝지은 산점도",
                 fontsize=14.5, fontweight="bold", y=0.995)
    fig.text(0.006, 0.004,
             "※ 점이 수십~수백 개인데 그중 최댓값을 고르면 아무 상관이 없어도 꽤 큰 r 이 "
             "나온다. 그래서 보고서 값을 무작위로 섞어 같은 탐색을 200번 돌려 우연히 나올 수 "
             "있는 최댓값(95 백분위)을 회색 띠로 그렸다. 띠를 넘지 못하면 '찾았다' 고 말할 수 "
             "없다.", fontsize=8.8, color=DIM)
    fig.tight_layout(rect=(0, 0.012, 1, 0.962))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=135)
    plt.close(fig)
    print("wrote", a.out)

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "보고서 계측과 가장 닮은 PS 점 탐색",
         "_주의": "여러 점 중 최댓값을 고른 값이다. 무작위 섞기 200회의 최대 |r| 95 "
                "백분위(null95_max_abs_r)를 넘지 못하면 우연과 구분되지 않는다.",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)

    print(f"\n{'교량':<12}{'점':>5}{'짝':>4}{'최적 r':>8}{'R²':>7}{'우연한계':>9}"
          f"{'판정':>10}  위치")
    for r in rows:
        print(f"{r['name']:<12}{r['n_points']:>5}{r['n_pairs']:>4}"
              f"{r['best_r']:>+8.2f}{r['best_r2']:>7.2f}{r['null95_max_abs_r']:>9.2f}"
              f"{('넘는다' if r['beats_null'] else '못 넘는다'):>10}  "
              f"교축 {r['best_station_m']} m · {r['best_member']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
