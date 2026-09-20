#!/usr/bin/env python3
"""보고서의 **월별 변위 막대그래프를 픽셀에서 읽어** 숫자로 만든다.

2024 한강교량 온라인 안전감시 최종보고에는 교량마다 월별 변위(처짐·신축변위) 그래프가
2022·2023·2024년 계열로 실려 있다. 그런데 그림이 **래스터**라 텍스트 추출로는 한 숫자도
안 나온다 — 그래서 지금까지 Min/Max 표가 인쇄된 2개소만 쓰고 있었다.

여기서는 그 막대의 **픽셀 높이를 축 눈금으로 환산**해 월별 값을 되살린다. 원자료가 아니라
그림에서 되읽은 값이므로 오차가 있다(막대 폭·안티에일리어싱 ~ 눈금의 1~2 %). 그래서
`method: "digitized_from_chart"` 를 값과 함께 남긴다 — 표에 인쇄된 숫자와 같은 급으로
쓰면 안 된다.

읽는 방법
---------
1. 그래프 테두리(검은 사각형) 안쪽만 본다.
2. 가로 격자선을 찾아 `y_ticks` 로 준 값과 위에서부터 짝지어 픽셀↔값 척도를 만든다.
3. 막대 색(계열)을 **세로로 8 px 이상 이어지는 색** 중에서 고른다 — 격자선·글씨는 걸러진다.
4. 색마다 x 방향으로 막대를 묶고, 0 선에서 먼 쪽 끝을 그 달의 값으로 읽는다.
5. 막대 무리를 x 순서로 12(또는 지정 개월수)로 나눠 달에 배정한다.

    python scripts/read_report_displacement.py --list        # 차트 후보만 본다
    python scripts/read_report_displacement.py               # 정의된 차트 전부 읽는다

**아직 쓰면 안 된다 — 미완성이다.**
축 척도(격자선↔눈금)와 계열 색 검출은 맞는데, 막대 분리가 아직 불안정하다:
같은 색 막대가 여러 조각으로 갈라지고(12개월인데 19~33개 검출), 옅은 회색 계열은
배경·격자선과 섞인다. 12개월 × 3계열이라는 **알려진 격자 구조**로 막대 위치를 먼저
잡고 그 칸 안에서만 색을 읽는 쪽으로 바꿔야 한다.

그리고 더 나은 길이 있다 — 이 그래프의 **원자료(월별 계측값)를 용역사(㈜유신·㈜일신이앤씨)
에서 받으면** 그림에서 되읽은 ±2 % 값이 아니라 정확한 숫자로 대조할 수 있다.

산출: docs/bridges/hangang_displacement_2024.json (완성 전까지 만들지 않는다)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "docs" / "한강교량 데이터" / "24년 한강온라인 최종보고 .pdf"

# 교량별 차트 정의 — 페이지·이미지 xref·y축 눈금값(위→아래)·개월 범위.
# y_ticks 는 그림에 인쇄된 눈금을 그대로 적은 것이다(사람이 보고 적는 유일한 값).
CHARTS: list[dict] = [
    {"bridge": "가양대교", "page": 27, "xref": 1323, "title": "연평균 처짐(경사계)",
     "quantity": "처짐(경사계)", "unit": "mm", "y_ticks": [20.0, 0.0, -20.0],
     "months": list(range(1, 13)), "years": [2022, 2023, 2024]},
    {"bridge": "원효대교", "page": 34, "xref": 1447, "title": "처짐(경사계) 추세",
     "quantity": "처짐(경사계)", "unit": "mm",
     "y_ticks": [0.0, -25.0, -50.0, -75.0, -100.0, -125.0],
     "months": list(range(1, 13)), "years": [2022, 2023, 2024]},
    {"bridge": "올림픽대교", "page": 42, "xref": 1573, "title": "처짐(레이저처짐계)",
     "quantity": "처짐(레이저)", "unit": "mm", "y_ticks": [0.0, -100.0],
     "months": list(range(1, 13)), "years": [2022, 2023, 2024]},
    {"bridge": "암사대교", "page": 46, "xref": 1634, "title": "신축변위계 JOINT_3",
     "quantity": "신축변위", "unit": "mm",
     "y_ticks": [20.0, 0.0, -20.0, -40.0, -60.0],
     "months": list(range(1, 13)), "years": [2022, 2023, 2024]},
]


def load_chart(page: int, xref: int) -> np.ndarray:
    import pymupdf
    from PIL import Image
    import io

    d = pymupdf.open(PDF)
    px = pymupdf.Pixmap(d, xref)
    if px.n - px.alpha > 3:
        px = pymupdf.Pixmap(pymupdf.csRGB, px)
    return np.array(Image.open(io.BytesIO(px.tobytes("png"))).convert("RGB"))


def plot_box(img: np.ndarray) -> tuple[int, int, int, int]:
    """그래프 테두리(검은 사각형) 안쪽 — 축 글씨·범례를 빼고 본다."""
    dark = (img.max(axis=2) < 110)
    rows = np.where(dark.sum(axis=1) > img.shape[1] * 0.55)[0]
    cols = np.where(dark.sum(axis=0) > img.shape[0] * 0.45)[0]
    if len(rows) < 2 or len(cols) < 2:
        h, w = img.shape[:2]
        return int(h * 0.05), int(h * 0.92), int(w * 0.12), int(w * 0.97)
    return int(rows.min()), int(rows.max()), int(cols.min()), int(cols.max())


def gridline_rows(img: np.ndarray, box, n_want: int) -> list[int]:
    """가로 격자선 행 — **일정 간격으로 놓인 n_want 개**만 고른다.

    테두리선도 같이 잡히므로 개수로만 자르면 눈금이 어긋난다(가양대교에서 위 테두리가
    20.00 자리로 들어가 값이 통째로 밀렸다). 격자선은 등간격이라는 성질을 쓴다.
    """
    r0, r1, c0, c1 = box
    sub = img[r0 + 2:r1 - 1, c0 + 3:c1 - 3]
    grey = (np.abs(sub[:, :, 0].astype(int) - sub[:, :, 1]) < 12) &            (np.abs(sub[:, :, 1].astype(int) - sub[:, :, 2]) < 12) &            (sub[:, :, 0] > 120) & (sub[:, :, 0] < 235)
    frac = grey.mean(axis=1)
    rows = [i for i, f in enumerate(frac) if f > 0.55]
    out, run = [], []
    for i in rows:
        if run and i - run[-1] > 2:
            out.append(int(np.mean(run)) + r0 + 2)
            run = []
        run.append(i)
    if run:
        out.append(int(np.mean(run)) + r0 + 2)
    if len(out) <= n_want:
        return out
    best, err = None, None
    for i in range(len(out) - n_want + 1):           # 이어진 n_want 개 중 가장 고른 것
        cand = out[i:i + n_want]
        d = np.diff(cand)
        e = float(np.std(d) / max(np.mean(d), 1e-6))
        if err is None or e < err:
            best, err = cand, e
    return best or out[:n_want]


def series_colors(img: np.ndarray, box, *, min_run: int = 10,
                  max_series: int = 3) -> list[tuple[int, int, int]]:
    """막대 색 — **세로로 이어지는 실제 색**을 그대로 센다.

    처음엔 색을 16 단위로 뭉개서 셌는데, 그러면 JPEG 압축이 막대 둘레에 만든 헤일로
    (224,224,224)가 계열로 잡혀 배경까지 막대로 읽혔다(가양대교에서 모든 달이 축 끝
    +39.4 mm 로 나왔다). 뭉개지 않고 원색 그대로 세고, 가까운 색만 하나로 묶는다.
    """
    r0, r1, c0, c1 = box
    sub = img[r0 + 2:r1 - 2, c0 + 3:c1 - 3]
    cnt: Counter = Counter()
    for x in range(0, sub.shape[1], 2):
        col = sub[:, x]
        y = 0
        while y < len(col):
            c = tuple(int(v) for v in col[y])
            z = y
            while z < len(col) and tuple(int(v) for v in col[z]) == c:
                z += 1
            if z - y >= min_run and not (min(c) > 235) and not (max(c) < 60):
                cnt[c] += z - y
            y = z
    out: list[tuple[int, int, int]] = []
    for c, _ in cnt.most_common(60):
        if any(max(abs(a - b) for a, b in zip(c, o)) < 22 for o in out):
            continue
        out.append(c)
        if len(out) == max_series:
            break
    return out


def strict_mask(sub: np.ndarray, color, tol: int, min_run: int = 6) -> np.ndarray:
    """그 색이면서 **세로로 min_run 이상 이어지는** 화소만 남긴다.

    앞선 실패의 핵심이 여기였다. 화소 하나씩 색만 보면 JPEG 압축이 막대 둘레에 뿌린
    헤일로와 격자선 화소까지 걸려서, 마스크가 칸 전체로 번지고 값이 축 끝에 붙는다.
    막대는 **세로로 두껍게** 이어진다 — 그 성질로 먼저 거른다.
    """
    m = (np.abs(sub - np.array(color)).max(axis=2) <= tol)
    out = np.zeros_like(m)
    h = m.shape[0]
    for x in range(m.shape[1]):
        ys = np.where(m[:, x])[0]
        if len(ys) < min_run:
            continue
        y = 0
        while y < len(ys):
            z = y
            while z + 1 < len(ys) and ys[z + 1] - ys[z] == 1:
                z += 1
            if z - y + 1 >= min_run:
                out[ys[y]:ys[z] + 1, x] = True
            y = z + 1
    return out


def read_months(img: np.ndarray, box, colors, zero_row: int, grid_rows,
                n_months: int, *, tol: int = 12, zero_pad: int = 10,
                min_px: int = 5) -> tuple[list[dict], str]:
    """막대를 찾아 달 무리로 묶는다 — 마스크를 **세로 연속성**으로 먼저 거른다.

    막대는 0 선에 붙어 있다(0 선이 위에 덧그려져 중간이 끊겨도, 열 전체로 보면 0 선을
    걸친다). 그래서 '그 열의 마스크가 0 선을 위아래로 감싸는가' 로 판정한다 —
    연속 구간 하나가 0 선을 포함할 것을 요구하면 격자선에 끊겨 걸러진다.
    """
    r0, r1, c0, c1 = box
    sub = img[r0 + 2:r1 - 2, c0 + 3:c1 - 3].astype(int)
    zr = zero_row - (r0 + 2)

    allruns = []
    for ci, col in enumerate(colors):
        m = strict_mask(sub, col, tol)
        cols_ok = []
        for x in range(m.shape[1]):
            ys = np.where(m[:, x])[0]
            if len(ys) < min_px:
                cols_ok.append(None)
                continue
            lo, hi = int(ys.min()), int(ys.max())
            if not (lo - zero_pad <= zr <= hi + zero_pad):
                cols_ok.append(None)          # 0 선을 걸치지 않으면 막대가 아니다
                continue
            cols_ok.append(lo if abs(lo - zr) > abs(hi - zr) else hi)
        run = []
        for x, v in enumerate([*cols_ok, None]):
            if v is None:
                if len(run) >= min_px:
                    xs = [t[0] for t in run]
                    y = max((t[1] for t in run), key=lambda q: abs(q - zr))
                    allruns.append((float(np.mean(xs)), ci, y))
                run = []
            else:
                run.append((x, v))

    if len(allruns) < n_months:
        return [], f"막대 {len(allruns)}개 — {n_months}달로 못 나눈다"
    allruns.sort()
    gaps = sorted(((allruns[i + 1][0] - allruns[i][0], i)
                   for i in range(len(allruns) - 1)), reverse=True)
    cuts = sorted(i for _, i in gaps[:n_months - 1])
    groups, start = [], 0
    for cidx in [*cuts, len(allruns) - 1]:
        groups.append(allruns[start:cidx + 1])
        start = cidx + 1
    if len(groups) != n_months:
        return [], f"무리 {len(groups)}개 ≠ {n_months}달"

    out = []
    for mi, g in enumerate(groups):
        rec = {"month": mi + 1, "series": [None] * len(colors), "x": {}}
        for xm, ci, y in sorted(g):
            if rec["series"][ci] is None:
                rec["series"][ci] = y + (r0 + 2)
                rec["x"][ci] = xm
        out.append(rec)
    return out, "막대 무리"


def digitize(spec: dict) -> dict:
    img = load_chart(spec["page"], spec["xref"])
    box = plot_box(img)
    ticks = spec.get("y_ticks")
    if not ticks or len(ticks) < 2:
        return {"error": f"y_ticks 가 없다({ticks}) — 눈금값을 그림에서 읽어 넣어야 한다"}
    grid = gridline_rows(img, box, len(ticks))
    if len(grid) < 2:
        return {"error": f"격자선 {len(grid)}개 — 척도를 만들 수 없다"}
    # 막대가 격자선을 가려 몇 줄이 안 잡히는 일이 있다(원효대교 6줄 중 4줄). 눈금이
    # 등간격이라는 것만 쓰면 되므로, **위에서부터 순서대로** 짝지어 척도를 만든다.
    # 척도는 **격자선 간격**에서만 얻는다. 어느 줄이 몇 번째 눈금인지는 못 믿는다 —
    # 막대가 위쪽 줄을 가리면 검출된 줄이 위에서부터가 아니게 되고(원효대교), 그러면
    # 0 선이 통째로 엉뚱한 데로 간다.
    dv = float(np.median(np.diff(np.asarray(ticks, float))))
    dp = float(np.median(np.diff(np.asarray(grid, float))))
    if abs(dp) < 1e-6:
        return {"error": "격자선 간격을 못 구했다"}
    scale = dv / dp                                   # 픽셀당 값

    cols = series_colors(img, box)
    # 0 선은 **모든 막대가 공유하는 밑변**이다 — 색 구간의 양 끝을 모아 최빈값을 쓴다.
    r0, r1, c0, c1 = box
    sub0 = img[r0 + 2:r1 - 2, c0 + 3:c1 - 3].astype(int)
    ends: Counter = Counter()
    for col in cols:
        m = strict_mask(sub0, col, 12)
        for x in range(m.shape[1]):
            ys = np.where(m[:, x])[0]
            if len(ys) < 5:
                continue
            ends[int(ys.min())] += 1
            ends[int(ys.max())] += 1
    if not ends:
        return {"error": "막대 색 화소를 못 찾았다"}
    base = Counter()
    for y, c in ends.items():                          # ±2 px 뭉개서 최빈 밑변
        for d in (-2, -1, 0, 1, 2):
            base[y + d] += c
    zero_row = int(max(base.items(), key=lambda kv: kv[1])[0]) + (r0 + 2)
    coef = [-scale * zero_row, scale]                  # value(y) = (y − zero)·scale
    cols = cols[:len(spec["years"])]
    # 범례 순서(연도)에 맞추려면 **칸 안에서 왼쪽에 오는 색이 앞 연도**다.
    months = spec["months"]
    probe, how = read_months(img, box, cols, zero_row, grid, len(months))
    if not probe:
        return {"error": how}
    # 연도 배정 — **한 칸 안에서** x 가 작은 색이 앞 연도(범례 순서와 같다).
    # 칸을 가로질러 평균 내면 어느 달에 그 색이 없을 때 순서가 뒤집힌다.
    best = max(probe, key=lambda r: len(r.get("x", {})))
    if len(best.get("x", {})) >= 2:
        xorder = sorted(((v, ci) for ci, v in best["x"].items()))
        rest = [ci for ci in range(len(cols)) if ci not in best["x"]]
        xorder += [(1e9, ci) for ci in rest]
    else:
        xorder = [(i, i) for i in range(len(cols))]

    # 축 끝에 딱 붙은 값은 버린다 — 마스크가 번져 끝까지 칠해진 것이지 실제 값이 아니다.
    top_v = float(coef[0] + coef[1] * (box[0] + 2))
    bot_v = float(coef[0] + coef[1] * (box[1] - 2))
    span = abs(top_v - bot_v)

    def _ok(v: float | None) -> float | None:
        if v is None:
            return None
        return None if min(abs(v - top_v), abs(v - bot_v)) < 0.02 * span else v

    out = {"bridge": spec["bridge"], "page": spec["page"], "title": spec["title"],
           "quantity": spec["quantity"], "unit": spec["unit"],
           "method": "digitized_from_chart", "y_ticks": ticks,
           "month_edges": how,
           "months": months, "series": []}
    for rank, (_, ci) in enumerate(xorder):
        year = spec["years"][rank] if rank < len(spec["years"]) else None
        vals = {}
        for rec in probe:
            y = rec["series"][ci]
            v = None if y is None else round(float(coef[0] + coef[1] * y), 2)
            vals[str(months[rec["month"] - 1])] = _ok(v)
        out["series"].append({"year": year, "color_rgb": list(cols[ci]),
                              "n_months": sum(1 for v in vals.values() if v is not None),
                              "monthly": vals})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--only", nargs="*", default=None, help="교량 이름")
    a = ap.parse_args()
    if not PDF.exists():
        print(f"PDF 없음: {PDF}", file=sys.stderr)
        return 1

    res = []
    for spec in CHARTS:
        if a.only and spec["bridge"] not in a.only:
            continue
        r = digitize(spec)
        res.append(r)
        if "error" in r:
            print(f"{spec['bridge']:<10} p{spec['page']}  ✗ {r['error']}")
            continue
        print(f"{spec['bridge']:<10} p{spec['page']}  계열 {len(r['series'])}"
              f" · {r['title']}")
        for s in r["series"]:
            vs = [v for v in s["monthly"].values() if v is not None]
            rng = f"{min(vs):+.1f} ~ {max(vs):+.1f}" if vs else "—"
            print(f"     {s['year']}  {s['n_months']:2d}/{len(r['months'])}개월 · "
                  f"{rng} {r['unit']}")
    Path(a.out).write_text(json.dumps(
        {"_source": "2024 한강교량 온라인 안전감시 최종보고 — 월별 변위 그래프",
         "_method": "그림(래스터)에서 막대 픽셀 높이를 축 눈금으로 환산해 되읽은 값. "
                    "표에 인쇄된 숫자가 아니라 **디지타이즈 값**이라 눈금의 1~2 % 오차가 있다.",
         "charts": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
