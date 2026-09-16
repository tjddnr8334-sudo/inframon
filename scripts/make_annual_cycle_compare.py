#!/usr/bin/env python3
"""보고서 월별 변위 ↔ 위성 InSAR — **연주기(진폭·위상)** 를 맞댄다.

왜 연주기인가
-------------
기간을 맞추면(보고서가 덮는 2022~2024) Sentinel-1 은 시점이 19개뿐이라 **장기 추세**를
가리지 못한다 — 신뢰구간이 ±7.8 mm/yr 까지 벌어진다(`make_gnss_insar_trend.py`).
그런데 **연주기는 다르다.** 3년 창에 19시점이면 계절 신호는 넉넉히 잡힌다. 그리고
보고서의 월별 처짐 곡선은 누가 봐도 열 연주기다 — 겨울에 한쪽, 여름에 반대쪽.

그래서 이 그림은 추세 대신 **계절 성분**을 맞댄다. 같은 교량에서

  · 진폭   보고서 처짐의 연주기 진폭 [mm] ↔ 위성 LOS 연주기 진폭을 연직 환산한 값
  · 위상   1년 중 언제가 최대인가 [월] — 부호 규약이 달라도 **위상은 비교된다**

두 계측이 같은 열거동을 보고 있다면 위상이 맞아야 한다. 진폭은 계측 위치(경사계는
한 점, 위성은 교면 전체 중앙값)가 달라 정확히 같을 이유가 없지만, 자릿수는 맞아야 한다.

읽을 때 조심할 것
------------------
· 보고서 값은 **그림을 눈으로 읽은 것**이다(±2~5 mm). 원자료가 아니다.
· 경사계는 한 지점의 처짐, 위성은 교면 결합 측점의 중앙값 LOS 다 — 같은 양이 아니다.
· 부호 규약이 서로 다르다(가양 +가 겨울, 원효 −가 여름). 위상 비교는 180° 뒤집힘을
  허용해서 본다 — 그 사실을 그림에 적는다.

    python scripts/make_annual_cycle_compare.py

산출: docs/img/hangang_연주기_대조.png · docs/bridges/hangang_annual_cycle.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from inframon.insar.chainage import _use_korean_font
from make_hangang_gnss_insar import read_bridge   # noqa: E402

GREEN, ORANGE, RED, BLUE, NAVY, GRAY = (
    "#2E7D32", "#E06C2C", "#C03028", "#1F6FB2", "#123A5E", "#8A8F96")
WIN = ("2022-01-01", "2024-12-31")

# 보고서가 밝힌 센서 위치 — 없는 곳은 비워 둔다(지어내지 않는다).
#   p12  "레이저처짐계(중앙경간) 타겟 프리즘 교체"        → 레이저처짐계는 중앙경간
#   p34  "2022, 23, 24년 처짐(경사계)-MP1~MP2-BT2D"      → 원효대교 경사계 구간 표기
SENSOR_AT = {
    "올림픽대교": {"where": "mid", "label": "레이저처짐계 — 중앙경간(보고서 p12)"},
    "암사대교": {"where": "mid", "label": "레이저처짐계 — 중앙경간(보고서 p12)"},
    "원효대교": {"where": None, "label": "경사계 MP1~MP2(보고서 p34) — 교축 위치는 미표기"},
    "가양대교": {"where": None, "label": "처짐(경사계) — 보고서에 위치 표기 없음"},
}


def fit_annual(t_year: np.ndarray, y: np.ndarray) -> dict:
    """상수 + 선형 + 연주기 최소제곱 → 진폭·최대월·추세.

    y ≈ c0 + c1·t + A·sin(2πt) + B·cos(2πt) = … + R·cos(2π(t − t_max))
    R = hypot(A,B) 가 진폭(half peak-to-peak), t_max 가 1년 중 최대가 되는 시점이다.
    """
    A = np.vstack([np.ones_like(t_year), t_year,
                   np.sin(2 * np.pi * t_year), np.cos(2 * np.pi * t_year)]).T
    c, *_ = np.linalg.lstsq(A, y, rcond=None)
    r = y - A @ c
    dof = max(len(t_year) - 4, 1)
    sig = float(np.sqrt(np.sum(r ** 2) / dof))
    cov = np.linalg.inv(A.T @ A) * sig ** 2
    amp = float(np.hypot(c[2], c[3]))
    # 진폭의 95% CI — sin·cos 계수 오차를 진폭 방향으로 투영
    g = np.array([0.0, 0.0, c[2] / max(amp, 1e-9), c[3] / max(amp, 1e-9)])
    amp_ci = 1.96 * float(np.sqrt(g @ cov @ g))
    phase = math.atan2(float(c[2]), float(c[3])) / (2 * math.pi)   # 0~1 (년 중 위치)
    peak_month = (phase % 1.0) * 12.0
    return {"c": [float(x) for x in c], "amp_mm": amp, "amp_ci_mm": amp_ci,
            "peak_month": float(peak_month), "sigma_mm": sig,
            "trend_mm_yr": float(c[1]),
            "trend_ci_mm_yr": 1.96 * float(np.sqrt(cov[1, 1])), "n": int(len(t_year))}


def model(t: np.ndarray, c) -> np.ndarray:
    return (c[0] + c[1] * t + c[2] * np.sin(2 * np.pi * t)
            + c[3] * np.cos(2 * np.pi * t))


def report_series(rec: dict) -> tuple[np.ndarray, np.ndarray] | None:
    """월별 표 → (십진연도, 값). 없는 달은 뺀다."""
    mon = rec.get("monthly") or {}
    ts, ys = [], []
    for ykey, arr in mon.items():
        try:
            yr = int(ykey[:4])
        except ValueError:
            continue
        for m, v in enumerate(arr, start=1):
            if v is None:
                continue
            ts.append(yr + (m - 0.5) / 12.0)
            ys.append(float(v))
    if len(ts) < 12:
        return None
    o = np.argsort(ts)
    return np.asarray(ts)[o], np.asarray(ys)[o]


def insar_series(d: dict, lo: str, hi: str) -> tuple[np.ndarray, np.ndarray] | None:
    """project.h5 시계열을 보고서 창으로 자르고 십진연도로."""
    if not d:
        return None
    lab = d.get("labels") or []
    med = np.asarray(d["median_mm"], float)
    lo_i, hi_i = lo.replace("-", ""), hi.replace("-", "")
    ts, ys = [], []
    for s, v in zip(lab, med):
        if not (lo_i <= s <= hi_i):
            continue
        y, m, dd = int(s[:4]), int(s[4:6]), int(s[6:8])
        doy = date(y, m, dd).timetuple().tm_yday
        ts.append(y + (doy - 0.5) / (366.0 if y % 4 == 0 else 365.0))
        ys.append(float(v))
    if len(ts) < 8:
        return None
    return np.asarray(ts), np.asarray(ys)


def insar_profile(folder: Path, lo: str, hi: str, *, nbins: int = 6) -> list[dict]:
    """교축 구간별 연주기 — **교면 전체 중앙값은 서로 다른 구간을 상쇄한다**.

    보고서 처짐계는 한 지점(대개 중앙경간)을 본다. 그런데 우리는 교면 결합 측점 **전체**의
    중앙값으로 적합했다. 구간마다 위상이 다르면 중앙값에서 서로 지워진다 — 가양대교에서
    전체 중앙값 진폭이 0.99 mm 인데 구간별로는 6.6~11.8 mm 였다. 그래서 교축을 나눠
    구간마다 따로 적합한다.
    """
    import h5py

    p5 = folder / "project.h5"
    if not p5.exists():
        return []
    with h5py.File(p5, "r") as f:
        los = np.asarray(f["insar/los"][()], float)
        lab = [b.decode() for b in f["insar/date_labels"][()]]
        st = np.asarray(f["insar/deck_station"][()], float)
        inc = float(np.median(np.asarray(f["insar/incidence_deg"][()], float)))
    lo_i, hi_i = lo.replace("-", ""), hi.replace("-", "")
    m = np.array([lo_i <= s <= hi_i for s in lab])
    if m.sum() < 10:
        return []
    t = []
    for s in np.asarray(lab)[m]:
        y, mo, d = int(s[:4]), int(s[4:6]), int(s[6:8])
        doy = date(y, mo, d).timetuple().tm_yday
        t.append(y + (doy - 0.5) / (366.0 if y % 4 == 0 else 365.0))
    t = np.asarray(t)
    L = los[:, m]
    cos_th = math.cos(math.radians(inc))
    edges = np.linspace(float(st.min()), float(st.max()), nbins + 1)
    out = []
    for i in range(nbins):
        sel = (st >= edges[i]) & ((st < edges[i + 1]) if i < nbins - 1
                                  else (st <= edges[i + 1]))
        if sel.sum() < 3:
            out.append({"s0": float(edges[i]), "s1": float(edges[i + 1]),
                        "n": int(sel.sum()), "amp_mm": None, "amp_vert_mm": None,
                        "peak_month": None})
            continue
        r = fit_annual(t, np.median(L[sel], axis=0))
        out.append({"s0": float(edges[i]), "s1": float(edges[i + 1]),
                    "n": int(sel.sum()), "amp_mm": r["amp_mm"],
                    "amp_vert_mm": r["amp_mm"] / cos_th,
                    "peak_month": r["peak_month"]})
    return out


def phase_gap(a: float, b: float) -> tuple[float, bool]:
    """최대월 차이 [월] — 부호 규약이 반대면 6개월 밀린다. 뒤집힘을 허용해 가까운 쪽."""
    d = abs(a - b) % 12.0
    d = min(d, 12.0 - d)
    flip = abs((abs(a - b + 6.0) % 12.0) - 0.0)
    flip = min(flip, 12.0 - flip)
    return (d, False) if d <= flip else (flip, True)


def figure(rows: list[dict], out: Path) -> None:
    n = len(rows)
    fig = plt.figure(figsize=(16.6, 4.9 * n + 1.3))
    gs = fig.add_gridspec(n, 3, left=0.058, right=0.965,
                          top=1 - 0.95 / (4.9 * n + 1.3), bottom=0.5 / (4.9 * n + 1.3),
                          hspace=0.58, wspace=0.30)
    for i, r in enumerate(rows):
        rt, ry = r["report_t"], r["report_y"]
        it, iy = r["insar_t"], r["insar_y"]
        rf, if_ = r["report_fit"], r["insar_fit"]

        a = fig.add_subplot(gs[i, 0])
        for yr, mk in ((2022, "o"), (2023, "s"), (2024, "^")):
            m = (rt >= yr) & (rt < yr + 1)
            if m.any():
                a.plot(rt[m], ry[m], mk + "-", ms=4.5, lw=1.2, label=str(yr))
        tt = np.linspace(rt.min(), rt.max(), 500)
        a.plot(tt, model(tt, rf["c"]), "-", lw=2.4, color=RED,
               label=f"연주기 적합 진폭 {rf['amp_mm']:.1f} mm")
        a.set_title(f"{r['name']} — 보고서 월별 {r['quantity']}\n"
                    f"{r['page']}쪽 · 눈으로 읽은 값(±2~5 mm)", fontsize=11, pad=5)
        a.set_ylabel(f"변위 [{r['unit']}]", fontsize=9.5)
        a.set_xlabel("연도", fontsize=9.5)
        a.legend(fontsize=7.8, framealpha=.92); a.grid(alpha=.22)
        a.tick_params(labelsize=8)

        b = fig.add_subplot(gs[i, 1])
        b.plot(it, iy, "o-", ms=4, lw=0.8, color="#9FB6C8")
        tt2 = np.linspace(it.min(), it.max(), 500)
        b.plot(tt2, model(tt2, if_["c"]), "-", lw=2.4, color=BLUE,
               label=f"연주기 적합 진폭 {if_['amp_mm']:.1f} mm (LOS)")
        b.set_title(f"{r['name']} — 위성 InSAR, 같은 창 {WIN[0][:7]}~{WIN[1][:7]}\n"
                    f"{if_['n']}시점 · 데크 중앙값 LOS", fontsize=11, pad=5)
        b.set_ylabel("LOS 변위 [mm]", fontsize=9.5)
        b.set_xlabel("연도", fontsize=9.5)
        b.legend(fontsize=7.8, framealpha=.92); b.grid(alpha=.22)
        b.tick_params(labelsize=8)

        c = fig.add_subplot(gs[i, 2], projection="polar")
        for lab, pm, amp, col in (("보고서", rf["peak_month"], rf["amp_mm"], RED),
                                  ("위성(연직환산)", if_["peak_month"],
                                   r["insar_amp_vert"], BLUE)):
            th = 2 * math.pi * (pm / 12.0)
            c.plot([th, th], [0, amp], "-", lw=3.2, color=col, label=f"{lab} {amp:.1f} mm")
            c.plot([th], [amp], "o", ms=8, color=col)
        c.set_theta_zero_location("N")
        c.set_theta_direction(-1)
        c.set_xticks([2 * math.pi * k / 12 for k in range(12)])
        c.set_xticklabels([f"{k + 1}M" for k in range(12)], fontsize=7.5)
        c.tick_params(labelsize=7)
        c.set_title(f"연주기 — 최대가 되는 달과 진폭\n"
                    f"위상차 {r['phase_gap']:.1f}개월"
                    + ("  (부호 규약 반대로 보고 뒤집음)" if r["phase_flip"] else ""),
                    fontsize=10.5, pad=16)
        c.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.06),
                 ncol=1, framealpha=.92)

    fig.suptitle("보고서 월별 변위 ↔ 위성 InSAR — 추세 말고 **연주기**를 맞댄다 "
                 f"({WIN[0][:7]} ~ {WIN[1][:7]})",
                 fontsize=14.5, fontweight="bold", y=1 - 0.18 / (4.9 * n + 1.3))
    fig.text(0.008, 0.008,
             "3년 창에서 Sentinel-1 은 장기 추세를 못 가린다(±7.8 mm/yr) — 그러나 계절 "
             "성분은 19시점으로도 잡힌다. 보고서 값은 그림에서 눈으로 읽은 값이고, 경사계는 "
             "한 지점·위성은 교면 중앙값이라 진폭이 같을 이유는 없다. 위상이 맞는지를 본다.",
             fontsize=8.5, color=GRAY)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=135)
    plt.close(fig)
    print("wrote", out)


def figure_compact(rows: list[dict], out: Path) -> None:
    """발표용 — **교축 어디를 보느냐**로 진폭이 달라진다는 것을 보인다.

    보고서 처짐계는 한 지점(레이저처짐계는 중앙경간)을 본다. 우리가 교면 **전체**의
    중앙값으로 적합하면 위상이 다른 구간들이 서로 지워져 진폭이 죽는다 — 가양대교에서
    0.99 mm 였다. 교축을 나눠 구간마다 적합하면 6.6~11.8 mm 로, 보고서 22.6 mm 와
    자릿수가 맞는다. 그 사실을 한 장에 담는다.
    """
    n = len(rows)
    fig = plt.figure(figsize=(5.4 * n + 0.6, 4.9))
    gs = fig.add_gridspec(1, n, left=0.055, right=0.985, top=0.77, bottom=0.155,
                          wspace=0.40)
    for i, r in enumerate(rows):
        ax = fig.add_subplot(gs[0, i])
        prof = [q for q in r["profile"] if q["amp_vert_mm"] is not None]
        xs = [(q["s0"] + q["s1"]) / 2 for q in prof]
        wd = [(q["s1"] - q["s0"]) * 0.82 for q in prof]
        amp = [q["amp_vert_mm"] for q in prof]
        ax.bar(xs, amp, width=wd, color="#9FC3DD", edgecolor="#2C4E89", lw=.7,
               label="위성 구간별 진폭(연직환산)")
        ax.axhline(r["insar_amp_vert"], color=GRAY, ls=":", lw=2.0,
                   label=f"위성 교면 전체 중앙값 {r['insar_amp_vert']:.1f} mm")
        ax.axhline(r["report_fit"]["amp_mm"], color=RED, lw=2.4,
                   label=f"보고서 {r['report_fit']['amp_mm']:.1f} mm")
        for q, x in zip(prof, xs):
            ax.text(x, q["amp_vert_mm"] + max(amp) * 0.035,
                    f"{q['peak_month']:.0f}M", ha="center", fontsize=7.6, color=NAVY)
        sen = SENSOR_AT.get(r["name"], {})
        if sen.get("where") == "mid" and prof:
            mid = (prof[0]["s0"] + prof[-1]["s1"]) / 2
            ax.axvspan(mid - (prof[-1]["s1"] - prof[0]["s0"]) * 0.10,
                       mid + (prof[-1]["s1"] - prof[0]["s0"]) * 0.10,
                       color=GREEN, alpha=.12, zorder=0)
            ax.text(mid, max(amp) * 1.07, "중앙경간", ha="center", fontsize=8.5,
                    color=GREEN, fontweight="bold")
        ax.set_ylim(0, max(max(amp), r["report_fit"]["amp_mm"]) * 1.42)
        ax.set_xlabel("교축 거리 [m]", fontsize=9.5)
        if i == 0:
            ax.set_ylabel("연주기 진폭 [mm]", fontsize=9.5)
        ax.set_title(f"{r['name']}   {sen.get('label', '')}", fontsize=10.5, pad=6)
        ax.legend(fontsize=7.8, framealpha=.93, loc="upper right")
        ax.grid(axis="y", alpha=.22); ax.tick_params(labelsize=8)

    fig.suptitle("보고서 처짐계는 **한 지점**을 본다 — 위성도 그 구간만 보면 진폭이 맞는다  "
                 "(막대 위 숫자 = 그 구간의 최대가 되는 달)",
                 fontsize=13.5, fontweight="bold", y=0.955)
    fig.text(0.006, 0.012,
             "교면 **전체** 중앙값(회색 점선)은 위상이 다른 구간이 서로 지워져 진폭이 죽는다 — "
             "가양대교 0.99 mm. 구간별로 보면 6~12 mm 로 보고서와 자릿수가 맞는다. "
             "보고서가 센서 교축 위치를 밝힌 곳은 레이저처짐계(중앙경간)뿐이라, 나머지는 "
             "구간 프로파일을 그대로 보인다.", fontsize=8.5, color=GRAY)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--disp", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/hangang_연주기_대조.png")
    ap.add_argument("--compact-out", default="docs/img/hangang_연주기_요약.png",
                    help="발표용 가로형 — 1년으로 접어 위상을 겹친다")
    ap.add_argument("--json-out", default="docs/bridges/hangang_annual_cycle.json")
    a = ap.parse_args()
    _use_korean_font(plt)

    disp = json.loads(Path(a.disp).read_text(encoding="utf-8"))
    rows = []
    for name, rec in disp.items():
        if name.startswith("_") or not isinstance(rec, dict):
            continue
        if rec.get("status") != "read":
            continue
        rs = report_series(rec)
        if rs is None:
            print(f"{name}: 월별 값이 12개 미만 — 건너뜀")
            continue
        d = read_bridge(Path(a.root) / name)
        it = insar_series(d, *WIN)
        if it is None:
            print(f"{name}: 창 안 InSAR 시점 부족 — 건너뜀")
            continue
        rt, ry = rs
        itt, iy = it
        rf, if_ = fit_annual(rt, ry), fit_annual(itt, iy)
        cos_th = math.cos(math.radians(d["incidence_deg"]))
        amp_v = if_["amp_mm"] / cos_th
        gap, flip = phase_gap(rf["peak_month"], if_["peak_month"])
        prof = insar_profile(Path(a.root) / name, *WIN)
        rows.append({"name": name, "page": rec["page"], "quantity": rec["quantity"],
                     "unit": rec["unit"], "report_t": rt, "report_y": ry,
                     "insar_t": itt, "insar_y": iy, "report_fit": rf, "insar_fit": if_,
                     "insar_amp_vert": amp_v, "phase_gap": gap, "phase_flip": flip,
                     "profile": prof,
                     "incidence_deg": d["incidence_deg"]})

    if not rows:
        print("대조할 교량이 없다 — hangang_displacement_2024.json 의 status=read 확인",
              file=sys.stderr)
        return 1
    figure(rows, Path(a.out))
    figure_compact(rows, Path(a.compact_out))

    slim = {"_창": list(WIN),
            "_읽는법": "amp_mm=연주기 진폭(반진폭), peak_month=1년 중 최대가 되는 달, "
                    "phase_gap_month=보고서와 위성의 최대월 차이(부호 규약 반대면 뒤집어 본다). "
                    "보고서 값은 그림에서 눈으로 읽은 값(±2~5 mm).",
            "bridges": [{k: v for k, v in r.items()
                         if k not in ("report_t", "report_y", "insar_t", "insar_y")}
                        for r in rows]}
    Path(a.json_out).write_text(json.dumps(slim, ensure_ascii=False, indent=1,
                                           default=float), encoding="utf-8")
    print("wrote", a.json_out)

    print(f"\n{'교량':<9}{'보고서 진폭':>12}{'최대월':>8}   "
          f"{'위성 진폭(LOS)':>14}{'연직환산':>10}{'최대월':>8}   위상차")
    for r in rows:
        rf, if_ = r["report_fit"], r["insar_fit"]
        print(f"{r['name']:<9}{rf['amp_mm']:>10.1f} mm{rf['peak_month']:>7.1f}M   "
              f"{if_['amp_mm']:>11.1f} mm{r['insar_amp_vert']:>8.1f} mm"
              f"{if_['peak_month']:>7.1f}M   {r['phase_gap']:.1f}개월"
              + ("(뒤집음)" if r["phase_flip"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
