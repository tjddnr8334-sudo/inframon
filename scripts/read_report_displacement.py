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
     "quantity": "처짐(경사계)", "unit": "mm", "y_ticks": None,
     "months": list(range(1, 13)), "years": [2022, 2023, 2024]},
    {"bridge": "청담대교", "page": 40, "xref": 1522, "title": "처짐(V-leg 경사변위)",
     "quantity": "처짐(경사변위)", "unit": "mm", "y_ticks": None,
     "months": list(range(1, 13)), "years": [2022, 2023, 2024]},
    {"bridge": "올림픽대교", "page": 42, "xref": 1573, "title": "처짐(레이저처짐계)",
     "quantity": "처짐(레이저)", "unit": "mm", "y_ticks": [0.0, -100.0],
     "months": list(range(1, 13)), "years": [2022, 2023, 2024]},
    {"bridge": "암사대교", "page": 46, "xref": 1633, "title": "레이저처짐계",
     "quantity": "처짐(레이저)", "unit": "mm", "y_ticks": [400.0, 200.0, 0.0, -200.0],
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


def _bar_runs(cell_mask: np.ndarray, zr: int, zero_pad: int, min_px: int):
    """열별로 0 선에 붙은 세로 구간을 찾아 **막대 덩어리**로 묶는다."""
    far = []
    for x in range(cell_mask.shape[1]):
        ys = np.where(cell_mask[:, x])[0]
        hit = None
        y = 0
        while y < len(ys):
            z = y
            while z + 1 < len(ys) and ys[z + 1] - ys[z] <= 4:
                z += 1
            lo, hi = int(ys[y]), int(ys[z])
            if lo - zero_pad <= zr <= hi + zero_pad:
                hit = lo if abs(lo - zr) > abs(hi - zr) else hi
                break
            y = z + 1
        far.append(hit)
    runs, cur = [], []
    for x, v in enumerate(far):
        if v is None:
            if len(cur) >= min_px:
                runs.append(cur)
            cur = []
        else:
            cur.append((x, v))
    if len(cur) >= min_px:
        runs.append(cur)
    return runs


def read_months(img: np.ndarray, box, colors, zero_row: int, grid_rows,
                n_months: int, *, tol: int = 10, zero_pad: int = 8,
                min_px: int = 4) -> tuple[list[dict], str]:
    """**막대를 먼저 찾고 12무리로 묶는다** — 칸을 x 로 등분하지 않는다.

    등분하면 달이 밀린다(막대 그래프는 축 양끝에 여백이 있다). 세로 격자선을 쓰려 해도
    막대에 가려 안 잡힌다. 그래서 막대 자체를 쓴다 — 전체 막대를 x 순으로 늘어놓고
    **가장 큰 틈 n_months−1 개**로 자르면 그것이 달 경계다. 무리 안에서는 x 가 작은
    색이 앞 연도다(범례 순서).
    """
    r0, r1, c0, c1 = box
    sub = img[r0 + 2:r1 - 2, c0 + 3:c1 - 3].astype(int)
    # 격자선을 지우지 않는다 — 지우면 막대가 그 줄에서 끊겨 값이 눈금에 붙어 버린다
    # (가양대교에서 모든 큰 값이 +19.5 mm 로 잘렸다). 색이 달라 어차피 안 섞이고,
    # 세로 구간을 이을 때 7 px 까지 건너뛰어 격자선 두께를 넘어간다.
    zr = zero_row - (r0 + 2)

    per_color = {}
    allruns = []
    for ci, col in enumerate(colors):
        m = (np.abs(sub - np.array(col)).max(axis=2) <= tol)
        rs = _bar_runs(m, zr, zero_pad, min_px)
        per_color[ci] = rs
        for r in rs:
            allruns.append((float(np.mean([p[0] for p in r])), ci, r))
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
        for xm, ci, r in sorted(g):
            y = max((p[1] for p in r), key=lambda t: abs(t - zr))
            if rec["series"][ci] is None:
                rec["series"][ci] = y
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
    if len(grid) != len(ticks):
        return {"error": f"격자선 {len(grid)}개 ≠ 눈금 {len(ticks)}개"}
    A = np.vstack([np.ones(len(grid)), np.asarray(grid, float)]).T
    coef, *_ = np.linalg.lstsq(A, np.asarray(ticks, float), rcond=None)
    zero_row = int(round((0.0 - coef[0]) / coef[1]))

    cols = series_colors(img, box)
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
            vals[str(months[rec["month"] - 1])] = (
                None if y is None else round(float(coef[0] + coef[1] * (y + box[0] + 2)), 2))
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
