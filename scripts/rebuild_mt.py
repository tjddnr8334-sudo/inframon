#!/usr/bin/env python3
"""MT-InSAR 결과로 **교량 산출물을 처음부터 다시** 만든다 — 트윈·PINN·FRAM·GNSS 대조.

지금까지의 `project.h5` 는 '데크 ±30 m' 로 고른 점에서 나왔다. 그 점들은 교면이 아니라
강변 지반이었고(`make_deck_vs_ground.py`), 그 위에서 돌아간 PINN·CRI·트윈도 같은 땅을
보고 있었다. MT-InSAR 를 붙였으니 그 결과로 다시 돌린다.

  ① MT-InSAR — 기선망·점별 DEM 오차·열팽창·APS 를 같이 풀고 γ 를 낸다.
  ② 교면 PS 선별 — 코히런스로 지오로케이션 밀림을 되돌린 자리에서 ±폭/2 안,
     γ 문턱을 넘는 점만. 점이 너무 적으면 문턱을 단계적으로 낮추고 **낮췄다고 적는다**.
  ③ 그 점들의 **APS 보정된 LOS** 로 track_deck_mt.h5 를 쓴다.
  ④ 기존 파이프라인 그대로 — IFC 프록시 → 결합 → twin.glb → PINN → FRAM/CRI →
     twin_cri.glb. 값이 바뀌었을 뿐 경로는 같다.
  ⑤ 보고서 계측이 있는 교량은 R²·추세·연주기를 다시 재고, 우연 기준선을 같이 낸다.

옛 산출물은 지우지 않고 `project.h5` 만 새로 쓴다(트윈·뷰어는 덮어쓴다 — 그게 보여야
할 최신 값이다). 무엇이 바뀌었는지는 `재도출.json` 과 `결과.md` 에 남는다.

    python scripts/rebuild_mt.py --only 가양대교 월드컵대교
    python scripts/rebuild_mt.py                     # 처리폴더가 있는 전 교량

산출(교량마다): track_deck_mt.h5 · project.h5(갱신) · twin*.glb · 재도출.json
전체: docs/bridges/rebuild_mt_all.json · docs/img/value/재도출_요약.png
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import fields as dc_fields
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
from inframon.insar.geolocation import los_ground_unit                # noqa: E402
from inframon.insar.residual_height import collect_bperp              # noqa: E402
from insar_series import dec_year, report_series, slope_ci            # noqa: E402
from kaia_theme import MPL, use_mpl_style                             # noqa: E402
from make_deck_vs_ground import (                                     # noqa: E402
    annual_z, dist_to_polyline, dmon, peak_month, to_local,
)

use_mpl_style()

HEADING_DEG = -13.3
GAMMA_LADDER = (0.45, 0.35, 0.25, 0.15, 0.0)     # 점이 모자라면 차례로 낮춘다
MIN_DECK_PTS = 12
SECTION = "## MT-InSAR 로 재도출"


def load_bridge(folder: Path):
    """bridge.json → bridge_run.Bridge (모르는 키는 버린다)."""
    from bridge_run import Bridge

    d = json.loads((folder / "bridge.json").read_text(encoding="utf-8"))
    keep = {f.name for f in dc_fields(Bridge)}
    return Bridge(**{k: v for k, v in d.items() if k in keep})


def select_deck(folder: Path, a) -> dict | None:
    """MT-InSAR 를 돌리고 교면 PS 를 고른다. 못 고르면 사유를 담아 돌려준다."""
    import h5py

    rh, dp, bj = (folder / "track_rh_full.h5", folder / "deck_polyline.json",
                  folder / "bridge.json")
    if not (rh.exists() and dp.exists() and bj.exists()):
        return None
    b = json.loads(bj.read_text(encoding="utf-8"))
    proc, master = b.get("proc"), str(b.get("master") or "")
    if not proc or not Path(proc).exists():
        return {"skipped": f"SNAP 처리폴더가 없다 — B⊥ 를 못 읽는다 ({proc})"}
    bp = collect_bperp(proc, master)
    if len(bp) < 5:
        return {"skipped": f"B⊥ 쌍이 {len(bp)}개뿐이다"}

    with h5py.File(rh, "r") as h:
        ll = np.asarray(h["pixel_lonlat"][()], float)
        los0 = np.asarray(h["los_mm"][()], float)
        coh = np.asarray(h["coh"][()], float)
        inc_pt = np.asarray(h["incidenceAngle"][()], float)
        ep = [s.decode() if isinstance(s, bytes) else str(s) for s in h["epochs"][()]]
        rh_pt = (np.asarray(h["residual_height_m"][()], float)
                 if "residual_height_m" in h else None)

    bperp = np.asarray([0.0 if e == master else bp.get(e, {}).get("bperp_m", np.nan)
                        for e in ep], float)
    ok = np.isfinite(bperp)
    if ok.sum() < 5:
        return {"skipped": "시점과 B⊥ 가 맞는 것이 5개 미만"}
    any_pair = next(iter(bp.values()))
    R = float(any_pair["slant_range_m"])
    inc = float(np.nanmedian(inc_pt)) or float(any_pair["incidence_deg"])
    t = np.asarray([dec_year(e) for e in ep], float)[ok]
    ep = [e for e, k in zip(ep, ok) if k]
    los0, bperp = los0[:, ok], bperp[ok]

    poly = np.asarray(json.loads(dp.read_text(encoding="utf-8"))["geometry"], float)
    lat0 = float(np.mean(ll[:, 1]))
    P, V = to_local(ll, lat0), to_local(poly[:, ::-1], lat0)
    half = max(6.0, float(b.get("width_m") or 20.0) / 2)
    ue, un = los_ground_unit(HEADING_DEG)

    # 밀림 되돌림 — 평균 코히런스가 가장 높은 자리(GNSS 를 보지 않는 기준)
    best = None
    for o in range(-40, 51, 5):
        m = dist_to_polyline(P - np.array([ue, un]) * o, V) <= half
        if m.sum() < 10:
            continue
        c = float(np.mean(coh[m]))
        if best is None or c > best[1]:
            best = (o, c, m)
    if best is None:
        return {"skipped": "교면 회랑 안 점이 10개 미만"}
    off, cbar, corridor = best

    temp = resolve_temperature(ep, lat=b.get("lat"), lon=b.get("lon"),
                               csv_path=a.temperature_csv, fetch=not a.no_fetch)
    out = mt.run(los0, t, bperp, P, slant_range_m=R, incidence_deg=inc,
                 temperature_C=temp["temperature"], gamma_min=GAMMA_LADDER[0],
                 aps_radius_m=a.aps_radius, n_iter=a.iters)

    g = out["fit"].gamma
    used = None
    for thr in GAMMA_LADDER:
        sel = corridor & (g >= thr)
        if sel.sum() >= MIN_DECK_PTS:
            used = thr
            break
    if used is None:
        used = 0.0
        sel = corridor
    d0 = dist_to_polyline(P, V)
    return {"b": b, "P": P, "dist": d0, "corridor": corridor, "sel": sel,
            "gamma": g, "gamma_used": used, "offset_m": off, "coh_mean": cbar,
            "half_m": half, "t": t, "epochs": ep, "bperp": bperp, "coh": coh,
            "inc_pt": inc_pt, "ll": ll, "rh_pt": rh_pt, "out": out, "temp": temp,
            "slant_range_m": R, "incidence_deg": inc, "master": master}


def write_track(folder: Path, s: dict) -> Path:
    """고른 교면 PS 의 **APS 보정된 LOS** 로 새 트랙을 쓴다(기존 포맷 그대로)."""
    import h5py

    sel = s["sel"]
    p = folder / "track_deck_mt.h5"
    with h5py.File(p, "w") as h:
        h.create_dataset("pixel_lonlat", data=s["ll"][sel].astype(np.float64))
        h.create_dataset("los_mm", data=s["out"]["los_corrected_mm"][sel].astype(np.float32))
        h.create_dataset("coh", data=s["coh"][sel].astype(np.float32))
        h.create_dataset("incidenceAngle", data=s["inc_pt"][sel].astype(np.float32))
        h.create_dataset("epochs", data=np.array(s["epochs"], dtype="S8"))
        h.create_dataset("temporal_coherence", data=s["gamma"][sel].astype(np.float32))
        h.create_dataset("residual_height_m",
                         data=s["out"]["fit"].dh_m[sel].astype(np.float32))
        if s["out"]["fit"].thermal_mm_per_C is not None:
            h.create_dataset("thermal_mm_per_C",
                             data=s["out"]["fit"].thermal_mm_per_C[sel].astype(np.float32))
        h.attrs["HEADING"] = str(HEADING_DEG)
        h.attrs["RADAR_WAVELENGTH"] = str(mt.RADAR_WAVELENGTH_M)
        h.attrs["source"] = (
            f"MT-InSAR 재도출 — 밀림 {s['offset_m']:+d} m 되돌림 · 회랑 ±{s['half_m']:.0f} m · "
            f"γ≥{s['gamma_used']:.2f} · APS 제거 · 점별 Δh/열팽창 동시 추정")
        h.attrs["gamma_min_used"] = str(s["gamma_used"])
        h.attrs["n_points"] = str(int(sel.sum()))
    return p


def rebuild_twin(folder: Path, track: Path, s: dict) -> dict:
    """기존 파이프라인 그대로 — IFC → 결합 → twin.glb → PINN → CRI → twin_cri.glb."""
    import bridge_run as br

    b = load_bridge(folder)
    b.track = str(track)
    tw = br.build_twin(b, track, folder)
    pin = br.run_pinn(b, tw, folder)
    return {"elements": tw["elements"], "points": tw["points"], "bound": tw["bound"],
            "cri": None if pin is None else pin["cri"],
            "warning": None if pin is None else pin["warning"],
            "notes": b.notes}


def read_fram(folder: Path) -> dict:
    import h5py

    p = folder / "project.h5"
    if not p.exists():
        return {}
    with h5py.File(p, "r") as f:
        out = {}
        if "fram/CRI" in f:
            c = np.asarray(f["fram/CRI"][()], float)
            c = np.nanmedian(c, axis=1) if c.ndim == 2 else c
            out.update({"cri_point_median": round(float(np.nanmedian(c)), 3),
                        "cri_point_max": round(float(np.nanmax(c)), 3),
                        "cri_over_0p8": int(np.sum(c >= 0.8))})
        if "fram/network_resonance" in f:
            nr = np.asarray(f["fram/network_resonance"][()], float)
            out["network_resonance_max"] = round(float(np.nanmax(nr)), 3)
        if "insar/velocity_mm_yr" in f:
            v = np.asarray(f["insar/velocity_mm_yr"][()], float)
            out.update({"velocity_median_mm_yr": round(float(np.nanmedian(v)), 2),
                        "n_points": int(v.size)})
        if "pinn/EI" in f:
            out["EI_median"] = float(np.nanmedian(np.asarray(f["pinn/EI"][()], float)))
    return out


# ── GNSS 대조 ─────────────────────────────────────────────────────────────
def ym(t):
    y = np.floor(t).astype(int)
    return list(zip(y, np.clip(((t - y) * 12).astype(int) + 1, 1, 12)))


def compare_gnss(auto: dict, eye: dict, name: str, t: np.ndarray,
                 los_deck: np.ndarray, rng) -> dict | None:
    """교면 평균 ↔ 보고서 계측 — R²·추세·연주기, 그리고 우연 기준선."""
    rp = report_series(auto, eye, name)
    if rp is None:
        return None
    tr, vr, what, how = rp
    kr, ki = ym(tr), ym(t)
    common = sorted(set(kr) & set(ki))
    if len(common) < 6:
        return {"quantity": what, "read": how, "n_months": len(common),
                "note": "짝지어지는 달이 6개 미만 — 대조하지 않는다"}
    ridx = {k: i for i, k in enumerate(kr)}
    x = np.asarray([np.mean(los_deck[[i for i, k in enumerate(ki) if k == c]])
                    for c in common])
    y = np.asarray([vr[ridx[c]] for c in common], float)
    tc = np.asarray([c[0] + (c[1] - .5) / 12 for c in common])

    r = float(np.corrcoef(x, y)[0, 1]) if np.std(x) > 1e-9 and np.std(y) > 1e-9 else np.nan
    null = []
    for _ in range(500):
        xp = x[rng.permutation(len(x))]
        null.append(abs(float(np.corrcoef(xp, y)[0, 1])))
    r2_null = float(np.nanpercentile(null, 95)) ** 2

    bi, ci = slope_ci(tc, x[None, :])
    bg, cg = slope_ci(tc, y[None, :])
    overlap = bool(abs(float(bi[0]) - float(bg[0])) <= float(ci[0]) + float(cg[0]))

    zi = complex(np.ravel(annual_z(tc, x))[0])
    zg = complex(np.ravel(annual_z(tc, y))[0])
    return {
        "quantity": what, "read": how, "n_months": len(common),
        "r": round(r, 3), "r2": round(r * r, 3), "r2_chance95": round(r2_null, 3),
        "beats_chance": bool(r * r > r2_null),
        "insar_slope_mm_yr": round(float(bi[0]), 2), "insar_slope_ci": round(float(ci[0]), 2),
        "report_slope_per_yr": round(float(bg[0]), 2), "report_slope_ci": round(float(cg[0]), 2),
        "trend_ci_overlap": overlap,
        "annual_phase_diff_months": round(dmon(peak_month(zi), peak_month(zg)), 2),
        "annual_amp_insar_mm": round(float(abs(zi)), 2),
        "annual_amp_report": round(float(abs(zg)), 2),
    }


def update_md(folder: Path, rec: dict) -> None:
    md = folder / "결과.md"
    if not md.exists():
        return
    txt = md.read_text(encoding="utf-8")
    L = [SECTION, "",
         "교면 PS 만 골라(밀림 되돌림 · 회랑 ±폭/2 · γ 문턱) APS 를 뺀 LOS 로 트윈·PINN·",
         "FRAM 을 다시 돌렸다. 아래 값이 현재 유효한 값이다 — 위쪽 본문의 ±30 m 선별 값은",
         "강변 지반이 섞인 옛 값이다.", "",
         "| 항목 | 값 |", "|---|---|",
         f"| 교면 PS | {rec['n_deck_ps']}점 (γ≥{rec['gamma_used']:.2f} · 회랑 ±{rec['half_m']:.0f} m · 밀림 {rec['offset_m']:+d} m) |",
         f"| 평균 코히런스 | {rec['coh_mean']:.3f} |",
         f"| IFC 결합 | 부재 {rec.get('elements', '—')} · 결합 {rec.get('bound', '—')} |",
         f"| 속도 중앙값 | {rec.get('velocity_median_mm_yr', '—')} mm/년 |",
         "| CRI | 점별 중앙 {} · 최대 {} · 전역 {} ({}) |".format(
             rec.get("cri_point_median", "—"), rec.get("cri_point_max", "—"),
             "—" if rec.get("cri") is None else f"{rec['cri']:.3f}",
             rec.get("warning", "—")),
         ]
    if rec.get("thermal_deck_mm_per_C") is not None:
        L.append(f"| 열팽창(교면) | {rec['thermal_deck_mm_per_C']:+.3f} mm/°C |")
    g = rec.get("gnss")
    if g and "r2" in g:
        L += [f"| 계측 대조 | {g['quantity']} · {g['n_months']}개월 ({g['read']}) |",
              f"| R² | {g['r2']:.2f} — 우연 기준선 {g['r2_chance95']:.2f} → "
              f"{'넘는다' if g['beats_chance'] else '못 넘는다'} |",
              f"| 추세 | InSAR {g['insar_slope_mm_yr']:+.2f}±{g['insar_slope_ci']:.2f} mm/년 · "
              f"계측 {g['report_slope_per_yr']:+.2f}±{g['report_slope_ci']:.2f} → "
              f"{'신뢰구간이 겹친다(다르다고 말할 수 없다)' if g['trend_ci_overlap'] else '겹치지 않는다'} |",
              f"| 연주기 | 위상차 {g['annual_phase_diff_months']:+.1f}개월 · "
              f"진폭 InSAR {g['annual_amp_insar_mm']:.1f} mm / 계측 {g['annual_amp_report']:.1f} |"]
    elif g:
        L.append(f"| 계측 대조 | {g.get('note', '불가')} |")
    else:
        L.append("| 계측 대조 | 보고서 계측 없음 |")
    L += ["", "3D: `twin.viewer.html`(속도) · `twin_cri.viewer.html`(CRI) — 더블클릭", ""]
    block = "\n".join(L)
    pat = re.compile(re.escape(SECTION) + r".*?(?=\n## |\Z)", re.S)
    txt = pat.sub(block, txt) if pat.search(txt) else txt.rstrip() + "\n\n" + block
    md.write_text(txt, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--aps-radius", type=float, default=400.0)
    ap.add_argument("--iters", type=int, default=2)
    ap.add_argument("--temperature-csv", default=None)
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--json-out", default="docs/bridges/rebuild_mt_all.json")
    ap.add_argument("--summary", default="docs/img/value/재도출_요약.png")
    a = ap.parse_args()

    auto = json.loads(Path("docs/bridges/hangang_gnss_monthly.json")
                      .read_text(encoding="utf-8"))
    eye = json.loads(Path("docs/bridges/hangang_displacement_2024.json")
                     .read_text(encoding="utf-8"))
    rng = np.random.default_rng(17)

    rows = []
    for d in sorted(Path(a.root).iterdir()):
        if not d.is_dir() or (a.only and d.name not in a.only):
            continue
        s = select_deck(d, a)
        if s is None:
            continue
        if s.get("skipped"):
            print(f"{d.name:<12} 건너뜀 — {s['skipped']}")
            rows.append({"name": d.name, "skipped": s["skipped"]})
            continue
        track = write_track(d, s)
        try:
            tw = rebuild_twin(d, track, s)
        except Exception as exc:                      # noqa: BLE001 — 사유를 남기고 계속
            print(f"{d.name:<12} 트윈 재생성 실패 — {type(exc).__name__}: {str(exc)[:70]}")
            rows.append({"name": d.name, "skipped": f"트윈 재생성 실패: {type(exc).__name__}"})
            continue

        sel = s["sel"]
        th = s["out"]["fit"].thermal_mm_per_C
        rec = {"name": d.name, "n_deck_ps": int(sel.sum()),
               "gamma_used": s["gamma_used"], "offset_m": s["offset_m"],
               "half_m": s["half_m"], "coh_mean": round(s["coh_mean"], 3),
               "gamma_median_deck": round(float(np.nanmedian(s["gamma"][sel])), 3),
               "n_epochs": len(s["epochs"]),
               "temperature_ok": bool(s["temp"]["meta"].get("ok")),
               "thermal_deck_mm_per_C": (None if th is None
                                         else round(float(np.nanmedian(th[sel])), 3)),
               **tw, **read_fram(d)}
        g = compare_gnss(auto, eye, d.name, s["t"],
                         s["out"]["los_corrected_mm"][sel].mean(axis=0), rng)
        rec["gnss"] = g
        (d / "재도출.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
        update_md(d, rec)
        rows.append(rec)
        msg = (f"{d.name:<12} 교면 {rec['n_deck_ps']:>4}점(γ≥{rec['gamma_used']:.2f}) · "
               f"결합 {rec.get('bound', '—')} · 속도 {rec.get('velocity_median_mm_yr', '—')} · "
               f"CRI {rec.get('cri', '—')}")
        if g and "r2" in g:
            msg += (f" | {g['quantity'][:10]} R² {g['r2']:.2f}/{g['r2_chance95']:.2f} "
                    f"추세{'겹침' if g['trend_ci_overlap'] else '다름'} "
                    f"위상 {g['annual_phase_diff_months']:+.1f}개월")
        print(msg)

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "MT-InSAR 로 고른 교면 PS 의 APS 보정 LOS 로 트윈·PINN·FRAM 재도출",
         "_주의": "gamma_used 가 낮은 교량은 문턱을 낮춰 점을 채운 것 — 그만큼 덜 믿을 값이다",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    summary(rows, Path(a.summary))
    return 0


def summary(rows: list, out: Path) -> None:
    ok = [r for r in rows if not r.get("skipped")]
    if not ok:
        return
    ok.sort(key=lambda r: r["n_deck_ps"], reverse=True)
    nm = [r["name"] for r in ok]
    y = np.arange(len(nm))
    fig, ax = plt.subplots(1, 3, figsize=(13.8, 0.36 * len(nm) + 2.6), sharey=True)

    col = [MPL["blue"] if r["gamma_used"] >= 0.25 else MPL["orange"] for r in ok]
    ax[0].barh(y, [r["n_deck_ps"] for r in ok], color=col, height=.62,
               edgecolor=MPL["slate"], linewidth=.4)
    ax[0].set_yticks(y)
    ax[0].set_yticklabels(nm, fontsize=9.5)
    ax[0].invert_yaxis()
    ax[0].set_xlabel("교면 PS 점 수", fontsize=10)
    ax[0].grid(axis="x", alpha=.25)
    ax[0].set_title("주황 = γ 문턱을 0.25 아래로 낮춰 채운 곳", fontsize=10.5,
                    color=MPL["ink"], pad=6)

    # 판정(경고 등급)에 쓰는 것은 **전역 CRI**(점·시점 최대)다. 점별 중앙값은 훨씬
    # 낮으므로(0.06~0.23) 같은 축에 두면 0.8 문턱이 뜻을 잃는다 — 둘을 같이 그린다.
    gl = [r.get("cri") or 0.0 for r in ok]
    md = [r.get("cri_point_median") or 0.0 for r in ok]
    ax[1].barh(y, gl, color=MPL["slate"], height=.62,
               edgecolor=MPL["slate"], linewidth=.4, label="전역 CRI(최대)")
    ax[1].scatter(md, y, s=26, color=MPL["blue"], edgecolors=MPL["slate"],
                  linewidths=.4, zorder=3, label="점별 중앙값")
    ax[1].axvline(0.8, color=MPL["red"], lw=1.2, ls="--")
    ax[1].set_xlim(0, 1.0)
    ax[1].set_xlabel("FRAM CRI (붉은 선 0.8 = 위험 문턱)", fontsize=10)
    ax[1].grid(axis="x", alpha=.25)
    ax[1].legend(fontsize=8.6, loc="lower right", framealpha=.9)
    ax[1].set_title("FRAM 공명 위험 지수", fontsize=10.5, color=MPL["ink"], pad=6)

    got = [(i, r) for i, r in enumerate(ok)
           if r.get("gnss") and "r2" in (r["gnss"] or {})]
    for i, r in got:
        g = r["gnss"]
        ax[2].barh(i - 0.16, g["r2"], height=.3, color=MPL["green"],
                   edgecolor=MPL["slate"], linewidth=.4)
        ax[2].barh(i + 0.16, g["r2_chance95"], height=.3, color=MPL["rule"],
                   edgecolor=MPL["slate"], linewidth=.4)
    ax[2].set_xlim(0, 1.05)
    ax[2].set_xlabel("R² — 초록 실제 · 회색 우연 기준선(95%)", fontsize=10)
    ax[2].grid(axis="x", alpha=.25)
    ax[2].set_title(f"보고서 계측이 있는 {len(got)}개소", fontsize=10.5,
                    color=MPL["ink"], pad=6)

    fig.suptitle("MT-InSAR 로 재도출 — 전 교량", fontsize=14.5, fontweight="bold",
                 color=MPL["ink"], y=0.995)
    fig.text(0.006, 0.008,
             "※ 교면 PS = 코히런스로 밀림을 되돌린 자리에서 ±폭/2 안, γ 문턱을 넘는 점. "
             "점이 12개에 못 미치면 문턱을 0.45→0.35→0.25→0.15 순으로 낮췄고 그 사실을 "
             "json 에 적었다. CRI·속도는 그 점들로 다시 돌린 PINN/FRAM 결과다.",
             fontsize=8.8, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.035, 1, 0.965))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("wrote", out)


if __name__ == "__main__":
    raise SystemExit(main())
