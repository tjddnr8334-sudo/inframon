#!/usr/bin/env python3
"""inframon 한 장 — 개요 + 교량 하나의 **실제 결과** (A4 가로).

"이게 뭘 하는 프로그램이고, 돌리면 무엇이 나오는가" 를 한 장으로 보여야 할 때가 있다.
말로 된 개요만 있으면 와닿지 않고, 결과 그림만 있으면 맥락이 없다. 이 스크립트는 둘을
한 장에 붙인다 — 위는 개요(무엇을 넣어 무엇이 나오나), 아래는 **교량 폴더에서 그대로
읽은** 실제 결과다. 숫자를 손으로 옮겨 적지 않는다.

    python scripts/make_onepager.py                      # 기본(성수대교)
    python scripts/make_onepager.py --bridge docs/bridges/암사대교

산출: docs/img/inframon_한장_<교량>.png · .pdf
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams
from matplotlib.patches import FancyBboxPatch

for _f in ("Malgun Gothic", "맑은 고딕", "NanumGothic"):
    if any(_f in f.name for f in font_manager.fontManager.ttflist):
        rcParams["font.family"] = _f
        break
rcParams["axes.unicode_minus"] = False

NAVY = "#12314F"
BLUE = "#2E6FB7"
SKY = "#E8F0F8"
GREEN = "#2E9E6B"
ORANGE = "#E08A2B"
RED = "#C8443C"
INK = "#1B2733"
DIM = "#5C6B7A"
LINE = "#C9D6E2"

STEPS = [("0", "SLC→InSAR"), ("①", "제원"), ("②", "데크선"), ("③", "지면"),
         ("④", "점 선택"), ("⑤", "잔차고도"), ("⑥", "IFC 트윈"), ("⑦", "PINN·CRI"),
         ("⑧", "브리프"), ("⑨", "감사"), ("⑩", "결과 문서")]


def md_table(md: str) -> dict[str, str]:
    """결과.md 의 `| 키 | 값 |` 표를 읽는다 — 숫자를 다시 적지 않기 위해."""
    out: dict[str, str] = {}
    for m in re.finditer(r"^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$", md, re.M):
        k, v = m.group(1).strip(), m.group(2).strip()
        if k and k not in ("---", "") and not set(k) <= {"-", " "}:
            out.setdefault(k, v)
    return out


def spec_row(md: str) -> list[str]:
    """제원 표는 헤더/값 두 줄짜리라 따로 읽는다."""
    m = re.search(r"\|\s*연장\s*\|.*?\n\|[-\s|]+\n\|(.+?)\|\s*\n", md, re.S)
    return [c.strip() for c in m.group(1).split("|")] if m else []


def box(ax, x, y, w, h, *, fc="white", ec=LINE, lw=1.2, r=0.012):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                                fc=fc, ec=ec, lw=lw, transform=ax.transAxes,
                                clip_on=False, zorder=1))


def txt(ax, x, y, s, *, size=9.5, color=INK, weight="normal", ha="left", va="top"):
    ax.text(x, y, s, fontsize=size, color=color, fontweight=weight, ha=ha, va=va,
            transform=ax.transAxes, zorder=3)


def put_image(fig, path: Path, rect, title: str) -> None:
    """그림 한 칸 — 비율을 지켜 넣고 제목을 단다."""
    ax = fig.add_axes(rect)
    ax.axis("off")
    ax.set_title(title, fontsize=10, color=NAVY, fontweight="bold", pad=5)
    try:
        im = plt.imread(str(path))
    except Exception:                                  # noqa: BLE001
        ax.text(.5, .5, "(그림 없음)", ha="center", va="center", color=DIM)
        return
    ax.imshow(im)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="docs/bridges/성수대교")
    ap.add_argument("--compare", default="docs/bridges/hangang_gnss_insar.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    bd = Path(a.bridge)
    name = bd.name
    md = (bd / "결과.md").read_text(encoding="utf-8")
    bj = json.loads((bd / "bridge.json").read_text(encoding="utf-8"))
    tb = md_table(md)
    sp = spec_row(md)

    cmp_ = {}
    try:
        j = json.loads(Path(a.compare).read_text(encoding="utf-8"))
        cmp_ = next((x for x in j["bridges"] if x["name"] == name), {})
    except Exception:                                  # noqa: BLE001
        pass
    ins = cmp_.get("insar") or {}

    fig = plt.figure(figsize=(16.54, 11.69))           # A4 가로
    bg = fig.add_axes([0, 0, 1, 1]); bg.axis("off")

    # ── 제목 ────────────────────────────────────────────────────────────────
    bg.add_patch(plt.Rectangle((0, 0.945), 1, 0.055, color=NAVY,
                               transform=bg.transAxes, zorder=0))
    txt(bg, .022, .982, "inframon", size=25, color="white", weight="bold", va="center")
    txt(bg, .118, .987, "좌표 하나로, 위성에서 교량까지", size=14.5, color="#CFE0F0",
        va="center")
    txt(bg, .118, .962, "Sentinel-1 SLC → InSAR → 교면 점 → PINN → IFC 디지털 트윈 → B-Maps",
        size=10.5, color="#9FBBD6", va="center")
    txt(bg, .978, .972, f"예시 · {name}", size=15, color="white", weight="bold",
        ha="right", va="center")

    # ── ① 개요 — 무엇을 넣고 무엇이 나오나 ──────────────────────────────────
    txt(bg, .022, .928, "개요 — 무엇을 넣으면 무엇이 나오나", size=13.5,
        color=NAVY, weight="bold")
    box(bg, .022, .845, .30, .072, fc=SKY, ec=BLUE)
    txt(bg, .034, .908, "넣는 것", size=10.5, color=BLUE, weight="bold")
    txt(bg, .034, .890,
        "· 교량 이름과 위경도  (그게 전부다)\n"
        "· 있으면: 이미 처리한 위성 트랙 · 교량 IFC", size=10, color=INK)
    box(bg, .345, .845, .30, .072, fc="#F3F8F3", ec=GREEN)
    txt(bg, .357, .908, "스스로 찾는 것", size=10.5, color=GREEN, weight="bold")
    txt(bg, .357, .890,
        "· 제원(전국교량표준데이터·실측 CSV) · 교면선(OSM)\n"
        "· 지면(DEM) · 기온(ERA5) · 필요하면 SLC 다운로드·SNAP 처리", size=10, color=INK)
    box(bg, .668, .845, .31, .072, fc="#FBF5EC", ec=ORANGE)
    txt(bg, .680, .908, "나오는 것", size=10.5, color=ORANGE, weight="bold")
    txt(bg, .680, .890,
        "· 교면 측점 변위속도 ± 신뢰구간 · 브리프 그림\n"
        "· IFC 프록시 트윈 + 부재에 결합된 PS · CRI 경보 · 감사표", size=10, color=INK)

    # 단계 띠
    txt(bg, .022, .832, "돌아가는 순서 — 명령 한 줄이면 이 열한 단계가 차례로 돈다",
        size=10.5, color=DIM)
    x0, w = .022, .956 / len(STEPS)
    for i, (num, nm) in enumerate(STEPS):
        x = x0 + i * w
        box(bg, x + .002, .782, w - .006, .032, fc="white", ec=BLUE, lw=1.0)
        txt(bg, x + w / 2, .806, num, size=11.5, color=BLUE, weight="bold",
            ha="center", va="center")
        txt(bg, x + w / 2, .791, nm, size=8.3, color=INK, ha="center", va="center")

    # ── ② 예시 결과 ─────────────────────────────────────────────────────────
    txt(bg, .022, .745, f"예시 결과 — {name} (산출물 그대로, 손으로 옮겨 적은 숫자 없음)",
        size=13.5, color=NAVY, weight="bold")

    put_image(fig, bd / "chain.png", [.022, .378, .468, .300],
              "① 측점은 어디서 나오나 — OSM 데크선 → 쉬프트 보정 → ±30 m 선별 → 트윈")
    put_image(fig, bd / "twin_ps.png", [.512, .378, .468, .300],
              "② 결과를 IFC 디지털 트윈 위에 — 부재 GlobalId 에 결합")
    put_image(fig, bd / "brief.png", [.022, .082, .468, .272],
              "③ 건기연 브리프 4단 — 점 배치 · 교면 검증 · 속도 · 시계열")

    # 오른쪽 아래 — 숫자판
    ax = fig.add_axes([.512, .082, .468, .272]); ax.axis("off")
    ax.set_title("④ 숫자 — 결과.md 에서 그대로", fontsize=10, color=NAVY,
                 fontweight="bold", pad=5)
    box(ax, 0, 0, 1, 1, fc="#FBFCFD")

    def row(y, k, v, *, color=INK, kw=.27, wide=58):
        """키-값 한 줄. 값이 길면 접는다 — 잘라 버리면 무슨 말인지 알 수 없다."""
        txt(ax, .030, y, k, size=10, color=DIM)
        v = str(v)
        if len(v) > wide:
            cut = v.rfind(" ", 0, wide)
            cut = cut if cut > wide * 0.5 else wide
            txt(ax, .030 + kw, y, v[:cut], size=10.2, color=color, weight="bold")
            txt(ax, .030 + kw, y - .042, v[cut:].strip(), size=10.2, color=color,
                weight="bold")
            return True
        txt(ax, .030 + kw, y, v, size=10.2, color=color, weight="bold")
        return False

    y = .935
    dy = .070
    lab = ["연장", "경간", "폭", "형하고", "지면", "방위", "형식", "시점"]
    if sp:
        if row(y, "제원(적용값)", " · ".join(f"{k} {v}" for k, v in zip(lab, sp)
                                          if v), color=INK):
            y -= .042
        y -= dy
    _w = row(y, "무엇을 썼나", tb.get("specs") or "—"); y -= dy + (.042 if _w else 0)
    _w = row(y, "교면 측점", tb.get("points") or "—"); y -= dy + (.042 if _w else 0)
    _w = row(y, "IFC 트윈", tb.get("IFC 트윈") or "—", color=GREEN); y -= dy + (.042 if _w else 0)
    _w = row(y, "상부구조", tb.get("superstructure") or "—"); y -= dy + (.042 if _w else 0)
    if ins:
        row(y, "LOS 변위속도",
            f"{ins['ann']['v']:+.2f} ± {ins['ann']['ci']:.2f} mm/yr  "
            f"({'유의' if ins.get('significant') else '95% CI 가 0 을 포함 — 유의차 없음'})",
            color=(RED if ins.get("significant") else GREEN)); y -= dy + (.042 if _w else 0)
        row(y, "연직 환산",
            f"{ins['v_vert_mm_yr']:+.2f} ± {ins['ci_vert_mm_yr']:.2f} mm/yr  "
            f"· {ins['n_points']}점 · {ins['n_epochs']}시점", color=INK); y -= dy + (.042 if _w else 0)
    if cmp_:
        _w = row(y, "현장 보고서", cmp_.get("report_trend") or "—"); y -= dy + (.042 if _w else 0)
        ag = cmp_.get("agree") or "—"
        _w = row(y, "대조", ag, color=(GREEN if ag.startswith("일치") else ORANGE))
        y -= dy
    _w = row(y, "PINN · CRI", tb.get("PINN · CRI") or "—", color=ORANGE); y -= dy + (.042 if _w else 0)
    aud = (tb.get("감사") or "—").replace("**", "")
    aud = aud.split(" — ")[0].strip()          # 판정과 한 줄 사유까지만
    row(y, "감사", aud + "  (사유 전문은 결과.md)", color=ORANGE, wide=52)

    # ── 꼬리말 ──────────────────────────────────────────────────────────────
    txt(bg, .022, .058,
        "정직하게 — 이 한 장의 숫자는 모두 " + f"docs/bridges/{name}/ 의 산출물에서 읽었다. "
        "발표용으로 다시 그리거나 고른 것이 없다.", size=9.6, color=DIM)
    txt(bg, .022, .040,
        "못 하는 것도 함께 적는다: InSAR 는 상대 변위라 절대 강성(EI)을 식별하지 못한다"
        "(EI·고유진동수는 설계 제원 기반) · 데크 위 측점 밀도는 Sentinel-1 화소(~11 m)가 정한다"
        " · CRI 등급은 관측조건이 기준과 다르면 잠정이다.", size=9.6, color=DIM)
    txt(bg, .022, .014, "※ 연구용 프로토타입 — 산출은 파이프라인 결과이며 실무 안전판정이 아니다.",
        size=9.8, color=RED)
    txt(bg, .978, .014, f"재현: python scripts/bridge_run.py --name {name} "
                        f"--lat {bj.get('lat')} --lon {bj.get('lon')}",
        size=9.2, color=DIM, ha="right")

    out = Path(a.out or f"docs/img/inframon_한장_{name}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    fig.savefig(out.with_suffix(".pdf"))
    plt.close(fig)
    print("wrote", out)
    print("wrote", out.with_suffix(".pdf"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
