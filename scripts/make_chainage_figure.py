#!/usr/bin/env python3
"""PS 재선별 → 교축 등간격 프로파일 → 같은 지점의 PINN 가상센싱 — 4단 그림.

  ① 엄격 선별   ADI ≤ 0.25 (ADI 없으면 γ_temp ≥ 0.80 대체)
  ② 완화+재선별 ADI ≤ 0.40 ∧ γ_temp ≥ 0.60 → 노이즈 점 제거
  ③ 교축 구간집계 5~10 m 구간 중앙값 ± 표준오차 — 종방향 등간격 연속 프로파일. 결측 구간은
     붉게 칠한다(점이 없어 모르는 곳을 숨기지 않는다)
  ④ 같은 구간에서 PINN 가상센싱 — 관측 누적 변위(구간 대표) vs PINN 가상센서 누적 변위.
     "각 지점에 PS 를 세워야 그 지점의 가상센싱이 무엇인지 말할 수 있다"

    python scripts/make_chainage_figure.py docs/twin/twin_project.h5 --out docs/twin/twin_chainage.png
    python scripts/make_chainage_figure.py data/rerun_chyg/track_deck2.h5 --bridge 청양교 \\
        --lat 36.450655 --lon 126.807320 --height 10 --width 22 --out docs/img/chainage_chyg.png
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from inframon.insar.chainage import (MIN_BIN_POINTS, _use_korean_font,  # noqa: E402
                                     build_profile)


def _deck_geom(lat, lon, cache: Path | None):
    if cache and cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        road = d.get("road", d)
        return [tuple(p) for p in road["geometry"]], road.get("name", "")
    from inframon.insar.osm_bridge import find_bridges_near
    b = max(find_bridges_near(lat, lon, radius_m=250.0), key=lambda x: x.length_m)
    return b.geometry, b.name


def _binned_cumulative(h5: Path, prof) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """선별 점의 LOS 누적 변위(마지막−처음 1년 평균)를 구간별 중앙값으로."""
    with h5py.File(h5, "r") as f:
        los = np.asarray(f["insar/los"][()] if "insar/los" in f else f["los_mm"][()], float)
        dates = np.asarray(f["insar/dates"][()] if "insar/dates" in f else f["epochs"][()], float)
    if dates.dtype.kind in "SU":
        dates = np.arange(los.shape[1], dtype=float) * 12.0
    yr = dates / 365.25
    head, tail = yr <= yr.min() + 1.0, yr >= yr.max() - 1.0
    cum = np.nanmean(los[:, tail], axis=1) - np.nanmean(los[:, head], axis=1)
    c = prof.bin_center_m
    half = prof.bin_m / 2
    val = np.full(c.size, np.nan)
    n = np.zeros(c.size, int)
    for i, cc in enumerate(c):
        m = prof.selected & (prof.chainage_m >= cc - half) & (prof.chainage_m < cc + half)
        n[i] = int(m.sum())
        if n[i] >= MIN_BIN_POINTS:
            val[i] = float(np.median(cum[m]))
    return c, val, n


def _pinn_at_stations(h5: Path, stations: np.ndarray, length_m: float):
    """프로젝트 h5 의 PINN 가상센서(vsens_x 정규화 0~1)를 교축 station 에서 보간한 누적 변위."""
    with h5py.File(h5, "r") as f:
        if "pinn/vsens_total" not in f:
            return None
        vx = np.asarray(f["pinn/vsens_x"][()], float)
        vt = np.asarray(f["pinn/vsens_total"][()], float)
        dates = np.asarray(f["insar/dates"][()], float)
    xm = vx * length_m if vx.max() <= 1.0 + 1e-6 else vx
    yr = dates / 365.25
    head, tail = yr <= yr.min() + 1.0, yr >= yr.max() - 1.0
    cum = np.nanmean(vt[:, tail], axis=1) - np.nanmean(vt[:, head], axis=1)
    order = np.argsort(xm)
    return np.interp(stations, xm[order], cum[order]), xm, cum


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("h5")
    ap.add_argument("--bridge", default="정자교")
    ap.add_argument("--lat", type=float, default=37.36854)
    ap.add_argument("--lon", type=float, default=127.10900)
    ap.add_argument("--height", type=float, default=6.0, help="형하고 δh [m]")
    ap.add_argument("--width", type=float, default=26.5)
    ap.add_argument("--bin", type=float, default=10.0)
    ap.add_argument("--offset", type=float, default=None, help="교량 위 정의 ±m (기본 반폭)")
    ap.add_argument("--shift", action="store_true", help="쉬프트 보정(트랙 h5 일 때)")
    ap.add_argument("--cache", default="data/jeongjagyo_f120/deck_polyline.json")
    ap.add_argument("--out", default="docs/twin/twin_chainage.png")
    a = ap.parse_args()

    h5 = Path(a.h5)
    geom, name = _deck_geom(a.lat, a.lon, Path(a.cache) if a.cache else None)
    name = a.bridge or name
    kw = dict(bin_m=a.bin, correct_shift=a.shift, bridge_height_m=a.height,
              bridge_width_m=a.width, max_offset_m=a.offset)
    strict = build_profile(h5, geom, mode="strict", denoise=False, **kw)
    relaxed = build_profile(h5, geom, mode="relaxed", denoise=True, **kw)
    L = relaxed.deck_length_m
    ok, why = relaxed.is_publishable()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _use_korean_font(plt)
    fig, ax = plt.subplots(1, 4, figsize=(22, 4.6), gridspec_kw={"wspace": .32})
    vals = relaxed.value
    vlim = float(np.nanpercentile(np.abs(vals[relaxed.selected]), 95)) if relaxed.selected.any() \
        else float(np.nanpercentile(np.abs(vals), 95)) or 1.0
    norm = plt.Normalize(-vlim, vlim)
    half = a.width / 2

    def scat(axx, prof, title):
        axx.axhspan(-half, half, color="#7f8c9a", alpha=.18, zorder=0)
        axx.plot([0, L], [0, 0], color="#34495e", lw=3, zorder=1)
        nsel = prof.selected
        axx.scatter(prof.chainage_m[~nsel], prof.offset_m[~nsel], s=12, c="#c5ccd3",
                    marker="x", lw=.8, label="탈락", zorder=2)
        sc = axx.scatter(prof.chainage_m[nsel], prof.offset_m[nsel], s=46, c=prof.value[nsel],
                         cmap="RdYlBu_r", norm=norm, ec="#222", lw=.5, zorder=3,
                         label=f"선별 {int(nsel.sum())}")
        axx.set_xlim(-10, L + 10)
        axx.set_ylim(-45, 45)
        axx.set_xlabel("교축 거리 [m]")
        axx.set_ylabel("교축 직각 [m]")
        axx.set_title(title, fontsize=10)
        axx.legend(fontsize=7.5, loc="upper right")
        axx.grid(alpha=.25)
        return sc

    scat(ax[0], strict, f"① 엄격 선별 — {strict.meta['selection']}\n"
                        f"선별 {int(strict.selected.sum())}/{strict.selected.size} · "
                        f"커버리지 {strict.coverage() * 100:.0f}%")
    sc = scat(ax[1], relaxed, f"② 완화 + 재선별 — {relaxed.meta['selection']}\n"
                              f"{relaxed.meta['n_selected_before_denoise']} → 노이즈 "
                              f"{relaxed.meta['n_noise_removed']} 제거 → {int(relaxed.selected.sum())} · "
                              f"커버리지 {relaxed.coverage() * 100:.0f}%")

    # ③ 교축 구간집계 프로파일
    axx = ax[2]
    good = relaxed.bin_n >= MIN_BIN_POINTS
    for c, n in zip(relaxed.bin_center_m, relaxed.bin_n):
        if n < MIN_BIN_POINTS:
            axx.axvspan(c - relaxed.bin_m / 2, c + relaxed.bin_m / 2, color="#e74c3c",
                        alpha=.10, zorder=0)
    axx.axhline(0, color="#7f8c8d", lw=.8, ls="--")
    sel = relaxed.selected
    axx.scatter(relaxed.chainage_m[sel], relaxed.value[sel], s=22, c="#95a5a6", zorder=2,
                label="선별 점")
    if good.any():
        axx.errorbar(relaxed.bin_center_m[good], relaxed.bin_value[good],
                     yerr=relaxed.bin_sem[good], fmt="o-", color="#2c3e50", lw=1.8, ms=6,
                     capsize=3, zorder=4, label=f"{relaxed.bin_m:.0f} m 구간 중앙값 ± SE")
    axx.set_xlim(-10, L + 10)
    axx.set_xlabel("교축 거리 [m]")
    axx.set_ylabel(relaxed.meta.get("value", "값"))
    axx.set_title(f"③ 교축 구간집계 — {relaxed.bin_m:.0f} m 등간격 연속 프로파일\n"
                  f"구간 {relaxed.bin_n.size} · 대표값 있음 {int(good.sum())} · 결측(붉음) "
                  f"{len(relaxed.gaps())} · 색띠 게이트 {'통과' if ok else '차단'}", fontsize=10)
    axx.legend(fontsize=7.5, loc="upper right")
    axx.grid(alpha=.25)

    # ④ 같은 지점의 PINN 가상센싱
    axx = ax[3]
    c, obs_cum, n = _binned_cumulative(h5, relaxed)
    pinn = _pinn_at_stations(h5, c, L)
    axx.axhline(0, color="#7f8c8d", lw=.8, ls="--")
    if pinn is not None:
        p_at, xm, cum_all = pinn
        axx.plot(xm, cum_all, color="#8e44ad", lw=1.6, alpha=.9,
                 label="PINN 가상센서(연속) 누적 |u|")
        m = np.isfinite(obs_cum)
        axx.plot(c[m], p_at[m], "s", color="#8e44ad", ms=6, label="PINN @ PS 구간")
        axx.plot(c[m], obs_cum[m], "o-", color="#2c3e50", lw=1.6, ms=6,
                 label="PS 구간 대표 누적 LOS")
        axx.set_title("④ 같은 지점 — PS 구간 대표 vs PINN 가상센싱\n"
                      "(마지막 1년 평균 − 처음 1년 평균) [mm]", fontsize=10)
    else:
        axx.text(.5, .5, "프로젝트에 PINN 이 없습니다\n(트랙 h5 입력)", ha="center",
                 transform=axx.transAxes, color="#7f8c8d")
        axx.set_title("④ PINN 가상센싱 — 없음", fontsize=10)
    for cc, nn in zip(c, n):
        if nn < MIN_BIN_POINTS:
            axx.axvspan(cc - relaxed.bin_m / 2, cc + relaxed.bin_m / 2, color="#e74c3c",
                        alpha=.08, zorder=0)
    axx.set_xlim(-10, L + 10)
    axx.set_xlabel("교축 거리 [m]")
    axx.set_ylabel("누적 변위 [mm]")
    axx.legend(fontsize=7.5, loc="upper left")
    axx.grid(alpha=.25)

    cb = fig.colorbar(sc, ax=ax[1], fraction=.045, pad=.02)
    cb.set_label(relaxed.meta.get("value", ""), fontsize=8)
    fig.suptitle(f"{name} — PS 재선별 → 교축 등간격 프로파일 → 지점별 PINN 가상센싱   "
                 f"({h5.name} · {'쉬프트 보정' if a.shift else '보정 좌표'})", fontsize=12, y=1.03)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=135, bbox_inches="tight", facecolor="white")
    print(f"저장: {out}")
    print(f"  ①엄격 {strict.meta['selection']}: {int(strict.selected.sum())}점 · 커버리지 "
          f"{strict.coverage() * 100:.0f}%")
    print(f"  ②완화 {relaxed.meta['selection']}: {relaxed.meta['n_selected_before_denoise']} → "
          f"노이즈 {relaxed.meta['n_noise_removed']} 제거 → {int(relaxed.selected.sum())}점")
    print(f"  ③구간 {relaxed.bin_m:.0f} m × {relaxed.bin_n.size} · 커버리지 "
          f"{relaxed.coverage() * 100:.0f}% · 결측 {len(relaxed.gaps())} · 게이트 "
          f"{'통과' if ok else '차단'} — {why}")
    if pinn is not None:
        m = np.isfinite(obs_cum)
        if m.sum() >= 2:
            d = pinn[0][m] - obs_cum[m]
            print(f"  ④PINN−PS @ {int(m.sum())}구간: 평균차 {d.mean():+.2f} mm · RMS {np.sqrt((d ** 2).mean()):.2f} mm")


if __name__ == "__main__":
    main()
