#!/usr/bin/env python3
"""GNSS 실측 ↔ 위성 InSAR — 추세선을 같은 방식으로 뽑아 나란히 놓는다.

한강교량 온라인 안전감시 보고서는 가양·월드컵·서강·샛강문화다리에 **GNSS 변위** 계측이
있고, 전부 '거동상태 양호 · 관리기준 이내'로 판정한다. 위성이 같은 기간을 보고 다른 말을
하면 둘 중 하나가 틀린 것이다.

그런데 추세는 **어떻게 뽑느냐가 결론을 바꾼다.** 1년치 GNSS 에 직선만 맞추면 계절(열)
주기가 통째로 기울기로 새어 나온다 — 실측 GNSS(인천대교 사장교, 시간별 1년)로 그것을
보인다: 직선만 −32~−141 mm/yr, 연주기를 같이 넣으면 −1.8~+12.8 mm/yr. 우리가 InSAR
에서 간섭도별 상수를 빼기 전/후에 겪은 것과 같은 종류의 함정이다.

  (a) GNSS 실측 6채널 — 직선만 vs 연주기 포함
  (b) 위성 InSAR — 한강 교량별 LOS 변위속도 ± 95% CI (GNSS 설치 교량 강조)

    python scripts/make_gnss_compare.py

산출: docs/img/gnss_insar_compare.png
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from inframon.insar.chainage import _use_korean_font

GREEN, ORANGE, RED, BLUE, GRAY = "#2E7D32", "#E06C2C", "#C03028", "#1F6FB2", "#8A8F96"
GNSS_DIR = (r"E:/D드라이브/data file/과제 진행중/건기연/2025/GNSS/"
            r"20250627-사장교 GNSS 수직변위")


def _fit(x, y, *, annual: bool):
    """선형(+연주기) 최소제곱 → (기울기, 95% CI 반폭, 잔차σ, 연주기 진폭)."""
    cols = [np.ones_like(x), x]
    if annual:
        cols += [np.sin(2 * np.pi * x), np.cos(2 * np.pi * x)]
    A = np.vstack(cols).T
    c, *_ = np.linalg.lstsq(A, y, rcond=None)
    r = y - A @ c
    sig = float(np.sqrt(np.sum(r ** 2) / max(len(x) - A.shape[1], 1)))
    se = sig * float(np.sqrt(np.linalg.inv(A.T @ A)[1, 1]))
    amp = float(np.hypot(c[2], c[3])) if annual else 0.0
    return float(c[1]), 1.96 * se, sig, amp


def read_gnss(folder: Path) -> list[dict]:
    """GNSS 수직변위 엑셀들 → 채널별 추세(직선만·연주기 포함)."""
    import openpyxl
    out = []
    for f in sorted(folder.glob("*.xlsx")):
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        ts, vs = [], []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] is None or row[1] is None:
                continue
            t = row[0] if isinstance(row[0], datetime) else datetime.fromisoformat(str(row[0]))
            try:
                vs.append(float(row[1]))
            except (TypeError, ValueError):
                continue
            ts.append(t)
        wb.close()
        if len(ts) < 100:
            continue
        x = np.array([(t - ts[0]).total_seconds() / 86400 / 365.25 for t in ts])
        y = np.array(vs)
        lin = _fit(x, y, annual=False)
        ann = _fit(x, y, annual=True)
        out.append({"name": f.stem.replace(" 수직변위", ""), "n": len(x),
                    "span": (ts[0].strftime("%Y-%m-%d"), ts[-1].strftime("%Y-%m-%d")),
                    "lin_v": lin[0], "lin_ci": lin[1],
                    "ann_v": ann[0], "ann_ci": ann[1], "sigma": ann[2], "amp": ann[3]})
    return out


def insar_rows(root: Path, names: list[str]) -> dict:
    import h5py
    out = {}
    for nm in names:
        p5 = root / nm / "project.h5"
        if not p5.exists():
            continue
        with h5py.File(p5, "r") as f:
            if "insar/los" not in f:
                continue
            los = np.asarray(f["insar/los"][()], float)
            t = np.asarray(f["insar/dates"][()], float) / 365.25
        A = np.vstack([np.ones_like(t), t]).T
        c, *_ = np.linalg.lstsq(A, los.T, rcond=None)
        r = los.T - A @ c
        sig = np.sqrt(np.sum(r ** 2, axis=0) / max(len(t) - 2, 1))
        ci = 1.96 * sig / (np.std(t) * np.sqrt(len(t)))
        out[nm] = {"v": float(np.median(c[1])), "ci": float(np.median(ci)),
                   "n_points": int(los.shape[0]), "n_epochs": int(los.shape[1])}
    return out


def figure(gnss: list[dict], ins: dict, gnss_bridges: set, out_png: Path) -> None:
    nrow = max(len(gnss), len(ins))
    # 슬라이드 한 장을 꽉 채우려면 가로로 길어야 한다 — 세로로 길면 폭이 줄어 글씨가 안 보인다.
    fig = plt.figure(figsize=(17.0, max(6.0, 0.30 * nrow + 2.6)))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.12, 1.0], wspace=0.30)

    # ── (a) GNSS 실측 ──
    a = fig.add_subplot(gs[0])
    y = np.arange(len(gnss))[::-1]
    for yi, g in zip(y, gnss):
        a.errorbar(g["lin_v"], yi + 0.16, xerr=g["lin_ci"], fmt="s", ms=5,
                   color=GRAY, ecolor=GRAY, elinewidth=1.5, capsize=3, zorder=3)
        c = GREEN if abs(g["ann_v"]) < g["ann_ci"] else ORANGE
        a.errorbar(g["ann_v"], yi - 0.16, xerr=g["ann_ci"], fmt="o", ms=6,
                   color=c, ecolor=c, elinewidth=2.0, capsize=3.5, zorder=4)
    a.axvline(0, color="k", lw=1.1)
    a.set_yticks(y); a.set_yticklabels([g["name"] for g in gnss], fontsize=9.5)
    a.set_xlabel("연직 변위속도 [mm/yr] · 오차막대 = 95% 신뢰구간", fontsize=10)
    sp = gnss[0]["span"] if gnss else ("", "")
    a.set_title(f"(a) GNSS 실측 (사장교 · 시간별 {sp[0]}~{sp[1]})\n"
                "같은 데이터를 두 가지로 맞춰 보면 결론이 뒤집힌다", fontsize=11.5, pad=8)
    a.grid(axis="x", alpha=.25)
    a.legend(handles=[Patch(color=GRAY, label="직선만 맞춤 — 계절 주기가 기울기로 샌다"),
                      Patch(color=GREEN, label="연주기 포함 — 신뢰구간이 0 을 포함"),
                      Patch(color=ORANGE, label="연주기 포함 — 그래도 0 을 벗어남")],
             fontsize=8.5, loc="lower left", framealpha=.92)

    # ── (b) InSAR ──
    b = fig.add_subplot(gs[1])
    names = list(ins)
    y2 = np.arange(len(names))[::-1]
    for yi, nm in zip(y2, names):
        d = ins[nm]
        c = GREEN if abs(d["v"]) < d["ci"] else ORANGE
        b.errorbar(d["v"], yi, xerr=d["ci"], fmt="o", ms=6, color=c, ecolor=c,
                   elinewidth=2.0, capsize=3.5, zorder=3)
        nm + ("  ◆GNSS" if nm in gnss_bridges else "")
        b.text(0, yi + 0.34, f"  {d['n_points']}점 · {d['n_epochs']}시점",
               fontsize=7.5, color=GRAY, va="center")
    b.axvline(0, color="k", lw=1.1)
    b.axvspan(-0.5, 0.5, color=GREEN, alpha=.08)
    b.set_yticks(y2)
    b.set_yticklabels([nm + ("  ◆" if nm in gnss_bridges else "") for nm in names], fontsize=9.5)
    b.set_xlabel("LOS 변위속도 [mm/yr] · 오차막대 = 95% 신뢰구간", fontsize=10)
    b.set_title("(b) 위성 InSAR — 한강 교량 (◆ = 보고서상 GNSS 계측 설치 교량)\n"
                "연직로 환산하면 ÷cos θ = ÷0.78 (입사각 39°)", fontsize=11.5, pad=8)
    b.grid(axis="x", alpha=.25)

    fig.suptitle("GNSS 실측 ↔ 위성 InSAR — 추세를 같은 방식으로 뽑으면 같은 결론에 온다",
                 fontsize=14, fontweight="bold", y=0.985)
    fig.subplots_adjust(left=0.105, right=0.99, top=0.80, bottom=0.105)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=140); plt.close(fig)
    print("wrote", out_png)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gnss-dir", default=GNSS_DIR)
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--bridges", default="docs/bridges/hangang16.json")
    ap.add_argument("--shm", default="docs/bridges/hangang_shm_2024.json")
    ap.add_argument("--out", default="docs/img/gnss_insar_compare.png")
    ap.add_argument("--json-out", default="docs/bridges/gnss_compare.json")
    a = ap.parse_args()
    _use_korean_font(plt)

    gnss = read_gnss(Path(a.gnss_dir))
    shm = {k: v for k, v in json.loads(Path(a.shm).read_text(encoding="utf-8")).items()
           if not k.startswith("_")}
    gnss_bridges = {k for k, v in shm.items()
                    if any("GNSS" in it for it, _ in (v.get("items") or []))}
    geo = json.loads(Path(a.bridges).read_text(encoding="utf-8"))
    order = [b["name"] for b in sorted(geo, key=lambda b: b["lon"])]
    ins = insar_rows(Path(a.root), order)

    Path(a.json_out).write_text(json.dumps(
        {"gnss": gnss, "insar": ins, "gnss_bridges": sorted(gnss_bridges)},
        ensure_ascii=False, indent=1), encoding="utf-8")
    figure(gnss, ins, gnss_bridges, Path(a.out))

    print(f"\nGNSS 채널 {len(gnss)}개 — 연주기 포함 추세")
    for g in gnss:
        mark = "0 포함" if abs(g["ann_v"]) < g["ann_ci"] else "0 벗어남"
        print(f"  {g['name']:<22} 직선만 {g['lin_v']:+8.1f} → 연주기포함 "
              f"{g['ann_v']:+7.2f} ± {g['ann_ci']:.2f} mm/yr ({mark})")
    print(f"\nInSAR {len(ins)}개 교량 · GNSS 설치 교량: {', '.join(sorted(gnss_bridges))}")
    for nm, d in ins.items():
        mark = "0 포함" if abs(d["v"]) < d["ci"] else "0 벗어남"
        star = " ◆GNSS" if nm in gnss_bridges else ""
        print(f"  {nm:<8}{star:<7} {d['v']:+6.2f} ± {d['ci']:.2f} mm/yr ({mark})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
