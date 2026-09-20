#!/usr/bin/env python3
"""보고서 월별 변위 ↔ 위성 InSAR — **교량별로 나란히** 놓는다.

`make_annual_cycle_compare.py` 는 연주기(계절 성분)만 맞대고, 그것도 월별 표를 눈으로
읽어 낸 3개소(가양·원효·올림픽)뿐이었다. 정작 "보고서에 그려진 그 변위 곡선과 우리
위성 곡선을 한 판에 놓아 달라" 는 요구에는 그림이 없었다. 이 스크립트가 그것을 만든다.

보이는 것(교량 한 칸에 두 줄)
  · 붉은 선 = 보고서에 인쇄된 월별 변위(처짐·신축변위·GNSS 연직변위 — 교량마다 다르다)
  · 파란 선 = 같은 기간 우리 위성 InSAR 의 교면 결합 측점 **중앙값** LOS 시계열
  · 회색 띠 = 보고서가 표로만 준 변동폭(샛강문화다리 — 월별 곡선이 없다)

**축을 따로 쓴다.** 두 값은 같은 양이 아니다 — 보고서는 한 지점의 처짐(또는 신축·GNSS
연직)이고, 위성은 교면 여러 점의 LOS 중앙값이다. 같은 축에 겹쳐 놓고 "얼마나 붙었나"
를 말하면 그건 거짓이다. 여기서 볼 것은 **오르내리는 시점이 같은가**(위상)이다.

    python scripts/make_report_vs_insar.py

산출: docs/img/hangang_보고서_변위_대조.png · docs/bridges/hangang_report_vs_insar.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams

for _f in ("Malgun Gothic", "맑은 고딕", "NanumGothic"):
    if any(_f in f.name for f in font_manager.fontManager.ttflist):
        rcParams["font.family"] = _f
        break
rcParams["axes.unicode_minus"] = False

RED = "#D64545"
BLUE = "#2E6FB7"
GRAY = "#8A9AA8"
GREEN = "#2E9E6B"

# 보고서에 월별 곡선이 실린 교량(사용자가 짚어 준 쪽) — 없는 곳은 왜 없는지 같이 적는다.
WANT = ["가양대교", "원효대교", "올림픽대교", "천호대교", "암사대교", "샛강문화다리"]


def report_monthly(rec: dict) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """월별 표 → {계열이름: (십진연도, 값)}. 천호대교처럼 센서별로 여럿이면 나눠 준다."""
    mon = rec.get("monthly") or {}
    out: dict[str, list[tuple[float, float]]] = {}
    for key, arr in mon.items():
        if not isinstance(arr, list):
            continue
        try:
            yr = int(str(key)[-4:])
        except ValueError:
            continue
        name = "연도별" if str(key)[:4].isdigit() and len(str(key)) == 4 else str(key)
        for m, v in enumerate(arr, start=1):
            if v is None:
                continue
            out.setdefault(name, []).append((yr + (m - 0.5) / 12.0, float(v)))
    res = {}
    for k, pairs in out.items():
        pairs.sort()
        res[k] = (np.asarray([p[0] for p in pairs]),
                  np.asarray([p[1] for p in pairs]))
    return res


def insar_series(folder: Path, lo: float, hi: float
                 ) -> tuple[np.ndarray, np.ndarray] | None:
    """project.h5 의 교면 중앙값 LOS 시계열을 보고서 창으로 잘라 십진연도로."""
    import h5py

    p = folder / "project.h5"
    if not p.exists():
        return None
    with h5py.File(p, "r") as f:
        if "insar" not in f:
            return None
        los = np.asarray(f["insar/los"][()], float)
        lab = [s.decode() if isinstance(s, bytes) else str(s)
               for s in f["insar/date_labels"][()]]
    med = np.median(los, axis=0)
    ts, ys = [], []
    for s, v in zip(lab, med):
        y, m, dd = int(s[:4]), int(s[4:6]), int(s[6:8])
        doy = date(y, m, dd).timetuple().tm_yday
        t = y + (doy - 0.5) / (366.0 if y % 4 == 0 else 365.0)
        # 창을 한 달쯤 넓힌다 — 보고서 창이 몇 달뿐이면(천호 7~11월) 딱 자르는 순간
        # 위성 시점이 한둘로 줄어 아무것도 못 그린다.
        if lo - 0.12 <= t <= hi + 0.12:
            ts.append(t)
            ys.append(float(v))
    if len(ts) < 2:
        return None
    o = np.argsort(ts)
    return np.asarray(ts)[o], np.asarray(ys)[o]


def gnss_band(rec: dict) -> list[dict]:
    """표로만 준 GNSS 변동폭(샛강문화다리) → [{year, min, max, pos}]."""
    out = []
    for tb in rec.get("tables") or []:
        for grp in tb.get("groups") or []:
            pos = grp.get("position") or ""
            for r in grp.get("rows") or []:
                if r.get("min") is None or r.get("max") is None:
                    continue
                out.append({"year": int(r["year"]), "min": float(r["min"]),
                            "max": float(r["max"]), "pos": pos})
    return out


def panel(ax, name: str, rep: dict, folder: Path, gnss: dict) -> dict:
    """교량 한 칸 — 보고서(붉은, 왼쪽 축) · 위성(파란, 오른쪽 축)."""
    info: dict = {"name": name, "page": rep.get("page"),
                  "quantity": rep.get("quantity"), "unit": rep.get("unit"),
                  "status": rep.get("status")}
    series = report_monthly(rep)
    band = gnss_band(gnss) if gnss else []

    lo = hi = None
    if series:
        allt = np.concatenate([t for t, _ in series.values()])
        lo, hi = float(allt.min()), float(allt.max())
    elif band:
        lo, hi = min(b["year"] for b in band), max(b["year"] for b in band) + 1.0

    drew_rep = False
    if series:
        for i, (k, (t, y)) in enumerate(sorted(series.items())):
            ax.plot(t, y, "-o", ms=3.2, lw=1.7, color=RED,
                    alpha=1.0 if i == 0 else 0.55,
                    label=("보고서 " + (rep.get("quantity") or "")
                           + (f" ({k})" if k != "연도별" else "")) if i < 2 else None)
        drew_rep = True
    elif band:
        for b in band:
            ax.add_patch(plt.Rectangle((b["year"] + 0.05, b["min"]), 0.9,
                                       b["max"] - b["min"], color=RED, alpha=0.16))
            ax.plot([b["year"] + 0.05, b["year"] + 0.95], [b["min"]] * 2, lw=1.2,
                    color=RED, alpha=.7)
            ax.plot([b["year"] + 0.05, b["year"] + 0.95], [b["max"]] * 2, lw=1.2,
                    color=RED, alpha=.7)
        drew_rep = True
        info["report_form"] = "연도별 Min/Max 표(월별 곡선 없음)"

    ax.set_ylabel(f"보고서 [{rep.get('unit') or 'mm'}]", color=RED, fontsize=9.5)
    ax.tick_params(axis="y", colors=RED, labelsize=9)
    ax.tick_params(axis="x", labelsize=9)

    a2 = ax.twinx()
    ins = insar_series(folder, lo if lo else 2022.0, hi if hi else 2025.0)
    if ins is not None:
        t, y = ins
        a2.plot(t, y - float(np.median(y)), "-", lw=1.5, color=BLUE, alpha=.85,
                label="위성 InSAR 교면 중앙값 LOS")
        a2.plot(t, y - float(np.median(y)), ".", ms=3.0, color=BLUE, alpha=.6)
        info["insar_n"] = int(len(t))
        info["insar_span"] = [round(float(t.min()), 2), round(float(t.max()), 2)]
        # 시점이 적으면 그 사실을 판 안에 적는다 — 선이 그려졌다고 촘촘한 게 아니다.
        a2.text(0.985, 0.04, f"위성 {len(t)}시점", transform=a2.transAxes,
                ha="right", va="bottom", fontsize=8.6, color=BLUE,
                bbox=dict(fc="white", ec=BLUE, alpha=.65, lw=.8, pad=2.2))
    else:
        info["insar_n"] = 0
        a2.text(0.5, 0.12, "이 창에 위성 시점이 거의 없다", transform=a2.transAxes,
                ha="center", fontsize=9, color=GRAY)
    a2.set_ylabel("위성 LOS [mm]", color=BLUE, fontsize=9.5)
    a2.tick_params(axis="y", colors=BLUE, labelsize=9)

    head = f"{name}"
    sub = f"보고서 p{rep.get('page')} · {rep.get('quantity') or '—'}"
    if rep.get("status") == "read_unreliable":
        sub += " · (!) 판독 신뢰도 낮음"
    elif rep.get("status") == "partial":
        sub += " · 표만 인쇄(월별 곡선 없음)"
    ax.set_title(f"{head}\n{sub}", fontsize=11, pad=7)
    ax.grid(alpha=.22)
    if lo is not None:
        ax.set_xlim(lo - 0.08, hi + 0.08)
    # 십진연도를 그대로 두면 짧은 창에서 '+2.024e3' 오프셋으로 찍혀 못 읽는다.
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    ax.xaxis.set_major_locator(MaxNLocator(6))
    ax.xaxis.set_major_formatter(FuncFormatter(
        lambda v, _: f"{int(v) % 100:02d}-{max(1, min(12, int(round((v % 1) * 12)) + 1)):02d}"))
    if not drew_rep:
        ax.text(0.5, 0.5, rep.get("why") or "보고서에 읽을 곡선이 없다",
                transform=ax.transAxes, ha="center", va="center", fontsize=9,
                color=GRAY, wrap=True)
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--disp", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--gnss", default="docs/bridges/hangang_gnss_2024.json")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/hangang_보고서_변위_대조.png")
    ap.add_argument("--json-out", default="docs/bridges/hangang_report_vs_insar.json")
    a = ap.parse_args()

    disp = json.loads(Path(a.disp).read_text(encoding="utf-8"))
    gn = json.loads(Path(a.gnss).read_text(encoding="utf-8"))

    rows = [n for n in WANT if n in disp]
    n = len(rows)
    ncol = 2
    nrow = (n + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(16.6, 3.5 * nrow))
    axes = np.atleast_1d(axes).ravel()

    out: list[dict] = []
    for ax, name in zip(axes, rows):
        out.append(panel(ax, name, disp[name], Path(a.root) / name, gn.get(name) or {}))
    for ax in axes[n:]:
        ax.axis("off")

    fig.suptitle("보고서에 인쇄된 변위 ↔ 위성 InSAR — 교량별 · 같은 기간\n"
                 "붉은색 = 보고서(왼쪽 축) · 파란색 = 위성 교면 중앙값 LOS(오른쪽 축)",
                 fontsize=14.5, fontweight="bold", y=0.985)
    fig.text(0.012, 0.030,
             "※ 두 값은 같은 양이 아니다 — 보고서는 한 지점의 처짐·신축변위·GNSS 연직변위이고, "
             "위성은 교면 결합 측점의 LOS 중앙값이다. 축을 따로 쓴 이유이고, 여기서 볼 것은 "
             "크기가 아니라 오르내리는 시점이 같은가(위상)다.",
             fontsize=9.4, color="#334155")
    fig.text(0.012, 0.008,
             "   위성 곡선은 중앙값을 빼 0 에 맞췄다. 이 스택은 2024년 취득이 6시점뿐이라 "
             "몇 달짜리 보고서 창(천호대교 7~11월)에서는 위성이 곡선을 이룰 만큼 촘촘하지 않다. "
             "x축은 연-월(예: 23-07).",
             fontsize=9.4, color="#334155")
    fig.subplots_adjust(left=0.052, right=0.948, top=0.90, bottom=0.075,
                        hspace=0.52, wspace=0.30)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=140)
    plt.close(fig)
    print("wrote", a.out)

    Path(a.json_out).write_text(json.dumps(
        {"_설명": "보고서 월별 변위 ↔ 위성 InSAR 교량별 대조",
         "_주의": "보고서와 위성은 같은 양이 아니다(지점 처짐/신축 vs 교면 LOS 중앙값). "
                "크기가 아니라 시점(위상)을 본다.",
         "bridges": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)

    for r in out:
        print(f"  {r['name']:<12} p{r['page']} {str(r['quantity'])[:16]:<18}"
              f"위성 {r.get('insar_n', 0):>3}시점  {r.get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
