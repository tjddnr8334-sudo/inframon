#!/usr/bin/env python3
"""교면 PS **한 점씩** 계측과 맞춰 본다 — 고르기의 값을 정직하게 치르고서.

"교면 점 중에 계측과 잘 맞는 게 없냐" 는 물음에 답하려면 두 가지를 같이 내야 한다.

  ① 그 점의 상관이 얼마인가
  ② **아무 관계가 없어도** 점 N개 중 최대 |r| 이 얼마나 나오는가(고르기의 값)

②를 빼면 점 10~34개 중 하나는 늘 잘 맞는다. 달이 12개뿐이라 더 그렇다 — 실제로
우연 기준선이 0.73~0.76 이나 된다. 그걸 넘어야 비로소 할 말이 생긴다.

그리고 넘더라도 한 점이면 약하다. 그래서 세 번째를 본다.

  ③ 잘 맞는 점들이 **한자리에 뭉쳐 있는가**. 구조물의 거동은 이웃한 점이 함께
     움직이므로, 상위 점들이 교축 한 구간에 모이면 우연으로 보기 어렵다.
     점 위치는 그대로 두고 상관값만 섞어 '상위 k 점의 측점 폭'의 분포를 얻는다.

비교는 **월별 기후값**끼리 한다 — 보고서 계측이 해마다 같은 달의 평균이라 시계열이
아니기 때문이다(`make_climatology_compare.py` 참조).

    python scripts/make_deck_point_match.py

산출: docs/img/value/교면점_계측일치.png · docs/bridges/deck_point_match.json
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
from make_deck_vs_ground import to_local                              # noqa: E402

use_mpl_style()

BRIDGES = ["가양대교", "샛강문화다리", "올림픽대교", "원효대교", "월드컵대교"]
TOP_K = 3


def climatology(t: np.ndarray, V: np.ndarray) -> np.ndarray:
    """달마다 평균 — [N,M] → [N,12]. 값이 없는 달은 NaN."""
    mo = np.clip(((np.asarray(t) % 1.0) * 12).astype(int), 0, 11)
    V = np.atleast_2d(np.asarray(V, float))
    out = np.full((V.shape[0], 12), np.nan)
    for k in range(12):
        m = mo == k
        if m.any():
            out[:, k] = V[:, m].mean(axis=1)
    return out


def station(lonlat: np.ndarray) -> np.ndarray:
    """교축 측점 — 점 구름의 주축에 사영한다(데크를 따라가는 거리)."""
    P = to_local(lonlat, float(np.mean(lonlat[:, 1])))
    P = P - P.mean(axis=0)
    u = np.linalg.svd(P, full_matrices=False)[2][0]
    return P @ u


def one(folder: Path, auto: dict, eye: dict, rng, n_perm: int) -> dict | None:
    import h5py

    p = folder / "track_deck_mt.h5"
    rp = report_series(auto, eye, folder.name)
    if not p.exists() or rp is None:
        return None
    tr, vr, what, how = rp
    with h5py.File(p, "r") as h:
        L = np.asarray(h["los_mm"][()], float)
        ll = np.asarray(h["pixel_lonlat"][()], float)
        ep = [s.decode() if isinstance(s, bytes) else str(s) for s in h["epochs"][()]]

    C = climatology(np.asarray([dec_year(e) for e in ep]), L)
    g = climatology(np.asarray(tr), np.asarray(vr))[0]
    ok = np.isfinite(g) & np.all(np.isfinite(C), axis=0)
    if ok.sum() < 8:
        return {"name": folder.name, "skipped": f"겹치는 달 {int(ok.sum())}개"}
    X, y, st = C[:, ok], g[ok], station(ll)
    live = np.std(X, axis=1) > 1e-9
    X, st = X[live], st[live]
    r = np.asarray([float(np.corrcoef(x, y)[0, 1]) for x in X])

    # ② 고르기의 값 — 달을 섞어 '점 N개 중 최대 |r|' 의 분포를 얻는다.
    null = []
    for _ in range(n_perm):
        yp = y[rng.permutation(len(y))]
        null.append(float(np.max(np.abs(
            [np.corrcoef(x, yp)[0, 1] for x in X]))))
    chance = float(np.percentile(null, 95))

    # ③ 뭉침 — 점 위치는 그대로, 상관만 섞어 상위 k 의 측점 폭 분포를 얻는다.
    top = np.argsort(-np.abs(r))[:TOP_K]
    spread = float(np.ptp(st[top]))
    nullsp = [float(np.ptp(st[rng.permutation(len(r))[:TOP_K]]))
              for _ in range(20000)]
    p_cluster = float(np.mean(np.asarray(nullsp) <= spread))

    return {
        "name": folder.name, "quantity": what, "read": how,
        "n_points": int(len(r)), "n_months": int(ok.sum()),
        "deck_span_m": round(float(np.ptp(st)), 1),
        "r_min": round(float(r.min()), 3), "r_median": round(float(np.median(r)), 3),
        "r_max": round(float(r.max()), 3),
        "abs_r_max": round(float(np.abs(r).max()), 3),
        "r2_max": round(float(np.abs(r).max() ** 2), 3),
        "r2_chance95": round(chance ** 2, 3),
        "chance95_abs_r": round(chance, 3),
        "beats_chance": bool(np.abs(r).max() > chance),
        "n_above_chance": int(np.sum(np.abs(r) > chance)),
        "top": [{"r": round(float(r[i]), 3), "r2": round(float(r[i] ** 2), 3),
                 "station_m": round(float(st[i]), 1)} for i in top],
        "top_spread_m": round(spread, 1),
        "cluster_p": round(p_cluster, 4),
        "clustered": bool(p_cluster < 0.05),
        "_r": r, "_st": st, "_X": X, "_y": y, "_top": top, "_chance": chance,
    }


def figure(rows: list, out: Path) -> None:
    got = [r for r in rows if "_r" in r]
    n = len(got)
    fig, axes = plt.subplots(2, n, figsize=(3.1 * n, 7.0),
                             gridspec_kw={"height_ratios": (1.0, 0.95)})
    axes = np.atleast_2d(axes)
    for k, rec in enumerate(got):
        ax = axes[0, k]
        r, st, ch = rec["_r"], rec["_st"], rec["_chance"]
        # R² 는 정의상 양수다 — 세로축은 R² 로 두고 **부호는 색으로** 보인다.
        pos = r >= 0
        ax.scatter(st[pos], r[pos] ** 2, s=38, color=MPL["blue"],
                   edgecolors=MPL["slate"], linewidths=.4, label="같은 방향 (r>0)")
        ax.scatter(st[~pos], r[~pos] ** 2, s=38, color=MPL["orange"],
                   edgecolors=MPL["slate"], linewidths=.4, marker="v",
                   label="반대 방향 (r<0)")
        ax.axhline(ch ** 2, color=MPL["red"], lw=1.2, ls="--")
        ax.set_ylim(0, 1.05)
        ax.set_xlabel("교축 측점 [m]", fontsize=9)
        if k == 0:
            ax.set_ylabel("계측과의 R² (월별 기후값)", fontsize=9.5)
            ax.legend(fontsize=8.0, loc="upper left", framealpha=.95)
        ax.grid(alpha=.25)
        good = rec["beats_chance"]
        ax.set_title(f"{rec['name']} · {rec['quantity'][:12]}\n"
                     f"점 {rec['n_points']} · R² 최대 {rec['r2_max']:.2f} vs "
                     f"우연 {rec['r2_chance95']:.2f}\n"
                     f"넘는 점 {rec['n_above_chance']}개 · 뭉침 p={rec['cluster_p']:.3f}",
                     fontsize=9.3, pad=6,
                     color=(MPL["green"] if good and rec["clustered"]
                            else MPL["red"] if good else MPL["gray"]))

        ax = axes[1, k]
        top = rec["_top"]
        y = rec["_y"]
        x = rec["_X"][top].mean(axis=0)
        m = np.arange(len(y)) + 1
        ax.plot(m, y / (np.max(np.abs(y)) or 1), "-o", ms=3.6, lw=2.0,
                color=MPL["red"], label="현장 계측")
        ax.plot(m, x / (np.max(np.abs(x)) or 1), "-o", ms=3.6, lw=2.0,
                color=MPL["blue"], label=f"상위 {TOP_K}점 평균")
        ax.axhline(0, color=MPL["rule"], lw=1)
        ax.set_xticks([1, 4, 7, 10])
        ax.set_xticklabels(["1월", "4월", "7월", "10월"], fontsize=9)
        ax.set_ylim(-1.35, 1.35)
        ax.grid(alpha=.25)
        if k == 0:
            ax.set_ylabel("월별 기후값 (각자 최대=1)", fontsize=9.5)
            ax.legend(fontsize=8.2, loc="lower left", framealpha=.95)
        ax.set_title("측점 " + " · ".join(f"{t['station_m']:+.0f}m" for t in rec["top"])
                     + f"  R² {rec['top'][0]['r2']:.2f}"
                     + ("  (부호 반대)" if rec["top"][0]["r"] < 0 else ""),
                     fontsize=9, color=MPL["ink"], pad=5)

    fig.suptitle("교면 PS 를 한 점씩 계측과 맞춘다 — 고르기의 값을 치르고서",
                 fontsize=14.5, fontweight="bold", color=MPL["ink"], y=0.985)
    fig.text(0.006, 0.010,
             "※ 세로축은 R²(정의상 양수)이고, **부호는 표식으로** 보인다 — 파란 동그라미가 "
             "같은 방향, 주황 세모가 반대 방향이다.\n"
             "   붉은 점선 = 점 N개 중 최대 R² 의 우연 기준선(달을 섞어 2000회, 95 백분위). "
             "점이 10~34개이고 달이 12개뿐이라 우연히도 R² 0.52~0.58 이 나온다 — 그걸 넘어야 "
             "할 말이 생긴다.\n"
             "   '뭉침 p' 는 점 위치를 그대로 두고 상관값만 섞어 얻은 상위 3점 측점 폭의 "
             "p 값이다. 잘 맞는 점들이 한자리에 모여야 구조물 거동으로 볼 수 있다 — "
             "한 점만 넘으면 운일 수 있다.\n"
             "   부호가 음이면 '반대로 움직인다' 가 아니라 '계측 부호 규약이 반대일 수 "
             "있다' 는 뜻이다. 보고서에 위/아래 정의가 없어 코드로는 못 정한다.",
             fontsize=8.9, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.085, 1, 0.955))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--perm", type=int, default=2000)
    ap.add_argument("--out", default="docs/img/value/교면점_계측일치.png")
    ap.add_argument("--json-out", default="docs/bridges/deck_point_match.json")
    a = ap.parse_args()

    auto = json.loads(Path(a.root, "hangang_gnss_monthly.json")
                      .read_text(encoding="utf-8"))
    eye = json.loads(Path(a.root, "hangang_displacement_2024.json")
                     .read_text(encoding="utf-8"))
    rng = np.random.default_rng(9)
    rows = []
    for nm in BRIDGES:
        r = one(Path(a.root) / nm, auto, eye, rng, a.perm)
        if r is None:
            print(f"{nm:<12} 자료 없음")
            continue
        rows.append(r)
        if r.get("skipped"):
            print(f"{nm:<12} {r['skipped']}")
            continue
        print(f"{nm:<12} 점 {r['n_points']:>3} · R² 최대 {r['r2_max']:.2f} "
              f"(우연 {r['r2_chance95']:.2f}, r={r['top'][0]['r']:+.2f}) · "
              f"넘는 점 {r['n_above_chance']} · "
              f"상위3 폭 {r['top_spread_m']:.0f} m (p={r['cluster_p']:.3f})"
              + ("  ✔우연을 넘는다" if r["beats_chance"] else "")
              + ("  ✔한자리에 뭉친다" if r["clustered"] else ""))

    figure(rows, Path(a.out))
    Path(a.json_out).write_text(json.dumps(
        {"_설명": "교면 PS 를 한 점씩 계측(월별 기후값)과 맞춘 결과",
         "_우연기준선": "달을 섞어 '점 N개 중 최대 |r|' 을 2000회 — 고르기의 값을 치른다",
         "_뭉침": "점 위치는 그대로 두고 상관값만 섞어 얻은 상위 3점 측점 폭의 p 값",
         "_부호": "음수는 '반대로 움직인다' 가 아니라 계측 부호 규약이 반대일 수 있다는 뜻",
         "bridges": [{k: v for k, v in r.items() if not k.startswith("_")}
                     for r in rows]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
