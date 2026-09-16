#!/usr/bin/env python3
"""교량 트랙에 **MT-InSAR 네 가지**를 적용한다 — ADI · 기선망 · Δh · APS.

지금 한강 산출물은 SNAP star-network + snaphu 까지다. 간섭도를 풀어 LOS 로 바꾼
데까지이고, 시계열 InSAR 가 그다음에 하는 일이 빠져 있다. 이 스크립트가 그 뒤를 잇는다.

  ① 수직기선 B⊥ 를 SNAP 처리폴더(`snaphu_<master>_<slave>/wrapped.dim`)에서 읽는다.
  ② 소기선 망을 유도해 연결성·중복도를 본다(star 망의 시간기선이 얼마나 벌어졌는지).
  ③ 점마다 `los = c + v·t + K·Δh` 를 **같이** 풀고 시간결맞음 γ 를 낸다.
  ④ γ 높은 점에서 APS 를 추정해(시간 고역통과 → 공간 저역통과) 전 점에서 뺀다.

그리고 **효과를 스스로 채점한다** — 보정 전후로 교량 위 점(0~10 m)과 먼 맨땅
(100~200 m)의 연주기 위상이 갈라지는지 본다. 갈라지면 교면 신호가 살아난 것이고,
그대로면 아직 지반을 보고 있는 것이다(`make_deck_vs_ground.py` 와 같은 잣대다).

    python scripts/run_mtinsar.py --only 가양대교 월드컵대교
    python scripts/run_mtinsar.py                      # 처리폴더가 있는 전 교량

산출(교량마다): docs/bridges/<교량>/mtinsar.json · mtinsar.png
전체 요약: docs/bridges/mtinsar_all.json
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

from inframon.insar import mtinsar as mt                              # noqa: E402
from inframon.insar.residual_height import collect_bperp              # noqa: E402
from insar_series import dec_year                                     # noqa: E402
from kaia_theme import MPL, use_mpl_style                             # noqa: E402
from make_deck_vs_ground import (                                     # noqa: E402
    annual_z, dist_to_polyline, dmon, peak_month, to_local,
)

use_mpl_style()


def load_track(folder: Path) -> dict | None:
    import h5py

    p = folder / "track_rh_full.h5"
    if not p.exists():
        return None
    with h5py.File(p, "r") as h:
        return {"ll": np.asarray(h["pixel_lonlat"][()], float),
                "los": np.asarray(h["los_mm"][()], float),
                "coh": np.asarray(h["coh"][()], float),
                "inc": np.asarray(h["incidenceAngle"][()], float),
                "epochs": [s.decode() if isinstance(s, bytes) else str(s)
                           for s in h["epochs"][()]]}


def deck_split(folder: Path, ll: np.ndarray):
    """교량 위(0~10 m) · 먼 맨땅(100~200 m) 마스크 — 채점에 쓴다."""
    dp = folder / "deck_polyline.json"
    if not dp.exists():
        return None
    poly = np.asarray(json.loads(dp.read_text(encoding="utf-8"))["geometry"], float)
    lat0 = float(np.mean(ll[:, 1]))
    P = to_local(ll, lat0)
    d = dist_to_polyline(P, to_local(poly[:, ::-1], lat0))
    return P, d, (d <= 10.0), (d >= 100.0) & (d < 200.0)


def phase_gap(t: np.ndarray, los: np.ndarray,
              near: np.ndarray, far: np.ndarray) -> float | None:
    """교량 위 ↔ 맨땅 연주기 위상차[개월]. 크면 교면 신호가 갈라진 것이다."""
    if near.sum() < 6 or far.sum() < 6:
        return None
    zn = complex(np.ravel(annual_z(t, np.mean(los[near], axis=0)))[0])
    zf = complex(np.ravel(annual_z(t, np.mean(los[far], axis=0)))[0])
    return float(dmon(peak_month(zn), peak_month(zf)))


def one(folder: Path, a) -> dict | None:
    tr = load_track(folder)
    bj = folder / "bridge.json"
    if tr is None or not bj.exists():
        return None
    b = json.loads(bj.read_text(encoding="utf-8"))
    proc, master = b.get("proc"), str(b.get("master") or "")
    if not proc or not Path(proc).exists():
        return {"name": folder.name,
                "skipped": f"SNAP 처리폴더가 없다 — B⊥ 를 못 읽는다 ({proc})"}
    bp = collect_bperp(proc, master)
    if len(bp) < 5:
        return {"name": folder.name, "skipped": f"B⊥ 쌍이 {len(bp)}개뿐이다"}

    # 마스터 자신은 B⊥=0 · Bt=0 이다(쌍이 없다) — 목록에 채워 넣는다.
    ep = tr["epochs"]
    bperp = np.asarray([0.0 if e == master else bp.get(e, {}).get("bperp_m", np.nan)
                        for e in ep], float)
    ok = np.isfinite(bperp)
    if ok.sum() < 5:
        return {"name": folder.name, "skipped": "시점과 B⊥ 가 맞는 것이 5개 미만"}
    any_pair = next(iter(bp.values()))
    R = float(any_pair["slant_range_m"])
    inc = float(np.nanmedian(tr["inc"])) or float(any_pair["incidence_deg"])

    t = np.asarray([dec_year(e) for e in ep], float)[ok]
    days = (t - t.min()) * 365.25
    los = tr["los"][:, ok]
    bperp = bperp[ok]
    sp = deck_split(folder, tr["ll"])
    if sp is None:
        return {"name": folder.name, "skipped": "deck_polyline.json 이 없다"}
    P, dist, near, far = sp

    net = mt.baseline_network(days, bperp, max_btemp_days=a.max_btemp,
                              max_bperp_m=a.max_bperp)
    out = mt.run(los, t, bperp, P, slant_range_m=R, incidence_deg=inc,
                 gamma_min=a.gamma_min, aps_radius_m=a.aps_radius,
                 n_iter=a.iters, aps_source_frac=a.aps_source_frac)

    before = phase_gap(t, los, near, far)
    after = phase_gap(t, out["los_corrected_mm"], near, far)
    keep = out["ps_mask"]
    near_ps = near & keep
    after_ps = (phase_gap(t, out["los_corrected_mm"], near_ps, far & keep)
                if near_ps.sum() >= 6 else None)

    rec = {
        "name": folder.name, "n_points": out["n_points"], "n_epochs": int(ok.sum()),
        "master": master, "slant_range_m": round(R, 1), "incidence_deg": round(inc, 2),
        "bperp_min_m": round(float(bperp.min()), 1),
        "bperp_max_m": round(float(bperp.max()), 1),
        "bperp_std_m": round(float(bperp.std()), 1),
        "K_max_mm_per_m": round(float(np.max(np.abs(out["K_mm_per_m"]))), 3),
        "network": {k: v for k, v in net.items() if k != "pairs"},
        "adi_note": out["adi_note"],
        "gamma_median": round(float(np.nanmedian(out["fit"].gamma)), 3),
        "gamma_min": a.gamma_min, "n_kept": out["n_kept"],
        "sigma_dh_median_m": round(out["sigma_dh_median_m"], 1),
        "sigma_v_median_mm_yr": round(float(np.nanmedian(out["fit"].sigma_v)), 2),
        "aps_rms_mm": round(float(out["iterations"][-1].get("aps_rms_mm", 0.0)), 2),
        "resid_rms_before_mm": round(out["iterations"][0]["resid_rms_mm"], 2),
        "resid_rms_after_mm": round(out["iterations"][-1]["resid_rms_mm"], 2),
        "n_deck_10m": int(near.sum()), "n_ground_100_200m": int(far.sum()),
        "n_deck_10m_kept": int(near_ps.sum()),
        "phase_gap_before_months": None if before is None else round(before, 2),
        "phase_gap_after_months": None if after is None else round(after, 2),
        "phase_gap_after_ps_months": None if after_ps is None else round(after_ps, 2),
    }
    rec["deck_signal_recovered"] = bool(
        after_ps is not None and abs(after_ps) >= 1.5)
    figure(folder, rec, t, los, out, near, far, dist)
    (folder / "mtinsar.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def figure(folder: Path, rec: dict, t, los, out, near, far, dist) -> None:
    fig, ax = plt.subplots(1, 4, figsize=(17.2, 4.1))
    g = out["fit"].gamma

    ax[0].hist(g, bins=40, color=MPL["blue"], edgecolor=MPL["slate"], linewidth=.3)
    ax[0].axvline(rec["gamma_min"], color=MPL["red"], lw=1.4, ls="--")
    ax[0].set_xlabel("시간결맞음 γ", fontsize=9.5)
    ax[0].set_ylabel("점 수", fontsize=9.5)
    ax[0].set_title(f"γ 중앙 {rec['gamma_median']:.2f} · "
                    f"{rec['n_kept']}점 남김", fontsize=10, color=MPL["ink"])
    ax[0].grid(alpha=.2)

    d = out["fit"].dh_m
    ax[1].scatter(dist, d, s=5, c=g, cmap="viridis", vmin=0, vmax=1)
    ax[1].axvline(10, color=MPL["red"], lw=1.2, ls="--")
    ax[1].set_xscale("symlog", linthresh=10)
    ax[1].set_ylim(np.nanpercentile(d, 1), np.nanpercentile(d, 99))
    ax[1].set_xlabel("교량 중심선에서 거리 [m]", fontsize=9.5)
    ax[1].set_ylabel("DEM 오차 Δh [m]", fontsize=9.5)
    ax[1].set_title(f"Δh — 점별 σ 중앙 {rec['sigma_dh_median_m']:.0f} m",
                    fontsize=10, color=MPL["ink"])
    ax[1].grid(alpha=.2)

    ax[2].plot(t, np.mean(los[near], axis=0), lw=1.3, color=MPL["orange"],
               label="교량±10 m (보정 전)")
    ax[2].plot(t, np.mean(out["los_corrected_mm"][near], axis=0), lw=1.6,
               color=MPL["blue"], label="교량±10 m (APS 제거 후)")
    ax[2].plot(t, np.mean(out["aps_mm"][near], axis=0), lw=1.1, color=MPL["rule"],
               label="뺀 APS")
    ax[2].set_xlabel("연도", fontsize=9.5)
    ax[2].set_ylabel("LOS [mm]", fontsize=9.5)
    ax[2].legend(fontsize=8.2, framealpha=.9)
    ax[2].grid(alpha=.2)
    ax[2].set_title(f"잔차 RMS {rec['resid_rms_before_mm']:.1f} → "
                    f"{rec['resid_rms_after_mm']:.1f} mm", fontsize=10,
                    color=MPL["ink"])

    labs, vals = [], []
    for k, lab in (("phase_gap_before_months", "보정 전"),
                   ("phase_gap_after_months", "APS 제거"),
                   ("phase_gap_after_ps_months", "APS+γ 선별")):
        if rec[k] is not None:
            labs.append(lab)
            vals.append(rec[k])
    col = [MPL["green"] if abs(v) >= 1.5 else MPL["red"] for v in vals]
    ax[3].barh(np.arange(len(vals)), vals, color=col, height=.55,
               edgecolor=MPL["slate"], linewidth=.4)
    for s in (-1.5, 1.5):
        ax[3].axvline(s, color=MPL["red"], lw=1.1, ls="--")
    ax[3].axvline(0, color=MPL["slate"], lw=1.1)
    ax[3].set_yticks(np.arange(len(vals)))
    ax[3].set_yticklabels(labs, fontsize=9.5)
    ax[3].set_xlim(-6.5, 6.5)
    ax[3].set_xlabel("교량 위 − 맨땅 연주기 위상차 [개월]", fontsize=9.5)
    ax[3].grid(axis="x", alpha=.25)
    ax[3].set_title("초록 = 교면 신호가 갈라졌다", fontsize=10, color=MPL["ink"])

    fig.suptitle(f"{rec['name']} — MT-InSAR 적용(ADI·기선망·Δh·APS)",
                 fontsize=14, fontweight="bold", color=MPL["ink"], y=0.985)
    fig.text(0.006, 0.012,
             f"※ B⊥ {rec['bperp_min_m']:+.0f}~{rec['bperp_max_m']:+.0f} m "
             f"(σ {rec['bperp_std_m']:.0f}) → K 최대 {rec['K_max_mm_per_m']:.3f} mm/m. "
             f"소기선 망 {rec['network']['n_pairs']}쌍·"
             f"{'연결됨' if rec['network']['connected'] else '끊김'}. {rec['adi_note']}",
             fontsize=8.6, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))
    fig.savefig(folder / "mtinsar.png", dpi=150)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--gamma-min", type=float, default=0.35)
    ap.add_argument("--aps-radius", type=float, default=400.0)
    ap.add_argument("--aps-source-frac", type=float, default=0.5)
    ap.add_argument("--iters", type=int, default=2)
    ap.add_argument("--max-btemp", type=float, default=400.0)
    ap.add_argument("--max-bperp", type=float, default=150.0)
    ap.add_argument("--json-out", default="docs/bridges/mtinsar_all.json")
    a = ap.parse_args()

    rows = []
    for d in sorted(Path(a.root).iterdir()):
        if not d.is_dir() or (a.only and d.name not in a.only):
            continue
        r = one(d, a)
        if r is None:
            continue
        rows.append(r)
        if r.get("skipped"):
            print(f"{d.name:<12} 건너뜀 — {r['skipped']}")
            continue
        print(f"{d.name:<12} γ중앙 {r['gamma_median']:.2f} · {r['n_kept']:>5}/"
              f"{r['n_points']}점 · σΔh {r['sigma_dh_median_m']:>4.0f} m · "
              f"APS {r['aps_rms_mm']:>5.1f} mm · 잔차 {r['resid_rms_before_mm']:.1f}"
              f"→{r['resid_rms_after_mm']:.1f} mm | 위상차 "
              f"{r['phase_gap_before_months']}→{r['phase_gap_after_months']}"
              f"→{r['phase_gap_after_ps_months']} 개월"
              + ("  ✔교면 갈라짐" if r["deck_signal_recovered"] else ""))

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "MT-InSAR 네 가지 적용 결과 — ADI·기선망·점별 DEM 오차·APS",
         "_채점": "교량 위(0~10 m)와 먼 맨땅(100~200 m)의 연주기 위상차가 1.5개월 "
                "이상 갈라지면 교면 신호가 살아난 것으로 본다",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
