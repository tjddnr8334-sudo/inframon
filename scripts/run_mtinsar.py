#!/usr/bin/env python3
"""교량 트랙에 **MT-InSAR 네 가지**를 적용한다 — ADI · 기선망 · Δh · APS.

지금 한강 산출물은 SNAP star-network + snaphu 까지다. 간섭도를 풀어 LOS 로 바꾼
데까지이고, 시계열 InSAR 가 그다음에 하는 일이 빠져 있다. 이 스크립트가 그 뒤를 잇는다.

  ① 수직기선 B⊥ 를 SNAP 처리폴더(`snaphu_<master>_<slave>/wrapped.dim`)에서 읽는다.
     **StaMPS 처리폴더를 주면 거기서 읽는다** — `--stamps` / `--stamps-root`.
     StaMPS 는 B⊥·입사각·슬랜트거리를 처리폴더에 이미 갖고 있어 SNAP 산출물이
     없어도 된다. SARvey 가 논문으로 인용하기 마땅치 않아 갈아 끼울 길을 둔 것이다.
  ② 소기선 망을 유도해 연결성·중복도를 본다(star 망의 시간기선이 얼마나 벌어졌는지).
  ③ 점마다 `los = c + v·t + K·Δh` 를 **같이** 풀고 시간결맞음 γ 를 낸다.
  ④ γ 높은 점에서 APS 를 추정해(시간 고역통과 → 공간 저역통과) 전 점에서 뺀다.

그리고 **효과를 스스로 채점한다** — 보정 전후로 교량 위 점(0~10 m)과 먼 맨땅
(100~200 m)의 연주기 위상이 갈라지는지 본다. 갈라지면 교면 신호가 살아난 것이고,
그대로면 아직 지반을 보고 있는 것이다(`make_deck_vs_ground.py` 와 같은 잣대다).

    python scripts/run_mtinsar.py --only 가양대교 월드컵대교
    python scripts/run_mtinsar.py                      # 처리폴더가 있는 전 교량
    python scripts/run_mtinsar.py --only 가양대교 --stamps /mnt/d/stamps/가양대교
    python scripts/run_mtinsar.py --stamps-root /mnt/d/stamps    # 전 교량 StaMPS 로

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
from inframon.insar.atmo import resolve_temperature                   # noqa: E402
from inframon.insar.residual_height import collect_bperp              # noqa: E402
from insar_series import dec_year                                     # noqa: E402
from kaia_theme import MPL, use_mpl_style                             # noqa: E402
import write_mtinsar_section                                         # noqa: E402
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


def stamps_dir(folder: Path, b: dict, a) -> Path | None:
    """이 교량의 StaMPS 처리폴더 — 준 것 · 루트 아래 이름 · bridge.json 순으로 본다."""
    for cand in (a.stamps, (Path(a.stamps_root) / folder.name) if a.stamps_root
                 else None, b.get("stamps")):
        if cand and Path(cand).is_dir():
            return Path(cand)
    return None


def load_stamps(d: Path, a) -> dict:
    """StaMPS 처리폴더에서 `one()` 이 쓰는 입력을 그대로 만든다.

    SNAP 경로가 `wrapped.dim` 에서 읽던 B⊥·슬랜트거리·입사각이 StaMPS 에는 처리폴더
    안에 이미 있다. 그래서 SNAP 산출물이 없어도 MT-InSAR 를 돌릴 수 있다.
    """
    from inframon.insar.stamps_io import read_stamps

    st = read_stamps(d)
    bp = np.asarray(st.bperp_m, float)
    src = dict(st.sources)
    if bp.ndim == 2:
        # 점별 B⊥ 는 한 교량(≲1 km) 안에서 차이가 무시할 만하다 — 시점 중앙값을 쓴다.
        bp = np.nanmedian(bp, axis=0)
        src["bperp"] += " → 시점별 중앙값으로 축약(교량 안에서는 차이가 없다)"
    R = st.slant_range_m
    if R is None:
        R = float(a.slant_range)
        src["slant_range"] = (f"ps2.mat 에 mean_range 가 없어 --slant-range 기본값 "
                              f"{R:,.0f} m 를 썼다 — 잔차고도 Δh 가 그만큼 치우친다")
    return {"tr": {"ll": st.lonlat, "los": st.los_mm, "coh": st.coherence,
                   "inc": st.incidence_deg, "epochs": list(st.epochs)},
            "bperp": bp, "R": float(R),
            "inc": float(np.nanmedian(st.incidence_deg)),
            "master": st.master or "", "sources": src}


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
    bj = folder / "bridge.json"
    if not bj.exists():
        return None
    b = json.loads(bj.read_text(encoding="utf-8"))

    # ── 입력을 어디서 가져오나 — StaMPS 처리폴더가 있으면 그쪽, 없으면 SNAP ──
    sd = stamps_dir(folder, b, a)
    if sd is not None:
        from inframon.insar.stamps_io import StampsError
        try:
            S = load_stamps(sd, a)
        except StampsError as exc:
            return {"name": folder.name, "skipped": f"StaMPS: {exc}"}
        tr, bperp, R, inc = S["tr"], S["bperp"], S["R"], S["inc"]
        master, ep = S["master"], tr["epochs"]
        ok = np.isfinite(bperp)
        origin = {"toolchain": "StaMPS", "dir": str(sd), **S["sources"]}
    else:
        tr = load_track(folder)
        if tr is None:
            return None
        proc, master = b.get("proc"), str(b.get("master") or "")
        if not proc or not Path(proc).exists():
            return {"name": folder.name,
                    "skipped": f"SNAP 처리폴더가 없다 — B⊥ 를 못 읽는다 ({proc}). "
                               "StaMPS 로 돌리려면 --stamps 나 --stamps-root 를 준다"}
        bp = collect_bperp(proc, master)
        if len(bp) < 5:
            return {"name": folder.name, "skipped": f"B⊥ 쌍이 {len(bp)}개뿐이다"}

        # 마스터 자신은 B⊥=0 · Bt=0 이다(쌍이 없다) — 목록에 채워 넣는다.
        ep = tr["epochs"]
        bperp = np.asarray([0.0 if e == master else bp.get(e, {}).get("bperp_m", np.nan)
                            for e in ep], float)
        ok = np.isfinite(bperp)
        any_pair = next(iter(bp.values()))
        R = float(any_pair["slant_range_m"])
        inc = float(np.nanmedian(tr["inc"])) or float(any_pair["incidence_deg"])
        origin = {"toolchain": "SNAP", "dir": str(proc),
                  "bperp": "snaphu_*/wrapped.dim"}
    if ok.sum() < 5:
        return {"name": folder.name, "skipped": "시점과 B⊥ 가 맞는 것이 5개 미만"}

    # 시점 목록은 시간순이 아니다(마스터가 맨 앞). 그림에서 첫 선분이 거꾸로 그어지고
    # 기선 표도 뒤죽박죽이 된다. 적합은 순서를 안 타지만 보기에 틀리므로 세워 둔다.
    order = np.argsort(ep)
    ep = [ep[i] for i in order]
    bperp, ok = bperp[order], ok[order]
    tr = {**tr, "los": tr["los"][:, order]}

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
    tab = mt.baseline_table([e for e, k in zip(ep, ok) if k], days, bperp, master)
    # 열팽창을 같이 풀려면 취득일 기온이 필요하다 — 없으면 그 항 없이 간다.
    temp = resolve_temperature([e for e, k in zip(ep, ok) if k],
                               lat=b.get("lat"), lon=b.get("lon"),
                               csv_path=a.temperature_csv, fetch=not a.no_fetch)
    out = mt.run(los, t, bperp, P, slant_range_m=R, incidence_deg=inc,
                 temperature_C=temp["temperature"],
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
        "origin": origin,
        "bperp_min_m": round(float(bperp.min()), 1),
        "bperp_max_m": round(float(bperp.max()), 1),
        "bperp_std_m": round(float(bperp.std()), 1),
        "K_max_mm_per_m": round(float(np.max(np.abs(out["K_mm_per_m"]))), 3),
        "network": {k: v for k, v in net.items() if k != "pairs"},
        "baseline": {k: v for k, v in tab.items() if k != "epochs"},
        "height_ambiguity_m_at_100m": round(
            mt.height_ambiguity_m(100.0, R, inc), 1),
        "temperature_source": temp["source"],
        "temperature_ok": bool(temp["meta"].get("ok")),
        "has_thermal": bool(out["has_thermal"]),
        "adi_note": out["adi_note"],
        "gamma_median": round(float(np.nanmedian(out["fit"].gamma)), 3),
        "gamma_min": a.gamma_min, "n_kept": out["n_kept"],
        "sigma_dh_median_m": round(out["sigma_dh_median_m"], 1),
        "sigma_v_median_mm_yr": round(float(np.nanmedian(out["fit"].sigma_v)), 2),
        "aps_rms_mm": round(float(out["iterations"][-1].get("aps_rms_mm", 0.0)), 2),
        "velocity_median_mm_yr": round(float(np.nanmedian(out["fit"].velocity_mm_yr)), 2),
        "dh_median_m": round(float(np.nanmedian(out["fit"].dh_m)), 1),
        "resid_rms_before_mm": round(out["iterations"][0]["resid_rms_mm"], 2),
        "resid_rms_after_mm": round(out["iterations"][-1]["resid_rms_mm"], 2),
        "n_deck_10m": int(near.sum()), "n_ground_100_200m": int(far.sum()),
        "n_deck_10m_kept": int(near_ps.sum()),
        "phase_gap_before_months": None if before is None else round(before, 2),
        "phase_gap_after_months": None if after is None else round(after, 2),
        "phase_gap_after_ps_months": None if after_ps is None else round(after_ps, 2),
    }
    th = out["fit"].thermal_mm_per_C
    if th is not None:
        sth = out["fit"].sigma_thermal
        md, mg = near & keep, far & keep
        for tag, m in (("deck", md), ("ground", mg)):
            if m.sum() >= 3:
                rec[f"thermal_{tag}_mm_per_C"] = round(float(np.nanmedian(th[m])), 3)
                rec[f"thermal_{tag}_sigma"] = round(float(np.nanmedian(sth[m])), 3)
                rec[f"thermal_{tag}_n"] = int(m.sum())
        if md.sum() >= 3 and mg.sum() >= 3:
            # 점이 3~10개뿐이라 정규분포를 가정하지 않는다 — 중앙값 차이를 붓스트랩한다.
            rng = np.random.default_rng(11)
            A, B = th[md], th[mg]
            dif = [float(np.median(rng.choice(A, A.size)) -
                         np.median(rng.choice(B, B.size))) for _ in range(2000)]
            lo, hi = np.percentile(dif, [2.5, 97.5])
            rec["thermal_deck_minus_ground"] = round(
                float(np.median(A) - np.median(B)), 3)
            rec["thermal_diff_ci95"] = [round(float(lo), 3), round(float(hi), 3)]
            rec["thermal_separates"] = bool(lo > 0 or hi < 0)
    # 열팽창을 같이 풀면 연주기가 그 항으로 흡수되므로, 남은 연주기 위상차보다
    # **열팽창계수 자체**가 교면과 지반을 가르는 더 곧은 잣대다.
    rec["deck_signal_recovered"] = bool(
        rec.get("thermal_separates")
        or (after_ps is not None and abs(after_ps) >= 1.5))
    # SARPROZ 가 내는 표와 같은 꼴 — 시점별 기선, 점별 변수.
    write_products(folder, rec, tab, temp, out, dist)
    # json 에만 두면 없는 것과 같다 — 기선과 γ 를 결과.md 에도 올린다.
    write_mtinsar_section.update_md(folder, rec)
    baseline_figure(folder, rec, tab, net, temp)
    figure(folder, rec, t, los, out, near, far, dist)
    (folder / "mtinsar.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def baseline_figure(folder: Path, rec: dict, tab: dict, net: dict,
                    temp: dict) -> None:
    """기선 그림 — SARPROZ 의 baseline plot 과 같은 것.

    가로축 시간, 세로축 수직기선. 마스터에서 뻗는 star 망과, 거기서 유도한 소기선
    쌍을 함께 그린다. 옆칸에는 취득일 기온을 놓는다 — 열팽창을 같이 풀었으므로
    '이 계절 분포로 α 를 가를 수 있는가' 가 눈에 보여야 한다.
    """
    ep = tab["epochs"]
    bt = np.asarray([r["btemp_days"] for r in ep], float)
    bp = np.asarray([r["bperp_m"] for r in ep], float)
    mi = int(np.argmax([r["is_master"] for r in ep])) if any(
        r["is_master"] for r in ep) else 0
    fig, ax = plt.subplots(1, 2, figsize=(12.6, 4.2),
                           gridspec_kw={"width_ratios": (1.35, 1.0)})
    for i, j in net["pairs"]:
        ax[0].plot([bt[i], bt[j]], [bp[i], bp[j]], lw=.5,
                   color=MPL["rule"], zorder=1)
    for i in range(len(ep)):
        if i != mi:
            ax[0].plot([bt[mi], bt[i]], [bp[mi], bp[i]], lw=.5,
                       color=MPL["blue_pale"], zorder=0)
    ax[0].scatter(bt, bp, s=26, color=MPL["blue"], edgecolors=MPL["slate"],
                  linewidths=.4, zorder=3, label="취득")
    ax[0].scatter([bt[mi]], [bp[mi]], s=110, marker="*", color=MPL["red"],
                  edgecolors=MPL["slate"], linewidths=.5, zorder=4,
                  label=f"마스터 {ep[mi]['date']}")
    ax[0].set_xlabel("시간기선 [일]", fontsize=10)
    ax[0].set_ylabel("수직기선 B⊥ [m]", fontsize=10)
    ax[0].grid(alpha=.25)
    ax[0].legend(fontsize=9, framealpha=.9)
    ax[0].set_title(f"B⊥ {tab['bperp_min_m']:+.0f}~{tab['bperp_max_m']:+.0f} m "
                    f"(폭 {tab['bperp_span_m']:.0f}) · 소기선 {net['n_pairs']}쌍 "
                    + ("연결됨" if net["connected"] else "끊김"),
                    fontsize=10.5, color=MPL["ink"], pad=6)

    T = temp.get("temperature")
    if T is not None:
        ax[1].scatter(bt, T, s=26, color=MPL["orange"],
                      edgecolors=MPL["slate"], linewidths=.4)
        ax[1].set_ylabel("취득일 기온 [°C]", fontsize=10)
        ax[1].set_title(f"기온 {np.min(T):.0f}~{np.max(T):.0f} °C "
                        f"({temp['source']}) — 열팽창을 가를 수 있는 폭",
                        fontsize=10.5, color=MPL["ink"], pad=6)
    else:
        ax[1].text(.5, .5, "취득일 기온을 못 구했다\n열팽창 항 없이 풀었다",
                   ha="center", va="center", fontsize=11, color=MPL["gray"],
                   transform=ax[1].transAxes)
        ax[1].set_title("기온 없음", fontsize=10.5, color=MPL["gray"], pad=6)
    ax[1].set_xlabel("시간기선 [일]", fontsize=10)
    ax[1].grid(alpha=.25)

    fig.suptitle(f"{rec['name']} — 기선·기온 (SARPROZ baseline plot 에 해당)",
                 fontsize=13.5, fontweight="bold", color=MPL["ink"], y=0.985)
    fig.text(0.006, 0.014,
             f"※ 높이 모호도 {rec['height_ambiguity_m_at_100m']:.0f} m "
             "(B⊥ 100 m 기준) — 위상 한 바퀴가 높이 이만큼이다. 작을수록 잔차고도를 "
             "잘 본다. Sentinel-1 은 B⊥ 가 작아 이 값이 크다.",
             fontsize=8.6, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.045, 1, 0.94))
    fig.savefig(folder / "mtinsar_baseline.png", dpi=150)
    plt.close(fig)


def write_products(folder: Path, rec: dict, tab: dict, temp: dict,
                   out: dict, dist: np.ndarray) -> None:
    """SARPROZ 가 내는 것과 같은 꼴로 — 기선표와 점별 변수표.

    SARPROZ 의 다중영상 해석은 점마다 속도·잔차고도·열팽창·결맞음을 내고, 스택은
    시점별 수직/시간 기선을 낸다. 같은 이름·같은 단위로 CSV 를 남겨 둔다.
    """
    import csv

    with (folder / "mtinsar_baselines.csv").open("w", newline="",
                                                 encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "bperp_m", "btemp_days", "is_master", "temp_C"])
        T = temp.get("temperature")
        for i, r in enumerate(tab["epochs"]):
            w.writerow([r["date"], r["bperp_m"], r["btemp_days"],
                        int(r["is_master"]),
                        "" if T is None else round(float(T[i]), 1)])

    f = out["fit"]
    th = f.thermal_mm_per_C
    with (folder / "mtinsar_points.csv").open("w", newline="",
                                              encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["idx", "dist_to_deck_m", "velocity_mm_yr", "sigma_v",
                    "height_dh_m", "sigma_dh", "thermal_mm_per_C",
                    "sigma_thermal", "temporal_coherence", "asi", "is_ps"])
        keep = out["ps_mask"]
        for i in range(out["n_points"]):
            w.writerow([i, round(float(dist[i]), 1),
                        round(float(f.velocity_mm_yr[i]), 3),
                        round(float(f.sigma_v[i]), 3),
                        round(float(f.dh_m[i]), 2),
                        round(float(f.sigma_dh[i]), 2),
                        "" if th is None else round(float(th[i]), 4),
                        "" if f.sigma_thermal is None
                        else round(float(f.sigma_thermal[i]), 4),
                        round(float(f.gamma[i]), 4),
                        "" if not np.isfinite(out["asi"][i])
                        else round(float(out["asi"][i]), 4),
                        int(keep[i])])
    rec["products"] = ["mtinsar_baselines.csv", "mtinsar_points.csv",
                       "mtinsar.json", "mtinsar.png", "mtinsar_baseline.png"]


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


def summary_figure(rows: list, out: Path) -> None:
    """전 교량 요약 — 잔차가 얼마나 줄었나, 교면이 갈라졌나."""
    ok = [r for r in rows if not r.get("skipped")]
    if not ok:
        return
    ok.sort(key=lambda r: r["resid_rms_before_mm"] - r["resid_rms_after_mm"])
    nm = [r["name"] for r in ok]
    y = np.arange(len(nm))
    fig, ax = plt.subplots(1, 2, figsize=(11.8, 0.34 * len(nm) + 2.4), sharey=True)

    ax[0].barh(y, [r["resid_rms_before_mm"] for r in ok], height=.62,
               color=MPL["rule"], edgecolor=MPL["slate"], linewidth=.4,
               label="보정 전")
    ax[0].barh(y, [r["resid_rms_after_mm"] for r in ok], height=.62,
               color=MPL["blue"], edgecolor=MPL["slate"], linewidth=.4,
               label="APS·Δh 보정 후")
    ax[0].set_yticks(y)
    ax[0].set_yticklabels(nm, fontsize=9.5)
    ax[0].invert_yaxis()
    ax[0].set_xlabel("잔차 RMS [mm]", fontsize=10)
    ax[0].grid(axis="x", alpha=.25)
    ax[0].legend(fontsize=9, framealpha=.9)
    ax[0].set_title("모델을 맞추고 남은 잔차", fontsize=11, color=MPL["ink"], pad=6)

    got = [(i, r) for i, r in enumerate(ok) if "thermal_diff_ci95" in r]
    for i, r in got:
        lo, hi = r["thermal_diff_ci95"]
        d = r["thermal_deck_minus_ground"]
        c = MPL["green"] if r.get("thermal_separates") else MPL["gray"]
        ax[1].plot([lo, hi], [i, i], lw=2.2, color=c, solid_capstyle="butt")
        ax[1].scatter([d], [i], s=34, color=c, edgecolors=MPL["slate"],
                      linewidths=.4, zorder=3)
    ax[1].axvline(0, color=MPL["slate"], lw=1.1)
    ax[1].set_xlabel("열팽창 교면 − 지반 [mm/°C] · 막대 = 붓스트랩 95%", fontsize=10)
    ax[1].grid(axis="x", alpha=.25)
    ax[1].set_title("초록 = 95% 구간이 0 을 비껴간다(교면이 갈라진다)",
                    fontsize=11, color=MPL["ink"], pad=6)

    fig.suptitle("MT-InSAR 적용 — 전 교량 요약", fontsize=14, fontweight="bold",
                 color=MPL["ink"], y=0.995)
    n_sep = sum(1 for _i, r in got if r.get("thermal_separates"))
    fig.text(0.006, 0.01,
             "※ 열팽창계수 α 는 los = c + v·t + K·Δh + α·(T−T̄) 를 점마다 같이 풀어 얻는다"
             "(취득일 기온 ERA5).\n"
             f"   교면 PS 가 3~10개뿐이라 정규분포를 가정하지 않고 중앙값 차이를 "
             f"붓스트랩했다. {n_sep}/{len(got)}개소에서 갈라지며, {len(got)}번 검정했으므로 "
             "하나쯤은 우연일 수 있다.",
             fontsize=8.8, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.04, 1, 0.965))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("wrote", out)


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
    ap.add_argument("--stamps", default=None,
                    help="StaMPS 처리폴더 하나(--only 로 교량 하나를 지정할 때)")
    ap.add_argument("--stamps-root", default=None,
                    help="교량 이름별 StaMPS 처리폴더가 있는 상위 폴더 "
                         "(<root>/<교량이름>). 있으면 SNAP 대신 이것을 쓴다")
    ap.add_argument("--slant-range", type=float, default=880_000.0,
                    help="ps2.mat 에 mean_range 가 없을 때 쓸 슬랜트 거리[m]")
    ap.add_argument("--temperature-csv", default=None,
                    help="취득일 기온 CSV(date,temp_C) — 주면 네트워크를 안 쓴다")
    ap.add_argument("--no-fetch", action="store_true",
                    help="기온을 인터넷에서 받지 않는다(열팽창 항 없이 간다)")
    ap.add_argument("--json-out", default="docs/bridges/mtinsar_all.json")
    ap.add_argument("--summary", default="docs/img/value/MTInSAR_요약.png")
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
        tag = "S" if r.get("origin", {}).get("toolchain") == "StaMPS" else " "
        print(f"{d.name:<12}{tag}γ중앙 {r['gamma_median']:.2f} · {r['n_kept']:>5}/"
              f"{r['n_points']}점 · σΔh {r['sigma_dh_median_m']:>4.0f} m · "
              f"APS {r['aps_rms_mm']:>5.1f} mm · 잔차 {r['resid_rms_before_mm']:.1f}"
              f"→{r['resid_rms_after_mm']:.1f} mm | 위상차 "
              f"{r['phase_gap_before_months']}→{r['phase_gap_after_ps_months']} 개월"
              + (f" | 열팽창 교면 {r['thermal_deck_mm_per_C']:+.2f} vs 지반 "
                 f"{r['thermal_ground_mm_per_C']:+.2f} → 차이 "
                 f"{r['thermal_deck_minus_ground']:+.2f} "
                 f"[{r['thermal_diff_ci95'][0]:+.2f},{r['thermal_diff_ci95'][1]:+.2f}]"
                 if "thermal_diff_ci95" in r else "")
              + ("  ✔교면 갈라짐" if r["deck_signal_recovered"] else ""))

    summary_figure(rows, Path(a.summary))
    Path(a.json_out).write_text(json.dumps(
        {"_설명": "MT-InSAR 네 가지 적용 결과 — ADI·기선망·점별 DEM 오차·APS",
         "_출처": "origin.toolchain 이 SNAP 이면 snaphu_*/wrapped.dim 에서 B⊥ 를 읽은 "
                "것이고, StaMPS 면 처리폴더의 ps2/bp2/la2 를 읽은 것이다",
         "_채점": "교량 위(0~10 m)와 먼 맨땅(100~200 m)의 연주기 위상차가 1.5개월 "
                "이상 갈라지면 교면 신호가 살아난 것으로 본다",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
