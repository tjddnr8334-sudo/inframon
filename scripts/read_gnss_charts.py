#!/usr/bin/env python3
"""보고서의 **GNSS 월별 막대 그래프**를 읽어 시계열로 만든다 (22·23·24년 3개 막대 포함).

`read_report_displacement.py` 는 달을 **막대 사이 간격**으로 묶는다. GNSS 그래프는 어느
달에 막대가 하나도 없는 칸이 있어(3M·12M) 그 방식이 깨진다("막대 11개 — 12달로 못
나눈다"). 여기서는 x축 1M~12M 이 **등간격**이라는 것만 쓰고, 막대 무리의 중심 x 로 달을
정한다. 그러면 빈 달이 있어도 배정이 흔들리지 않는다.

읽는 법
  1. 그래프 테두리 안에서 **막대가 없는 열**만 보고 가로 격자선을 찾는다
     (막대가 격자선을 덮어도 눈금 간격을 얻을 수 있다).
  2. 척도는 **격자선 간격**에서만 얻는다(눈금값 차 ÷ 픽셀 간격).
  3. 0 선은 **모든 막대가 공유하는 밑변**이다 — 막대 양 끝의 최빈 행.
  4. 막대마다 색으로 연도를, 중심 x 로 달을 정한다.

값을 지어내지 않는다 — 막대가 없는 달은 None 이다.

    python scripts/read_gnss_charts.py --plot

산출: docs/bridges/hangang_gnss_monthly.json · docs/img/보고서_GNSS_월별.png
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from read_report_displacement import load_chart, plot_box          # noqa: E402

# 보고서에서 눈으로 읽은 **눈금값만** 넣는다. 나머지는 그림에서 잰다.
CHARTS: list[dict] = [
    {"bridge": "샛강문화다리", "page": 50, "xref": 1669, "sensor": "P1_U",
     "pos": "좌측 주탑", "dir": "연직", "years": [2022, 2023, 2024],
     "colors": ["dgray", "green", "orange"],
     "y_ticks": [6, 4, 2, 0, -2, -4, -6, -8]},
    {"bridge": "샛강문화다리", "page": 50, "xref": 1668, "sensor": "1_2_U",
     "pos": "경간 중앙", "dir": "연직", "years": [2022, 2023, 2024],
     "colors": ["dgray", "green", "orange"],
     "y_ticks": [4, 2, 0, -2, -4, -6, -8, -10]},
    {"bridge": "샛강문화다리", "page": 50, "xref": 1670, "sensor": "P2_U",
     "pos": "우측 주탑", "dir": "연직", "years": [2022, 2023, 2024],
     "colors": ["dgray", "green", "orange"],
     "y_ticks": [5, 2, 0, -2, -5, -8, -10]},
    # 가양대교 p27(displacement(mm) · 3개년)은 자동 판독이 무너진다 — 가로로 매우 긴
    # 그림(1800x540)이라 세로 격자선까지 가로선으로 잡혀 척도가 틀어지고, 축 선을 막대로
    # 읽는다. 이 교량은 **이미 눈으로 읽은 값**이 hangang_displacement_2024.json 에
    # 있으므로 그쪽을 쓴다(원효·올림픽·천호·암사도 같다). 억지로 판독하지 않는다.
    # 월드컵대교는 한 해(2024)짜리 단일 계열이다 — 같은 코드로 읽는다.
    {"bridge": "월드컵대교", "page": 29, "xref": 1366, "sensor": "GP_S1M_01_Z",
     "pos": "S1M", "dir": "연직", "years": [2024], "colors": ["orange"],
     "y_ticks": [0, -10, -20, -30, -40, -50, -60]},
    {"bridge": "월드컵대교", "page": 29, "xref": 1368, "sensor": "GP_T1T_01_Z",
     "pos": "T1T(주탑)", "dir": "연직", "years": [2024], "colors": ["orange"],
     "y_ticks": [0, -10, -20, -30, -40]},
    {"bridge": "월드컵대교", "page": 29, "xref": 1367, "sensor": "GP_S2M_01_Z",
     "pos": "S2M", "dir": "연직", "years": [2024], "colors": ["orange"],
     "y_ticks": [4, 2, 0, -2, -4]},
]

YEAR_C = {2022: "#8C8C8C", 2023: "#6AA84F", 2024: "#E8A33D"}


# 막대 색 4종. 보고서 그래프는 이 넷 안에서 쓰인다 — 가양대교 p27 은 2022·2023 이
# **둘 다 회색**(밝은/짙은)이고, 샛강문화다리 p50 은 짙은회색·초록·주황이다.
# 색을 그림에서 자동으로 찾게 해 봤더니 한 해의 막대를 둘로 쪼개거나 서로 섞었다 —
# 어느 색이 어느 해인지는 **그래프마다 적어 두는 편**이 확실하다.
COLOR_CODE = {"lgray": 1, "dgray": 2, "green": 3, "orange": 4}


def color_map(sub: np.ndarray) -> np.ndarray:
    r, g, b = sub[..., 0], sub[..., 1], sub[..., 2]
    hi, lo = sub.max(2), sub.min(2)
    sat = hi - lo
    out = np.zeros(sub.shape[:2], dtype=np.uint8)
    out[(sat < 26) & (r >= 172) & (r <= 216)] = 1          # 밝은 회색
    out[(sat < 26) & (r >= 100) & (r <= 171)] = 2          # 짙은 회색
    out[(sat >= 26) & (g > r + 12) & (g > b + 20)] = 3     # 초록
    out[(sat >= 26) & (r > 180) & (r - b > 60) & (r >= g)] = 4   # 주황
    return out


def _drop_legend(cm: np.ndarray, sub: np.ndarray) -> np.ndarray:
    """범례 **상자**를 한 번 찾아 그 안의 색만 지운다.

    테두리 안의 글씨는 범례뿐이다(축 글씨는 테두리 밖). 그래서 가장자리를 뺀 안쪽의
    검은 화소 덩어리가 범례 글씨이고, 그 왼쪽에 색상자가 붙어 있다.

    열마다 '오른쪽에 글씨가 있으면 지운다' 로 해 봤더니 범례 **아래를 지나는 진짜
    막대**까지 지워졌다(월드컵 7~9M). 범례에 가려진 막대도 상자 아래로는 멀쩡히
    보이므로, 상자 안만 지우고 나머지는 그대로 두는 것이 맞다.
    """
    H, W = cm.shape
    m = 5
    dark = np.zeros((H, W), bool)
    dark[m:H - m, m:W - m] = (
        (sub[m:H - m, m:W - m, 0] < 110) & (sub[m:H - m, m:W - m, 1] < 110)
        & (sub[m:H - m, m:W - m, 2] < 110))
    if dark.sum() < 30:
        return cm
    ys, xs = np.where(dark)
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    if (y1 - y0) > 0.6 * H or (x1 - x0) > 0.9 * W:
        return cm                      # 글씨가 아니라 다른 것 — 건드리지 않는다
    x0 = max(0, x0 - 70)               # 글씨 왼쪽의 색상자까지
    out = cm.copy()
    out[max(0, y0 - 8):min(H, y1 + 9), x0:min(W, x1 + 8)] = 0
    return out


def _col_runs(on: np.ndarray, min_w: int = 3) -> list[tuple[int, int]]:
    """True 가 이어지는 열 구간 목록."""
    out, s = [], None
    for x in range(len(on) + 1):
        live = x < len(on) and bool(on[x])
        if live and s is None:
            s = x
        elif not live and s is not None:
            if x - s >= min_w:
                out.append((s, x - 1))
            s = None
    return out


def _runs_only(cm: np.ndarray, min_run: int) -> np.ndarray:
    """세로로 `min_run` 미만으로 끊기는 것은 막대가 아니다 — 격자선·글씨를 버린다."""
    out = np.zeros_like(cm)
    for x in range(cm.shape[1]):
        col = cm[:, x]
        y = 0
        while y < len(col):
            v = col[y]
            if v == 0:
                y += 1
                continue
            z = y
            while z < len(col) and col[z] == v:
                z += 1
            if z - y >= min_run:
                out[y:z, x] = v
            y = z
    return out


def grid_rows(sub: np.ndarray, bars: np.ndarray) -> list[int]:
    """가로 격자선 — **막대가 없는 열**에서만 찾는다."""
    r, g, b = sub[..., 0], sub[..., 1], sub[..., 2]
    gray = (abs(r - g) < 14) & (abs(g - b) < 14) & (r > 145) & (r < 243)
    nobar = ~(bars.sum(0) > 3)
    if nobar.sum() < 10:
        return []
    cnt = gray[:, nobar].sum(1)
    rows = [i for i in range(len(cnt)) if cnt[i] > 0.55 * nobar.sum()]
    out: list[int] = []
    for y in rows:
        if out and y - out[-1] <= 2:
            continue
        out.append(y)
    return out


def read_chart(spec: dict) -> dict:
    img = load_chart(spec["page"], spec["xref"])
    r0, r1, c0, c1 = plot_box(img)
    sub = img[r0:r1, c0:c1].astype(int)
    cm = color_map(sub)
    cm = _runs_only(cm, 6)          # 세로로 6px 이상 이어지는 것만 막대로 본다
    bars = cm > 0
    if bars.sum() < 50:
        return {**spec, "error": "막대 화소를 못 찾았다"}

    gr = grid_rows(sub, bars)
    if len(gr) < 2:
        return {**spec, "error": f"격자선 {len(gr)}개 — 척도를 만들 수 없다"}
    dv = float(np.median(np.diff(np.asarray(spec["y_ticks"], float))))
    dp = float(np.median(np.diff(np.asarray(gr, float))))
    scale = dv / dp                                   # 픽셀당 값(아래로 갈수록 −)

    ends: Counter = Counter()
    for x in range(bars.shape[1]):
        ys = np.where(bars[:, x])[0]
        if len(ys) < 4:
            continue
        for y in (int(ys.min()), int(ys.max())):
            for d in (-2, -1, 0, 1, 2):
                ends[y + d] += 1
    if not ends:
        return {**spec, "error": "막대 밑변을 못 찾았다"}
    zero = int(max(ends.items(), key=lambda kv: kv[1])[0])
    # **막대는 0 선에서 시작한다.** 0 선에 닿지 않는 세로 구간은 막대가 아니라
    # 범례 색상자다(그림 오른쪽 위에 있다). 구간 전체를 버리면 같은 열에 걸친 진짜
    # 막대까지 사라지므로 **화소 단위로** 지운다.
    cm = _drop_legend(cm, sub)
    bars = cm > 0

    # 달 격자 — 테두리가 1M~12M 을 딱 맞게 감싸지 않아(좌우 여백) 폭/12 로 그냥
    # 나누면 뒤쪽 달이 한 칸씩 밀린다. 실제 막대 위치에 격자를 맞춘다.
    w = bars.shape[1] / 12.0
    cen = [(a0 + b0) / 2.0 for a0, b0 in _col_runs(bars.sum(0) > 2, 3)]
    shift = 0.0
    if cen:
        best = None
        for sh in np.linspace(-w * 0.6, w * 0.6, 49):
            cost = sum(min(abs(c - (sh + (k + 0.5) * w)) for k in range(12))
                       for c in cen)
            if best is None or cost < best[0]:
                best = (cost, float(sh))
        shift = best[1]

    def slot(c: float) -> int:
        return int(max(0, min(11, round((c - shift) / w - 0.5))))

    years, cnames = spec["years"], spec["colors"]
    mo: dict[int, list] = {y: [None] * 12 for y in years}
    nbar = 0
    for yr, cn in zip(years, cnames):
        code = COLOR_CODE[cn]
        m = cm == code
        for a_, b_ in _col_runs(m.sum(0) > 2, 3):
            ys = np.where(m[:, a_:b_ + 1].any(1))[0]
            if not len(ys):
                continue
            y0, y1 = int(ys.min()), int(ys.max())
            far = y0 if abs(y0 - zero) > abs(y1 - zero) else y1
            k = slot((a_ + b_) / 2.0)
            val = round((far - zero) * scale, 2)
            # 한 칸에 같은 색 구간이 둘이면 **긴 쪽**이 진짜 막대다(짧은 쪽은 범례
            # 잔재나 축에 눌린 조각이다).
            cur = mo[yr][k]
            if cur is None or abs(val) > abs(cur):
                mo[yr][k] = val
            nbar += 1

    return {**spec, "monthly": {str(y): v for y, v in mo.items()},
            "_scale": round(scale, 4), "_zero": zero, "_grid": gr, "_nbar": nbar}


def figure(rows: list[dict], out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager, rcParams
    for f in ("Malgun Gothic", "맑은 고딕", "NanumGothic"):
        if any(f in q.name for q in font_manager.fontManager.ttflist):
            rcParams["font.family"] = f
            break
    rcParams["axes.unicode_minus"] = False

    good = [r for r in rows if "monthly" in r]
    n = len(good)
    fig, axes = plt.subplots(n, 1, figsize=(13.6, 2.7 * n))
    axes = np.atleast_1d(axes)
    mons = np.arange(1, 13)
    for ax, r in zip(axes, good):
        ys = r["years"]
        wd = 0.8 / len(ys)
        for j, y in enumerate(ys):
            v = [np.nan if q is None else q for q in r["monthly"][str(y)]]
            ax.bar(mons + (j - (len(ys) - 1) / 2) * wd, v, width=wd * 0.92,
                   color=YEAR_C.get(y, "#777"), label=f"{y}년",
                   edgecolor="white", lw=.5)
        ax.axhline(0, color="#333", lw=1.0)
        ax.grid(axis="y", alpha=.25)
        ax.set_xticks(mons)
        ax.set_xticklabels([f"{m}M" for m in mons])
        ax.set_ylabel("변위 [mm]", fontsize=10)
        ax.set_title(f"{r['bridge']} · {r['sensor']} ({r['pos']} · {r['dir']}) "
                     f"— 보고서 p{r['page']}", fontsize=12, color="#12314F", pad=6)
        ax.legend(fontsize=9, ncol=len(ys), loc="lower left", framealpha=.92)
    fig.suptitle("보고서 GNSS 월별 막대를 읽어 다시 그린 것 — 값은 그래프 판독값이다\n"
                 "막대가 없는 달은 비워 둔다(보고서에도 없다)",
                 fontsize=14.5, fontweight="bold", y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.962))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--json-out", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--fig-out", default="docs/img/보고서_GNSS_월별.png")
    a = ap.parse_args()

    rows = [read_chart(c) for c in CHARTS]
    for r in rows:
        if "error" in r:
            print(f"{r['bridge']} {r['sensor']:<12} 실패 — {r['error']}")
            continue
        print(f"{r['bridge']} {r['sensor']:<12} 격자 {len(r['_grid'])} · "
              f"{r['_scale']:.3f} mm/px · 막대 {r['_nbar']}개")
        for y in r["years"]:
            v = r["monthly"][str(y)]
            print(f"   {y}: " + " ".join("  —  " if q is None else f"{q:6.1f}"
                                         for q in v))
    Path(a.json_out).write_text(json.dumps(
        {"_설명": "보고서 GNSS 월별 막대를 그래프에서 읽은 값(수치표가 아니다)",
         "_읽은법": "격자선 간격으로 척도 · 막대 공유 밑변으로 0 선 · x축 12등분으로 달 "
                 "· 색으로 연도",
         "_주의": "그래프 판독값이다. 표로 인쇄된 Min/Max(hangang_gnss_2024.json)가 "
                "있으면 그쪽이 더 확실하다.",
         "charts": [{k: v for k, v in r.items() if not k.startswith("_")}
                    for r in rows]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", a.json_out)
    if a.plot:
        figure(rows, Path(a.fig_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
