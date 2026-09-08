#!/usr/bin/env python3
"""건기연 브리프(KICT_2PP, 2026-08-26) 2쪽 형식 — 교량당 4단 그림.

  (a) 검출 PS 를 교축 좌표계에 배치 (교면 사각형 안·밖)
  (b) DEM 대비 상대고도 종단 — **잔차고도 추정이 있어야 한다.** 우리 트랙에는 PSI 높이
      추정(B⊥)이 없어 아직 그릴 수 없다. 자리를 비우지 않고 그 사실을 적는다.
  (c) LOS 변위속도 vs 교축 — 점별 95% CI 오차막대, 참고범위 ±0.5 mm/yr 띠, 0 선.
      CI 가 0 을 포함하면 "변형 경향 없음".
  (d) 각 PS LOS 시계열(회색) + 중앙값(청색) + 추세

QC 는 브리프 방식(속도 불확실도 95% CI ≤ 1.0 mm/yr ∧ γ ≥ 0.4)이고, 기준점은 교대 구역
점 중앙값(있을 때)이다.

    python scripts/make_brief_figure.py docs/twin/twin_project.h5 --out docs/twin/twin_brief.png
    python scripts/make_brief_figure.py data/rerun_chyg/track_deck2.h5 --bridge 청양교 \\
        --lat 36.450655 --lon 126.807320 --height 10 --width 22 --shift \\
        --cache data/rerun_chyg/deck_polyline.json --out docs/img/brief_chyg.png
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from inframon.insar.chainage import (MIN_BIN_POINTS, REF_BAND_MM_YR,  # noqa: E402
                                     _epoch_days, _read_track, _use_korean_font,
                                     build_profile, reference_to_abutment)


def _deck_geom(lat, lon, cache: Path | None):
    if cache and cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        road = d.get("road", d)
        return [tuple(p) for p in road["geometry"]], road.get("name", "")
    from inframon.insar.osm_bridge import find_bridges_near
    b = max(find_bridges_near(lat, lon, radius_m=250.0), key=lambda x: x.length_m)
    return b.geometry, b.name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("h5")
    ap.add_argument("--bridge", default="정자교")
    ap.add_argument("--lat", type=float, default=37.36854)
    ap.add_argument("--lon", type=float, default=127.10900)
    ap.add_argument("--height", type=float, default=6.0)
    ap.add_argument("--width", type=float, default=26.5)
    ap.add_argument("--bin", type=float, default=10.0)
    ap.add_argument("--offset", type=float, default=None)
    ap.add_argument("--shift", action="store_true")
    ap.add_argument("--cache", default="data/jeongjagyo_f120/deck_polyline.json")
    ap.add_argument("--meta", default="", help="'준공 1990 · 연장 210 m …' 같은 머리말")
    ap.add_argument("--out", default="docs/twin/twin_brief.png")
    a = ap.parse_args()

    h5 = Path(a.h5)
    geom, _ = _deck_geom(a.lat, a.lon, Path(a.cache) if a.cache else None)
    prof = build_profile(h5, geom, bin_m=a.bin, mode="ci", denoise=True, reference="abutment",
                         correct_shift=a.shift, bridge_height_m=a.height,
                         bridge_width_m=a.width, max_offset_m=a.offset)
    L = prof.deck_length_m
    half = a.width / 2
    sel = prof.selected
    st, of, v = prof.chainage_m, prof.offset_m, prof.value
    ci = prof.ci95 if prof.ci95 is not None else np.full(v.size, np.nan)

    # 시계열(기준점 적용된 것과 같은 처리)
    tr = _read_track(h5)
    los = np.asarray(tr["los_mm"], float)
    days = _epoch_days(tr)
    if prof.reference and prof.reference.get("applied"):
        on = np.abs(of) <= (a.offset if a.offset is not None else half)
        los, _ = reference_to_abutment(los, st, L, on_deck=on)
    order = np.argsort(days)                     # 기준일이 앞에 오는 트랙 → 시간순으로
    days, los = days[order], los[:, order]
    yr = days / 365.25
    labels = np.asarray(tr.get("dates"))[order] if tr.get("dates") is not None else None
    if labels is not None and (labels.dtype.kind in "SU" or labels.dtype.kind in "iu"):
        lab = [x.decode() if isinstance(x, bytes) else str(int(x) if labels.dtype.kind in "iu" else x)
               for x in labels]
        t0, t1 = lab[0], lab[-1]
    else:
        t0 = t1 = ""

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    _use_korean_font(plt)
    fig, ax = plt.subplots(2, 2, figsize=(15, 9.2))
    ax = ax.ravel()

    # (a) 교축 좌표계 배치
    axx = ax[0]
    axx.add_patch(Rectangle((0, -half), L, 2 * half, fill=False, ec="#c0392b", ls="--",
                            lw=1.4, zorder=1, label=f"교면 {L:.0f} × {2 * half:.0f} m"))
    axx.plot([0, L], [0, 0], color="#34495e", lw=2, zorder=1)
    axx.scatter(st[~sel], of[~sel], s=14, c="#c5ccd3", marker="x", lw=.8, zorder=2,
                label=f"QC 탈락 {int((~sel).sum())}")
    axx.scatter(st[sel], of[sel], s=40, c="#2980b9", ec="#1b2631", lw=.5, zorder=3,
                label=f"유효 PS {int(sel.sum())}")
    if prof.reference and prof.reference.get("applied"):
        rs = np.asarray(prof.reference["ref_station_m"])
        axx.scatter(rs, np.zeros_like(rs) - half - 4, marker="^", s=60, c="#27ae60", zorder=4,
                    label=f"교대 기준점 {len(rs)}")
    axx.set_xlim(-12, L + 12)
    axx.set_ylim(-45, 45)
    axx.set_xlabel("교축 거리 (체이니지) [m]")
    axx.set_ylabel("교축 직각 [m]")
    axx.set_title(f"(a) 검출 PS {int(sel.sum())}점 — 교축 좌표계 배치\n"
                  f"QC: {prof.meta['selection']} · 노이즈 {prof.meta['n_noise_removed']} 제거", fontsize=10)
    axx.legend(fontsize=7.5, loc="upper right")
    axx.grid(alpha=.25)

    # (b) 잔차고도 — 트랙에 있으면 그린다, 없으면 없다고 적는다
    axx = ax[1]
    rh = tr.get("residual_height_m")
    rs = tr.get("residual_height_sigma_m")
    axx.set_xlim(-12, L + 12)
    if rh is not None and np.isfinite(rh).any():
        import json as _json
        rh, rs = np.asarray(rh, float), np.asarray(rs, float)
        gmeta = _json.loads(str(tr["attrs"].get("residual_height", "{}") or "{}"))
        gt = gmeta.get("group_test") or {}
        on_deck_all = np.abs(of) <= (a.offset if a.offset is not None else half)
        in_span = (st >= -2) & (st <= L + 2)
        on = on_deck_all & in_span
        # 지면 기준선 = 교면 밖 점의 가중평균(기준점 오프셋을 흡수한다)
        base = gt.get("mean_off_m", float(np.nanmedian(rh[~on])) if (~on).any() else 0.0)
        axx.axhline(base, color="#8a7a5c", lw=1.4, label=f"지표 기준선 (교면 밖 평균 {base:+.1f} m)")
        axx.axhline(base + a.height, color="#2471a3", lw=1.2, ls="--",
                    label=f"교면 예상 = 지표 + 형하고 {a.height:g} m")
        axx.errorbar(st[~on & in_span], rh[~on & in_span], yerr=rs[~on & in_span], fmt="o",
                     ms=4, color="#bdc3c7", ecolor="#d5dbdb", elinewidth=.8, capsize=2,
                     zorder=2, label=f"교면 밖 {int((~on & in_span).sum())}")
        axx.errorbar(st[on], rh[on], yerr=rs[on], fmt="o", ms=5, color="#2980b9",
                     ecolor="#5d6d7e", elinewidth=1, capsize=2.5, zorder=3,
                     label=f"교면 위 {int(on.sum())}")
        if gt.get("ok"):
            axx.axhspan(gt["mean_on_m"] - gt["se_on_m"], gt["mean_on_m"] + gt["se_on_m"],
                        color="#2980b9", alpha=.12, zorder=1)
            ttl = (f"(b) DEM 대비 상대고도(잔차고도) — 교면 위 {gt['mean_on_m']:+.1f}±{gt['se_on_m']:.1f} m "
                   f"vs 밖 {gt['mean_off_m']:+.1f}±{gt['se_off_m']:.1f} m\n"
                   f"차이 {gt['diff_m']:+.1f} ± {gt['se_diff_m']:.1f} m (z={gt['z']:.2f}) · "
                   f"점별 σ 중앙 {np.nanmedian(rs):.0f} m · B⊥ {gmeta.get('bperp_min_m', 0):+.0f}~{gmeta.get('bperp_max_m', 0):+.0f} m")
        else:
            ttl = "(b) DEM 대비 상대고도(잔차고도)"
        lo = np.nanpercentile(rh - rs, 5)
        hi = np.nanpercentile(rh + rs, 95)
        axx.set_ylim(min(lo, base - 5), max(hi, base + a.height + 5))
        axx.set_title(ttl, fontsize=9.5)
        axx.legend(fontsize=7, loc="lower right")
    else:
        axx.set_ylim(-25, 5)
        axx.axhline(0, color="#7f8c8d", lw=.8, ls="--")
        axx.axhline(-a.height, color="#8a7a5c", lw=1.2, label=f"지표 기준선 −{a.height:g} m (형하고)")
        axx.text(.5, .55, "잔차고도(DEM 대비 PS 상대고도) — 아직 없음\n\n"
                 "PSI 높이 추정(수직 기선 B⊥)이 있어야 한다.\n"
                 "이 트랙에는 그 값이 없어 '교면 위 산란체' 를 고도로 증명하지 못한다.\n"
                 "지금 근거는 평면 배치(a)와 보도선 정렬뿐이다.",
                 ha="center", va="center", transform=axx.transAxes, fontsize=9.5, color="#7f8c8d",
                 bbox=dict(boxstyle="round", fc="#f8f9f9", ec="#bdc3c7"))
        axx.set_title("(b) DEM 대비 상대고도 종단 — 잔차고도 추정 필요(다음 단계)", fontsize=10)
        axx.legend(fontsize=7.5, loc="lower right")
    axx.set_xlabel("교축 거리 [m]")
    axx.set_ylabel("DEM 대비 상대고도 [m]")
    axx.grid(alpha=.25)

    # (c) LOS 속도 vs 교축, 95% CI
    axx = ax[2]
    axx.axhspan(-REF_BAND_MM_YR, REF_BAND_MM_YR, color="#a9dfbf", alpha=.45, zorder=0,
                label=f"참고범위 ±{REF_BAND_MM_YR:g} mm/yr")
    axx.axhline(0, color="#2c3e50", lw=.9, zorder=1)
    good = np.isfinite(ci)
    m = sel & good
    on_deck = (np.abs(of) <= (a.offset if a.offset is not None else half)) & good & ~sel
    if on_deck.any():                       # 탈락 점도 CI 와 함께 — 왜 탈락했는지 보이게
        axx.errorbar(st[on_deck], v[on_deck], yerr=ci[on_deck], fmt="o", ms=4, color="#bdc3c7",
                     ecolor="#d5dbdb", elinewidth=.8, capsize=2, zorder=2,
                     label=f"QC 탈락(교면 위) {int(on_deck.sum())} — CI 반폭 중앙 "
                           f"{np.nanmedian(ci[on_deck]):.1f} mm/yr")
    zero_in = np.abs(v[m]) <= ci[m]
    axx.errorbar(st[m], v[m], yerr=ci[m], fmt="o", ms=5, color="#2980b9", ecolor="#5d6d7e",
                 elinewidth=1, capsize=2.5, zorder=3,
                 label=f"유효 PS 속도 ± 95% CI  (0 포함 {int(zero_in.sum())}/{int(m.sum())})")
    gb = prof.bin_n >= MIN_BIN_POINTS
    if gb.any():
        axx.errorbar(prof.bin_center_m[gb], prof.bin_value[gb], yerr=prof.bin_ci95[gb],
                     fmt="s-", ms=6, color="#c0392b", lw=1.6, capsize=3, zorder=4,
                     label=f"{prof.bin_m:.0f} m 구간 대표값 ± 95% CI")
    axx.set_xlim(-12, L + 12)
    shown = m | on_deck
    ymax = max(1.0, float(np.nanmax(np.abs(v[shown]) + ci[shown]) * 1.15)) if shown.any() else 1.0
    axx.set_ylim(-ymax, ymax)
    axx.set_xlabel("교축 거리 [m]")
    axx.set_ylabel("LOS 변위속도 [mm/yr]")
    ref_txt = ("교대 기준점 적용" if (prof.reference and prof.reference.get("applied"))
               else "교대 기준점 미적용(양끝 점 없음)")
    axx.set_title(f"(c) LOS 변위속도 — {ref_txt}\n"
                  f"속도 {np.nanmin(v[shown]) if shown.any() else 0:+.2f} ~ "
                  f"{np.nanmax(v[shown]) if shown.any() else 0:+.2f} mm/yr"
                  + ("" if m.any() else "  (유효 PS 없음 — 교면 위 점 참고 표시)"), fontsize=10)
    axx.legend(fontsize=7.5, loc="upper right")
    axx.grid(alpha=.25)

    # (d) 시계열 + 중앙값 + 추세 — 유효 점이 없으면 교면 위 점을 참고로(그렇게 적는다)
    axx = ax[3]
    on_deck_mask = np.abs(of) <= (a.offset if a.offset is not None else half)
    show_d = sel if sel.any() else (on_deck_mask & (st >= -2) & (st <= L + 2))
    d_note = "" if sel.any() else " — QC 미통과, 교면 위 점 참고"
    for i in np.where(show_d)[0]:
        axx.plot(yr, los[i], color="#95a5a6", lw=.6, alpha=.7, zorder=1)
    med = np.nanmedian(los[show_d], axis=0) if show_d.any() else np.zeros_like(yr)
    axx.plot(yr, med, color="#2471a3", lw=2.0, zorder=3, label="중앙값 시계열")
    if show_d.any():
        X = np.column_stack([np.ones(yr.size), yr])
        b, *_ = np.linalg.lstsq(X, med, rcond=None)
        axx.plot(yr, X @ b, color="#c0392b", lw=1.4, ls="--", zorder=4,
                 label=f"추세 {b[1]:+.2f} mm/yr")
    axx.axhline(0, color="#7f8c8d", lw=.8, ls=":")
    axx.set_xlabel(f"경과 [년]  ({t0} ~ {t1})")
    axx.set_ylabel("LOS 변위 [mm]")
    axx.set_title(f"(d) {'유효' if sel.any() else '교면 위'} PS {int(show_d.sum())}점 LOS 시계열(회색) · "
                  f"중앙값(청색) · 추세{d_note}", fontsize=10)
    axx.legend(fontsize=7.5, loc="upper left")
    axx.grid(alpha=.25)

    head = a.meta or f"연장 {L:.0f} m · 폭 {a.width:g} m · 형하고 {a.height:g} m"
    fig.suptitle(f"{a.bridge} — 중소형 교량 InSAR 시계열 변위 분석 (건기연 브리프 형식)\n"
                 f"{head} · 시점 {los.shape[1]} · {t0}~{t1} · 유효 PS {int(sel.sum())}",
                 fontsize=12, y=1.0)
    fig.tight_layout()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=135, bbox_inches="tight", facecolor="white")
    print(f"저장: {out}")
    print(f"  QC {prof.meta['selection']} → 유효 {int(sel.sum())}/{sel.size} · "
          f"기준점 {prof.reference} · 구간 대표값 {int(gb.sum())}/{gb.size}")
    if m.any():
        print(f"  (c) 95%CI 가 0 포함 {int(zero_in.sum())}/{int(m.sum())} → "
              f"{'유의한 변형 경향 없음' if zero_in.mean() > 0.8 else '일부 점 변형 경향'}")


if __name__ == "__main__":
    main()
