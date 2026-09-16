#!/usr/bin/env python3
"""**월별로 접어서** 맞댄다 — 보고서 계측이 시계열이 아니라 계절 기후값이기 때문이다.

앞서 R²·추세가 이상하게 나온 데에는 내 잘못이 하나 있었다. 보고서의 월별 막대는
"2022, 23, 24년 **연평균** 처짐" 처럼 **해마다 같은 달의 평균**이다. 실제로 세 해
막대가 거의 겹친다 — 해간 표준편차가 계절 진폭의 4~14% 뿐이다.

  · 그러니 그 12×3 값을 시계열로 놓고 **기울기를 재는 것은 성립하지 않는다**.
    계측 쪽 추세는 만들어질 수 없고, 그래서 '추세가 겹친다' 는 결과도 뜻이 없었다.
  · 공정한 비교는 **InSAR 도 같은 방식으로 접는 것**이다. 달마다 여러 해의 취득을
    평균하면 잡음이 √n 만큼 줄고, 그제서야 같은 물건끼리 맞대게 된다.

여기서는 그렇게 다시 잰다. 그리고 **교면 PS 와 먼 맨땅을 같이** 맞댄다 — 교면 쪽이
맨땅보다 계측에 가까워야 '교면을 봤다' 고 말할 수 있다.

우연 기준선은 12개 달을 섞어 3000회 돌린 |r| 의 95 백분위다(달이 12개뿐이라 우연히
0.5 를 넘기 쉽다 — 그 사실을 숫자로 같이 낸다).

    python scripts/make_climatology_compare.py

산출: docs/img/value/월별기후값_대조.png · docs/bridges/climatology_compare.json
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
from make_deck_vs_ground import dist_to_polyline, to_local            # noqa: E402

use_mpl_style()

# 보고서에 월별 계측 곡선이 실린 교량. 올림픽대교는 GNSS 가 아니라 레이저 처짐계다
# (p41~43 계측항목표에 GNSS 가 없다) — 그래도 월별 곡선이 있으니 같이 맞댄다.
BRIDGES = ["가양대교", "샛강문화다리", "올림픽대교", "원효대교", "월드컵대교"]


def climatology(t: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """달마다 평균 — 몇 개가 쌓였는지도 같이 돌려준다."""
    mo = np.clip(((np.asarray(t) % 1.0) * 12).astype(int), 0, 11)
    out, n = np.full(12, np.nan), np.zeros(12, int)
    for k in range(12):
        m = mo == k
        if m.any():
            out[k] = float(np.mean(np.asarray(v)[m]))
            n[k] = int(m.sum())
    return out, n


def chance95(x: np.ndarray, y: np.ndarray, rng, n: int = 3000) -> float:
    """달을 섞어 얻는 |r|² 의 95 백분위 — 12개뿐이라 우연히도 꽤 높다."""
    k = len(x)
    return float(np.percentile(
        [abs(float(np.corrcoef(x[rng.permutation(k)], y)[0, 1])) for _ in range(n)],
        95)) ** 2


def one(folder: Path, auto: dict, eye: dict, rng) -> dict | None:
    import h5py

    mtp, rhp, dpp = (folder / "track_deck_mt.h5", folder / "track_rh_full.h5",
                     folder / "deck_polyline.json")
    if not (mtp.exists() and rhp.exists() and dpp.exists()):
        return None
    rp = report_series(auto, eye, folder.name)
    if rp is None:
        return None
    tr, vr, what, how = rp

    with h5py.File(mtp, "r") as h:
        dl = np.asarray(h["los_mm"][()], float)
        ep = [s.decode() if isinstance(s, bytes) else str(s) for s in h["epochs"][()]]
        n_ps = dl.shape[0]
    with h5py.File(rhp, "r") as h:
        ll = np.asarray(h["pixel_lonlat"][()], float)
        fl = np.asarray(h["los_mm"][()], float)
        fe = [s.decode() if isinstance(s, bytes) else str(s) for s in h["epochs"][()]]
    poly = np.asarray(json.loads(dpp.read_text(encoding="utf-8"))["geometry"], float)
    lat0 = float(np.mean(ll[:, 1]))
    d = dist_to_polyline(to_local(ll, lat0), to_local(poly[:, ::-1], lat0))
    far = (d >= 100) & (d < 400)

    cg, ng = climatology(tr, vr)
    cd, nd = climatology(np.asarray([dec_year(e) for e in ep]), dl.mean(axis=0))
    cf, _ = climatology(np.asarray([dec_year(e) for e in fe]),
                        np.median(fl[far], axis=0))
    ok = np.isfinite(cg) & np.isfinite(cd) & np.isfinite(cf)
    if ok.sum() < 8:
        return {"name": folder.name, "skipped": f"겹치는 달 {int(ok.sum())}개"}

    rd = float(np.corrcoef(cd[ok], cg[ok])[0, 1])
    rf = float(np.corrcoef(cf[ok], cg[ok])[0, 1])
    return {
        "name": folder.name, "quantity": what, "read": how,
        "n_deck_ps": int(n_ps), "n_ground": int(far.sum()),
        "months": int(ok.sum()),
        "insar_per_month_median": int(np.median(nd[nd > 0])),
        "report_years_per_month": int(np.median(ng[ng > 0])),
        "r_deck": round(rd, 3), "r2_deck": round(rd * rd, 3),
        "r_ground": round(rf, 3), "r2_ground": round(rf * rf, 3),
        "r2_chance95": round(chance95(cd[ok], cg[ok], rng), 3),
        "deck_beats_chance": bool(rd * rd > chance95(cd[ok], cg[ok], rng)),
        "deck_better_than_ground": bool(abs(rd) > abs(rf) + 0.2),
        "_cd": cd, "_cg": cg, "_cf": cf, "_ok": ok,
    }


def figure(rows: list, out: Path) -> None:
    got = [r for r in rows if "_cd" in r]
    n = len(got)
    fig, axes = plt.subplots(1, n, figsize=(3.05 * n, 4.5))
    axes = np.atleast_1d(axes)
    mo = np.arange(1, 13)
    for ax, r in zip(axes, got):
        for key, col, lab in (("_cg", MPL["red"], "현장 계측"),
                              ("_cd", MPL["blue"], "InSAR 교면 PS"),
                              ("_cf", MPL["gray"], "InSAR 먼 맨땅")):
            v = r[key].copy()
            s = np.nanmax(np.abs(v)) or 1.0
            ax.plot(mo, v / s, "-o", ms=3.4, lw=(2.0 if key != "_cf" else 1.2),
                    color=col, alpha=(1.0 if key != "_cf" else .7), label=lab)
        ax.axhline(0, color=MPL["rule"], lw=1)
        ax.set_xticks([1, 4, 7, 10])
        ax.set_xticklabels(["1월", "4월", "7월", "10월"], fontsize=9)
        ax.set_ylim(-1.35, 1.35)
        ax.grid(alpha=.25)
        good = r["deck_beats_chance"]
        ax.set_title(f"{r['name']}\n{r['quantity'][:13]} · PS {r['n_deck_ps']}점\n"
                     f"교면 r {r['r_deck']:+.2f} · 맨땅 {r['r_ground']:+.2f}\n"
                     f"R² {r['r2_deck']:.2f} vs 우연 {r['r2_chance95']:.2f}",
                     fontsize=9.4, color=(MPL["green"] if good else MPL["red"]),
                     pad=6)
    axes[0].set_ylabel("월별 기후값 (각자 최대=1)", fontsize=10)
    axes[0].legend(fontsize=8.2, loc="lower left", framealpha=.95)
    fig.suptitle("달마다 접어서 맞댄다 — 보고서 계측은 시계열이 아니라 계절 기후값이다",
                 fontsize=14, fontweight="bold", color=MPL["ink"], y=0.985)
    fig.text(0.006, 0.012,
             "※ 보고서의 월별 막대는 해마다 같은 달의 평균이다(세 해가 거의 겹친다 — "
             "해간 표준편차가 계절 진폭의 4~14%). 그래서 그 값으로 **추세를 재는 것은 "
             "성립하지 않고**, InSAR 도 같은 방식으로 접어야 같은 물건끼리 맞대게 된다.\n"
             "   달이 12개뿐이라 우연히도 R² 0.33~0.36 은 나온다 — 그 기준선을 같이 적었다. "
             "회색(먼 맨땅)보다 파란색(교면 PS)이 계측에 가까워야 '교면을 봤다' 고 말할 수 있다.",
             fontsize=9, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.075, 1, 0.945))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/value/월별기후값_대조.png")
    ap.add_argument("--json-out", default="docs/bridges/climatology_compare.json")
    a = ap.parse_args()

    auto = json.loads(Path(a.root, "hangang_gnss_monthly.json")
                      .read_text(encoding="utf-8"))
    eye = json.loads(Path(a.root, "hangang_displacement_2024.json")
                     .read_text(encoding="utf-8"))
    rng = np.random.default_rng(5)
    rows = []
    for nm in BRIDGES:
        r = one(Path(a.root) / nm, auto, eye, rng)
        if r is None:
            print(f"{nm:<12} 자료 없음")
            continue
        rows.append(r)
        if r.get("skipped"):
            print(f"{nm:<12} {r['skipped']}")
            continue
        print(f"{nm:<12} {r['quantity'][:12]:<14} 달 {r['months']}개 · "
              f"InSAR {r['insar_per_month_median']}회/달 | 교면 r {r['r_deck']:+.2f} "
              f"(R² {r['r2_deck']:.2f}) · 맨땅 r {r['r_ground']:+.2f} · "
              f"우연 {r['r2_chance95']:.2f}"
              + ("  ✔우연을 넘는다" if r["deck_beats_chance"] else "")
              + ("  ✔맨땅보다 낫다" if r["deck_better_than_ground"] else ""))

    figure(rows, Path(a.out))
    Path(a.json_out).write_text(json.dumps(
        {"_설명": "월별 기후값으로 접어 맞댄 결과 — 보고서 계측이 계절 기후값이라 "
                "시계열·추세 비교는 성립하지 않는다",
         "_우연기준선": "달 12개를 섞어 3000회 — 표본이 12개뿐이라 R² 0.33~0.36 은 우연히 나온다",
         "_교면판정": "교면 PS 의 |r| 이 먼 맨땅보다 0.2 이상 크면 '교면을 봤다'",
         "bridges": [{k: v for k, v in r.items() if not k.startswith("_")}
                     for r in rows]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
