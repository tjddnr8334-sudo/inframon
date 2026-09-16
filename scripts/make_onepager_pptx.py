#!/usr/bin/env python3
"""inframon 한 장 — 개요 + 교량 하나의 실제 결과를 **PPT 한 장**으로.

`make_onepager.py` 가 내는 PNG 는 고치려면 다시 돌려야 한다. 발표 자리에서 문구 한 줄,
그림 한 칸을 바꾸는 일이 흔하니 **편집되는 형태**로도 낸다. 기존 발표자료
(`make_kict_slides.py`)의 서식 함수를 그대로 가져다 써서, 이 한 장을 그 덱에 그대로
끼워 넣어도 톤이 어긋나지 않는다.

숫자는 `결과.md` 표와 대조 JSON 에서 읽는다 — 손으로 옮겨 적지 않는다.

    python scripts/make_onepager_pptx.py                        # 기본(성수대교)
    python scripts/make_onepager_pptx.py --bridge docs/bridges/암사대교

산출: docs/inframon_한장_<교량>.pptx  (슬라이드 1장 · 16:9)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from pptx.enum.text import PP_ALIGN                              # noqa: E402
from pptx.util import Emu                                        # noqa: E402

from make_kict_slides import (BLUE, BLUE_L, GRAY, GRAY_L, GREEN, NAVY,  # noqa: E402
                              NAVY_L, ORANGE, RED, SH, SW, blank, box, emu,
                              footer, header, picture_fit, text)


def tx(slide, x, y, w, h, s_, *, size=10, bold=False, color=NAVY, center=False):
    """한 줄짜리 글상자 — make_kict_slides.text() 의 runs 형식을 감싼다."""
    paras = [[(ln, size, bold, color)] for ln in str(s_).split("\n")]
    return text(slide, x, y, w, h, paras,
                align=PP_ALIGN.CENTER if center else PP_ALIGN.LEFT)

STEPS = [("0", "SLC→InSAR"), ("1", "제원"), ("2", "데크선"), ("3", "지면"),
         ("4", "점 선택"), ("5", "잔차고도"), ("6", "IFC 트윈"), ("7", "PINN·CRI"),
         ("8", "브리프"), ("9", "감사"), ("10", "결과 문서")]


def md_table(md: str) -> dict[str, str]:
    """결과.md 의 `| 키 | 값 |` 표를 읽는다."""
    out: dict[str, str] = {}
    for m in re.finditer(r"^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$", md, re.M):
        k, v = m.group(1).strip(), m.group(2).strip()
        if k and not set(k) <= {"-", " "}:
            out.setdefault(k, v)
    return out


def spec_row(md: str) -> list[str]:
    m = re.search(r"\|\s*연장\s*\|.*?\n\|[-\s|]+\n\|(.+?)\|\s*\n", md, re.S)
    return [c.strip() for c in m.group(1).split("|")] if m else []


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
    tb, sp = md_table(md), spec_row(md)

    cmp_ = {}
    try:
        j = json.loads(Path(a.compare).read_text(encoding="utf-8"))
        cmp_ = next((x for x in j["bridges"] if x["name"] == name), {})
    except Exception:                                    # noqa: BLE001
        pass
    ins = cmp_.get("insar") or {}

    from pptx import Presentation

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(emu(SW)), Emu(emu(SH))
    s = blank(prs)

    header(s, f"inframon — 좌표 하나로, 위성에서 교량까지", f"예시 · {name}")

    # ── 개요 3칸 ────────────────────────────────────────────────────────────
    w3 = (SW - 0.9 - 0.24 * 2) / 3
    for i, (ttl, body, col, fill) in enumerate([
        ("넣는 것",
         "· 교량 이름과 위경도 — 그게 전부다\n· 있으면: 이미 처리한 위성 트랙 · 교량 IFC",
         BLUE, BLUE_L),
        ("스스로 찾는 것",
         "· 제원(전국교량표준데이터·실측 CSV) · 교면선(OSM)\n"
         "· 지면(DEM) · 기온(ERA5) · 필요하면 SLC 받아 SNAP 처리",
         GREEN, GRAY_L),
        ("나오는 것",
         "· 교면 측점 변위속도 ± 95% 신뢰구간 · 브리프 그림\n"
         "· IFC 트윈 + 부재에 결합된 PS · CRI 경보 · 감사표",
         ORANGE, GRAY_L),
    ]):
        x = 0.45 + i * (w3 + 0.24)
        box(s, x, 1.02, w3, 0.86, fill=fill, line=col)
        tx(s, x + 0.13, 1.08, w3 - 0.26, 0.24, ttl, size=12, bold=True, color=col)
        tx(s, x + 0.13, 1.34, w3 - 0.26, 0.50, body, size=10, color=NAVY)

    # ── 11단계 띠 ───────────────────────────────────────────────────────────
    tx(s, 0.45, 1.95, 8.0, 0.22, "돌아가는 순서 — 명령 한 줄이면 이 열한 단계가 차례로 돈다",
            size=10, color=GRAY)
    ws = (SW - 0.9) / len(STEPS)
    for i, (num, nm) in enumerate(STEPS):
        x = 0.45 + i * ws
        box(s, x + 0.02, 2.19, ws - 0.04, 0.42, fill=GRAY_L, line=BLUE)
        tx(s, x + 0.02, 2.22, ws - 0.04, 0.20, num, size=11, bold=True,
           color=BLUE, center=True)
        tx(s, x + 0.02, 2.40, ws - 0.04, 0.20, nm, size=8.5, color=NAVY,
           center=True)

    # ── 결과 4칸 ────────────────────────────────────────────────────────────
    tx(s, 0.45, 2.70, 9.0, 0.26,
            f"예시 결과 — {name}  (산출물 그대로 · 손으로 옮겨 적은 숫자 없음)",
            size=13, bold=True, color=NAVY)

    pw, ph = (SW - 0.9 - 0.24) / 2, 1.55
    cap = 0.22
    cells = [
        (0.45, 2.98, bd / "chain.png",
         "① 측점은 어디서 나오나 — OSM 데크선 → 쉬프트 보정 → ±30 m 선별"),
        (0.45 + pw + 0.24, 2.98, bd / "twin_ps.png",
         "② 결과를 IFC 디지털 트윈 위에 — 부재 GlobalId 에 결합"),
        (0.45, 2.98 + ph + cap + 0.10, bd / "brief.png",
         "③ 건기연 브리프 4단 — 점 배치 · 교면 검증 · 속도 · 시계열"),
    ]
    for x, y, img, title in cells:
        tx(s, x, y, pw, cap, title, size=10, bold=True, color=NAVY_L)
        if img.exists():
            picture_fit(s, str(img), x, y + cap, pw, ph)

    # ④ 숫자판
    x4, y4 = 0.45 + pw + 0.24, 2.98 + ph + cap + 0.10
    tx(s, x4, y4, pw, cap, "④ 숫자 — 결과.md 에서 그대로", size=10, bold=True,
            color=NAVY_L)
    box(s, x4, y4 + cap, pw, ph, fill=GRAY_L, line=GRAY)

    rows: list[tuple[str, str, object]] = []
    lab = ["연장", "경간", "폭", "형하고", "지면", "방위", "형식", "시점"]
    if sp:
        rows.append(("제원(적용값)",
                     " · ".join(f"{k} {v}" for k, v in zip(lab, sp) if v), NAVY))
    rows.append(("무엇을 썼나", tb.get("specs") or "—", NAVY))
    rows.append(("교면 측점", tb.get("points") or "—", NAVY))
    rows.append(("IFC 트윈", tb.get("IFC 트윈") or "—", GREEN))
    rows.append(("상부구조", tb.get("superstructure") or "—", NAVY))
    if ins:
        rows.append(("LOS 변위속도",
                     f"{ins['ann']['v']:+.2f} ± {ins['ann']['ci']:.2f} mm/yr"
                     + ("  (유의)" if ins.get("significant")
                        else "  (95% CI 가 0 을 포함 — 유의차 없음)"),
                     RED if ins.get("significant") else GREEN))
        rows.append(("연직 환산",
                     f"{ins['v_vert_mm_yr']:+.2f} ± {ins['ci_vert_mm_yr']:.2f} mm/yr"
                     f"  · {ins['n_points']}점 · {ins['n_epochs']}시점", NAVY))
    if cmp_:
        rows.append(("현장 보고서", cmp_.get("report_trend") or "—", NAVY))
        ag = cmp_.get("agree") or "—"
        rows.append(("대조", ag, GREEN if ag.startswith("일치") else ORANGE))
    rows.append(("PINN · CRI", tb.get("PINN · CRI") or "—", ORANGE))
    aud = (tb.get("감사") or "—").replace("**", "").split(" — ")[0].strip()
    rows.append(("감사", aud + "  (사유 전문은 결과.md)", ORANGE))

    ry = y4 + cap + 0.09
    rh = (ph - 0.18) / max(len(rows), 1)
    for k, v, col in rows:
        # 한 칸에 11줄이라 줄바꿈이 나면 다음 줄을 덮는다 — 길면 줄여서 한 줄로 둔다.
        v = str(v)
        if len(v) > 66:
            v = v[:65].rstrip() + "…"
        tx(s, x4 + 0.12, ry, 1.30, rh, k, size=8, color=GRAY)
        tx(s, x4 + 1.44, ry, pw - 1.56, rh, v, size=8.5, bold=True, color=col)
        ry += rh

    footer(s, "이 한 장의 숫자는 모두 "
              f"docs/bridges/{name}/ 의 산출물에서 읽었다 — 발표용으로 다시 그리거나 "
              "고른 것이 없다.  못 하는 것도 함께: InSAR 는 상대 변위라 절대 강성(EI)을 "
              "식별하지 못하고(EI·고유진동수는 설계 제원 기반), 측점 밀도는 Sentinel-1 "
              "화소(~11 m)가 정하며, CRI 등급은 관측조건이 기준과 다르면 잠정이다.  "
              "※ 연구용 프로토타입 — 실무 안전판정이 아니다.  재현: "
              f"python scripts/bridge_run.py --name {name} "
              f"--lat {bj.get('lat')} --lon {bj.get('lon')}")

    out = Path(a.out or f"docs/inframon_한장_{name}.pptx")
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    print(f"wrote {out}  (슬라이드 {len(prs.slides.__iter__.__self__._sldIdLst)}장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
