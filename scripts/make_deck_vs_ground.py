#!/usr/bin/env python3
"""교량 위 점이 **정말 교량을 보고 있나** — 거리별 연주기로 확인한다.

GNSS 와 InSAR 가 안 맞는 이유를 통계로 찾으려 했지만, 원인은 통계가 아니라 자료였다.
이 스크립트는 GNSS 를 전혀 쓰지 않고 InSAR 안에서만 판정한다.

  교량에서 **직각으로 떨어진 거리**별로 점을 묶고, 각 묶음의 연주기 성분을 낸다.
  교량 위(0~10 m) 묶음의 위상이 멀리 떨어진 맨땅(100~400 m)과 **같으면**,
  그 점들은 교량이 아니라 주변 지반을 보고 있는 것이다.

교량 데크는 여름에 내려가고(강재·케이블이 늘어난다) 강변 지반은 여름에 부푼다.
둘은 물리적으로 다른 신호라서, 진짜 교면 산란체를 잡았다면 위상이 갈라져야 한다.

시간축은 `date_labels` 의 **달력 날짜**로 잡는다. `insar/dates` 는 첫 촬영일부터의
경과일이라 그대로 1년으로 나누면 첫 촬영일의 연중 위치만큼 위상이 통째로 밀린다
(가양·샛강 2018-06-19 → 5.6개월, 월드컵 2021-09-07 → 8.2개월).

    python scripts/make_deck_vs_ground.py

산출: docs/img/value/교면인가_지반인가.png · docs/bridges/deck_vs_ground.json
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaia_theme import MPL, use_mpl_style                              # noqa: E402
from make_trend_agree import dec_year                                  # noqa: E402

use_mpl_style()

BANDS = [(0, 10), (10, 25), (25, 50), (50, 100), (100, 200), (200, 400)]
LAB = ["0~10", "10~25", "25~50", "50~100", "100~200", "200~400"]


def to_local(ll: np.ndarray, lat0: float) -> np.ndarray:
    return np.column_stack([ll[:, 0] * 111320.0 * np.cos(np.radians(lat0)),
                            ll[:, 1] * 110540.0])


def dist_to_polyline(P: np.ndarray, V: np.ndarray) -> np.ndarray:
    """점 [N,2] → 꺾은선 [M,2] 까지의 최단거리. 구간마다 사영해 가장 작은 값."""
    best = np.full(P.shape[0], np.inf)
    for a, b in zip(V[:-1], V[1:]):
        ab = b - a
        L2 = float(ab @ ab)
        if L2 < 1e-9:
            continue
        s = np.clip(((P - a) @ ab) / L2, 0.0, 1.0)
        q = a + s[:, None] * ab
        best = np.minimum(best, np.hypot(*(P - q).T))
    return best


def annual_z(t: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """직선 + 연주기를 맞추고 연주기 복소진폭 A+iB 만 돌려준다."""
    A = np.vstack([np.ones_like(t), t, np.sin(2 * np.pi * t),
                   np.cos(2 * np.pi * t)]).T
    c, *_ = np.linalg.lstsq(A, np.atleast_2d(Y).T, rcond=None)
    return c[2] + 1j * c[3]


def peak_month(z) -> float:
    """A·sin+B·cos = R·cos(2πt−φ), φ=atan2(A,B) → 최대가 되는 연중 달."""
    return float((np.arctan2(np.real(z), np.imag(z)) / (2 * np.pi) % 1.0) * 12 + 0.5)


def dmon(a: float, b: float) -> float:
    return ((a - b + 6) % 12) - 6


def bridge_bands(folder: Path) -> dict | None:
    import h5py

    rh = folder / "track_rh_full.h5"
    dp = folder / "deck_polyline.json"
    if not rh.exists() or not dp.exists():
        return None
    with h5py.File(rh, "r") as h:
        ll = np.asarray(h["pixel_lonlat"][()], float)
        los = np.asarray(h["los_mm"][()], float)
        ep = [s.decode() if isinstance(s, bytes) else str(s)
              for s in h["epochs"][()]]
    poly = np.asarray(json.loads(dp.read_text(encoding="utf-8"))["geometry"], float)
    if poly.shape[0] < 2:
        return None
    lat0 = float(np.mean(ll[:, 1]))
    P = to_local(ll, lat0)
    V = to_local(poly[:, ::-1], lat0)              # (lat,lon) → (lon,lat)
    d = dist_to_polyline(P, V)

    t = np.asarray([dec_year(s) for s in ep], float)
    z = annual_z(t, los)

    out = {"name": folder.name, "n_epochs": len(ep), "first_epoch": ep[0],
           "deck_len_m": round(float(np.sum(np.hypot(*np.diff(V, axis=0).T))), 1),
           "bands": []}
    for (lo, hi), lab in zip(BANDS, LAB):
        m = (d >= lo) & (d < hi)
        rec = {"band_m": lab, "n": int(m.sum())}
        if m.sum() >= 6:
            zz = z[m]
            rec.update({
                "peak_month": round(peak_month(np.mean(zz)), 2),
                "amp_mm": round(float(abs(np.mean(zz))), 2),
                "phase_R": round(float(abs(np.mean(zz / np.abs(zz)))), 3)})
        out["bands"].append(rec)
    near = next((b for b in out["bands"] if b["band_m"] == "0~10"
                 and "peak_month" in b), None)
    far = next((b for b in out["bands"] if b["band_m"] == "100~200"
                and "peak_month" in b), None)
    if near and far:
        out["near_minus_far_months"] = round(
            dmon(near["peak_month"], far["peak_month"]), 2)
    return out


def figure(rows: list, out: Path) -> None:
    rows = [r for r in rows if "near_minus_far_months" in r]
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.9),
                             gridspec_kw={"width_ratios": (1.55, 1.0)})
    ax = axes[0]
    x = np.arange(len(LAB))
    for i, r in enumerate(rows):
        y = [b.get("peak_month", np.nan) for b in r["bands"]]
        s = [max(18, 9 * (b.get("amp_mm") or 0)) for b in r["bands"]]
        ax.plot(x, y, "-", lw=1.4, alpha=.85, label=r["name"])
        ax.scatter(x, y, s=s, alpha=.85, edgecolors=MPL["slate"], linewidths=.4)
    ax.axvspan(-0.4, 0.4, color=MPL["blue_pale"], alpha=.55, zorder=0)
    ax.text(0.0, 12.45, "교량 위", ha="center", fontsize=9.5, color=MPL["blue"])
    ax.text(4.5, 12.45, "맨땅", ha="center", fontsize=9.5, color=MPL["gray"])
    ax.set_xticks(x)
    ax.set_xticklabels([f"{s} m" for s in LAB], fontsize=9.5)
    ax.set_xlabel("교량 중심선에서 직각으로 떨어진 거리", fontsize=10.5)
    ax.set_ylabel("연주기 최대 시점 [월]", fontsize=10.5)
    ax.set_ylim(0, 13)
    ax.set_yticks(range(1, 13))
    ax.grid(alpha=.25)
    # 범례를 그림 안에 두면 선을 덮는다 — 아래로 내린다.
    ax.legend(fontsize=8.6, ncol=5, loc="upper center",
              bbox_to_anchor=(0.5, -0.155), frameon=False)
    ax.set_title("교량 위 점의 계절 위상이 먼 맨땅과 같은가", fontsize=12.5,
                 color=MPL["ink"], pad=8)

    ax2 = axes[1]
    nm = [r["name"] for r in rows]
    dv = [r["near_minus_far_months"] for r in rows]
    col = [MPL["red"] if abs(v) < 1.5 else MPL["green"] for v in dv]
    ax2.barh(np.arange(len(nm)), dv, color=col, height=.55,
             edgecolor=MPL["slate"], linewidth=.5)
    ax2.axvline(0, color=MPL["slate"], lw=1.1)
    for s in (-1.5, 1.5):
        ax2.axvline(s, color=MPL["red"], lw=1.1, ls="--")
    ax2.set_yticks(np.arange(len(nm)))
    ax2.set_yticklabels(nm, fontsize=10)
    ax2.set_xlim(-6.5, 6.5)
    ax2.set_xlabel("교량 위 − 맨땅  위상차 [개월]", fontsize=10.5)
    ax2.grid(axis="x", alpha=.25)
    for i, v in enumerate(dv):
        ax2.text(v + (.2 if v >= 0 else -.2), i, f"{v:+.1f}", va="center",
                 ha="left" if v >= 0 else "right", fontsize=9.5,
                 color=MPL["ink"])
    ax2.set_title("붉은색 = 구별이 안 된다(|차이| < 1.5개월)", fontsize=11.5,
                  color=MPL["ink"], pad=8)

    fig.suptitle("교면을 본 것인가, 강변 지반을 본 것인가 — GNSS 없이 InSAR 안에서만 판정",
                 fontsize=14.5, fontweight="bold", color=MPL["ink"], y=0.985)
    fig.text(0.006, 0.012,
             "※ 점 크기는 연주기 진폭이다. 교량 데크는 여름에 내려가고(강재·케이블이 늘어난다) "
             "강변 지반은 여름에 부푼다 — 물리적으로 다른 신호다.\n"
             "   그런데 교량 위 0~10 m 점의 위상이 100~200 m 떨어진 맨땅과 같다면, "
             "그 점들은 교량이 아니라 지반을 보고 있는 것이다. 그러면 GNSS 와 맞을 수가 없다.\n"
             "   시간축은 촬영 날짜(date_labels)로 잡았다 — 첫 촬영일부터의 경과일을 그대로 "
             "쓰면 연중 위치만큼 위상이 통째로 밀린다.",
             fontsize=9, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.135, 1, 0.955))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/value/교면인가_지반인가.png")
    ap.add_argument("--json-out", default="docs/bridges/deck_vs_ground.json")
    a = ap.parse_args()

    rows = []
    for d in sorted(Path(a.root).iterdir()):
        if not d.is_dir():
            continue
        r = bridge_bands(d)
        if r is None:
            continue
        rows.append(r)
        near = next((b for b in r["bands"] if b["band_m"] == "0~10"), {})
        far = next((b for b in r["bands"] if b["band_m"] == "100~200"), {})
        print(f"{r['name']:<12} 교량±10m {near.get('n', 0):>4}점 "
              f"최대월 {near.get('peak_month', float('nan')):>5.1f} | "
              f"맨땅 {far.get('n', 0):>4}점 최대월 "
              f"{far.get('peak_month', float('nan')):>5.1f} | "
              f"차이 {r.get('near_minus_far_months', float('nan')):>+5.1f}개월")

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "교량 직각거리 구간별 연주기 — 교면 점과 맨땅이 구별되는지",
         "_판정": "|교량 위 − 맨땅| < 1.5개월이면 구별 안 됨 = 그 점들은 지반이다",
         "_시간축": "date_labels 의 달력 날짜(dec_year). insar/dates 는 첫 촬영일 기준 "
                 "경과일이라 그대로 쓰면 위상이 밀린다",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    figure(rows, Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
