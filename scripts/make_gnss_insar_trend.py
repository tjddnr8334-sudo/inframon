#!/usr/bin/env python3
"""보고서 GNSS ↔ 위성 InSAR — **같은 기간, 같은 적합으로** 추세선을 맞댄다.

기간을 맞춘다
-------------
보고서(2024 한강교량 온라인 안전감시 최종보고)가 실제로 덮는 기간은 딱 두 가지다.

  · 월드컵대교      GNSS 2024-01 ~ 2024-11 (한 해)
  · 샛강문화다리    GNSS 2022 ~ 2024 (연도별 3점)

위성 스택은 2018-06 ~ 2025-12 로 8년이다. 그 8년 추세를 보고서의 1년·3년과 나란히 놓으면
애초에 같은 것을 재고 있지 않다. 그래서 이 그림은 **위성 시계열을 보고서 기간만 잘라
다시 적합**해서 맞댄다. 잘랐을 때와 안 잘랐을 때가 얼마나 다른지도 같이 보인다 —
"창을 어디로 잡았나"가 결론을 바꾸기 때문이다.

  (a) 월드컵대교  — 보고서 GNSS 2024 변동폭 ↔ 같은 기간 InSAR 시계열·추세선
  (b) 샛강문화다리 — 보고서 GNSS 2022~24 연도값 ↔ 같은 기간 InSAR 시계열·추세선
  (c) 기울기 맞댐 — 보고서 GNSS vs InSAR(같은 창)
  (d) 창을 바꾸면 — 교량별 InSAR 속도: 전체 8년 / 2022~24 / 2024

읽을 때 조심할 것
------------------
· 보고서 GNSS 는 원시 시계열이 아니라 **인쇄된 Min/Max/변동폭**이다. 연도별 3점이 있는
  샛강문화다리만 중점 이동으로 기울기를 낼 수 있고, 그것도 거친 값이다.
· 1년 창(월드컵)은 위성 시점이 ~6개뿐이라 신뢰구간이 넓고 연주기를 분리할 수 없다.
  그래서 1년 창은 직선만 맞춘다 — 그렇게 적어 둔다.

    python scripts/make_gnss_insar_trend.py

산출: docs/img/gnss_insar_추세선.png · docs/bridges/gnss_insar_추세.json
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

from inframon.insar.chainage import _use_korean_font

sys.path.insert(0, str(ROOT / "scripts"))
from make_hangang_gnss_insar import model, read_bridge, window   # noqa: E402

GREEN, ORANGE, RED, BLUE, NAVY, GRAY = (
    "#2E7D32", "#E06C2C", "#C03028", "#1F6FB2", "#123A5E", "#8A8F96")

# 보고서가 덮는 기간 — 여기가 비교의 기준이다.
WIN = {"월드컵대교": ("20240101", "20241130", "2024-01 ~ 11"),
       "샛강문화다리": ("20220101", "20241231", "2022 ~ 2024")}


# ── 보고서 GNSS 표 읽기 ───────────────────────────────────────────────────────
def report_yearly(gn: dict, bridge: str) -> list[dict]:
    """연도별 Min/Max 가 3년 있는 표 → 중점 이동 기울기(mm/yr). 거친 값이다."""
    out = []
    for tb in (gn.get(bridge, {}).get("tables") or []):
        for g in (tb.get("groups") or []):
            rows = [r for r in g["rows"] if "min" in r and "max" in r]
            if len(rows) < 3:
                continue
            yr = np.array([r["year"] for r in rows], float)
            mid = np.array([(r["min"] + r["max"]) / 2 for r in rows], float)
            A = np.vstack([np.ones_like(yr), yr - yr.mean()]).T
            c, *_ = np.linalg.lstsq(A, mid, rcond=None)
            r = mid - A @ c
            sig = float(np.sqrt(np.sum(r ** 2) / max(len(yr) - 2, 1)))
            se = sig * float(np.sqrt(np.linalg.inv(A.T @ A)[1, 1]))
            out.append({"position": g["position"],
                        "vertical": "연직" in tb.get("band_position", ""),
                        "years": yr.tolist(), "mid": mid.tolist(),
                        "band": [r["band"] for r in rows],
                        "v": float(c[1]), "ci": 1.96 * se})
    return out


def report_single_year(gn: dict, bridge: str) -> list[dict]:
    """한 해치 Min/Max/변동폭 표(월드컵대교 2024) — 기울기는 못 낸다. 폭만 읽는다."""
    out = []
    for tb in (gn.get(bridge, {}).get("tables") or []):
        for r in (tb.get("rows") or []):
            out.append({"sensor": r["sensor"].replace("GP_", ""),
                        "direction": ("교축" if "교축방향" in tb["direction_in_report"]
                                      else "교축직각"),
                        "min": r["min"], "max": r["max"], "band": r["band"]})
    return out


# ── 그림 ──────────────────────────────────────────────────────────────────────
def _series(ax, w: dict, *, title: str, note: str):
    """보고서 기간만 잘라 다시 적합한 위성 시계열 + 추세선."""
    t = np.asarray(w["t_yr"]); y = np.asarray(w["median_mm"])
    ax.plot(t, y, "o-", ms=4.5, lw=0.8, color="#9FB6C8", zorder=2)
    tt = np.linspace(float(t.min()), float(t.max()), 400)
    ax.plot(tt, model(tt, w["lin"]["coef"]), "--", lw=1.8, color=GRAY, zorder=4,
            label=f"직선만  {w['lin']['v']:+.2f} ± {w['lin']['ci']:.2f} mm/yr")
    if w["annual_fit"]:
        c = ORANGE if w["significant"] else GREEN
        ax.plot(tt, model(tt, w["ann"]["coef"]), "-", lw=2.2, color=c, zorder=5,
                label=f"연주기 포함  {w['ann']['v']:+.2f} ± {w['ann']['ci']:.2f} mm/yr")
    ax.axhline(0, color="k", lw=0.8, alpha=.5)
    ax.set_title(title, fontsize=11, pad=5)
    ax.set_xlabel("경과 [년]", fontsize=9)
    ax.set_ylabel("LOS 변위 [mm]", fontsize=9)
    ax.legend(fontsize=8, loc="upper left", framealpha=.92, handlelength=1.8)
    ax.grid(alpha=.22)
    ax.tick_params(labelsize=8)
    ax.text(0.99, 0.02, note, transform=ax.transAxes, fontsize=7.8, color=GRAY,
            ha="right", va="bottom")


def figure(data: dict, out: Path) -> None:
    fig = plt.figure(figsize=(17.0, 10.6))
    gs = fig.add_gridspec(2, 3, height_ratios=[1.0, 1.0],
                          left=0.055, right=0.99, top=0.885, bottom=0.065,
                          hspace=0.48, wspace=0.26)

    # (a) 월드컵대교 — 보고서 GNSS 2024 변동폭
    a0 = fig.add_subplot(gs[0, 0])
    wc = data["월드컵대교"]
    rows = wc["report_rows"]
    xs = np.arange(len(rows))
    cols = [BLUE if r["direction"] == "교축" else "#6FA8D6" for r in rows]
    a0.bar(xs, [r["band"] for r in rows], color=cols)
    for i, r in enumerate(rows):
        a0.text(i, r["band"] + 0.7, f"{r['band']:.1f}", ha="center", fontsize=8)
    a0.set_xticks(xs)
    a0.set_xticklabels([f"{r['sensor']}\n{r['direction']}" for r in rows], fontsize=7.4)
    a0.set_ylabel("변동폭 [mm]", fontsize=9.5)
    a0.set_title("월드컵대교 — 보고서 GNSS 변동폭 (2024-01~11)\n"
                 "한 해치라 기울기는 못 낸다 · 연직은 표 없이 그래프뿐", fontsize=11, pad=5)
    a0.grid(axis="y", alpha=.25)

    a1 = fig.add_subplot(gs[0, 1])
    if wc["insar_win"]:
        _series(a1, wc["insar_win"],
                title="월드컵대교 — 같은 기간 위성 InSAR (2024-01~11)",
                note=f"{wc['insar_win']['n_epochs']}시점 · 1년이라 직선만")
    else:
        a1.axis("off")
        a1.text(.5, .5, "같은 기간 위성 시점이 모자람", ha="center", va="center",
                fontsize=11, color=RED)

    # (b) 샛강문화다리
    b0 = fig.add_subplot(gs[0, 2])
    sg = data["샛강문화다리"]
    for i, r in enumerate(sg["report_rows"]):
        col = ([BLUE, "#4A90C8", "#8FBEDD"] if not r["vertical"]
               else ["#3E6B2A", "#6E9E52", "#A6C48A"])[i % 3]
        b0.plot(r["years"], r["mid"], "o-", ms=5, lw=1.5, color=col,
                label=f"{r['position']}{' 연직' if r['vertical'] else ' 수평'} "
                      f"{r['v']:+.2f}")
    b0.set_xticks([2022, 2023, 2024])
    b0.set_xlabel("연도", fontsize=9.5)
    b0.set_ylabel("변위 [mm] · Min/Max 중점", fontsize=9.5)
    b0.set_title("샛강문화다리 — 보고서 GNSS 연도값 (2022~2024)\n"
                 "3년치라 기울기를 낼 수 있는 유일한 곳", fontsize=11, pad=5)
    b0.legend(fontsize=7, ncol=2, framealpha=.92)
    b0.grid(alpha=.22)

    b1 = fig.add_subplot(gs[1, 0])
    if sg["insar_win"]:
        _series(b1, sg["insar_win"],
                title="샛강문화다리 — 같은 기간 위성 InSAR (2022~2024)",
                note=f"{sg['insar_win']['n_epochs']}시점")
    else:
        b1.axis("off")

    # (c) 기울기 맞댐 — 보고서 GNSS vs 같은 창 InSAR
    c = fig.add_subplot(gs[1, 1])
    labs, vv, cc, col = [], [], [], []
    for r in sg["report_rows"]:
        labs.append(f"GNSS {r['position']}{' 연직' if r['vertical'] else ' 수평'}")
        vv.append(r["v"]); cc.append(r["ci"]); col.append(BLUE)
    if sg["insar_win"]:
        w = sg["insar_win"]
        labs.append("InSAR 2022~24 (같은 창)")
        vv.append(w["ann"]["v"]); cc.append(w["ann"]["ci"]); col.append(RED)
    if sg["insar_full"]:
        labs.append("InSAR 2018~25 (전체)")
        vv.append(sg["insar_full"]["ann"]["v"])
        cc.append(sg["insar_full"]["ann"]["ci"]); col.append(GRAY)
    y = np.arange(len(labs))[::-1]
    c.axvline(0, color="k", lw=1.1)
    for yi, v, ci, cl in zip(y, vv, cc, col):
        c.errorbar(v, yi, xerr=ci, fmt="o", ms=7 if cl != BLUE else 5.6,
                   capsize=4, elinewidth=2.2 if cl != BLUE else 1.7, color=cl, zorder=4)
    c.set_yticks(y); c.set_yticklabels(labs, fontsize=8.4)
    c.set_ylim(-0.7, len(labs) - 0.3)
    c.set_xlabel("변위속도 [mm/yr] · 95% CI", fontsize=9.5)
    c.set_title("샛강문화다리 — 보고서 GNSS ↔ 위성, 같은 창에서\n"
                "회색은 창을 안 맞췄을 때(전체 8년)", fontsize=11, pad=5)
    c.grid(axis="x", alpha=.25)

    # (d) 창을 바꾸면 결론이 바뀌는가
    d = fig.add_subplot(gs[1, 2])
    rows_d = data["window_scan"]
    y2 = np.arange(len(rows_d))[::-1]
    for yi, r in zip(y2, rows_d):
        for dy, key, cl, ms in ((0.26, "full", GRAY, 4.6),
                                (0.00, "w3", BLUE, 5.6),
                                (-0.26, "w1", ORANGE, 4.6)):
            if r.get(key) is None:
                continue
            v, ci = r[key]
            d.errorbar(v, yi + dy, xerr=ci, fmt="o", ms=ms, capsize=2.6,
                       elinewidth=1.5, color=cl, zorder=3)
    d.axvline(0, color="k", lw=1.1)
    d.set_yticks(y2); d.set_yticklabels([r["name"] for r in rows_d], fontsize=8.2)
    d.set_ylim(-0.7, len(rows_d) - 0.3)
    d.set_xlabel("LOS 변위속도 [mm/yr] · 95% CI", fontsize=9.5)
    d.set_title("창을 바꾸면 — 회색 전체 8년 / 파랑 2022~24 / 주황 2024\n"
                "짧은 창일수록 신뢰구간이 넓어진다", fontsize=11, pad=5)
    d.grid(axis="x", alpha=.25)

    fig.suptitle("보고서 GNSS ↔ 위성 InSAR — 보고서가 덮는 기간만 잘라 맞댄다",
                 fontsize=15.5, fontweight="bold", y=0.975)
    fig.text(0.008, 0.008,
             "보고서에 GNSS 수치표가 있는 교량은 월드컵대교(2024 한 해)와 "
             "샛강문화다리(2022~24 3년) 둘뿐이다. 위성 8년 추세를 그대로 맞대면 같은 것을 "
             "재는 게 아니라, 같은 창으로 잘라 다시 적합했다. "
             "보고서 GNSS 값은 원시 시계열이 아니라 인쇄된 Min/Max 의 중점이라 거친 값이다.",
             fontsize=8.6, color=GRAY)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=135)
    plt.close(fig)
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--gnss-json", default="docs/bridges/hangang_gnss_2024.json")
    ap.add_argument("--list", default="docs/bridges/hangang16.json")
    ap.add_argument("--out", default="docs/img/gnss_insar_추세선.png")
    ap.add_argument("--json-out", default="docs/bridges/gnss_insar_추세.json")
    a = ap.parse_args()
    _use_korean_font(plt)

    root = Path(a.root)
    gn = {k: v for k, v in
          json.loads(Path(a.gnss_json).read_text(encoding="utf-8")).items()
          if not k.startswith("_")}

    data = {}
    for nm, (lo, hi, _lab) in WIN.items():
        full = read_bridge(root / nm)
        data[nm] = {
            "insar_full": full,
            "insar_win": window(full, lo, hi) if full else None,
            "report_rows": (report_yearly(gn, nm) if nm == "샛강문화다리"
                            else report_single_year(gn, nm)),
        }

    # (d) 창을 바꿔 가며 — 보고서 대상 교량 전부
    names = [b["name"] for b in json.loads(Path(a.list).read_text(encoding="utf-8"))]
    scan = []
    for nm in names:
        d = read_bridge(root / nm)
        if not d:
            continue
        w3 = window(d, "20220101", "20241231")
        w1 = window(d, "20240101", "20241231")
        scan.append({"name": nm,
                     "full": (d["ann"]["v"], d["ann"]["ci"]),
                     "w3": (w3["ann"]["v"], w3["ann"]["ci"]) if w3 else None,
                     "w1": (w1["lin"]["v"], w1["lin"]["ci"]) if w1 else None})
    data["window_scan"] = scan

    figure(data, Path(a.out))

    slim = {"_기간": {k: list(v) for k, v in WIN.items()},
            "_읽는법": "lin=직선만, ann=직선+연주기. v=mm/yr, ci=95% CI 반폭. "
                    "insar_win 은 보고서 기간만 잘라 다시 적합한 것.",
            "월드컵대교": {"보고서_GNSS_2024": data["월드컵대교"]["report_rows"],
                      "insar_2024": _slim_win(data["월드컵대교"]["insar_win"]),
                      "insar_전체": _slim_full(data["월드컵대교"]["insar_full"])},
            "샛강문화다리": {"보고서_GNSS_연도추세": data["샛강문화다리"]["report_rows"],
                       "insar_2022_2024": _slim_win(data["샛강문화다리"]["insar_win"]),
                       "insar_전체": _slim_full(data["샛강문화다리"]["insar_full"])},
            "창_비교": scan}
    Path(a.json_out).write_text(json.dumps(slim, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    print("wrote", a.json_out)

    for nm in WIN:
        d = data[nm]
        print(f"\n━━ {nm}  (보고서 창 {WIN[nm][2]})")
        if nm == "샛강문화다리":
            for r in d["report_rows"]:
                print(f"   보고서 GNSS {r['position']:<14}"
                      f"{'연직' if r['vertical'] else '수평'}  "
                      f"{r['v']:+7.2f} ± {r['ci']:.2f} mm/yr")
        else:
            for r in d["report_rows"]:
                print(f"   보고서 GNSS {r['sensor']:<12}{r['direction']:<6}"
                      f"변동폭 {r['band']:6.2f} mm  (기울기 없음)")
        w, f = d["insar_win"], d["insar_full"]
        if w:
            k = "ann" if w["annual_fit"] else "lin"
            print(f"   위성 InSAR 같은 창    {w[k]['v']:+7.2f} ± {w[k]['ci']:.2f} mm/yr"
                  f"  ({w['n_epochs']}시점, {'연주기 포함' if w['annual_fit'] else '직선만'})")
        if f:
            print(f"   위성 InSAR 전체 8년   {f['ann']['v']:+7.2f} ± "
                  f"{f['ann']['ci']:.2f} mm/yr  ({f['n_epochs']}시점)")
    return 0


def _slim_win(w):
    if not w:
        return None
    return {k: w[k] for k in ("lo", "hi", "n_epochs", "span", "lin", "ann",
                              "annual_fit", "significant")}


def _slim_full(f):
    if not f:
        return None
    return {"n_epochs": f["n_epochs"], "span": f["span"],
            "lin": f["lin"], "ann": f["ann"]}


if __name__ == "__main__":
    raise SystemExit(main())
