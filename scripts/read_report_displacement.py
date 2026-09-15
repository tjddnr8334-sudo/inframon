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


def series_colors(img: np.ndarray, box, *, min_run: int = 8,
                  max_series: int = 3) -> list[tuple[int, int, int]]:
    """막대 색 — **세로로 이어지는** 색만 센다(격자선·글씨는 이어지지 않는다)."""
    r0, r1, c0, c1 = box
    sub = img[r0 + 2:r1 - 2, c0 + 3:c1 - 3]
    q = (sub // 16 * 16).astype(np.uint8)            # 안티에일리어싱 뭉개기
    cnt: Counter = Counter()
    for x in range(0, q.shape[1], 2):
        col = q[:, x]
        y = 0
        while y < len(col):
            c = tuple(int(v) for v in col[y])
            z = y
            while z < len(col) and tuple(int(v) for v in col[z]) == c:
                z += 1
            if z - y >= min_run and not (c[0] > 235 and c[1] > 235 and c[2] > 235) \
                    and not (c[0] < 60 and c[1] < 60 and c[2] < 60):
                cnt[c] += z - y
            y = z
    out = []
    for c, _ in cnt.most_common(40):
        if any(max(abs(a - b) for a, b in zip(c, o)) < 40 for o in out):
            continue                                  # 비슷한 색은 한 계열
        out.append(c)
        if len(out) == max_series:
            break
    return out


def bars(img: np.ndarray, box, color, zero_row: int, *, tol: int = 12,
         min_w: int = 6, zero_pad: int = 7) -> list[tuple[float, int]]:
    """그 색의 막대들 — (중심 x, 0 선에서 가장 먼 y).

    **막대는 0 선에 붙어 있다.** 그 성질로 범례 색상칩·글씨·눈금 표시를 걸러낸다 —
    그것들은 0 선과 떨어져 떠 있다.
    """
    r0, r1, c0, c1 = box
    sub = img[r0 + 2:r1 - 2, c0 + 3:c1 - 3].astype(int)
    m = (np.abs(sub - np.array(color)).max(axis=2) <= tol)
    # 가로 격자선은 폭의 대부분을 덮는다 — 막대 색과 비슷해도 막대가 아니다.
    m[m.mean(axis=1) > 0.6, :] = False
    zr = zero_row - (r0 + 2)
    far: list[int | None] = []
    for x in range(m.shape[1]):
        ys = np.where(m[:, x])[0]
        if len(ys) == 0:
            far.append(None)
            continue
        # 0 선을 포함하는 연속 구간만 막대로 본다(0 선 ±3 px 허용)
        hit = None
        y = 0
        while y < len(ys):
            z = y
            while z + 1 < len(ys) and ys[z + 1] - ys[z] <= 2:
                z += 1
            lo, hi = ys[y], ys[z]
            if lo - zero_pad <= zr <= hi + zero_pad:
                hit = lo if abs(lo - zr) > abs(hi - zr) else hi
                break
            y = z + 1
        far.append((hit + r0 + 2) if hit is not None else None)

    out, run = [], []
    gap = 0
    for x, v in enumerate(far):
        if v is None:
            gap += 1
            if gap > 2 and run:
                if run[-1][0] - run[0][0] + 1 >= min_w:
                    xs = [p[0] for p in run]
                    y = max((p[1] for p in run), key=lambda t: abs(t - zero_row))
                    out.append((float(np.mean(xs)) + c0 + 3, y))
                run = []
        else:
            gap = 0
            run.append((x, v))
    if run and run[-1][0] - run[0][0] + 1 >= min_w:
        xs = [p[0] for p in run]
        y = max((p[1] for p in run), key=lambda t: abs(t - zero_row))
        out.append((float(np.mean(xs)) + c0 + 3, y))
    return out


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
    def val(y: float) -> float:
        return float(coef[0] + coef[1] * y)
    zero_row = int(round((0.0 - coef[0]) / coef[1])) if coef[1] else box[1]

    cols = series_colors(img, box)
    out: dict = {"bridge": spec["bridge"], "page": spec["page"],
                 "title": spec["title"], "quantity": spec["quantity"],
                 "unit": spec["unit"], "method": "digitized_from_chart",
                 "y_ticks": ticks, "n_series_found": len(cols), "series": []}
    groups = []
    for c in cols:
        bs = bars(img, box, c, zero_row)
        if len(bs) >= 4:
            groups.append((c, bs))
    if not groups:
        return {**out, "error": "막대를 찾지 못했다"}

    # 계열을 연도에 배정 — 같은 달 안에서 x 가 작은 쪽이 앞 연도(범례 순서와 같다)
    order = sorted(range(len(groups)),
                   key=lambda i: float(np.mean([b[0] for b in groups[i][1]][:3])))
    months = spec["months"]
    for rank, gi in enumerate(order):
        c, bs = groups[gi]
        bs = sorted(bs, key=lambda t: t[0])
        year = spec["years"][rank] if rank < len(spec["years"]) else None
        # x 를 개월 칸에 나눠 담는다
        r0, r1, c0, c1 = box
        w = (c1 - c0) / len(months)
        vals: dict[int, float] = {}
        for x, y in bs:
            k = int(min(len(months) - 1, max(0, (x - c0) // w)))
            v = round(val(y), 2)
            if months[k] not in vals or abs(v) > abs(vals[months[k]]):
                vals[months[k]] = v
        out["series"].append({"year": year, "color_rgb": list(c),
                              "n_bars": len(bs),
                              "monthly": {str(m): vals.get(m) for m in months}})
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
            print(f"     {s['year']}  막대 {s['n_bars']:2d}개 · {len(vs)}개월 · {rng} "
                  f"{r['unit']}")
    Path(a.out).write_text(json.dumps(
        {"_source": "2024 한강교량 온라인 안전감시 최종보고 — 월별 변위 그래프",
         "_method": "그림(래스터)에서 막대 픽셀 높이를 축 눈금으로 환산해 되읽은 값. "
                    "표에 인쇄된 숫자가 아니라 **디지타이즈 값**이라 눈금의 1~2 % 오차가 있다.",
         "charts": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
