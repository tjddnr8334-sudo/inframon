#!/usr/bin/env python3
"""현장 계측(SHM) ↔ 위성 InSAR 대조 그림 — 추세가 서로 맞는가.

한강교량 온라인 안전감시시스템 2024 최종보고는 16개 교량 전부를 '관리기준 이내 ·
추세 양호'로 판정했다. 위성이 같은 기간을 보고 다른 말을 하면 둘 중 하나가 틀린
것이다. 그래서 **같은 축 위에 놓고** 본다.

  (a) 숲그림(forest plot) — 교량별 LOS 변위속도 ± 95% 신뢰구간. 구간이 0 선을 가로지르면
      '유의한 거동 없음'이고, 그것이 SHM 판정과 같은 말이다. 서→동 순으로 늘어놓아
      한강 전체의 공간 패턴도 같이 보인다.
  (b) 계측항목 격자 — 교량마다 무엇을 재고 있고(O) 무엇이 비어 있는지(X·△).
      위성 InSAR 가 채울 자리가 어디인지가 이 격자에서 바로 읽힌다.

    python scripts/make_shm_compare.py --shm docs/bridges/hangang_shm_2024.json \
        --bridges docs/bridges/hangang16.json --root docs/bridges

산출: docs/img/hangang_shm_compare.png
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

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from inframon.insar.chainage import _use_korean_font

GREEN, ORANGE, RED, BLUE, GRAY = "#2E7D32", "#E06C2C", "#C03028", "#1F6FB2", "#8A8F96"
MARK = {"O": ("O", GREEN), "△": ("△", ORANGE), "X": ("X", RED)}


def insar_velocity(folder: Path) -> dict | None:
    """교량 폴더 → 교면 측점 LOS 속도의 중앙값·95% CI·0 포함 비율.

    속도 하나만 보면 안 된다 — 신뢰구간이 0 을 품는지가 '추세가 있다/없다'의 판단이다.
    """
    p5 = folder / "project.h5"
    if not p5.exists():
        return None
    with h5py.File(p5, "r") as f:
        if "insar/los" not in f or "insar/dates" not in f:
            return None
        los = np.asarray(f["insar/los"][()], float)
        t = np.asarray(f["insar/dates"][()], float) / 365.25
        lab = ([s.decode() if isinstance(s, bytes) else str(s)
                for s in f["insar/date_labels"][()]] if "insar/date_labels" in f else [])
    if los.shape[1] < 5:
        return None
    A = np.vstack([np.ones_like(t), t]).T
    coef, *_ = np.linalg.lstsq(A, los.T, rcond=None)
    resid = los.T - A @ coef
    sig = np.sqrt(np.sum(resid ** 2, axis=0) / max(len(t) - 2, 1))
    ci = 1.96 * sig / (np.std(t) * np.sqrt(len(t)))
    v = coef[1]
    return {"n_points": int(los.shape[0]), "n_epochs": int(los.shape[1]),
            "v_med": float(np.median(v)), "ci_med": float(np.median(ci)),
            "frac_zero": float(np.mean(np.abs(v) < ci)),
            "noise_mm": float(np.median(sig)),
            "span": (lab[0], lab[-1]) if lab else None}


def figure(order: list[str], shm: dict, ins: dict, out_png: Path) -> None:
    n = len(order)
    fig = plt.figure(figsize=(15.6, 0.46 * n + 3.0))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.45, 1.0], wspace=0.03)

    # ── (a) 숲그림 ──
    a = fig.add_subplot(gs[0])
    y = np.arange(n)[::-1]
    ytop = n + 1.9                       # (b) 열 제목 자리 — 두 축이 같은 범위를 써야 행이 맞는다
    notes: list[tuple[float, str]] = []
    for yi, nm in zip(y, order):
        d = ins.get(nm)
        if not d:
            notes.append((yi, "미처리"))
            continue
        crosses = abs(d["v_med"]) < d["ci_med"]
        c = GREEN if crosses else ORANGE
        a.errorbar(d["v_med"], yi, xerr=d["ci_med"], fmt="o", ms=5.5, color=c,
                   ecolor=c, elinewidth=1.8, capsize=3.5, zorder=3)
        notes.append((yi, (f"{d['v_med']:+.2f} ± {d['ci_med']:.2f} mm/yr"
                          f"   {d['n_points']}점 · {d['n_epochs']}시점")))
    # 주석 글자가 축 밖으로 잘리지 않게 오른쪽을 넉넉히 — 값이 안 보이면 그림이 아니다.
    fin = [d for d in ins.values() if d]
    if fin:
        lo = min(d["v_med"] - d["ci_med"] for d in fin)
        hi = max(d["v_med"] + d["ci_med"] for d in fin)
        rng = max(hi - lo, 2.0)
        a.set_xlim(lo - 0.08 * rng, hi + 0.50 * rng)
        for yi, txt in notes:                # 숫자는 오른쪽 한 열로 모아 읽기 쉽게
            a.text(hi + 0.06 * rng, yi, txt, va="center", fontsize=7.8,
                   color=(GRAY if txt == "미처리" else "#10263D"))
    a.axvline(0, color="k", lw=1.1, zorder=1)
    a.axvspan(-0.5, 0.5, color=GREEN, alpha=0.08, zorder=0)
    a.set_yticks(y); a.set_yticklabels(order, fontsize=9.5)
    a.set_xlabel("LOS 변위속도 [mm/yr] · 오차막대 = 95% 신뢰구간", fontsize=10)
    a.set_title("(a) 위성 InSAR — 신뢰구간이 0 선을 지나면 '유의한 거동 없음'",
                fontsize=11.5, pad=8)
    a.grid(axis="x", alpha=.25)
    a.set_ylim(-0.8, ytop)
    a.legend(handles=[Patch(color=GREEN, label="95% CI 가 0 을 포함 — 추세 없음"),
                      Patch(color=ORANGE, label="0 을 벗어남 — 확인 필요")],
             fontsize=8.5, loc="lower right", framealpha=.9)

    # ── (b) 계측항목 격자 ──
    b = fig.add_subplot(gs[1])
    cols: list[str] = []
    for nm in order:
        for it, _ in shm.get(nm, {}).get("items", []):
            key = it.split("(")[0]
            if key not in cols:
                cols.append(key)
    for j, cname in enumerate(cols):
        b.text(j - 0.15, n - 0.35, cname, rotation=55, ha="left", va="bottom", fontsize=8)
    for yi, nm in zip(y, order):
        got = {it.split("(")[0]: st for it, st in shm.get(nm, {}).get("items", [])}
        for j, cname in enumerate(cols):
            st = got.get(cname)
            if st is None:
                b.add_patch(plt.Rectangle((j - .42, yi - .38), .84, .76,
                                          fc="#F2F4F6", ec="none", zorder=1))
                continue
            txt, col = MARK[st]
            b.add_patch(plt.Rectangle((j - .42, yi - .38), .84, .76,
                                      fc=col, alpha=.16, ec=col, lw=.7, zorder=1))
            b.text(j, yi, txt, ha="center", va="center", fontsize=9,
                   fontweight="bold", color=col, zorder=2)
    b.set_xlim(-0.6, len(cols) - 0.4); b.set_ylim(-0.8, ytop)
    b.set_yticks([]); b.set_xticks([])
    for sp in b.spines.values():
        sp.set_visible(False)
    b.set_title("(b) 현장 계측항목 — O 설치 · △ 대체(경사계) · X 미설치 · 빈칸 = 감시항목 아님",
                fontsize=11.5, pad=40)

    fig.suptitle("한강교량 온라인 안전감시(2024 최종보고) ↔ 위성 InSAR — 같은 기간, 같은 축 위에서",
                 fontsize=14, fontweight="bold", y=0.99)
    fig.subplots_adjust(left=0.075, right=0.995, top=0.86, bottom=0.09)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=140); plt.close(fig)
    print("wrote", out_png)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shm", default="docs/bridges/hangang_shm_2024.json")
    ap.add_argument("--bridges", default="docs/bridges/hangang16.json")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/hangang_shm_compare.png")
    ap.add_argument("--json-out", default="docs/bridges/hangang_compare.json")
    a = ap.parse_args()

    _use_korean_font(plt)
    shm = {k: v for k, v in json.loads(Path(a.shm).read_text(encoding="utf-8")).items()
           if not k.startswith("_")}
    geo = json.loads(Path(a.bridges).read_text(encoding="utf-8"))
    order = [b["name"] for b in sorted(geo, key=lambda b: b["lon"])]        # 서 → 동
    order += [k for k in shm if k not in order]                            # 좌표 없는 것은 끝에

    ins, rows = {}, []
    for nm in order:
        d = insar_velocity(Path(a.root) / nm)
        if d:
            ins[nm] = d
        h = shm.get(nm, {})
        rows.append({"name": nm, "shm_verdict": h.get("verdict"), "shm_trend": h.get("trend"),
                     "shm_items": h.get("items"), "insar": d,
                     "agree": (None if not d else bool(abs(d["v_med"]) < d["ci_med"]))})
    Path(a.json_out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    figure(order, shm, ins, Path(a.out))

    done = [r for r in rows if r["insar"]]
    agree = [r for r in done if r["agree"]]
    print(f"처리 {len(done)}/{len(rows)}개 · 신뢰구간이 0 을 포함(추세 없음) {len(agree)}개")
    for r in done:
        d = r["insar"]
        print(f"  {r['name']:<8} {d['v_med']:+6.2f} ± {d['ci_med']:.2f} mm/yr "
              f"· 0포함 {100 * d['frac_zero']:3.0f}% · {'일치' if r['agree'] else '확인 필요'}"
              f"  | SHM {r['shm_verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
