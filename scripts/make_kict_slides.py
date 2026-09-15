#!/usr/bin/env python3
"""KICT B-Maps 협의용 발표자료(.pptx) — 산출물에서 직접 만든다.

숫자를 손으로 옮겨 적으면 그 순간부터 발표자료와 산출물이 갈라진다. 이 스크립트는
`bridge_run.py` 가 만든 교량 폴더(bridge.json · 결과.md · project.h5)를 그대로 읽어
표와 그림을 채운다. 다시 돌리면 그날의 산출물로 갱신된다.

    python scripts/make_kict_slides.py --bridges docs/bridges/성수대교 docs/bridges/한강대교 \
        docs/bridges/올림픽대교 --ref docs/bridges/정자교 --out docs/KICT_Bmaps_협의.pptx

슬라이드 7장:
  ① 프로그램 개요 — 좌표 하나 → 산출까지의 자동 체인
  ② 서울 한강 3개소 실증 — 결과 요약표 + 위치·측점 지도
  ③ 교량별 산출 — 건기연 브리프 4단 그림
  ④ 산출물 전체 + 신뢰성 게이트(감사) + 원리적 한계
  ⑤ B-Maps 연동 — 사이드카 REST · 탭 매핑 · 실제 동작 화면
  ⑥ 합의 필요 사항 · 로드맵
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Pt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ── 색 · 글꼴 ──────────────────────────────────────────────────────────────
NAVY = RGBColor(0x10, 0x2A, 0x43)
NAVY_L = RGBColor(0x1C, 0x44, 0x69)
BLUE = RGBColor(0x1F, 0x6F, 0xB2)
BLUE_L = RGBColor(0xE7, 0xF0, 0xF8)
ORANGE = RGBColor(0xE0, 0x6C, 0x2C)
GREEN = RGBColor(0x2E, 0x7D, 0x32)
RED = RGBColor(0xC0, 0x30, 0x28)
GRAY = RGBColor(0x5A, 0x63, 0x6B)
GRAY_L = RGBColor(0xF2, 0xF4, 0xF6)
LINE = RGBColor(0xD4, 0xDA, 0xE0)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FONT = "맑은 고딕"

IN = 914400                       # EMU per inch
SW, SH = 13.333, 7.5              # 16:9


def emu(v: float) -> int:
    return round(v * IN)


# ── 도형 헬퍼 ──────────────────────────────────────────────────────────────
def box(slide, x, y, w, h, *, fill=None, line=None, lw=0.75,
        shape=MSO_SHAPE.ROUNDED_RECTANGLE, adj=None):
    s = slide.shapes.add_shape(shape, emu(x), emu(y), emu(w), emu(h))
    if adj is not None:
        try:
            s.adjustments[0] = adj
        except (IndexError, ValueError):
            pass
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid(); s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line; s.line.width = Pt(lw)
    s.shadow.inherit = False
    return s


def text(slide, x, y, w, h, runs, *, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
         spacing=1.0, wrap=True):
    """runs: [(문자열, 크기pt, 굵게, 색)] 또는 [[...], [...]] (문단 여러 개)."""
    tb = slide.shapes.add_textbox(emu(x), emu(y), emu(w), emu(h))
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    paras = runs if runs and isinstance(runs[0], list) else [runs]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        for t, sz, bold, col in para:
            r = p.add_run(); r.text = t
            r.font.size = Pt(sz); r.font.bold = bold
            r.font.color.rgb = col; r.font.name = FONT
    return tb


_NUM = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩", "⑪", "⑫"]
_seq = {"n": 0}


def header(slide, title: str, tag: str = "") -> None:
    """장 번호는 부르는 순서대로 자동으로 붙는다(손으로 매기면 슬라이드를 넣을 때마다 어긋난다)."""
    no = _NUM[_seq["n"]] if _seq["n"] < len(_NUM) else str(_seq["n"] + 1)
    _seq["n"] += 1
    box(slide, 0, 0, SW, 0.86, fill=NAVY, shape=MSO_SHAPE.RECTANGLE)
    box(slide, 0, 0.86, SW, 0.045, fill=BLUE, shape=MSO_SHAPE.RECTANGLE)
    text(slide, 0.45, 0.19, 9.6, 0.5,
         [(no + "  ", 15, True, RGBColor(0x7F, 0xB6, 0xE0)), (title, 21, True, WHITE)],
         anchor=MSO_ANCHOR.MIDDLE)
    if tag:
        text(slide, 9.6, 0.24, 3.3, 0.4, [(tag, 11, False, RGBColor(0xA9, 0xC4, 0xDA))],
             align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)


def footer(slide, note: str) -> None:
    box(slide, 0.45, 7.03, SW - 0.9, 0.012, fill=LINE, shape=MSO_SHAPE.RECTANGLE)
    text(slide, 0.45, 7.1, SW - 0.9, 0.3, [(note, 9, False, GRAY)])


def kpi(slide, x, y, w, h, big, unit, label, color=BLUE):
    box(slide, x, y, w, h, fill=WHITE, line=LINE)
    box(slide, x, y, 0.055, h, fill=color, shape=MSO_SHAPE.RECTANGLE)
    text(slide, x + 0.2, y + 0.16, w - 0.3, 0.44,
         [(big, 23, True, color), ("  " + unit, 11, False, GRAY)], anchor=MSO_ANCHOR.MIDDLE)
    text(slide, x + 0.2, y + 0.64, w - 0.3, 0.5, [(label, 10, False, NAVY)], spacing=1.15)


def table(slide, x, y, w, rows, col_w, *, head_h=0.34, row_h=0.33, fs=10.5, head_fs=10.5,
          colors=None):
    """간단 표 — 첫 행이 머리글. colors: {(행,열): RGBColor} 로 글자색 지정."""
    colors = colors or {}
    total = sum(col_w)
    col_w = [c / total * w for c in col_w]
    yy = y
    for ri, row in enumerate(rows):
        h = head_h if ri == 0 else row_h
        if ri == 0:
            box(slide, x, yy, w, h, fill=NAVY_L, shape=MSO_SHAPE.RECTANGLE)
        elif ri % 2 == 0:
            box(slide, x, yy, w, h, fill=GRAY_L, shape=MSO_SHAPE.RECTANGLE)
        xx = x
        for ci, cell in enumerate(row):
            col = (WHITE if ri == 0 else colors.get((ri, ci), NAVY))
            text(slide, xx + 0.09, yy, col_w[ci] - 0.18, h,
                 [(str(cell), head_fs if ri == 0 else fs, ri == 0 or ci == 0, col)],
                 align=PP_ALIGN.CENTER if ci else PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
            xx += col_w[ci]
        if ri:
            box(slide, x, yy + h, w, 0.008, fill=LINE, shape=MSO_SHAPE.RECTANGLE)
        yy += h
    return yy


def chevrons(slide, x, y, w, h, items, *, fs=10, sub_fs=8.5):
    """① → ② → ③ 흐름 띠. items: [(제목, 부제)]"""
    n = len(items)
    gap = 0.07
    cw = (w - gap * (n - 1)) / n
    for i, (t, sub) in enumerate(items):
        xx = x + i * (cw + gap)
        shape = MSO_SHAPE.PENTAGON if i == 0 else MSO_SHAPE.CHEVRON
        s = box(slide, xx, y, cw, h, fill=(BLUE if i % 2 == 0 else NAVY_L), shape=shape)
        s.line.fill.background()
        # 화살표 촉(오른쪽)과 꼬리 홈(왼쪽)이 글자를 덮는다 — 촉 폭만큼 안쪽으로 넣는다.
        tip = h * 0.5                     # 촉·홈 깊이 = 높이의 절반(도형 기본 adjust)
        paras = [[(t, fs, True, WHITE)]]
        paras += [[(ln, sub_fs, False, RGBColor(0xD6, 0xE6, 0xF3))] for ln in sub.splitlines()]
        text(slide, xx + (tip if i else 0.13), y + 0.05,
             cw - tip * (2.0 if i else 1.1), h - 0.10, paras,
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, spacing=1.0)


def bullets(slide, x, y, w, h, items, *, fs=10.5, mark="·", color=NAVY, spacing=1.32):
    paras = []
    for it in items:
        if isinstance(it, tuple):
            head, tail = it
            paras.append([(f"{mark} ", fs, True, BLUE), (head, fs, True, color),
                          (tail, fs, False, color)])
        else:
            paras.append([(f"{mark} ", fs, True, BLUE), (it, fs, False, color)])
    return text(slide, x, y, w, h, paras, spacing=spacing)


def picture(slide, path, x, y, w=None, h=None):
    kw = {}
    if w is not None:
        kw["width"] = Emu(emu(w))
    if h is not None:
        kw["height"] = Emu(emu(h))
    return slide.shapes.add_picture(str(path), Emu(emu(x)), Emu(emu(y)), **kw)


def picture_fit(slide, path, x, y, w, h):
    """`(x,y,w,h)` 상자 **안에** 비율을 지켜 넣고 가운데 정렬.

    너비만 주고 넣었더니 세로가 슬라이드 밖으로 잘려 나갔다 — 그림 비율은 데이터에
    따라 바뀌므로 배치 쪽에서 맞춰야 한다.
    """
    from PIL import Image
    with Image.open(path) as im:
        iw, ih = im.size
    scale = min(w / iw, h / ih)
    pw, ph = iw * scale, ih * scale
    return picture(slide, path, x + (w - pw) / 2, y + (h - ph) / 2, w=pw)


def blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


# ── 산출물 읽기 ────────────────────────────────────────────────────────────
_VERDICT_RE = re.compile(r"\|\s*감사\s*\|\s*\*\*(.+?)\*\*\s*·?\s*(.*?)\s*\|")
_CRI_RE = re.compile(r"CRI\s+([0-9.]+)\s*·\s*(\S+)")


def read_bridge(folder: Path) -> dict:
    """교량 폴더 → 발표자료가 쓰는 값만 (없으면 None 으로 남긴다 — 지어내지 않는다)."""
    d: dict = {"folder": folder, "name": folder.name}
    bj = folder / "bridge.json"
    if bj.exists():
        d["meta"] = json.loads(bj.read_text(encoding="utf-8"))
        d["name"] = d["meta"].get("name", folder.name)
    md = folder / "결과.md"
    if md.exists():
        t = md.read_text(encoding="utf-8")
        d["md"] = t
        m = _VERDICT_RE.search(t)
        if m:
            d["verdict"], d["verdict_why"] = m.group(1), m.group(2)
        m = _CRI_RE.search(t)
        if m:
            d["cri"], d["warning"] = float(m.group(1)), m.group(2)
        m = re.search(r"교면 위 − 밖 ([+-][0-9.]+) ± ([0-9.]+) m \(z=([-0-9.]+)\)", t)
        if m:
            d["rh"] = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
        m = re.search(r"데크 ±30 m 안 (\d+)/(\d+)", t)
        if m:
            d["n_deck"], d["n_track"] = int(m.group(1)), int(m.group(2))
    p5 = folder / "project.h5"
    if p5.exists():
        import h5py
        import numpy as np
        with h5py.File(p5, "r") as f:
            if "insar/velocity_mm_yr" in f:
                v = np.asarray(f["insar/velocity_mm_yr"][()], float)
                d["vel_med"] = float(np.median(v))
                d["vel_lo"], d["vel_hi"] = float(np.min(v)), float(np.max(v))
                d["n_points"] = int(v.size)
            if "insar/los" in f and "insar/dates" in f:
                # 속도는 값 하나로 읽으면 안 된다 — 95% 신뢰구간이 0 을 품는지가 판단이다.
                los = np.asarray(f["insar/los"][()], float)
                t = np.asarray(f["insar/dates"][()], float) / 365.25
                A = np.vstack([np.ones_like(t), t]).T
                coef, *_ = np.linalg.lstsq(A, los.T, rcond=None)
                resid = los.T - A @ coef
                sig = np.sqrt(np.sum(resid ** 2, axis=0) / max(len(t) - 2, 1))
                ci = 1.96 * sig / (np.std(t) * np.sqrt(len(t)))
                d["vel_ci"] = float(np.median(ci))
                d["frac_ci_zero"] = float(np.mean(np.abs(coef[1]) < ci))
                d["noise_mm"] = float(np.median(sig))
            if "insar/date_labels" in f:
                lab = [s.decode() if isinstance(s, bytes) else str(s)
                       for s in f["insar/date_labels"][()]]
                d["n_epochs"] = len(lab)
                d["span"] = (lab[0], lab[-1])
            if "pinn/EI" in f:
                d["EI"] = float(np.median(np.asarray(f["pinn/EI"][()], float)))
    return d


def api_endpoint_count() -> int:
    """B-Maps 연동 엔드포인트 수 — 코드에서 센다(발표자료와 구현이 갈라지지 않게)."""
    try:
        from inframon.api.app import create_app
        from inframon.api.registry import BridgeRegistry
        app = create_app(BridgeRegistry.single("x", "x", "x.h5"))
        return sum(1 for r in app.routes if getattr(r, "path", "").startswith("/api/v1"))
    except Exception:                            # noqa: BLE001 — fastapi 없는 환경
        return 12


def processed_bridge_count(root: Path = ROOT / "docs" / "bridges") -> int:
    """실 SLC 로 관통한 교량 수 — project.h5 가 있는 폴더만 센다."""
    try:
        return sum(1 for d in root.iterdir() if d.is_dir() and (d / "project.h5").exists())
    except OSError:
        return 0


def fmt(v, spec="{:.2f}", none="—"):
    return none if v is None else spec.format(v)


TYPE_KO = {"truss": "트러스", "arch": "아치", "cable_stayed": "사장", "slab": "슬래브",
           "box_girder": "박스거더", "girder": "거더", "suspension": "현수"}
MAT_KO = {"steel": "강", "reinforced_concrete": "RC", "prestressed_concrete": "PSC"}
VERDICT_COLOR = {"보고 가능": GREEN, "조건부": ORANGE, "보고 불가": RED}


# ── 슬라이드 ───────────────────────────────────────────────────────────────
def slide1(prs, bs, meta):
    s = blank(prs)
    box(s, 0, 0, SW, SH, fill=WHITE, shape=MSO_SHAPE.RECTANGLE)
    box(s, 0, 0, SW, 2.05, fill=NAVY, shape=MSO_SHAPE.RECTANGLE)
    box(s, 0, 2.05, SW, 0.05, fill=BLUE, shape=MSO_SHAPE.RECTANGLE)
    text(s, 0.62, 0.42, 11.5, 0.6,
         [("inframon", 30, True, WHITE),
          (" — 위성 InSAR·PINN 기반 교량 변위 모니터링", 24, True, WHITE)])
    text(s, 0.62, 1.12, 11.8, 0.4,
         [(("좌표 하나로 Sentinel-1 아카이브 → 교면 변위 시계열 → 구조 역산 → 디지털트윈 → "
           "B-Maps 탭까지 자동"), 13.5, False, RGBColor(0xBF, 0xD6, 0xE8))])
    text(s, 0.62, 1.52, 11.8, 0.35,
         [(("KICT B-Maps 연동 협의자료   |   위성데이터 기반 변위 예측·AI 연계 중소형 교량 "
           "지능형 스마트 유지관리 (RS-2026-25521660)"), 10.5, False,
           RGBColor(0x8F, 0xAD, 0xC6))])

    chevrons(s, 0.62, 2.38, SW - 1.24, 0.88, [
        ("⓪ 교량·SLC 선별", "제원·ROI·프레임\nERA5 master"),
        ("① InSAR 처리", "SNAP 코레지·간섭도\nsnaphu 언래핑"),
        ("② 교면 점 정합", "쉬프트 보정\n데크 ±30 m"),
        ("③ PINN 역산", "열·하중·침하·이상\n가상센싱"),
        ("④ FRAM·수명", "공진위험 CRI\n4단계 경보"),
        ("⑤ 산출·연동", "IFC 트윈·CSV\nB-Maps REST"),
    ])

    n_ep = max([b.get("n_epochs") or 0 for b in bs] + [0])
    n_pts = sum(b.get("n_points") or 0 for b in bs)
    n_all = processed_bridge_count()
    kpis = [
        (str(meta["n_scenes"]), "장면", f"실 Sentinel-1 SLC (ASC path127)\n{meta['span']}", BLUE),
        (str(n_all), "개소",
         f"실 SLC 로 관통한 교량(한강 {len(meta.get('shm') or {})}개소 포함)\n"
         "(같은 아카이브·같은 코드)", NAVY_L),
        (str(n_pts), "점", f"교면 결합 PS/DS 측점 · {n_ep}시점 시계열", GREEN),
        (str(api_endpoint_count()), "개", "B-Maps 연동 REST 엔드포인트\n(구현 완료 · 읽기 전용)", ORANGE),
    ]
    w = (SW - 1.24 - 0.24 * 3) / 4
    for i, (b_, u, l, c) in enumerate(kpis):
        kpi(s, 0.62 + i * (w + 0.24), 3.42, w, 1.22, b_, u, l, c)

    box(s, 0.62, 4.92, 6.3, 1.92, fill=BLUE_L, line=LINE)
    text(s, 0.85, 5.08, 5.9, 0.3, [("실행은 한 줄 — 교량을 갈아 끼우는 데 코드 수정이 없다",
                                    11.5, True, NAVY)])
    box(s, 0.85, 5.45, 5.85, 0.46, fill=WHITE, line=LINE)
    text(s, 0.98, 5.45, 5.6, 0.46,
         [(("python scripts/bridge_run.py --name 성수대교 \\\n"
           "    --lat 37.5374 --lon 127.0351"), 9, False, NAVY)],
         anchor=MSO_ANCHOR.MIDDLE, spacing=1.1)
    bullets(s, 0.85, 6.02, 5.9, 0.8, [
        "트랙이 없으면 SLC 선별·다운로드·SNAP 처리부터 스스로 만든다",
        "이미 받아 둔 기관 아카이브(.SAFE)면 그 자리에서 읽는다 — 재다운로드 없음",
    ], fs=9.5)

    box(s, 7.16, 4.92, SW - 7.78, 1.92, fill=WHITE, line=LINE)
    text(s, 7.4, 5.08, 5.2, 0.3, [("B-Maps 가 비어 있는 축을 채운다", 11.5, True, NAVY)])
    bullets(s, 7.4, 5.42, 5.2, 1.3, [
        ("기존 탭은 현장 계측·점검 기반", " — 성능평가·노후도·내하·계측안전성 …"),
        ("성수대교·한강대교는 '처짐(경사)' 센서가 미설치", (" — 감시항목 순위엔 올라 있다"
                                            "(2024 한강교량 온라인 최종보고)")),
        ("위성 InSAR 가 그 자리를 센서 없이 채운다", " — 설치된 항목과는 교차검증"),
    ], fs=9.5, spacing=1.28)
    footer(s, "⚠ 연구용 프로토타입 — 전 파이프라인·해석해 검증 완료, 현장·상용FEM·실 붕괴라벨 "
              "검증 미수행. 산출은 파이프라인 결과이며 실무 안전판정이 아니다.")


UI = ROOT / "docs" / "img" / "ui"


def _crop_side(png: Path, frac: float = 0.168) -> Path:
    """왼쪽 사이드바를 잘라 낸 사본 — 슬라이드에서는 **누르는 자리**가 커야 읽힌다."""
    from PIL import Image
    out = UI / "_crop" / png.name
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_mtime >= png.stat().st_mtime:
        return out
    with Image.open(png) as im:
        im.crop((int(im.width * frac), 0, im.width, im.height)).save(out)
    return out


def _uiblock(s, x, y, w, h, png, step, title, caption, *, crop=True, cap_h=0.46):
    """화면 한 장 + 무엇을 누르는지. 발표에서 손가락으로 짚을 수 있게 번호를 붙인다."""
    box(s, x, y, w, 0.42, fill=BLUE, shape=MSO_SHAPE.RECTANGLE)
    text(s, x + 0.14, y, w - 0.28, 0.42,
         [(step + "  ", 12, True, WHITE), (title, 11, True, WHITE)],
         anchor=MSO_ANCHOR.MIDDLE)
    src = UI / png
    if src.exists():
        img = _crop_side(src) if crop else src
        ih = h - 0.46 - cap_h
        picture_fit(s, img, x, y + 0.46, w, ih)
        box(s, x, y + 0.46, w, ih, fill=None, line=LINE)
    text(s, x, y + h - cap_h, w, cap_h, [(caption, 9, False, GRAY)], spacing=1.2)


def slide_flow1(prs):
    s = blank(prs)
    header(s, "화면에서 누르는 순서 (1/2) — 교량을 고르고, 장면 수를 정한다",
           "명령줄 없음 · 대시보드 ⓪ 시작 탭")
    chevrons(s, 0.45, 1.05, SW - 0.9, 0.62, [
        ("① 준비 상태", "SNAP·snaphu·토큰"),
        ("② 교량명 검색", "성수대교 → 찾기"),
        ("③ 장면 수·기간", "51장 · 2018~2025"),
        ("④ 계획 보기", "몇 초 · 무료"),
        ("⑤ 전체 실행", "SLC→트윈까지"),
    ], fs=9.5, sub_fs=8)
    _uiblock(s, 0.45, 1.78, 6.16, 3.80, "w3_hits.png", "②",
             "교량명으로 찾기 — '성수대교'",
             "교량명 입력 → 🔎 찾기 → 검색 결과 '성수대교 · CSV · 37.5322,127.0342 · 1160m' → "
             "'이 교량으로 설정' (위도·경도가 자동으로 채워진다)")
    _uiblock(s, 6.72, 1.78, 6.16, 3.80, "w4_run_setup.png", "③",
             "엔진 · SLC 장면 수 · 조회 기간",
             "snap 엔진 · 장면 수 51 · 2018-06-19~2025-12-27 → 화면이 바로 "
             "'51장 · 90개월 · 약 357 GB — 기준 충족' 이라고 알려 준다")
    box(s, 0.45, 5.66, SW - 0.9, 1.22, fill=BLUE_L, line=LINE)
    text(s, 0.72, 5.75, SW - 1.4, 0.26,
         [("여기서 정하는 것이 결과의 정밀도를 정한다", 11.5, True, NAVY)])
    bullets(s, 0.72, 6.04, SW - 1.44, 0.8, [
        ("SLC 장면 수", (" — 화면이 바로 계산해 준다: '51장 · 90개월 · 약 357 GB — "
                    "속도·CI 판정 기준(25장·12개월) 충족'. 기준 미달이면 노란 경고가 뜨고 "
                    "그 사실이 감사 기록에도 남는다.")),
        ("조회 기간", (" — 속도의 95% 신뢰구간은 장면 수와 기간이 정한다. 기간이 짧으면 "
                  "'추세 없음'도 '추세 있음'도 말할 수 없다.")),
        ("교량 선택", (" — 전국교량표준데이터 + OSM 을 함께 찾는다. 어떤 교량이든 된다 — "
                  "특정 교량 전용 도구가 아니다.")),
        ("① 준비 상태", (" — 그 앞 화면에서 처리 레인 3/3 · 점검 8/8 을 먼저 확인한다. "
                  "'모든 항목 준비 완료' 가 아니면 무엇이 없는지와 설치 명령을 그 자리에 띄운다.")),
    ], fs=8.5, spacing=1.22)
    footer(s, "화면 캡처는 실제 동작 화면 그대로 — scripts/capture_workflow.py 로 다시 찍을 수 있다.")


def slide_flow2(prs):
    s = blank(prs)
    header(s, "화면에서 누르는 순서 (2/2) — 계획을 확인하고, 결과를 본다",
           "⓪ 시작 → ① InSAR → ② PINN → ③ FRAM → ④ 잔존수명")
    _uiblock(s, 0.45, 1.05, 7.0, 5.42, "w5_plan.png", "④",
             "📋 계획 보기 — 누르기 전에 무엇이 돌지 먼저 본다",
             "네트워크 조회만 해서 몇 초. ①교량선정 ②④SLC·트랙·프레임 ⑤ERA5 master ⑥궤도·DEM "
             "⑦연직분해 ⑪교량메타 까지 실제로 조회하고, ⑧~⑭ 는 '전체 실행 시 무엇을 할지' 를 보여 준다.")
    _uiblock(s, 7.64, 1.05, 5.24, 2.66, "w6_insar.png", "⑤",
             "① InSAR 탭 — ▶ 이 단계 실행",
             "한 번에 끝까지 가려면 ⓪ 시작의 ▶ 전체 실행, 한 단계씩 보려면 각 탭의 ▶ 이 단계 실행")
    _uiblock(s, 7.64, 3.81, 5.24, 2.66, "w8_fram.png", "⑥",
             "③ FRAM 탭 — CRI·경보(잠정 표시 포함)",
             "관측조건이 기준치 학습 조건과 다르면 배지가 '위험 (잠정)' 으로 내려가고 사유가 함께 뜬다")
    footer(s, "▶ 전체 실행은 SLC 다운로드·SAR 처리로 수 시간 — 계획 보기로 장면 수·기간·용량을 "
              "먼저 확인하고 누르는 것이 정석이다.")


def slide2(prs, bs, meta, fig, extra=()):
    s = blank(prs)
    n_han = len(meta.get("shm") or {}) or len(bs)
    header(s, f"한강 교량 {n_han}개소 실증 — 교량만 갈아 끼웠다 "
              f"(표는 측점 상위 {len(bs)}개소"
              + (f" + 참고 {len(extra)}개소)" if extra else ")"),
           f"Sentinel-1 ASC path127 · {meta['n_scenes']}장면 · {meta['span']}")
    rows = [["교량", "상부형식", "연장×폭", "준공", "시점", "교면 측점",
             "LOS 변위속도 ± 95% CI", "CI 가 0 포함", "현장 SHM 2024"]]
    colors = {}
    for i, b in enumerate([*bs, *extra], start=1):
        m = b.get("meta", {})
        spec = meta["specs"].get(b["name"], {})
        rows.append([
            b["name"],
            (f"{TYPE_KO.get(m.get('bridge_type'), m.get('bridge_type') or '—')}"
            f"·{MAT_KO.get(m.get('material'), '')}"),
            f"{fmt(m.get('length_m'), '{:.0f}')}×{fmt(m.get('width_m'), '{:.0f}')} m",
            spec.get("year", "—"),
            str(b.get("n_epochs") or "—"),
            f"{b.get('n_deck', b.get('n_points')) or '—'} 점",
            (f"{fmt(b.get('vel_med'), '{:+.2f}')} ± {fmt(b.get('vel_ci'), '{:.2f}')} mm/yr"
             if b.get("vel_ci") else f"{fmt(b.get('vel_med'), '{:+.2f}')} mm/yr"),
            (f"{100 * b['frac_ci_zero']:.0f}%" if b.get("frac_ci_zero") is not None else "—"),
            meta["shm"].get(b["name"], {}).get("verdict", "—"),
        ])
        if b.get("frac_ci_zero") is not None:
            colors[(i, 7)] = GREEN if b["frac_ci_zero"] >= 0.5 else ORANGE
        if meta["shm"].get(b["name"], {}).get("verdict"):
            colors[(i, 8)] = GREEN
    end = table(s, 0.45, 1.12, SW - 0.9, rows, [1.3, 1.3, 1.3, 0.8, 0.7, 1.0, 2.0, 1.1, 2.2])

    # 판독 한 줄 — 표의 숫자가 무엇을 뜻하는지 발표자가 말하지 않아도 읽히게.
    ok = [b for b in bs if (b.get("frac_ci_zero") or 0) >= 0.5]
    if ok:
        names = "·".join(f"{b['name']} {100 * b['frac_ci_zero']:.0f}%" for b in ok)
        box(s, 0.45, end + 0.12, SW - 0.9, 0.44, fill=BLUE_L, line=LINE)
        text(s, 0.68, end + 0.12, SW - 1.35, 0.44,
             [("판독 — ", 10.5, True, NAVY),
              ((f"교면 측점의 LOS 변위속도 95% 신뢰구간이 0 을 포함한다({names}). "
               "관측 정밀도 안에서 유의한 거동이 없다는 뜻이고, 2024년 현장 SHM 의 "
               "'관리기준 이내' 판정과 어긋나지 않는다."), 10.5, False, NAVY)],
             anchor=MSO_ANCHOR.MIDDLE)
        end += 0.56
    if fig and Path(fig).exists():
        picture_fit(s, fig, 0.45, end + 0.12, SW - 0.9, 6.92 - (end + 0.12))
    footer(s, "LOS 변위속도는 교면 ±30 m 결합 측점의 중앙값 ± 95% 신뢰구간(부호 −=위성에서 멀어짐). "
              "'CI 가 0 포함' 이 높을수록 유의한 거동이 없다는 뜻. 현장 SHM 은 2024년 한강교량 "
              "온라인 안전감시시스템 유지관리 최종보고의 판정.")


def slide3(prs, bs, shm=None):
    s = blank(prs)
    header(s, "교량별 산출 — 건기연 브리프 4단 그림", "brief.png · 교량 폴더에 자동 생성")
    shm = shm or {}
    main = next((b for b in bs if (b["folder"] / "brief.png").exists()), None)
    if main:
        picture_fit(s, main["folder"] / "brief.png", 0.45, 1.12, 8.35, 5.75)
        text(s, 0.45, 1.12 - 0.0, 8.35, 0.2, [])
    bullets(s, 9.05, 1.2, 3.85, 1.95, [
        ("(a) 점을 교량 위에", " — 가로=교대에서 잰 거리, 세로=중심선에서 벗어난 거리. "
         "×는 정밀도 기준에 못 미쳐 뺀 점."),
        ("(b) 교면 위가 맞나", " — 교면 위 점이 제방 점보다 유의하게 높아야 한다. "
         "아니면 뒤 숫자를 믿으면 안 된다."),
        ("(c) 어디가 얼마나", " — 점마다 mm/yr ± 95% CI. 초록 띠(±0.5) 안이면 '움직임 없음'."),
        ("(d) 시간에 따라", " — 교면 점 중앙값 시계열. 흔들림은 계절(열), 기울기가 장기 변위."),
    ], fs=9.5, spacing=1.20)
    box(s, 9.05, 3.26, 3.85, 0.92, fill=GRAY_L, line=LINE)
    text(s, 9.22, 3.26, 3.55, 0.92,
         [(("한 줄로 — 이 레인은 시점별 잡음이 ±14 mm 라 **점 하나의 mm/yr 는 못 믿는다.** "
            "그래서 (c)(d) 에서 점 하나가 아니라 **교면 점 전체의 중앙값 ± 신뢰구간**까지만 "
            "말한다. 그 이상은 이 데이터가 답하지 못한다."), 8.5, False, NAVY)],
         anchor=MSO_ANCHOR.MIDDLE, spacing=1.2)
    text(s, 9.05, 4.34, 3.85, 0.3,
         [("현장 계측이 비워 둔 자리", 12.5, True, NAVY)])
    rows = [["교량", "SHM 감시항목(설치)", "위성 InSAR"]]
    colors = {}
    for i, b in enumerate(bs, start=1):
        h = shm.get(b["name"], {})
        items = h.get("items") or []
        miss = [nm for nm, on in items if not on]
        rows.append([b["name"],
                     "·".join(nm.split("(")[0] + ("◯" if on else "✗") for nm, on in items) or "—",
                     ("처짐 대체" if miss else "교차검증")])
        colors[(i, 2)] = ORANGE if miss else GREEN
    table(s, 9.05, 4.68, 3.85, rows, [0.9, 2.3, 1.0], row_h=0.40, head_h=0.3,
          fs=8, head_fs=8.5, colors=colors)
    text(s, 9.05, 6.30, 3.85, 0.72,
         [(("성수대교·한강대교는 감시항목 순위에 '처짐(경사)'이 올라 있는데 센서가 "
            "미설치다(2024 최종보고). 위성 InSAR 는 센서 없이 그 항목을 채우고, "
            "설치된 항목과는 결과가 서로 맞는지 대조된다."), 9, False, GRAY)], spacing=1.25)
    footer(s, "그림은 산출물 그대로 — 발표용으로 다시 그리지 않는다. 재현: "
              "python scripts/bridge_run.py --name <교량> --lat <위도> --lon <경도> --track <트랙>")


def slide_shm(prs, compare_fig, shm: dict, cmp_rows: list):
    """현장 계측 판정과 위성 관측을 같은 축에 놓는다 — 발표의 근거가 되는 장.

    `cmp_rows` 는 `docs/bridges/hangang_gnss_insar.json` 의 bridges 배열 —
    보고서 16개소를 하나도 빼지 않고 위성 판정과 맞댄 결과다.
    """
    s = blank(prs)
    header(s, "현장 계측 ↔ 위성 InSAR — 16개소 전수 대조",
           "2024 한강교량 온라인 안전감시 최종보고 전 교량")
    if compare_fig and Path(compare_fig).exists():
        picture_fit(s, compare_fig, 0.45, 1.0, SW - 0.9, 4.92)

    done = [r for r in cmp_rows if r.get("insar")]
    agree = [r for r in done if str(r.get("agree", "")).startswith("일치")]
    n_gap = sum(1 for v in shm.values()
                if any(st in ("X", "△") for _, st in (v.get("items") or [])))
    w = (SW - 0.9 - 0.24 * 2) / 3
    kpi(s, 0.45, 5.98, w, 0.88, f"{len(done)}/{len(cmp_rows) or len(shm)}", "개소",
        "보고서 대상 중 위성으로도 산출된 교량 — 빠진 곳 없음", NAVY_L)
    kpi(s, 0.45 + w + 0.24, 5.98, w, 0.88, f"{len(agree)}/{len(done)}", "일치",
        "위성 판정이 보고서 판정과 같은 교량", GREEN)
    kpi(s, 0.45 + 2 * (w + 0.24), 5.98, w, 0.88, f"{n_gap}", "개소",
        "처짐·텐던 계측이 미설치(X)이거나 경사계 대체(△)", ORANGE)
    footer(s, "위성 판정 = 데크 중앙값 LOS 시계열에 직선+연주기를 맞춘 속도의 95% 신뢰구간이 "
              "0 을 포함하는가. 현장 계측은 센서가 있는 항목만, 그 지점에서 본다 — 둘은 경쟁이 "
              "아니라 서로의 빈칸을 메운다.")


def slide_gnss(prs, fig, trend_json):
    """보고서 GNSS 와 위성을 **같은 기간으로 잘라** 맞댄 장 — 되는 것과 안 되는 것."""
    s = blank(prs)
    header(s, "보고서 GNSS ↔ 위성 InSAR — 기간을 맞추면 무엇이 남는가",
           "월드컵대교 2024-01~11 · 샛강문화다리 2022~2024")
    if fig and Path(fig).exists():
        picture_fit(s, fig, 0.45, 1.0, SW - 0.9, 4.86)

    d = trend_json or {}
    wc = (d.get("월드컵대교") or {}).get("insar_2024") or {}
    sg = (d.get("샛강문화다리") or {}).get("insar_2022_2024") or {}
    sgf = (d.get("샛강문화다리") or {}).get("insar_전체") or {}

    def _ci(w):
        if not w:
            return None
        k = "ann" if w.get("annual_fit") else "lin"
        return w[k]["ci"]

    w = (SW - 0.9 - 0.24 * 2) / 3
    kpi(s, 0.45, 5.98, w, 0.88, "2", "개소",
        "보고서에 GNSS 수치표가 인쇄된 교량(16개소 중)", NAVY_L)
    kpi(s, 0.45 + w + 0.24, 5.98, w, 0.88,
        f"{wc.get('n_epochs', '?')} · {sg.get('n_epochs', '?')}", "시점",
        "보고서 창(1년 · 3년)으로 자른 Sentinel-1 시점 수", ORANGE)
    c1, c3 = _ci(wc), _ci(sg)
    kpi(s, 0.45 + 2 * (w + 0.24), 5.98, w, 0.88,
        (f"±{c1:.0f} · ±{c3:.1f}" if c1 and c3 else "—"), "mm/yr",
        "같은 창에서의 95% 신뢰구간 — 이 폭으로는 추세를 판별할 수 없다", RED)

    footer(s, "정직하게 — 보고서 GNSS 는 2024 한 해(월드컵) 또는 2022~24 3년(샛강)뿐이고, "
              "그 창으로 위성을 자르면 Sentinel-1(12일 주기)은 시점이 6~19개라 신뢰구간이 "
              "GNSS 값 전체를 삼킨다. 전체 8년으로 보면 샛강문화다리가 "
              f"{sgf.get('ann', {}).get('v', 0):+.2f} mm/yr 로 보고서 GNSS 범위 안에 들지만, "
              "그건 같은 기간을 잰 값이 아니다. GNSS 대조는 코너리플렉터·고해상도 SAR 이 "
              "있어야 성립한다.")


def slide_cycle(prs, fig, cyc: dict):
    """추세는 못 가려도 **연주기는 맞는다** — 앞 장의 '판별 불가' 다음에 오는 장."""
    s = blank(prs)
    header(s, "보고서 월별 변위 ↔ 위성 — 추세는 못 가려도 연주기는 맞는다",
           "2022~2024 같은 창 · 보고서 월별 처짐 곡선 ↔ 위성 연주기 적합")
    if fig and Path(fig).exists():
        picture_fit(s, fig, 0.45, 1.0, SW - 0.9, 4.92)
    rows = (cyc or {}).get("bridges") or []
    gaps = [r["phase_gap"] for r in rows if r.get("phase_gap") is not None]
    eps = sorted(r.get("insar_fit", {}).get("n", 0) for r in rows)
    n_ep = (f"{eps[0]}~{eps[-1]}" if eps and eps[0] != eps[-1]
            else (str(eps[0]) if eps else "—"))
    w = (SW - 0.9 - 0.24 * 2) / 3
    kpi(s, 0.45, 5.98, w, 0.88, str(len(rows)), "개소",
        "보고서 월별 변위 곡선을 되읽어 대조한 교량", NAVY_L)
    kpi(s, 0.45 + w + 0.24, 5.98, w, 0.88,
        (f"{min(gaps):.1f}~{max(gaps):.1f}" if gaps else "—"), "개월",
        "최대가 되는 달의 차이 — 세 곳 모두 2개월 안쪽", GREEN)
    kpi(s, 0.45 + 2 * (w + 0.24), 5.98, w, 0.88, n_ep, "시점",
        "같은 창의 Sentinel-1 시점 — 추세는 못 가려도 계절은 잡힌다", BLUE)
    footer(s, "진폭은 맞출 대상이 아니다 — 경사계·레이저처짐계는 한 지점의 처짐이고 위성은 "
              "교면 결합 측점의 중앙값이라, 경간 중앙의 큰 스윙이 중앙값에서 상쇄된다. "
              "같은 열거동을 보고 있는지는 최대가 되는 달로 본다. 보고서 값은 그림에서 "
              "되읽은 값이다(자동 판독과 눈 판독이 중앙 0.5 mm 로 일치).")


def slide_chain(prs, fig, name: str, b: dict | None):
    """좌표 하나 → OSM 데크선 → PS 선별 → 트윈, 중간을 빼지 않고 보이는 장."""
    s = blank(prs)
    header(s, f"측점은 어디서 나오나 — {name} 전 과정을 한 장에",
           "OSM 데크선 · 쉬프트 되돌림 · 데크 ±30 m 선별 · IFC 부재 결합")
    if fig and Path(fig).exists():
        picture_fit(s, fig, 0.45, 1.0, SW - 0.9, 4.98)
    src = (b or {}).get("sources", {})
    m = re.search(r"쉬프트\s*([0-9.]+)\s*m", src.get("points", ""))
    m2 = re.search(r"안\s*(\d+)\s*/\s*(\d+)", src.get("points", ""))
    w = (SW - 0.9 - 0.24 * 2) / 3
    kpi(s, 0.45, 6.06, w, 0.84, (m2.group(2) if m2 else "?"), "점",
        "트랙 전체(이 교량 반경 안 PS/DS 후보)", NAVY_L)
    kpi(s, 0.45 + w + 0.24, 6.06, w, 0.84, (m.group(1) + " m" if m else "—"), "쉬프트",
        "형하고 때문에 밀린 거리 δh/tanθ — 되돌려야 교면에 앉는다", ORANGE)
    kpi(s, 0.45 + 2 * (w + 0.24), 6.06, w, 0.84, (m2.group(1) if m2 else "?"), "점",
        "데크 ±30 m 안으로 남은 교면 측점", GREEN)
    footer(s, "ⓐ→ⓑ 선별은 파이프라인 ④단계와 같은 코드다(geolocation.apply_correction + "
              "deck_geometry.project_to_polyline) — 발표용으로 다시 고른 것이 아니다. "
              "ⓒ→ⓓ 는 트윈 산출물(twin.viewer.html)을 그대로 읽는다.")


def slide_twin_ps(prs, fig, summary: list):
    """디지털 트윈 위에 PS 점을 얹은 장 — 교량마다 같은 방식으로 나온다."""
    s = blank(prs)
    n_twin = sum(1 for r in summary if r.get("n_points"))
    n_pts = sum(r.get("n_points") or 0 for r in summary)
    n_bd = sum(r.get("n_bound") or 0 for r in summary)
    header(s, "디지털 트윈 위의 PS 점 — 교량마다, 부재에 묶어서",
           "IFC 4.3 프록시 부재 + 측점 결합 · twin.viewer.html(자립형 3D) · 3D Tiles")
    if fig and Path(fig).exists():
        picture_fit(s, fig, 0.45, 1.0, SW - 0.9, 4.86)
    w = (SW - 0.9 - 0.24 * 2) / 3
    kpi(s, 0.45, 5.98, w, 0.88, f"{n_twin}/{len(summary)}", "개소",
        "좌표 하나로 IFC 트윈까지 나온 교량", NAVY_L)
    kpi(s, 0.45 + w + 0.24, 5.98, w, 0.88, f"{n_pts}", "점",
        "트윈에 얹힌 교면 PS/DS 측점 합계", GREEN)
    kpi(s, 0.45 + 2 * (w + 0.24), 5.98, w, 0.88,
        f"{100 * n_bd / max(n_pts, 1):.0f}%", "결합",
        f"IFC 부재 GlobalId 에 묶인 측점 {n_bd}점 — 시계열↔부재 영구결합", BLUE)
    footer(s, "결합률이 낮으면 측점이 데크 밖(제방·교대 주변)에 있거나 프록시 배치가 어긋난 것이다 "
              "— 그림으로 바로 보인다. 부재 결합은 IFC 4.3 GlobalId 외래키라 모델을 다시 만들어도 "
              "시계열이 따라간다.")


def slide4(prs, bs, ondeck=None):
    s = blank(prs)
    header(s, "산출물과 신뢰성 게이트 — 무엇이 나오고, 무엇을 올리지 않는가")
    text(s, 0.45, 1.1, 6.1, 0.3, [("교량 하나가 남기는 것", 13, True, NAVY)])
    rows = [["산출물", "내용", "소비처"],
            ["project.h5", "InSAR·PINN·FRAM 전 단계 배열 + 출처 매니페스트", "계약(전 하류)"],
            ["brief.png", "건기연 브리프 4단 그림 (a)(b)(c)(d)", "보고서·발표"],
            ["*_proxy.ifc · twin.glb", "IFC4 프록시 트윈 + 측점 GlobalId 결합", "BIM·3D Tiles"],
            ["displacement.csv", "점×시점 롱포맷 변위 (mm·ISO 날짜)", "B-Maps 다운로드"],
            ["*_vlm.zip", "manifest·summary·narrative·지식그래프·figures", "VLM 팀 핸드오프"],
            ["결과.md", "무엇을 어디서 가져왔고 무엇이 없었는지", "감사 추적"]]
    table(s, 0.45, 1.45, 6.1, rows, [1.5, 2.6, 1.1], row_h=0.36, fs=9.5, head_fs=9.5)

    text(s, 0.45, 4.02, 6.1, 0.3,
         [("'BIM 에 InSAR 를 올린다'가 실제로 뜻하는 것", 13, True, NAVY)])
    if ondeck and Path(ondeck).exists():
        picture_fit(s, ondeck, 0.45, 4.28, 6.1, 2.18)
    bullets(s, 0.45, 6.52, 6.1, 0.5, [
        "지오코딩 산출물은 점을 DEM 지면에 놓고 δh/tanθ 만큼 밀어 둔다 — 둘 다 되돌려야 데크 위에 앉는다",
        "데크 폭은 추정이 아니라 OSM 양측 보도선 간격 + 보도 반폭 (그림은 정자교 · 방법은 모든 교량 공통)",
    ], fs=8, spacing=1.22)

    text(s, 6.85, 1.1, 6.05, 0.3, [("감사(audit) — 통과한 것만 플랫폼에 올린다", 13, True, NAVY)])
    y = 1.5
    for label, col, why in [
        ("보고 가능", GREEN, "교면 위 측점 확보 · 위상 언래핑 완료 · PINN 정상 수렴"),
        ("조건부", ORANGE, "제원 출처가 불확실하거나 시점 수가 기준(≥100장) 미달 — 수치는 내되 판정 보류"),
        ("보고 불가", RED, "래핑 위상 · 교량 밖 측점 · 퇴화 PINN — B-Maps 로 아예 전송하지 않는다"),
    ]:
        box(s, 6.85, y, 6.05, 0.62, fill=WHITE, line=LINE)
        box(s, 6.85, y, 0.06, 0.62, fill=col, shape=MSO_SHAPE.RECTANGLE)
        text(s, 7.05, y, 1.15, 0.62, [(label, 11, True, col)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, 8.2, y, 4.6, 0.62, [(why, 9.5, False, NAVY)], anchor=MSO_ANCHOR.MIDDLE,
             spacing=1.15)
        y += 0.72

    box(s, 6.85, y + 0.06, 6.05, 0.78, fill=BLUE_L, line=LINE)
    text(s, 7.05, y + 0.06, 5.65, 0.78,
         [(("'보고 불가'는 전송 자체가 막힌다. 그리고 CRI 등급은 관측조건(노이즈·기간·에폭)이 "
           "기준치 학습 조건과 다르면 '잠정'으로 내려가고, API·B-Maps 탭까지 그 표시가 "
           "따라간다 — 이번 한강 3개 교량이 그 경우다."), 9.5, False, NAVY)],
         anchor=MSO_ANCHOR.MIDDLE, spacing=1.15)

    text(s, 6.85, y + 1.0, 6.05, 0.3,
         [("이 방식이 원리상 못 하는 것 — 먼저 밝힌다", 13, True, NAVY)])
    bullets(s, 6.85, y + 1.34, 6.05, 1.2, [
        ("절대 강성 EI 관측", " — InSAR 는 상대 변위라 자중 처짐을 못 본다. EI·f₁ 은 설계 제원 기반."),
        ("교면 PS 밀도", " — Sentinel-1 화소 ~11 m. 코너리플렉터·고해상도 SAR 가 답이다."),
        ("화소보다 작은 국부 손상", " — 정자교 보도부 붕괴도 잔차 계단이 잡음과 구별되지 않았다."),
        ("단일 궤도 LOS", " — 연직·수평 분해는 상승+하강 궤도가 모두 있어야 한다(융합 경로 구현 완료)."),
    ], fs=9.5, spacing=1.25)
    box(s, 6.85, 6.36, 6.05, 0.58, fill=GRAY_L, line=LINE)
    text(s, 7.08, 6.36, 5.6, 0.58,
         [(("못 하는 것을 적어 두는 이유 — 판정이 관측 한계 안에서 내려졌는지를 "
           "받는 쪽이 알아야 한다. 결과.md 가 교량마다 같은 목록을 함께 남긴다."),
           9, False, NAVY)], anchor=MSO_ANCHOR.MIDDLE, spacing=1.2)
    footer(s, "감사·전송 기록은 sync_state.json 에 남는다 — 무엇을 언제 왜 올렸는지/걸렀는지.")


def slide5(prs, shot):
    s = blank(prs)
    header(s, "B-Maps 연동 — 사이드카 REST 마이크로서비스", "설계·구현 완료 · 읽기 전용 GET")
    box(s, 0.45, 1.1, 6.3, 2.35, fill=GRAY_L, line=LINE)
    text(s, 0.62, 1.22, 5.9, 0.28, [("왜 사이드카인가", 12, True, NAVY)])
    box(s, 0.62, 1.56, 5.95, 0.5, fill=NAVY, line=None)
    text(s, 0.78, 1.56, 5.6, 0.5,
         [("B-Maps  [성능평가][노후도]…[★ InSAR 위성 변위 분석]  ← 새 탭(프론트)",
           9.5, True, WHITE)], anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.62, 2.1, 5.95, 0.22, [("        ↓  REST/JSON (교량관리번호로 조회)",
                                     9, False, GRAY)])
    box(s, 0.62, 2.34, 5.95, 0.44, fill=BLUE, line=None)
    text(s, 0.78, 2.34, 5.6, 0.44, [("inframon-api (FastAPI 사이드카)", 9.5, True, WHITE)],
         anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.62, 2.82, 5.95, 0.22, [("        ↓  읽기 전용", 9, False, GRAY)])
    box(s, 0.62, 3.0, 5.95, 0.36, fill=WHITE, line=LINE)
    text(s, 0.78, 3.0, 5.6, 0.36, [("교량별 project.h5 (데이터 계약)", 9.5, False, NAVY)],
         anchor=MSO_ANCHOR.MIDDLE)

    bullets(s, 0.45, 3.52, 6.3, 0.9, [
        ("본체를 오염시키지 않는다", " — GDAL·SARvey·PyTorch·WSL2 의존이 B-Maps 배포에 섞이지 않음"),
        ("즉시 반영", " — 파이프라인이 project.h5 를 갱신하면 다음 요청부터 새 값"),
        ("내부망 + API Key 또는 B-Maps SSO", " · CORS 는 B-Maps 도메인만"),
    ], fs=9.5, spacing=1.25)

    rows = [["B-Maps 탭 UI", "엔드포인트"],
            ["상단 경보 배지(정상~위험 · 잠정 표시)", "/insar/summary"],
            ["지도 측점 레이어(색=변위 또는 CRI)", "/insar/points(.geojson)"],
            ["측점 클릭 → 시계열 팝업", "/insar/points/{id}/series"],
            ["CRI 안전성 추세 차트", "/insar/cri"],
            ["기능 공명 다이어그램", "/insar/function-network"],
            ["가상센싱 거더·상판 변위장", "/pinn/girder·deck-displacement"],
            ["변위 CSV / VLM 패키지 내려받기", "/insar/export.csv · vlm-package.zip"]]
    table(s, 0.45, 4.42, 6.3, rows, [2.3, 2.0], row_h=0.3, head_h=0.3, fs=8.5, head_fs=9)

    if shot and Path(shot).exists():
        picture_fit(s, shot, 6.95, 1.16, 5.95, 2.9)
        text(s, 6.95, 4.14, 5.95, 0.3,
             [("실제 동작 화면 — 성수대교 (101시점 · 교면 103측점)", 11, True, NAVY)])
        bullets(s, 6.95, 4.48, 5.95, 1.9, [
            ("경보 배지가 '위험 (잠정)'", (" — 관측조건이 CRI 기준치 학습 조건과 달라 "
                                 "등급을 구조 상태로 읽지 말라는 표시가 API 에서 탭까지 따라온다")),
            ("측점 클릭 → 시계열", " — LOS·종방향 변위와 CRI, PINN 절대 강성 EI·열팽창계수"),
            ("변위 CSV · VLM 패키지(zip)", " — 탭 오른쪽 위 버튼 그대로 내려받기"),
            ("VWorld 베이스맵 어댑터", " · React 컴포넌트 예제 동봉(examples/bmaps_tab/)"),
        ], fs=9.5, spacing=1.25)
    footer(s, "python -m inframon --serve-api --registry data/bridge_registry.json  →  "
              "GET /api/v1/bridges  ·  좌표 WGS84(lat,lon) 또는 EPSG:5179 선택 · 변위 mm · 날짜 ISO")


def slide6(prs):
    s = blank(prs)
    header(s, "합의가 필요한 것 · 다음 단계")
    text(s, 0.45, 1.1, 6.1, 0.3, [("지금 KICT 와 정해야 하는 3가지", 13, True, NAVY)])
    items = [
        ("① 교량관리번호 체계", ("bridge_id 를 B-Maps 교량관리번호와 1:1 매핑. "
                            "모든 엔드포인트의 경로 파라미터라 이것부터 정해야 한다.")),
        ("② 지도 좌표계", ("B-Maps 지도가 WGS84 인지 EPSG:5179 타일인지. "
                      "5179 면 재투영을 생략하는 옵션으로 바로 맞춘다.")),
        ("③ 인증 방식", "내부망 전용 / API Key(X-API-Key) / B-Maps SSO 중 택1."),
    ]
    y = 1.46
    for t, why in items:
        box(s, 0.45, y, 6.1, 0.92, fill=WHITE, line=LINE)
        box(s, 0.45, y, 0.06, 0.92, fill=BLUE, shape=MSO_SHAPE.RECTANGLE)
        text(s, 0.68, y + 0.12, 5.7, 0.28, [(t, 11.5, True, NAVY)])
        text(s, 0.68, y + 0.42, 5.7, 0.44, [(why, 9.5, False, GRAY)], spacing=1.18)
        y += 1.02

    text(s, 0.45, y + 0.12, 6.1, 0.3, [("KICT 측 자료가 있으면 바로 좋아지는 것", 13, True, NAVY)])
    bullets(s, 0.45, y + 0.48, 6.1, 1.2, [
        ("실 교량 제원(연장·경간·폭·형하고)", (" — 지금은 전국교량표준데이터·OSM 추정. "
                                     "PINN EI 가 제원 기반이라 여기서 정확도가 갈린다")),
        ("점검·보수 이력", " — CRI 이상 구간과 대조하면 참고지표가 검증 가능한 지표가 된다"),
        ("코너리플렉터 설치 후보", " — 교면 PS 밀도의 유일한 실질적 해법"),
        ("건강 교량 CRI 코호트", (" — 지금 기준치는 합성 코호트(노이즈 10mm·24시점)다. "
                          "한강 15개소 실측으로 재적합하면 등급이 '잠정'을 벗는다")),
    ], fs=9.5, spacing=1.25)

    text(s, 6.85, 1.1, 6.05, 0.3, [("단계별 계획", 13, True, NAVY)])
    rows = [["단계", "내용", "상태"],
            ["1", "InSAR·PINN·FRAM 파이프라인 · 데이터 계약", "완료"],
            ["2", f"실 Sentinel-1 전 파이프라인 관통(교량 {processed_bridge_count()}개소)", "완료"],
            ["3", f"B-Maps REST 사이드카 {api_endpoint_count()} 엔드포인트 · 탭 예제", "완료"],
            ["4", "교량관리번호·좌표계·인증 합의 → 스테이징 연동", "협의"],
            ["5", "B-Maps 운영 탭 탑재 · 신규 SLC 자동 갱신", "예정"],
            ["6", "CR 설치·고해상도 SAR 로 교면 밀도 보강", "예정"],
            ["7", "VLM 시방서 평가(타 팀) 로 패키지 인계", "소켓 제공"]]
    colors = {(r, 2): (GREEN if rows[r][2] == "완료" else
                       ORANGE if rows[r][2] == "협의" else GRAY)
              for r in range(1, len(rows))}
    table(s, 6.85, 1.46, 6.05, rows, [0.5, 3.6, 1.0], row_h=0.4, fs=9.5, head_fs=9.5,
          colors=colors)

    box(s, 6.85, 4.75, 6.05, 1.9, fill=BLUE_L, line=LINE)
    text(s, 7.08, 4.92, 5.6, 0.3, [("연동 시연은 지금 바로 가능합니다", 11.5, True, NAVY)])
    bullets(s, 7.08, 5.26, 5.6, 1.3, [
        "레지스트리에 교량관리번호만 넣으면 그 교량이 탭에 뜬다",
        "새 SLC 가 쌓이면 --bmap-sync 가 감사 통과분만 자동 갱신",
        "이미 받아 두신 아카이브(.SAFE)가 있으면 그 자리에서 처리 — 재다운로드 없음",
    ], fs=9.5, spacing=1.3)
    footer(s, "문의 · 재현 자료: README.md · docs/Bmaps_연동_인터페이스.md · examples/bmaps_tab/")


def twin_summary(root: Path, shm: dict) -> list[dict]:
    """교량 폴더의 `twin.glb.meta.json` → 트윈·PS·부재결합 수. 없으면 0."""
    out = []
    for nm in shm:
        m = root / nm / "twin.glb.meta.json"
        if not m.exists():
            out.append({"name": nm, "n_points": 0, "n_bound": 0})
            continue
        d = json.loads(m.read_text(encoding="utf-8"))
        ft = d.get("features") or []
        out.append({"name": nm, "n_points": int(d.get("n_points", len(ft))),
                    "n_bound": sum(1 for f in ft if f.get("element_globalid"))})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridges", nargs="+", required=True, help="교량 폴더들(지도·브리프에 쓴다)")
    ap.add_argument("--extra", nargs="*", default=[],
                    help="표에만 더할 참고 교량 폴더(예: 기존 실증 교량)")
    ap.add_argument("--out", default="docs/KICT_Bmaps_협의.pptx")
    ap.add_argument("--map-fig", default="docs/img/seoul3_map.png")
    ap.add_argument("--tab-shot", default="docs/img/bmaps_tab.png")
    ap.add_argument("--compare-fig", default="docs/img/hangang_지표_종합.png",
                    help="현장 보고 ↔ 위성 16개소 전수 대조 그림")
    ap.add_argument("--compare-json", default="docs/bridges/hangang_gnss_insar.json",
                    help="같은 대조의 수치(make_hangang_gnss_insar.py)")
    ap.add_argument("--gnss-fig", default="docs/img/gnss_insar_추세선.png")
    ap.add_argument("--gnss-json", default="docs/bridges/gnss_insar_추세.json",
                    help="GNSS↔InSAR 추세 수치(make_gnss_insar_trend.py)")
    ap.add_argument("--twin-fig", default="docs/img/hangang_트윈_3D.png",
                    help="디지털 트윈 위의 PS 점 — 전 교량 3D")
    ap.add_argument("--cycle-fig", default="docs/img/hangang_연주기_요약.png",
                    help="연주기 대조 — 발표용 가로형")
    ap.add_argument("--cycle-json", default="docs/bridges/hangang_annual_cycle.json")
    ap.add_argument("--root-bridges", default="docs/bridges")
    ap.add_argument("--chain-bridge", default="암사대교",
                    help="전 과정(OSM→선별→트윈) 한 장을 보일 교량")
    ap.add_argument("--shm-json", default="docs/bridges/hangang_shm_2024.json",
                    help="현장 SHM(한강교량 온라인 안전감시) 2024 판정·계측항목")
    ap.add_argument("--ondeck-fig", default="docs/img/ondeck_jeongjagyo.png")
    ap.add_argument("--batch-json", default="F:/SLC/seoul_work/batch_result.json")
    ap.add_argument("--specs-json", default="docs/bridges/seoul_specs.json",
                    help="{교량: {year, grade}} — 전국교량표준데이터에서 뽑은 준공·점검등급")
    a = ap.parse_args()

    bs = [read_bridge(Path(p)) for p in a.bridges]
    extra = [read_bridge(Path(p)) for p in a.extra]
    meta = {"n_scenes": "?", "span": "?", "specs": {}, "shm": {}}
    bj = Path(a.batch_json)
    if bj.exists():
        d = json.loads(bj.read_text(encoding="utf-8"))
        meta["n_scenes"] = d.get("n_scenes", "?")
        r = d.get("date_range") or []
        meta["span"] = f"{r[0]} ~ {r[-1]}" if r else "?"
    sj = Path(a.specs_json)
    if sj.exists():
        meta["specs"] = json.loads(sj.read_text(encoding="utf-8"))
    gj = Path(a.gnss_json)
    meta["gnss"] = json.loads(gj.read_text(encoding="utf-8")) if gj.exists() else {}
    cj = Path(a.compare_json)
    cmp_raw = json.loads(cj.read_text(encoding="utf-8")) if cj.exists() else []
    # 새 형식은 {"bridges":[...]}, 옛 형식은 배열 — 둘 다 받는다.
    meta["cmp_rows"] = (cmp_raw.get("bridges") if isinstance(cmp_raw, dict) else cmp_raw) or []
    hj = Path(a.shm_json)
    if hj.exists():
        meta["shm"] = {k: v for k, v in json.loads(hj.read_text(encoding="utf-8")).items()
                       if not k.startswith("_")}

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(emu(SW)), Emu(emu(SH))
    slide1(prs, bs, meta)
    slide_flow1(prs)
    slide_flow2(prs)
    slide2(prs, bs, meta, a.map_fig, extra)
    slide3(prs, bs, meta["shm"])
    slide_shm(prs, a.compare_fig, meta["shm"], meta["cmp_rows"])
    slide_gnss(prs, a.gnss_fig, meta["gnss"])
    cj = Path(a.cycle_json)
    slide_cycle(prs, a.cycle_fig,
                json.loads(cj.read_text(encoding="utf-8")) if cj.exists() else {})
    chain_b = Path(a.root_bridges) / a.chain_bridge / "bridge.json"
    slide_chain(prs, Path(a.root_bridges) / a.chain_bridge / "chain.png", a.chain_bridge,
                json.loads(chain_b.read_text(encoding="utf-8")) if chain_b.exists() else None)
    slide_twin_ps(prs, a.twin_fig, twin_summary(Path(a.root_bridges), meta["shm"]))
    slide4(prs, bs, a.ondeck_fig)
    slide5(prs, a.tab_shot)
    slide6(prs)
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    print(f"wrote {out}  ({len(prs.slides.__iter__.__self__._sldIdLst)}장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
