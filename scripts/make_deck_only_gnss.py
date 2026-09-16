#!/usr/bin/env python3
"""**교면 점만** 골라 GNSS 와 맞춰 본다 — 할 수 있는 보정을 다 하고도 얼마나 어긋나나.

"교면에 대한 InSAR 데이터로만 비교해야지" 가 맞는 순서다. 그래서 다음을 전부 한다.

  ① 지오로케이션 밀림 되돌리기 — 교량은 DEM 보다 높아 점이 δh/tanθ 만큼 밀려 찍힌다.
     δh 는 점별 추정이 σ≈26 m 라 못 믿으므로, **강체 오프셋을 −40~+50 m 훑어
     평균 코히런스가 가장 높은 자리**를 교면으로 본다(GNSS 를 쓰지 않는 기준이다).
  ② 교면 회랑 — 교량 중심선에서 ±(폭/2) 안의 점만.
  ③ 지반 공통성분 제거 — 100~400 m 밖 점들의 시점별 중앙값을 빼면 대기·지반 공통
     신호가 빠지고 교량 고유 성분만 남는다(InSAR 에서 쓰는 기준점 보정과 같은 생각).
  ④ 달력 시간축으로 직선+연주기 적합, 점 붓스트랩으로 위상 95% 폭.

그러고 나서 GNSS 연주기 위상과 비교한다. 결과를 미리 적어 두면: **그래도 맞지 않는다.**
위상 폭은 0.1~3.3개월로 좁은데(신호는 또렷하다) GNSS 와는 1.3~5.8개월 어긋난다.
또렷한데 다른 신호 — 교면이 아니라 다른 것을 보고 있다는 뜻이다.

    python scripts/make_deck_only_gnss.py

산출: docs/img/value/교면전용_GNSS대조.png · docs/bridges/deck_only_gnss.json
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

from inframon.insar.geolocation import los_ground_unit                 # noqa: E402
from kaia_theme import MPL, use_mpl_style                              # noqa: E402
from make_deck_vs_ground import (                                      # noqa: E402
    annual_z, dist_to_polyline, dmon, peak_month, to_local,
)
from make_trend_agree import dec_year                                  # noqa: E402

use_mpl_style()

HEADING_DEG = -13.3          # 결과.md 가 적어 둔 궤도 heading
WITH_GNSS = ["가양대교", "샛강문화다리", "월드컵대교", "원효대교", "올림픽대교"]


def z1(t, y) -> complex:
    return complex(np.ravel(annual_z(t, y))[0])


def gnss_series(auto: dict, eye: dict, nm: str) -> list:
    out = []
    for c in auto.get("charts", []):
        if c.get("bridge") != nm:
            continue
        ts, vs = [], []
        for y, arr in c["monthly"].items():
            for i, v in enumerate(arr):
                if v is not None:
                    ts.append(int(y) + (i + 0.5) / 12)
                    vs.append(float(v))
        out.append((c["sensor"], np.asarray(ts), np.asarray(vs)))
    e = eye.get(nm, {})
    if e.get("status") == "read":
        ts, vs = [], []
        for y, arr in e["monthly"].items():
            for i, v in enumerate(arr):
                if v is not None:
                    ts.append(int(y) + (i + 0.5) / 12)
                    vs.append(float(v))
        out.append((e.get("quantity", "계측"), np.asarray(ts), np.asarray(vs)))
    return out


def deck_only(folder: Path, half_min: float = 6.0) -> dict | None:
    """교면 점만 남긴 시계열 — 오프셋은 코히런스로 정한다(GNSS 를 보지 않는다)."""
    import h5py

    rh = folder / "track_rh_full.h5"
    dp = folder / "deck_polyline.json"
    bj = folder / "bridge.json"
    if not (rh.exists() and dp.exists() and bj.exists()):
        return None
    b = json.loads(bj.read_text(encoding="utf-8"))
    half = max(half_min, float(b.get("width_m") or 20.0) / 2)
    with h5py.File(rh, "r") as h:
        ll = np.asarray(h["pixel_lonlat"][()], float)
        los = np.asarray(h["los_mm"][()], float)
        coh = np.asarray(h["coh"][()], float)
        ep = [s.decode() if isinstance(s, bytes) else str(s)
              for s in h["epochs"][()]]
    poly = np.asarray(json.loads(dp.read_text(encoding="utf-8"))["geometry"], float)
    lat0 = float(np.mean(ll[:, 1]))
    P = to_local(ll, lat0)
    V = to_local(poly[:, ::-1], lat0)
    ue, un = los_ground_unit(HEADING_DEG)

    best = None
    for o in range(-40, 51, 5):
        m = dist_to_polyline(P - np.array([ue, un]) * o, V) <= half
        if m.sum() < 10:
            continue
        c = float(np.mean(coh[m]))
        if best is None or c > best[1]:
            best = (o, c, m)
    if best is None:
        return None
    off, cbar, near = best

    d0 = dist_to_polyline(P, V)
    far = (d0 >= 100) & (d0 < 400)
    if far.sum() < 8:
        return None
    t = np.asarray([dec_year(s) for s in ep], float)
    ground = np.median(los[far], axis=0)
    D = los[near] - ground                      # 교면 − 지반 공통성분
    mean = np.mean(D, axis=0)

    rng = np.random.default_rng(7)
    pk = []
    for _ in range(400):
        s = rng.integers(0, D.shape[0], D.shape[0])
        pk.append(peak_month(z1(t, np.mean(D[s], axis=0))))
    ang = (np.asarray(pk) - 0.5) / 12 * 2 * np.pi
    R = abs(np.mean(np.exp(1j * ang)))
    ci = float(np.sqrt(max(-2 * np.log(max(R, 1e-9)), 0.0)) / (2 * np.pi) * 12 * 1.96)

    z = z1(t, mean)
    return {"name": folder.name, "offset_m": off, "coh": round(cbar, 3),
            "n_deck": int(near.sum()), "n_ground": int(far.sum()),
            "half_width_m": round(half, 1), "t": t, "series": mean,
            "peak_month": round(peak_month(z), 2), "amp_mm": round(abs(z), 2),
            "peak_ci_months": round(ci, 2)}


def monthly_cycle(t: np.ndarray, v: np.ndarray) -> np.ndarray:
    """직선 성분을 뺀 뒤 달별로 모아 평균 — 연주기 모양을 눈으로 보려고."""
    A = np.vstack([np.ones_like(t), t]).T
    c, *_ = np.linalg.lstsq(A, v, rcond=None)
    r = v - A @ c
    mo = np.clip(((t % 1.0) * 12).astype(int), 0, 11)
    out = np.full(12, np.nan)
    for k in range(12):
        if np.any(mo == k):
            out[k] = np.mean(r[mo == k])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--auto", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--eye", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--out", default="docs/img/value/교면전용_GNSS대조.png")
    ap.add_argument("--json-out", default="docs/bridges/deck_only_gnss.json")
    a = ap.parse_args()

    auto = json.loads(Path(a.auto).read_text(encoding="utf-8"))
    eye = json.loads(Path(a.eye).read_text(encoding="utf-8"))

    rows, panels = [], []
    for nm in WITH_GNSS:
        d = deck_only(Path(a.root) / nm)
        g = gnss_series(auto, eye, nm)
        if d is None or not g:
            continue
        zg = np.mean([z1(tt, vv) for _, tt, vv in g])
        d["gnss_peak_month"] = round(peak_month(zg), 2)
        d["gnss_amp_mm"] = round(abs(zg), 2)
        d["phase_diff_months"] = round(dmon(d["peak_month"], d["gnss_peak_month"]), 2)
        gm = np.nanmean(np.vstack([monthly_cycle(tt, vv) for _, tt, vv in g]), axis=0)
        panels.append((d, monthly_cycle(d["t"], d["series"]), gm))
        rows.append({k: v for k, v in d.items() if k not in ("t", "series")})
        print(f"{nm:<12} 오프셋 {d['offset_m']:>+4} m · 코히 {d['coh']:.3f} · "
              f"교면 {d['n_deck']:>3}점 | InSAR 최대월 {d['peak_month']:>5.1f} "
              f"(±{d['peak_ci_months']:.1f}) · GNSS {d['gnss_peak_month']:>5.1f} "
              f"→ 어긋남 {d['phase_diff_months']:>+5.1f}개월")

    n = len(panels)
    fig, axes = plt.subplots(1, n, figsize=(2.95 * n, 4.6), sharex=True)
    axes = np.atleast_1d(axes)
    mo = np.arange(1, 13)
    for ax, (d, ins, gn) in zip(axes, panels):
        a1 = np.nanmax(np.abs(ins)) or 1.0
        a2 = np.nanmax(np.abs(gn)) or 1.0
        ax.plot(mo, gn / a2, "-o", ms=4, lw=2.0, color=MPL["red"], label="현장 계측")
        ax.plot(mo, ins / a1, "-o", ms=4, lw=2.0, color=MPL["blue"],
                label="InSAR 교면")
        ax.axhline(0, color=MPL["rule"], lw=1)
        ax.set_xticks([1, 4, 7, 10])
        ax.set_xticklabels(["1월", "4월", "7월", "10월"], fontsize=9)
        ax.set_ylim(-1.35, 1.35)
        ax.grid(alpha=.25)
        bad = abs(d["phase_diff_months"]) > 1.5
        ax.set_title(f"{d['name']}\n교면 {d['n_deck']}점 · 코히 {d['coh']:.2f}\n"
                     f"어긋남 {d['phase_diff_months']:+.1f}개월",
                     fontsize=10, color=(MPL["red"] if bad else MPL["green"]),
                     pad=6)
    axes[0].set_ylabel("연주기 성분 (각자 최대=1 로 맞춤)", fontsize=10)
    axes[0].legend(fontsize=9, loc="lower left", framealpha=.95)
    fig.suptitle("교면 점만 남기고 지반 성분까지 뺀 뒤 — 그래도 계절이 어긋난다",
                 fontsize=14.5, fontweight="bold", color=MPL["ink"], y=0.985)
    fig.text(0.006, 0.012,
             "※ 한 것: ① 코히런스로 지오로케이션 밀림(−40~+50 m) 되돌리기 "
             "② 교량 중심선 ±폭/2 안만 ③ 100~400 m 밖 지반 공통성분 빼기 "
             "④ 달력 시간축으로 연주기 적합.\n"
             "   위상 95% 폭은 0.1~3.3개월로 좁다 — InSAR 신호 자체는 또렷하다. "
             "또렷한데 GNSS 와 다르다는 것은, 그 신호가 교면 거동이 아니라는 뜻이다.",
             fontsize=9, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.075, 1, 0.945))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("wrote", out)

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "교면 점만 남기고 지반 공통성분을 뺀 뒤의 연주기 대조",
         "_오프셋_기준": "평균 코히런스 최대 — GNSS 를 보지 않고 정한다",
         "_결론": "위상 신뢰폭은 좁은데 GNSS 와 1.3~5.8개월 어긋난다 — 또렷하지만 "
                "교면 거동이 아닌 신호다",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
