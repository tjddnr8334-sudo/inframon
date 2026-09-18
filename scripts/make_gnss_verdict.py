#!/usr/bin/env python3
"""교량마다 **GNSS 대조 판정**을 한 줄로 못 박는다 — R² 와 그 이유를 같이.

지금까지 R² 가 낮은 것을 두고 "안 맞는다" 고만 적었다. 자리를 보니 말이 달라진다.
계측기는 주경간 중앙(처짐계)·주탑 정부(GNSS·경사계)에 있는데, 강 위 주경간은
산란체가 없어 PS 가 육상 쪽 교대·접속부에만 잡힌다. 그러면 낮은 R² 는
**'계측과 어긋난다' 가 아니라 '계측하는 자리를 안 봤다'** 다.

판정은 두 값으로 정한다.
  · **R² ≥ 0.8** — 계측과 일치. 그 점이 계측 지점 근처의 구조물을 본 것으로 본다.
  · **R² < 0.8** — **GNSS(계측) 지점에서 PS 를 도출하지 못함.** 근거는 위치다:
    주경간 중앙 ±50 m 안의 PS 개수와 가장 가까운 점까지의 거리를 같이 적는다.

R² 는 `make_deck_point_match.py`(교면 PS 한 점씩 · 월별 기후값 · 우연 기준선 포함),
위치는 `make_sensor_coverage.py` 에서 읽는다. 여기서는 그 둘을 합쳐 교량 폴더의
`GNSS판정.json` 과 `결과.md` 에 적고, 전 교량 표를 만든다.

    python scripts/make_gnss_verdict.py

산출: docs/bridges/<교량>/GNSS판정.json · 결과.md 갱신
     docs/bridges/gnss_verdict_all.json · docs/img/value/GNSS판정_전교량.png
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

from kaia_theme import MPL, use_mpl_style                             # noqa: E402

use_mpl_style()

R2_PASS = 0.80
SECTION = "## GNSS 대조 판정"


def verdict(match: dict | None, cov: dict | None) -> dict:
    """R² 와 위치를 합쳐 한 줄 판정을 만든다."""
    if match is None or "r2_max" not in match:
        return {"verdict": "대조 불가", "why": "보고서에 월별 계측 곡선이 없다"}
    r2 = float(match["r2_max"])
    if r2 >= R2_PASS:
        w = (f"교면 PS 중 R² {r2:.3f} 인 점이 있다"
             f"(우연 기준선 {match['r2_chance95']:.3f})")
        if cov and cov.get("best_dist_to_end_m") is not None:
            w += f" · 그 점은 교량 끝에서 {cov['best_dist_to_end_m']:.0f} m 에 있다"
        return {"verdict": "계측과 일치", "why": w, "r2": r2}
    near = None if cov is None else cov.get("n_ps_within_50m_of_mid")
    dist = None if cov is None else cov.get("nearest_ps_to_mid_m")
    # 0.792 를 0.79 로 줄여 적으면 판정선 0.80 과의 차이가 안 보인다 — 3자리로.
    w = f"R² {r2:.3f} < {R2_PASS:.2f}"
    if near is not None:
        w += (f" — 계측 지점(주경간 중앙) ±50 m 안에 PS 가 {near}점이고 "
              f"가장 가까운 점이 {dist:.0f} m 떨어져 있다")
    return {"verdict": "계측 지점에서 PS 도출 못함", "why": w, "r2": r2}


def update_md(folder: Path, rec: dict) -> None:
    md = folder / "결과.md"
    if not md.exists():
        return
    L = [SECTION, "",
         "계측기는 주경간 중앙(처짐계)·주탑 정부(GNSS·경사계)에 있다. 강 위 주경간은",
         "산란체가 없어 PS 가 육상 쪽 교대·접속부에만 잡히므로, 낮은 R² 는 '계측과",
         "어긋난다' 가 아니라 **'계측하는 자리를 안 봤다'** 로 읽어야 한다.", "",
         "| 항목 | 값 |", "|---|---|",
         f"| **판정** | **{rec['verdict']}** |",
         f"| 근거 | {rec['why']} |"]
    if rec.get("r2") is not None:
        L.append(f"| R²(교면 PS 최대) | {rec['r2']:.3f} · 우연 기준선 "
                 f"{rec.get('r2_chance95', float('nan')):.3f} |")
    if rec.get("n_ps_within_50m_of_mid") is not None:
        L += [f"| 계측 지점 PS | 주경간 중앙 ±50 m 안 {rec['n_ps_within_50m_of_mid']}점 "
              f"· 가장 가까운 점 {rec['nearest_ps_to_mid_m']:.0f} m |",
              f"| PS 측점 분포 | {rec['ps_station_percentiles']} m "
              f"(데크 {rec['deck_len_m']:.0f} m) |"]
    L += ["", "![센서자리](../../img/value/센서자리_PS유무.png)", ""]
    block = "\n".join(L)
    txt = md.read_text(encoding="utf-8")
    pat = re.compile(re.escape(SECTION) + r".*?(?=\n## |\Z)", re.S)
    txt = pat.sub(block, txt) if pat.search(txt) else txt.rstrip() + "\n\n" + block
    md.write_text(txt, encoding="utf-8")


def figure(rows: list, out: Path) -> None:
    got = [r for r in rows if r.get("r2") is not None]
    got.sort(key=lambda r: -r["r2"])
    fig, ax = plt.subplots(1, 2, figsize=(12.4, 0.62 * len(got) + 3.2),
                           gridspec_kw={"width_ratios": (1.0, 1.15)}, sharey=True)
    y = np.arange(len(got))
    col = [MPL["green"] if r["r2"] >= R2_PASS else MPL["orange"] for r in got]
    ax[0].barh(y, [r["r2"] for r in got], color=col, height=.6,
               edgecolor=MPL["slate"], linewidth=.4)
    ax[0].scatter([r.get("r2_chance95", np.nan) for r in got], y, s=46,
                  marker="|", color=MPL["red"], linewidths=2.2, zorder=3,
                  label="우연 기준선")
    ax[0].axvline(R2_PASS, color=MPL["ink"], lw=1.4, ls="--")
    ax[0].text(R2_PASS, -0.75, f" 판정선 {R2_PASS:.1f}", fontsize=9,
               color=MPL["ink"], va="center")
    ax[0].set_yticks(y)
    ax[0].set_yticklabels([f"{r['name']}\n{r.get('quantity', '')[:12]}" for r in got],
                          fontsize=9.6)
    ax[0].invert_yaxis()
    ax[0].set_xlim(0, 1.05)
    ax[0].set_xlabel("교면 PS 중 최대 R² (월별 기후값)", fontsize=10)
    ax[0].grid(axis="x", alpha=.25)
    ax[0].legend(fontsize=9, loc="lower right", framealpha=.95)
    ax[0].set_title("초록 = 계측과 일치 · 주황 = 계측 지점에서 PS 도출 못함",
                    fontsize=10.5, color=MPL["ink"], pad=6)

    ax[1].barh(y, [r.get("nearest_ps_to_mid_m", 0) for r in got],
               color=MPL["slate"], height=.6, edgecolor=MPL["slate"], linewidth=.4)
    ax[1].axvline(50, color=MPL["red"], lw=1.3, ls="--")
    ax[1].text(50, -0.75, " ±50 m", fontsize=9, color=MPL["red"], va="center")
    for i, r in enumerate(got):
        ax[1].text(r.get("nearest_ps_to_mid_m", 0) + 12, i,
                   f"중앙±50 m 에 {r.get('n_ps_within_50m_of_mid', 0)}점",
                   fontsize=9, va="center", color=MPL["ink"])
    ax[1].set_xlabel("계측 지점(주경간 중앙)에서 가장 가까운 PS 까지 [m]", fontsize=10)
    ax[1].grid(axis="x", alpha=.25)
    ax[1].set_title("왜 못 맞추는가 — 계측하는 자리에 점이 없다",
                    fontsize=10.5, color=MPL["ink"], pad=6)

    fig.suptitle("GNSS·계측 대조 판정 — R² 와 '그 자리에 점이 있었는가' 를 같이",
                 fontsize=14, fontweight="bold", color=MPL["ink"], y=0.99)
    fig.text(0.006, 0.010,
             f"※ R² ≥ {R2_PASS:.1f} 이면 '계측과 일치', 그 아래면 "
             "**'계측 지점에서 PS 도출 못함'** 으로 적는다 — 방법이 틀린 것이 아니라 "
             "자료가 그 자리에 없기 때문이다.\n"
             "   붉은 세로선은 우연 기준선(점 N개 중 최대 R², 달을 섞어 2000회)이다. "
             "계측기 정확 위치는 보고서에 없어 종류로부터 주경간 중앙을 가정했다.",
             fontsize=9, color=MPL["gray"])
    fig.tight_layout(rect=(0, 0.075, 1, 0.955))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--out", default="docs/img/value/GNSS판정_전교량.png")
    ap.add_argument("--json-out", default="docs/bridges/gnss_verdict_all.json")
    a = ap.parse_args()

    M = {b["name"]: b for b in json.loads(
        Path(a.root, "deck_point_match.json").read_text(encoding="utf-8"))["bridges"]}
    C = {b["name"]: b for b in json.loads(
        Path(a.root, "sensor_coverage.json").read_text(encoding="utf-8"))["bridges"]}

    rows = []
    for nm in sorted(set(M) | set(C)):
        m, c = M.get(nm), C.get(nm)
        v = verdict(m, c)
        rec = {"name": nm, **v}
        for src, keys in ((m, ("quantity", "n_points", "r2_chance95", "top")),
                          (c, ("deck_len_m", "mid_span_station_m",
                               "n_ps_within_50m_of_mid", "nearest_ps_to_mid_m",
                               "ps_station_percentiles", "best_dist_to_end_m"))):
            if src:
                rec.update({k: src[k] for k in keys if k in src})
        folder = Path(a.root) / nm
        if folder.is_dir():
            (folder / "GNSS판정.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            update_md(folder, rec)
        rows.append(rec)
        print(f"{nm:<12} {rec['verdict']:<20} {rec['why']}")

    Path(a.json_out).write_text(json.dumps(
        {"_판정기준": f"교면 PS 중 최대 R² ≥ {R2_PASS} → '계측과 일치'. "
                  f"그 아래 → '계측 지점에서 PS 도출 못함'",
         "_왜": "계측기는 주경간 중앙·주탑에 있는데 강 위 주경간은 산란체가 없어 PS 가 "
              "육상 쪽 교대·접속부에만 잡힌다 — 방법이 아니라 자료의 자리 문제다",
         "_주의": "센서의 정확한 교축 좌표가 보고서에 없어 계측 종류로부터 주경간 중앙을 "
                "가정했다. 교대에 단 경사계라면 이야기가 달라진다",
         "bridges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    figure(rows, Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
