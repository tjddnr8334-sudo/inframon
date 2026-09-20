#!/usr/bin/env python3
"""보고서의 **GNSS 쪽만** 원본 그대로 뽑는다 — 내가 읽은 것이 맞는지 대조하라고.

우리가 쓴 GNSS 수치는 전부 2024 최종보고 PDF 에서 읽은 것이다. 읽은 값만 들고 다니면
맞는지 확인할 방법이 없다. 이 스크립트는 **GNSS 가 나오는 쪽을 원본 그대로 렌더**하고,
그 옆에 우리가 그 쪽에서 무엇을 읽었는지를 붙인다. 눈으로 바로 대조하라는 것이다.

    python scripts/make_gnss_evidence.py

산출:
    docs/img/보고서_GNSS_원본.png        대조표(쪽 이미지 + 우리가 읽은 것)
    docs/img/gnss_pages/p<NN>.png        쪽 원본(고해상도 · 확대해 보라고)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams

for _f in ("Malgun Gothic", "맑은 고딕", "NanumGothic"):
    if any(_f in f.name for f in font_manager.fontManager.ttflist):
        rcParams["font.family"] = _f
        break
rcParams["axes.unicode_minus"] = False

NAVY = "#12314F"
BLUE = "#2E6FB7"
GREEN = "#2E9E6B"
ORANGE = "#D98324"
RED = "#C8443C"
DIM = "#55636F"

# GNSS 가 나오는 쪽 — 왜 넣었는지와, 우리가 그 쪽에서 무엇을 했는지.
PAGES: list[dict] = [
    {"page": 16, "bridge": "(전체)", "what": "프로그램 개선 내역",
     "read": "‘GNSS 데이터 연계(행주대교 2개소 등 17개소)’ — 계측항목 표의 설치(◯)와 "
             "다른 층위다. 우리는 이 줄을 수치로 쓰지 않았다.",
     "flag": "확인 필요"},
    {"page": 27, "bridge": "가양대교", "what": "계측항목 표 · 처짐(경사계)",
     "read": "GNSS 가 감시항목 1순위에 ◯ 로 올라 있으나 **GNSS 그래프·수치표는 없다.** "
             "이 쪽에서 읽은 것은 처짐(경사계) 월별 변위(22·23·24년)다.",
     "flag": "GNSS 수치 없음"},
    {"page": 29, "bridge": "월드컵대교", "what": "GNSS 연직방향 변위량 비교 (2024-01~11)",
     "read": "월별 막대 3장(GP_S1M_01_Z 0~-60 · GP_T1T_01_Z 0~-40 · GP_S3M_01_Z ±4 mm). "
             "Min/Max 표는 없다. 앞서 ‘y축 눈금이 없다’ 고 적었으나 **그건 내가 틀렸다** — "
             "눈금은 있다. 다만 다음 쪽(p30)에 수치표가 인쇄돼 있어 그쪽을 쓴다.",
     "flag": "정정 — 읽을 수 있다"},
    {"page": 30, "bridge": "월드컵대교", "what": "GNSS 교축·교축직각 Min/Max/변동폭 표",
     "read": "표로 인쇄된 값을 그대로 옮겼다(예: Min −23.94 · Max −6.13 · 변동폭 17.81). "
             "월드컵대교의 확실한 GNSS 수치는 이쪽이다.",
     "flag": "표 그대로"},
    {"page": 33, "bridge": "서강대교", "what": "계측항목 표 · 케이블장력",
     "read": "GNSS 가 항목에 ◯ 로 올라 있으나 본문에 실린 것은 케이블장력(CA_01~CA_07) "
             "뿐이다. **GNSS 그래프·수치표 없음.**",
     "flag": "GNSS 수치 없음"},
    {"page": 50, "bridge": "샛강문화다리", "what": "GNSS 연직방향 변위량 (2022~2024)",
     "read": "‘2022, 23, 24년 GNSS 연직방향 변위량 양호’ + 월별 막대. 막대는 눈으로 읽지 "
             "않았다 — 다음 쪽 표가 더 확실해서다.",
     "flag": "표로 대체"},
    {"page": 51, "bridge": "샛강문화다리", "what": "GNSS 교축·교축직각 연도별 Min/Max/변동폭",
     "read": "연도별 표를 그대로 옮겼다(2022 −3.656/2.718/6.373 …). 좌측 주탑(P1)·우측 "
             "주탑(P2) 두 묶음. 우리 비교 그림의 붉은 띠가 이 값이다.",
     "flag": "표 그대로"},
]

FLAG_COLOR = {"표 그대로": GREEN, "GNSS 수치 없음": DIM, "읽지 않음": ORANGE,
              "표로 대체": BLUE, "확인 필요": RED, "정정 — 읽을 수 있다": RED}


def render(pdf: Path, pages: list[int], out_dir: Path, dpi: int = 200) -> dict[int, Path]:
    """쪽을 원본 그대로 PNG 로. 확대해 봐야 하니 해상도를 넉넉히 준다."""
    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf))
    made = {}
    for p in pages:
        pm = doc[p - 1].get_pixmap(dpi=dpi)
        q = out_dir / f"p{p:02d}.png"
        pm.save(str(q))
        made[p] = q
    doc.close()
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default="docs/한강교량 데이터/24년 한강온라인 최종보고 .pdf")
    ap.add_argument("--gnss-json", default="docs/bridges/hangang_gnss_2024.json")
    ap.add_argument("--out", default="docs/img/보고서_GNSS_원본.png")
    ap.add_argument("--pages-dir", default="docs/img/gnss_pages")
    ap.add_argument("--dpi", type=int, default=200)
    a = ap.parse_args()

    pdf = Path(a.pdf)
    if not pdf.exists():
        print(f"PDF 가 없다: {pdf}")
        return 2
    imgs = render(pdf, [d["page"] for d in PAGES], Path(a.pages_dir), a.dpi)

    n = len(PAGES)
    fig = plt.figure(figsize=(16.6, 3.5 * n))
    for i, d in enumerate(PAGES):
        ax = fig.add_axes([0.035, 1 - (i + 1) / n + 0.012, 0.42, 1 / n - 0.030])
        ax.axis("off")
        try:
            ax.imshow(plt.imread(str(imgs[d["page"]])))
        except Exception:                                # noqa: BLE001
            ax.text(.5, .5, "(렌더 실패)", ha="center", va="center")

        tx = fig.add_axes([0.485, 1 - (i + 1) / n + 0.012, 0.485, 1 / n - 0.030])
        tx.axis("off")
        tx.text(0, 0.95, f"p{d['page']}  ·  {d['bridge']}", fontsize=15,
                color=NAVY, fontweight="bold", va="top")
        tx.text(0, 0.80, d["what"], fontsize=12, color=BLUE, va="top")
        tx.text(0, 0.66, "우리가 이 쪽에서 한 것", fontsize=10.5, color=DIM, va="top")
        tx.text(0, 0.58, d["read"], fontsize=11.5, color="#1B2733", va="top",
                wrap=True)
        c = FLAG_COLOR.get(d["flag"], DIM)
        tx.text(0, 0.10, f"[ {d['flag']} ]", fontsize=12, color=c, fontweight="bold",
                va="bottom")

    fig.suptitle("2024 한강교량 최종보고 — GNSS 가 나오는 쪽 전부 (원본 그대로)\n"
                 "왼쪽이 보고서 원본, 오른쪽이 우리가 그 쪽에서 읽은 것 — 대조해 보라고 나란히 둔다",
                 fontsize=16, fontweight="bold", y=1 - 0.004)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print("wrote", a.out)
    for p, q in imgs.items():
        print("wrote", q)

    try:
        gj = json.loads(Path(a.gnss_json).read_text(encoding="utf-8"))
        have = [k for k, v in gj.items()
                if not k.startswith("_") and (v.get("tables") or [])]
        print("\nGNSS 수치표를 실제로 가진 교량:", ", ".join(have) or "없음")
        print("보고서 계측항목에 GNSS ◯ 인 교량:",
              ", ".join(k for k in gj if not k.startswith("_")))
    except Exception:                                    # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
