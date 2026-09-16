#!/usr/bin/env python3
"""GNSS **설치 위치** 옆의 PS 점과 비교한다 — 고르지 않고, 미리 정한 자리에서.

지금까지는 점 수십~수백 개 중 **상관이 가장 큰 점**을 골라 R² 를 냈다. 그러면 우연히
잘 맞는 점이 뽑혀 숫자가 부풀고, 우연 기준선을 못 넘는다. 계측기가 어디 붙어 있는지
알면 그 자리 옆 점만 보면 되고, 그게 제대로 된 대조다.

위치는 보고서 **입면도에서 잰다**(지어내지 않는다).
  · 샛강문화다리 p51 — 주탑 2기가 교량 길이의 25% · 75% 지점에 있다(픽셀로 측정).
    센서 P1_U=좌측 주탑 · 1_2_U=경간 중앙(50%) · P2_U=우측 주탑.
  · 월드컵대교 p30 — 센서 3개가 주경간(주탑 P11 주변)에 붙어 있다.

교축 좌표의 **방향**(0 이 어느 쪽 교대인가)은 보고서 그림만으로는 못 정한다. 그래서
양쪽 방향을 다 계산해 **둘 다 적는다** — 한쪽만 골라 내면 그것도 고르기다.

    python scripts/make_gnss_nearest_ps.py

산출: docs/img/qps/GNSS_인접PS_<교량>.png · docs/bridges/gnss_nearest_ps.json
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
from matplotlib import font_manager, rcParams

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_qps_style_figure import PALETTE, scatter_panel                # noqa: E402
from make_trend_agree import load_points, slope_ci                      # noqa: E402

for _f in ("Malgun Gothic", "맑은 고딕", "NanumGothic"):
    if any(_f in f.name for f in font_manager.fontManager.ttflist):
        rcParams["font.family"] = _f
        break
rcParams["axes.unicode_minus"] = False

NAVY = "#12314F"
DIM = "#55636F"
RED = "#C8443C"

# 보고서 입면도에서 **픽셀로 잰** 센서 위치(교량 길이에 대한 비율).
#   샛강 p51(1552x390): 교대 x≈35·1518, 주탑 x≈406·1148 → 0.250 / 0.750
SENSORS: dict[str, list[dict]] = {
    "샛강문화다리": [
        {"sensor": "P1_U", "where": "좌측 주탑(P1)", "frac": 0.250},
        {"sensor": "1_2_U", "where": "경간 중앙", "frac": 0.500},
        {"sensor": "P2_U", "where": "우측 주탑(P2)", "frac": 0.750},
    ],
    # 월드컵대교 p30 — 센서 3개가 모두 주경간(주탑 P11 부근)에 있다. 주경간교는
    # 전체 1492 m 중 855 m 이고 도면이 그 구간만 그린다. 비율은 전체 길이 기준의
    # 대략치이고, 정확한 값은 도면 축척이 없어 확정할 수 없다 — 그렇다고 적는다.
    "월드컵대교": [
        {"sensor": "GP_S1M_01_Z", "where": "주경간 좌측 데크", "frac": 0.44,
         "approx": True},
        {"sensor": "GP_T1T_01_Z", "where": "주탑 정부(P11)", "frac": 0.53,
         "approx": True},
        {"sensor": "GP_S2M_01_Z", "where": "주경간 우측 데크", "frac": 0.58,
         "approx": True},
    ],
}


def gnss_monthly(auto: dict, bridge: str, sensor: str) -> dict[tuple[int, int], float]:
    for c in auto.get("charts", []):
        if c.get("bridge") == bridge and c.get("sensor") == sensor:
            out = {}
            for ykey, arr in (c.get("monthly") or {}).items():
                for i, q in enumerate(arr):
                    if q is not None:
                        out[(int(ykey), i + 1)] = float(q)
            return out
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out-dir", default="docs/img/qps")
    ap.add_argument("--json-out", default="docs/bridges/gnss_nearest_ps.json")
    ap.add_argument("--near-m", type=float, default=60.0,
                    help="센서 위치에서 이 거리 안의 점만 본다")
    a = ap.parse_args()

    auto = json.loads(Path(a.auto).read_text(encoding="utf-8")) \
        if Path(a.auto).exists() else {}
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []

    for bridge, sens in SENSORS.items():
        pts = load_points(Path(a.root) / bridge)
        if pts is None:
            continue
        los, ti, st, mem, inc = pts
        L = float(np.nanmax(st)) if np.isfinite(st).any() else 0.0
        if L <= 0:
            continue

        def ym(t):
            y = np.floor(t).astype(int)
            return list(zip(y, np.clip(((t - y) * 12).astype(int) + 1, 1, 12)))

        ki = ym(ti)
        rows, panels = [], []
        for s in sens:
            g = gnss_monthly(auto, bridge, s["sensor"])
            common = sorted(set(g) & set(ki))
            rec = {"bridge": bridge, "sensor": s["sensor"], "where": s["where"],
                   "frac": s["frac"], "approx": bool(s.get("approx")),
                   "deck_length_m": round(L, 1), "n_months": len(common)}
            # 방향을 못 정하므로 양쪽 다 본다.
            for tag, pos in (("정방향", s["frac"] * L), ("역방향", (1 - s["frac"]) * L)):
                d = np.abs(st - pos)
                near = np.where(np.isfinite(d) & (d <= a.near_m))[0]
                item = {"station_m": round(float(pos), 1),
                        "n_near": int(len(near)),
                        "nearest_dist_m": (None if not np.isfinite(d).any()
                                           else round(float(np.nanmin(d)), 1))}
                if len(near) >= 1 and len(common) >= 4:
                    idx = {c: [i for i, k in enumerate(ki) if k == c] for c in common}
                    X = np.vstack([los[near][:, idx[c]].mean(axis=1)
                                   for c in common]).T
                    y = np.asarray([g[c] for c in common], float)
                    m = X.mean(axis=0)                     # 인접 점 평균
                    if np.std(m) > 1e-9 and np.std(y) > 1e-9:
                        r = float(np.corrcoef(m, y)[0, 1])
                        item.update({"r": round(r, 3), "r2": round(r * r, 3)})
                        b1, c1 = slope_ci(np.asarray([c[0] + (c[1] - .5) / 12
                                                      for c in common]), m[None, :])
                        b2, c2 = slope_ci(np.asarray([c[0] + (c[1] - .5) / 12
                                                      for c in common]), y[None, :])
                        item.update({
                            "insar_slope": round(float(b1[0]), 2),
                            "insar_ci": round(float(c1[0]), 2),
                            "gnss_slope": round(float(b2[0]), 2),
                            "gnss_ci": round(float(c2[0]), 2)})
                        if tag == "정방향":
                            panels.append((s, m, y, item))
                rec[tag] = item
            rows.append(rec)
        report.extend(rows)

        ok = [p for p in panels if "r2" in p[3]]
        if ok:
            fig, axes = plt.subplots(1, len(ok), figsize=(4.3 * len(ok), 4.9))
            axes = np.atleast_1d(axes)
            for k, (ax, (s, m, y, item)) in enumerate(zip(axes, ok)):
                scatter_panel(ax, y, m, PALETTE[k % len(PALETTE)], s["sensor"], fs=11)
                ax.set_xlabel("GNSS 연직변위 [mm]", fontsize=10)
                if k == 0:
                    ax.set_ylabel("InSAR Displacement [mm]", fontsize=11)
                ax.set_title(f"{s['where']} · 교축 {item['station_m']:.0f} m\n"
                             f"인접 점 {item['n_near']}개(≤{a.near_m:.0f} m) · "
                             f"짝 {item.get('n_months', len(y))}개월",
                             fontsize=9.5, color=NAVY, pad=6)
            fig.suptitle(f"{bridge} — GNSS **설치 위치 옆** PS 점과 대조 "
                         f"(고르지 않았다)".replace("**", ""),
                         fontsize=15, fontweight="bold", y=0.985)
            fig.text(0.006, 0.006,
                     "※ 센서 위치는 보고서 입면도에서 픽셀로 쟀다. 교축 0 이 어느 교대인지는 "
                     "도면만으로 못 정해 양쪽 방향을 다 계산했고(JSON), 여기 그린 것은 "
                     "정방향이다. 인접 점들의 **평균**을 썼다 — 한 점을 고르면 그것도 고르기다.",
                     fontsize=9, color=DIM)
            fig.tight_layout(rect=(0, 0.02, 1, 0.94))
            p = out / f"GNSS_인접PS_{bridge}.png"
            fig.savefig(p, dpi=150)
            plt.close(fig)
            print("wrote", p)

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "GNSS 설치 위치 옆 PS 점과의 대조(점을 고르지 않음)",
         "_위치출처": "보고서 입면도에서 픽셀로 측정 — 샛강 p51 주탑 25%·75%, "
                  "월드컵 p30 주경간(축척 없어 대략치)",
         "_방향": "교축 0 이 어느 교대인지 도면으로 못 정해 정방향·역방향 둘 다 적는다",
         "sensors": report}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)

    print(f"\n{'교량':<12}{'센서':<14}{'위치':<14}{'교축[m]':>8}{'인접':>5}"
          f"{'최근접[m]':>10}{'짝':>4}{'R²':>7}")
    for r in report:
        for tag in ("정방향", "역방향"):
            it = r.get(tag) or {}
            print(f"{r['bridge']:<12}{r['sensor']:<14}{(r['where'] + ' ' + tag)[:13]:<14}"
                  f"{it.get('station_m', float('nan')):>8.0f}{it.get('n_near', 0):>5}"
                  f"{str(it.get('nearest_dist_m')):>10}{r['n_months']:>4}"
                  f"{it.get('r2', float('nan')):>7.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
