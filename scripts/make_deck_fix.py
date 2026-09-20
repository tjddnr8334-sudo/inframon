#!/usr/bin/env python3
"""`docs/bridges` 전 교량을 **교면 전용**으로 다시 뽑는다.

지금까지의 교량별 산출물은 '교량 중심선에서 ±30 m' 로 점을 골랐다. 교량 폭이
11~35 m 인데 ±30 m 는 60 m 폭이라 강변 지반이 통째로 딸려 들어왔고, 그래서 계절
신호가 100~400 m 떨어진 맨땅과 구별되지 않았다(`make_deck_vs_ground.py`).

이 스크립트는 교량마다 다시 고른다.

  ① 지오로케이션 밀림 — 교량은 DEM 보다 높아 점이 δh/tanθ 만큼 밀려 찍힌다. 점별 δh 는
     추정 σ≈26 m 라 못 믿으므로 강체 오프셋 −40~+50 m 를 훑어 **평균 코히런스가 가장
     높은 자리**를 교면으로 본다. GNSS 를 보지 않고 정하는 기준이다.
  ② 교량 중심선 ±폭/2 안의 점만 — 회랑을 실제 데크 폭으로 좁힌다.
  ③ 100~400 m 밖 점들의 시점별 중앙값(지반·대기 공통성분)을 뺀다.
  ④ 직선(속도)·연주기를 달력 시간축으로 맞추고, 점 붓스트랩으로 95% 구간을 낸다.

보고서 계측은 겹쳐 그리지 않는다. 지금 값으로는 대조가 아무것도 주장하지 못해
결과물 전반에서 걷어냈는데(`strip_gnss_from_bridges.py`), 여기서 다시 그리면 이
스크립트를 돌릴 때마다 되살아난다.

    python scripts/make_deck_fix.py              # 전 교량
    python scripts/make_deck_fix.py --only 가양대교 월드컵대교

산출(교량마다): docs/bridges/<교량>/교면전용.json · 교면전용.png
             결과.md 에 '## 교면 전용 재선별' 절을 갱신
전체 요약: docs/bridges/deck_fix_all.json · docs/img/value/교면전용_전교량.png
"""

from __future__ import annotations

import argparse
import json
import re
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
from make_deck_only_gnss import (                                      # noqa: E402
    gnss_series, monthly_cycle, z1,
)
from make_deck_vs_ground import (                                      # noqa: E402
    dist_to_polyline, dmon, peak_month, to_local,
)
from insar_series import dec_year                                      # noqa: E402

use_mpl_style()

HEADING_DEG = -13.3
SECTION = "## 교면 전용 재선별"


def slope_ci(t: np.ndarray, v: np.ndarray) -> tuple[float, float]:
    """직선 기울기와 95% 반폭 — 0 을 품으면 '유의한 추세 없음' 이다."""
    A = np.vstack([np.ones_like(t), t]).T
    c, *_ = np.linalg.lstsq(A, v, rcond=None)
    r = v - A @ c
    s = float(np.sqrt(np.sum(r ** 2) / max(len(t) - 2, 1)))
    return float(c[1]), float(1.96 * s / (np.std(t) * np.sqrt(len(t))))


def pick(folder: Path) -> dict | None:
    """교면 점을 다시 고른다. 못 고르면 None 과 이유를 남긴다."""
    import h5py

    rh, dp, bj = (folder / "track_rh_full.h5", folder / "deck_polyline.json",
                  folder / "bridge.json")
    if not (rh.exists() and dp.exists() and bj.exists()):
        return None
    b = json.loads(bj.read_text(encoding="utf-8"))
    half = max(6.0, float(b.get("width_m") or 20.0) / 2)
    with h5py.File(rh, "r") as h:
        ll = np.asarray(h["pixel_lonlat"][()], float)
        los = np.asarray(h["los_mm"][()], float)
        coh = np.asarray(h["coh"][()], float)
        ep = [s.decode() if isinstance(s, bytes) else str(s)
              for s in h["epochs"][()]]
    # **시점 목록은 시간순이 아니다** — 마스터가 맨 앞에 오고 그 뒤에 나머지가 온다.
    # 그대로 그리면 첫 선분이 마스터(가운데 어딘가)에서 가장 이른 시점으로 거꾸로
    # 그어져, 시작점과 가운데점을 잇는 가짜 선이 생긴다. 최소제곱·조화적합은 순서를
    # 타지 않아 숫자는 멀쩡했지만, 그림은 틀렸다. 읽자마자 세워 둔다.
    order = np.argsort(ep)
    ep = [ep[i] for i in order]
    los = los[:, order]
    poly = np.asarray(json.loads(dp.read_text(encoding="utf-8"))["geometry"], float)
    if poly.shape[0] < 2:
        return None
    lat0 = float(np.mean(ll[:, 1]))
    P, V = to_local(ll, lat0), to_local(poly[:, ::-1], lat0)
    ue, un = los_ground_unit(HEADING_DEG)

    scan = []
    for o in range(-40, 51, 5):
        m = dist_to_polyline(P - np.array([ue, un]) * o, V) <= half
        scan.append((o, int(m.sum()),
                     float(np.mean(coh[m])) if m.sum() else 0.0))
    ok = [s for s in scan if s[1] >= 10]
    if not ok:
        return {"name": folder.name, "skipped": "교면 회랑 안 점 10개 미만",
                "half_width_m": round(half, 1), "scan": scan}
    off, n_deck, cbar = max(ok, key=lambda s: s[2])
    edge = off in (-40, 50)
    near = dist_to_polyline(P - np.array([ue, un]) * off, V) <= half
    d0 = dist_to_polyline(P, V)
    far = (d0 >= 100) & (d0 < 400)
    if far.sum() < 8:
        return {"name": folder.name, "skipped": "지반 기준 점 8개 미만",
                "half_width_m": round(half, 1), "scan": scan}

    t = np.asarray([dec_year(s) for s in ep], float)
    ground = np.median(los[far], axis=0)
    raw = np.mean(los[near], axis=0)
    cor = raw - ground

    vel, vci = slope_ci(t, cor)
    z = z1(t, cor)
    rng = np.random.default_rng(7)
    D = los[near] - ground
    pk, amp = [], []
    for _ in range(400):
        s = rng.integers(0, D.shape[0], D.shape[0])
        zz = z1(t, np.mean(D[s], axis=0))
        pk.append(peak_month(zz))
        amp.append(abs(zz))
    ang = (np.asarray(pk) - 0.5) / 12 * 2 * np.pi
    R = abs(np.mean(np.exp(1j * ang)))
    ci = float(np.sqrt(max(-2 * np.log(max(R, 1e-9)), 0.0)) / (2 * np.pi) * 12 * 1.96)

    return {
        "name": folder.name, "half_width_m": round(half, 1),
        "offset_m": off, "offset_at_scan_edge": bool(edge),
        "coh_mean": round(cbar, 3), "n_deck": int(near.sum()),
        "n_ground": int(far.sum()), "n_epochs": len(ep),
        "span": [ep[0], ep[-1]],
        "velocity_mm_yr": round(vel, 2), "velocity_ci95": round(vci, 2),
        "velocity_significant": bool(abs(vel) > vci),
        "annual_amp_mm": round(float(abs(z)), 2),
        "annual_peak_month": round(peak_month(z), 2),
        "annual_peak_ci95_months": round(ci, 2),
        "annual_amp_ci95_mm": round(float(1.96 * np.std(amp)), 2),
        "scan": scan,
        "_t": t, "_raw": raw, "_cor": cor, "_ground": ground,
        "_P": P, "_V": V, "_near": near, "_far": far, "_coh": coh,
    }


def per_bridge_figure(r: dict, g: list, out: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15.0, 4.4),
                             gridspec_kw={"width_ratios": (1.05, 1.25, 1.0)})
    P, V, near = r["_P"], r["_V"], r["_near"]
    c0 = P.mean(axis=0)

    ax = axes[0]
    ax.scatter(*(P[~near] - c0).T, s=6, color=MPL["rule"], label="주변 점")
    ax.scatter(*(P[near] - c0).T, s=16, color=MPL["blue"],
               edgecolors=MPL["slate"], linewidths=.3, label="교면으로 고른 점")
    ax.plot(*(V - c0).T, color=MPL["ink"], lw=1.6, label="교량 중심선")
    ax.set_aspect("equal")
    ax.set_xlabel("동 [m]", fontsize=9.5)
    ax.set_ylabel("북 [m]", fontsize=9.5)
    ax.legend(fontsize=8.4, loc="best", framealpha=.9)
    ax.grid(alpha=.2)
    ax.set_title(f"교면 {r['n_deck']}점 · 회랑 ±{r['half_width_m']:.0f} m\n"
                 f"밀림 되돌림 {r['offset_m']:+d} m · 코히 {r['coh_mean']:.2f}",
                 fontsize=10, color=MPL["ink"], pad=6)

    ax = axes[1]
    t = r["_t"]
    ax.plot(t, r["_ground"], lw=1.2, color=MPL["rule"], label="지반 공통성분")
    ax.plot(t, r["_raw"], lw=1.2, color=MPL["orange"], alpha=.75,
            label="교면 평균(보정 전)")
    ax.plot(t, r["_cor"], "-o", ms=2.6, lw=1.6, color=MPL["blue"],
            label="교면 평균 − 지반")
    ax.axhline(0, color=MPL["rule"], lw=1)
    ax.set_xlabel("연도", fontsize=9.5)
    ax.set_ylabel("LOS 변위 [mm]", fontsize=9.5)
    ax.legend(fontsize=8.4, loc="best", framealpha=.9)
    ax.grid(alpha=.2)
    sig = "유의" if r["velocity_significant"] else "0 을 품음"
    ax.set_title(f"속도 {r['velocity_mm_yr']:+.2f} ± {r['velocity_ci95']:.2f} mm/년 "
                 f"({sig})", fontsize=10, color=MPL["ink"], pad=6)

    ax = axes[2]
    mo = np.arange(1, 13)
    ins = monthly_cycle(t, r["_cor"])
    a1 = np.nanmax(np.abs(ins)) or 1.0
    ax.plot(mo, ins / a1, "-o", ms=4, lw=2.0, color=MPL["blue"], label="InSAR 교면")
    ttl = (f"연주기 {r['annual_amp_mm']:.1f} mm · 최대 {r['annual_peak_month']:.1f}월 "
           f"(±{r['annual_peak_ci95_months']:.1f})")
    # 계측 곡선은 겹치지 않는다 — 지금 값으로는 대조가 아무것도 주장하지 못해
    # 결과물 전반에서 걷어냈다(strip_gnss_from_bridges.py). 여기서 다시 그리면
    # 이 스크립트를 돌릴 때마다 되살아난다.
    col = MPL["ink"]
    ax.axhline(0, color=MPL["rule"], lw=1)
    ax.set_xticks([1, 4, 7, 10])
    ax.set_xticklabels(["1월", "4월", "7월", "10월"], fontsize=9)
    ax.set_ylim(-1.35, 1.35)
    ax.grid(alpha=.25)
    ax.legend(fontsize=8.4, loc="lower left", framealpha=.9)
    ax.set_title(ttl, fontsize=10, color=col, pad=6)

    fig.suptitle(f"{r['name']} — 교면 전용 재선별",
                 fontsize=14, fontweight="bold", color=MPL["ink"], y=0.985)
    fig.text(0.006, 0.012,
             "※ 회랑을 ±30 m 에서 ±폭/2 로 좁히고, 코히런스로 지오로케이션 밀림을 되돌리고, "
             "100~400 m 밖 지반 공통성분을 뺐다. 연주기는 각자 최대=1 로 맞춰 겹쳤다.",
             fontsize=8.6, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.045, 1, 0.94))
    fig.savefig(out, dpi=150)
    plt.close(fig)


def update_md(folder: Path, r: dict, g: list) -> None:
    md = folder / "결과.md"
    if not md.exists():
        return
    txt = md.read_text(encoding="utf-8")
    lines = [
        SECTION, "",
        "기존 선별은 교량 중심선 **±30 m** 였다 — 교량 폭보다 넓어 강변 지반이 섞였다.",
        "여기서는 회랑을 **±폭/2** 로 좁히고, 코히런스로 지오로케이션 밀림을 되돌리고,",
        "100~400 m 밖 지반 공통성분을 뺐다.", "",
        "| 항목 | 값 |", "|---|---|",
        f"| 교면 회랑 | ±{r['half_width_m']:.0f} m |",
        f"| 밀림 되돌림 | {r['offset_m']:+d} m"
        + ("  ⚠ 훑은 범위의 끝이라 봉우리를 못 찾았다 — 믿을 값이 아니다" if r["offset_at_scan_edge"] else "")
        + " |",
        f"| 교면 점 · 평균 코히런스 | {r['n_deck']}점 · {r['coh_mean']:.3f} |",
        f"| 지반 기준 점 | {r['n_ground']}점 (100~400 m) |",
        f"| 속도(지반 보정 후) | {r['velocity_mm_yr']:+.2f} ± {r['velocity_ci95']:.2f} mm/년"
        f" — {'유의' if r['velocity_significant'] else '95% 구간이 0 을 품는다'} |",
        f"| 연주기 | 진폭 {r['annual_amp_mm']:.1f} ± {r['annual_amp_ci95_mm']:.1f} mm ·"
        f" 최대 {r['annual_peak_month']:.1f}월 ± {r['annual_peak_ci95_months']:.1f} |",
    ]
    # 보고서 GNSS 대조 행은 쓰지 않는다 — 결과물 전반에서 걷어낸 것이라
    # 여기서 다시 쓰면 이 스크립트를 돌릴 때마다 되살아난다.
    lines += ["", "![교면전용](교면전용.png)", ""]
    block = "\n".join(lines)

    pat = re.compile(re.escape(SECTION) + r".*?(?=\n## |\Z)", re.S)
    txt = pat.sub(block, txt) if pat.search(txt) else txt.rstrip() + "\n\n" + block
    md.write_text(txt, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--auto", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--eye", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--summary", default="docs/img/value/교면전용_전교량.png")
    ap.add_argument("--json-out", default="docs/bridges/deck_fix_all.json")
    a = ap.parse_args()

    auto = json.loads(Path(a.auto).read_text(encoding="utf-8"))
    eye = json.loads(Path(a.eye).read_text(encoding="utf-8"))
    rows = []
    for d in sorted(Path(a.root).iterdir()):
        if not d.is_dir() or (a.only and d.name not in a.only):
            continue
        r = pick(d)
        if r is None:
            continue
        if r.get("skipped"):
            print(f"{d.name:<12} 건너뜀 — {r['skipped']}")
            rows.append({k: v for k, v in r.items() if not k.startswith("_")})
            continue
        g = gnss_series(auto, eye, d.name)
        per_bridge_figure(r, g, d / "교면전용.png")
        rec = {k: v for k, v in r.items() if not k.startswith("_")}
        if g:
            zg = np.mean([z1(tt, vv) for _, tt, vv in g])
            rec["gnss_sensors"] = [s for s, _t, _v in g]
            rec["gnss_amp_mm"] = round(float(abs(zg)), 2)
            rec["gnss_peak_month"] = round(peak_month(zg), 2)
            rec["phase_diff_months"] = round(
                dmon(r["annual_peak_month"], peak_month(zg)), 2)
            rec["same_seasonal_behaviour"] = bool(abs(rec["phase_diff_months"]) <= 1.5)
        (d / "교면전용.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        update_md(d, r, g)
        rows.append(rec)
        msg = (f"{d.name:<12} 오프셋 {r['offset_m']:>+4} m · 코히 {r['coh_mean']:.3f} "
               f"· 교면 {r['n_deck']:>4}점 · 속도 {r['velocity_mm_yr']:+6.2f}"
               f"±{r['velocity_ci95']:.2f} · 연주기 {r['annual_amp_mm']:>5.1f} mm "
               f"최대 {r['annual_peak_month']:>4.1f}월")
        if g:
            msg += f" | GNSS {rec['gnss_peak_month']:>4.1f}월 → {rec['phase_diff_months']:+5.1f}개월"
        print(msg + ("  ⚠오프셋 범위끝" if r["offset_at_scan_edge"] else ""))

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "교면 전용 재선별(회랑 ±폭/2 · 코히런스로 밀림 되돌림 · 지반 공통성분 제거)",
         "_주의": "offset_at_scan_edge 가 참이면 코히런스 봉우리를 못 찾은 것 — 그 교량의 "
                "오프셋은 믿을 값이 아니다",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    summary_figure(rows, Path(a.summary))
    return 0


def summary_figure(rows: list, out: Path) -> None:
    rows = [r for r in rows if "annual_peak_month" in r]
    if not rows:
        return
    rows.sort(key=lambda r: r["n_deck"], reverse=True)
    nm = [r["name"] for r in rows]
    y = np.arange(len(nm))
    fig, axes = plt.subplots(1, 3, figsize=(13.6, 0.36 * len(nm) + 2.6),
                             gridspec_kw={"width_ratios": (1.0, 1.15, 1.0)},
                             sharey=True)
    ax = axes[0]
    ax.barh(y, [r["n_deck"] for r in rows], color=MPL["blue"], height=.6,
            edgecolor=MPL["slate"], linewidth=.4)
    ax.set_yticks(y)
    ax.set_yticklabels(nm, fontsize=9.5)
    ax.invert_yaxis()
    ax.set_xlabel("교면 점 수", fontsize=10)
    ax.grid(axis="x", alpha=.25)
    ax.set_title("회랑 ±폭/2 안에 남은 점", fontsize=11, color=MPL["ink"], pad=6)

    ax = axes[1]
    for i, r in enumerate(rows):
        ax.errorbar(r["velocity_mm_yr"], i, xerr=r["velocity_ci95"], fmt="o",
                    ms=5, color=(MPL["red"] if r["velocity_significant"]
                                 else MPL["green"]),
                    ecolor=MPL["slate"], elinewidth=1.1, capsize=3)
    ax.axvline(0, color=MPL["slate"], lw=1.1)
    ax.set_xlabel("지반 보정 후 LOS 속도 [mm/년] · 막대 = 95%", fontsize=10)
    ax.grid(axis="x", alpha=.25)
    ax.set_title("초록 = 95% 구간이 0 을 품는다(유의한 추세 없음)", fontsize=11,
                 color=MPL["ink"], pad=6)

    ax = axes[2]
    got = [(i, r) for i, r in enumerate(rows) if "phase_diff_months" in r]
    for i, r in got:
        c = MPL["green"] if r["same_seasonal_behaviour"] else MPL["red"]
        ax.barh(i, r["phase_diff_months"], color=c, height=.6,
                edgecolor=MPL["slate"], linewidth=.4)
        ax.text(r["phase_diff_months"] + (.2 if r["phase_diff_months"] >= 0 else -.2),
                i, f"{r['phase_diff_months']:+.1f}", va="center", fontsize=9,
                ha="left" if r["phase_diff_months"] >= 0 else "right",
                color=MPL["ink"])
    for s in (-1.5, 1.5):
        ax.axvline(s, color=MPL["red"], lw=1.1, ls="--")
    ax.axvline(0, color=MPL["slate"], lw=1.1)
    ax.set_xlim(-6.5, 6.5)
    ax.set_xlabel("InSAR − GNSS 연주기 위상차 [개월]", fontsize=10)
    ax.grid(axis="x", alpha=.25)
    ax.set_title(f"보고서 GNSS 가 있는 {len(got)}개소만", fontsize=11,
                 color=MPL["ink"], pad=6)

    fig.suptitle("교면 전용 재선별 — 전 교량 요약",
                 fontsize=14.5, fontweight="bold", color=MPL["ink"], y=0.995)
    fig.text(0.006, 0.008,
             "※ 회랑을 교량 폭에 맞춰 좁히고 지반 공통성분을 뺀 결과다. 점이 10개 미만인 "
             "교량은 빠져 있다. 위상차 |1.5개월| 안이면 같은 계절 거동으로 본다.",
             fontsize=8.8, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.035, 1, 0.965))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("wrote", out)


if __name__ == "__main__":
    raise SystemExit(main())
