#!/usr/bin/env python3
"""**센서가 있는 자리에 PS 가 있는가** — 안 맞는 이유를 위치로 설명한다.

R² 가 낮은 것을 두고 "InSAR 가 교량을 못 본다" 고 말해 왔는데, 한 걸음 더 들어가면
다른 그림이 나온다. 계측기는 아무 데나 달지 않는다.

  · **처짐계·레이저처짐계** — 처짐이 가장 큰 **주경간 중앙**
  · **GNSS·경사계** — 주탑 정부, 또는 지점부(교대·교각)
  · 어느 쪽이든 **강 위 주경간**이 중심이다

그런데 강 위 주경간은 산란체가 없다. 물은 레이더를 되돌려 주지 않고, 데크는
Sentinel-1 화소(지상 5×20 m)로 1~2 칸이다. 그래서 PS 는 **육상 쪽 교대·접속부**에
몰린다. 그러면 '계측과 안 맞는다' 가 아니라 **'계측하는 자리를 안 보고 있다'** 가 된다.

이 스크립트는 그걸 위치로 확인한다.
  ① 교량마다 PS 의 교축 측점 분포
  ② 주경간 중앙·주탑 위치 (IFC 프록시에서)
  ③ 주경간 중앙 ±50 m 안의 PS 개수 ← 센서 자리가 보이는가
  ④ 계측과 가장 잘 맞는 점이 어디에 있는가

    python scripts/make_sensor_coverage.py

산출: docs/img/value/센서자리_PS유무.png · docs/bridges/sensor_coverage.json
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "src"))

from insar_series import dec_year, report_series                      # noqa: E402
from kaia_theme import MPL, use_mpl_style                             # noqa: E402

use_mpl_style()

BRIDGES = ["가양대교", "샛강문화다리", "올림픽대교", "원효대교", "월드컵대교"]
NEAR_M = 50.0


def climatology(t: np.ndarray, V: np.ndarray) -> np.ndarray:
    mo = np.clip(((np.asarray(t) % 1.0) * 12).astype(int), 0, 11)
    V = np.atleast_2d(np.asarray(V, float))
    out = np.full((V.shape[0], 12), np.nan)
    for k in range(12):
        m = mo == k
        if m.any():
            out[:, k] = V[:, m].mean(axis=1)
    return out


def deck_length(folder: Path) -> float:
    poly = np.asarray(json.loads((folder / "deck_polyline.json")
                                 .read_text(encoding="utf-8"))["geometry"], float)
    lat0 = float(poly[:, 0].mean())
    mx, my = 111320.0 * np.cos(np.radians(lat0)), 110540.0
    return float(np.hypot(np.diff(poly[:, 1]) * mx,
                          np.diff(poly[:, 0]) * my).sum())


def pylon_stations(folder: Path, L: float) -> list:
    """IFC 프록시의 주탑 위치 → 데크 측점. 프록시 원점은 데크 중점이다."""
    ej = list(folder.glob("*_elements.json"))
    if not ej:
        return []
    els = json.loads(ej[0].read_text(encoding="utf-8")).get("elements", [])
    return sorted({round(float((e["bbox_min"][0] + e["bbox_max"][0]) / 2) + L / 2)
                   for e in els if e.get("member") == "pylon"})


def one(folder: Path, auto: dict, eye: dict) -> dict | None:
    import h5py

    p5 = folder / "project.h5"
    if not p5.exists() or not (folder / "deck_polyline.json").exists():
        return None
    with h5py.File(p5, "r") as f:
        if "insar/deck_station" not in f:
            return None
        st = np.asarray(f["insar/deck_station"][()], float)
        los = np.asarray(f["insar/los"][()], float)
        lab = [s.decode() if isinstance(s, bytes) else str(s)
               for s in f["insar/date_labels"][()]]
    L = deck_length(folder)
    mid = L / 2
    rec = {
        "name": folder.name, "deck_len_m": round(L, 1),
        "n_ps": int(len(st)), "mid_span_station_m": round(mid, 1),
        "pylon_stations_m": pylon_stations(folder, L),
        "ps_station_percentiles": [round(float(v), 1)
                                   for v in np.percentile(st, [0, 25, 50, 75, 100])],
        "n_ps_within_50m_of_mid": int(np.sum(np.abs(st - mid) <= NEAR_M)),
        "n_ps_in_middle_third": int(np.sum((st > L / 3) & (st < 2 * L / 3))),
        "nearest_ps_to_mid_m": round(float(np.min(np.abs(st - mid))), 1),
    }
    rp = report_series(auto, eye, folder.name)
    if rp is not None:
        tr, vr, what, how = rp
        C = climatology(np.asarray([dec_year(s) for s in lab]), los)
        g = climatology(np.asarray(tr), np.asarray(vr))[0]
        ok = np.isfinite(g) & np.all(np.isfinite(C), axis=0)
        if ok.sum() >= 8:
            r = np.asarray([float(np.corrcoef(x, g[ok])[0, 1])
                            if np.std(x) > 1e-9 else np.nan for x in C[:, ok]])
            o = int(np.nanargmax(np.abs(r)))
            rec.update({
                "quantity": what,
                "best_r": round(float(r[o]), 3),
                "best_r2": round(float(r[o] ** 2), 3),
                "best_station_m": round(float(st[o]), 1),
                "best_dist_to_mid_m": round(float(abs(st[o] - mid)), 1),
                "best_dist_to_end_m": round(float(min(st[o], L - st[o])), 1),
                "_r": r})
    rec["_st"] = st
    rec["_L"] = L
    return rec


def figure(rows: list, out: Path) -> None:
    got = [r for r in rows if "_st" in r]
    fig, ax = plt.subplots(figsize=(13.0, 0.72 * len(got) + 3.0))
    for i, r in enumerate(got):
        L, st = r["_L"], r["_st"]
        y = len(got) - 1 - i
        ax.plot([0, 1], [y, y], lw=9, color=MPL["rule"], solid_capstyle="butt",
                zorder=1)                                    # 데크 전체
        # 가운데 1/3 — 계측기가 주로 놓이는 구간
        ax.plot([1 / 3, 2 / 3], [y, y], lw=9, color=MPL["blue_pale"],
                solid_capstyle="butt", zorder=2)
        rr = r.get("_r")
        if rr is not None:
            c = np.abs(rr)
            s = ax.scatter(st / L, np.full(len(st), y), s=42, c=c, cmap="YlOrRd",
                           vmin=0, vmax=1, edgecolors=MPL["slate"], linewidths=.4,
                           zorder=4)
        else:
            ax.scatter(st / L, np.full(len(st), y), s=30, color=MPL["gray"],
                       edgecolors=MPL["slate"], linewidths=.3, zorder=4)
        ax.plot([0.5], [y], marker="v", ms=11, color=MPL["red"], zorder=5)
        for q in r["pylon_stations_m"]:
            ax.plot([q / L], [y], marker="|", ms=16, color=MPL["ink"], zorder=5)
        ax.text(1.02, y, f"{r['n_ps']}점 · 중앙±50 m 에 "
                         f"{r['n_ps_within_50m_of_mid']}점 · "
                         f"가장 가까운 점 {r['nearest_ps_to_mid_m']:.0f} m",
                fontsize=9.2, va="center", color=MPL["ink"])
    ax.set_yticks(range(len(got)))
    ax.set_yticklabels([r["name"] for r in reversed(got)], fontsize=10.5)
    ax.set_xlim(-0.03, 1.55)
    ax.set_xticks([0, .25, .5, .75, 1])
    ax.set_xticklabels(["교대", "1/4", "주경간 중앙", "3/4", "교대"], fontsize=9.5)
    ax.set_xlabel("교축 위치 (데크 길이로 정규화)", fontsize=10.5)
    ax.grid(axis="x", alpha=.25)
    if any("_r" in r for r in got):
        cb = fig.colorbar(s, ax=ax, shrink=.5, pad=.01, aspect=16)
        cb.set_label("계측과의 |r| (월별 기후값)", fontsize=9.5)
    ax.set_title("붉은 ▽ = 주경간 중앙(계측기가 놓이는 자리) · 검은 | = 주탑 · "
                 "옅은 파랑 = 가운데 1/3",
                 fontsize=11, color=MPL["ink"], pad=8)
    fig.suptitle("센서가 있는 자리에 PS 가 있는가 — 안 맞는 이유를 위치로 본다",
                 fontsize=14.5, fontweight="bold", color=MPL["ink"], y=0.985)
    fig.text(0.006, 0.012,
             "※ 계측기는 아무 데나 달지 않는다 — 처짐계는 처짐이 가장 큰 주경간 중앙, "
             "GNSS·경사계는 주탑 정부나 지점부다. 그런데 강 위 주경간은 산란체가 없어\n"
             "   PS 가 육상 쪽 교대·접속부에 몰린다. 그러면 '계측과 안 맞는다' 가 아니라 "
             "**'계측하는 자리를 안 보고 있다'** 가 맞는 말이다.\n"
             "   점 색은 계측과의 |r| 이다 — 잘 맞는 점조차 교량 끝에 있다는 것이 이 그림의 요점이다.",
             fontsize=9, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.10, 1, 0.945))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/value/센서자리_PS유무.png")
    ap.add_argument("--json-out", default="docs/bridges/sensor_coverage.json")
    a = ap.parse_args()

    auto = json.loads(Path(a.root, "hangang_gnss_monthly.json")
                      .read_text(encoding="utf-8"))
    eye = json.loads(Path(a.root, "hangang_displacement_2024.json")
                     .read_text(encoding="utf-8"))
    rows = []
    for nm in BRIDGES:
        r = one(Path(a.root) / nm, auto, eye)
        if r is None:
            print(f"{nm:<12} 자료 없음")
            continue
        rows.append(r)
        print(f"{nm:<12} PS {r['n_ps']:>3} · 데크 {r['deck_len_m']:>6.0f} m · "
              f"중앙 {r['mid_span_station_m']:>5.0f} m | 중앙±50 m 에 "
              f"{r['n_ps_within_50m_of_mid']}점 · 가운데 1/3 에 "
              f"{r['n_ps_in_middle_third']}점 · 가장 가까운 점 "
              f"{r['nearest_ps_to_mid_m']:>5.0f} m"
              + (f" | 최적점 R² {r['best_r2']:.2f} @ 끝에서 "
                 f"{r['best_dist_to_end_m']:.0f} m" if "best_r2" in r else ""))

    figure(rows, Path(a.out))
    Path(a.json_out).write_text(json.dumps(
        {"_설명": "계측기가 놓이는 자리(주경간 중앙·주탑)에 PS 가 있는지",
         "_요점": "PS 는 육상 쪽 교대·접속부에 몰린다 — 강 위 주경간은 산란체가 없다. "
                "그래서 '계측과 안 맞는다' 가 아니라 '계측하는 자리를 안 보고 있다'",
         "_주의": "센서의 정확한 교축 좌표는 보고서에 없다. 계측 종류로부터 '주경간 중앙' 을 "
                "가정한 것이며, 교대에 단 경사계라면 이야기가 달라진다",
         "bridges": [{k: v for k, v in r.items() if not k.startswith("_")}
                     for r in rows]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
