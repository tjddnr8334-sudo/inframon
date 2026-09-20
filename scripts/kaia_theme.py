#!/usr/bin/env python3
"""KAIA 자문회의 자료의 **톤**을 그대로 쓰기 위한 도형·색·글꼴 모듈.

`2026 KAIA 자문회의 ppt_v2.pptx` 에서 실제로 읽어 온 값이다(눈대중이 아니다).
  · 머리띠   y 0 ~ 0.68, 채움 #10171E · 장 번호 22pt 흰색 · 제목 25pt 흰색
  · 소제목   x 0.33, y 0.74, #373D47 굵게 — 그 장에서 실제로 말할 한 줄
  · 본문상자 흰 바탕 + 옅은 테두리, 위 모서리에 #373D47 탭을 물려 제목을 넣는다
  · 바닥띠   x 0 ~ 3.0, y 7.28, 높이 0.20, 채움 #E9EBEE · 쪽번호는 오른쪽
  · 강조색   #1C5B8A(파랑) · #DAECF6(옅은 파랑) · 회색 #F5F7F9 / #E9EBEE
  · 글꼴     Paperlogy 7 Bold / Paperlogy 5 Medium

같은 톤을 그림(matplotlib)에도 쓰려고 색은 16진 문자열로도 같이 둔다. 발표자료와
그림의 색이 다르면 한 장에 붙였을 때 바로 티가 난다.
"""

from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

IN = 914400
SW, SH = 13.333, 7.5

# ── 색 (원본에서 읽은 값) ──────────────────────────────────────────────────
INK_H = "10171E"        # 머리띠
SLATE_H = "373D47"      # 소제목 · 탭
BLUE_H = "1C5B8A"       # 강조
BLUE_P_H = "DAECF6"     # 옅은 파랑 채움
BLUE_F_H = "EDF5F8"     # 더 옅은 파랑
PAPER_H = "F5F7F9"      # 옅은 회색 면
RULE_H = "E9EBEE"       # 바닥띠 · 선
ORANGE_H = "ED6C00"
RED_H = "C0392B"
GREEN_H = "1E7A54"
GRAY_H = "6B7480"
WHITE_H = "FFFFFF"


def C(h: str) -> RGBColor:
    return RGBColor.from_string(h)


INK, SLATE, BLUE, BLUE_P, BLUE_F = C(INK_H), C(SLATE_H), C(BLUE_H), C(BLUE_P_H), C(BLUE_F_H)
PAPER, RULE, ORANGE, RED, GREEN = C(PAPER_H), C(RULE_H), C(ORANGE_H), C(RED_H), C(GREEN_H)
GRAY, WHITE = C(GRAY_H), C(WHITE_H)

FONT_B = "Paperlogy 7 Bold"
FONT_M = "Paperlogy 5 Medium"


def emu(v: float) -> int:
    return round(v * IN)


def _typeface(run, name: str) -> None:
    """라틴·한글·기호를 한 서체로 묶는다.

    `font.name` 만 넣으면 라틴만 바뀌고 한글은 기본 서체로 남는다 — 한 줄 안에서
    서체가 갈라져 보이는 이유가 이것이다.
    """
    run.font.name = name
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", name)


def box(slide, x, y, w, h, *, fill=None, line=None, lw=0.75,
        shape=MSO_SHAPE.RECTANGLE, adj=None):
    s = slide.shapes.add_shape(shape, emu(x), emu(y), emu(w), emu(h))
    if adj is not None:
        try:
            s.adjustments[0] = adj
        except (IndexError, ValueError):
            pass
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(lw)
    s.shadow.inherit = False
    return s


def text(slide, x, y, w, h, runs, *, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
         spacing=1.0, wrap=True):
    """runs: [(문자열, pt, 굵게, 색)] 또는 문단 여러 개를 리스트의 리스트로."""
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
            r = p.add_run()
            r.text = t
            r.font.size = Pt(sz)
            r.font.bold = bool(bold)
            r.font.color.rgb = col
            _typeface(r, FONT_B if bold else FONT_M)
    return tb


_seq = {"n": 0}


def reset_pages() -> None:
    _seq["n"] = 0


def header(slide, title: str, sub: str = "", tag: str = "") -> int:
    """머리띠 + 장 번호 + 제목, 그리고 그 아래 소제목 한 줄. 쪽번호를 돌려준다."""
    _seq["n"] += 1
    no = _seq["n"]
    box(slide, 0, 0, SW, 0.68, fill=INK)
    box(slide, 0, 0.645, SW, 0.035, fill=SLATE)
    text(slide, 0.06, 0.15, 0.6, 0.4, [(f"{no:02d}", 22, True, WHITE)],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    box(slide, 0.62, 0.17, 0.02, 0.35, fill=C("4A5361"))
    text(slide, 0.78, 0.14, 9.4, 0.51, [(title, 21, True, WHITE)],
         anchor=MSO_ANCHOR.MIDDLE)
    if tag:
        box(slide, 10.6, 0.15, 2.6, 0.38, fill=C("1B2530"))
        text(slide, 10.6, 0.15, 2.6, 0.38, [(tag, 10.5, False, C("A9B6C4"))],
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    if sub:
        box(slide, 0.33, 0.83, 0.055, 0.3, fill=BLUE)
        text(slide, 0.48, 0.79, 12.4, 0.38, [(sub, 14.5, True, SLATE)],
             anchor=MSO_ANCHOR.MIDDLE)
    return no


def footer(slide, note: str = "", left: str = "2026 KAIA 자문회의") -> None:
    """바닥띠 — 원본과 같은 자리·같은 크기. 근거 각주는 그 위에 작게."""
    if note:
        text(slide, 0.45, 6.95, SW - 0.9, 0.3, [(note, 8.5, False, GRAY)],
             spacing=1.18)
    box(slide, 0, 7.28, 3.0, 0.22, fill=RULE)
    text(slide, 0.12, 7.28, 2.8, 0.22, [(left, 10, True, SLATE)],
         anchor=MSO_ANCHOR.MIDDLE)
    box(slide, 0, 7.455, SW, 0.045, fill=INK)
    text(slide, 10.33, 7.26, 2.9, 0.22,
         [(f"{_seq['n']}", 10, True, SLATE)],
         align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)


def panel(slide, x, y, w, h, title: str = "", *, fill=WHITE, tab=SLATE,
          tab_w: float | None = None, title_fs: float = 12.5):
    """흰 상자 + 위 모서리에 물린 탭 — 원본의 본문 상자 형태 그대로."""
    box(slide, x, y, w, h, fill=fill, line=RULE, lw=1.0)
    if title:
        tw = tab_w if tab_w is not None else min(w - 0.5, 0.3 + len(title) * 0.135)
        tx = x + (w - tw) / 2
        box(slide, tx, y - 0.155, tw, 0.31, fill=tab)
        text(slide, tx, y - 0.155, tw, 0.31, [(title, title_fs, True, WHITE)],
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    return y + 0.28 if title else y + 0.14


def bullets(slide, x, y, w, h, items, *, fs=10.5, mark="·", color=SLATE,
            spacing=1.34):
    paras = []
    for it in items:
        if isinstance(it, tuple):
            head, tail = it
            paras.append([(f"{mark} ", fs, True, BLUE), (head, fs, True, color),
                          (tail, fs, False, color)])
        else:
            paras.append([(f"{mark} ", fs, True, BLUE), (it, fs, False, color)])
    return text(slide, x, y, w, h, paras, spacing=spacing)


def table(slide, x, y, w, rows, col_w, *, head_h=0.34, row_h=0.33, fs=10.5,
          head_fs=10.5, colors=None):
    colors = colors or {}
    total = sum(col_w)
    col_w = [c / total * w for c in col_w]
    yy = y
    for ri, row in enumerate(rows):
        h = head_h if ri == 0 else row_h
        if ri == 0:
            box(slide, x, yy, w, h, fill=SLATE)
        elif ri % 2 == 0:
            box(slide, x, yy, w, h, fill=PAPER)
        xx = x
        for ci, cell in enumerate(row):
            col = WHITE if ri == 0 else colors.get((ri, ci), SLATE)
            text(slide, xx + 0.09, yy, col_w[ci] - 0.18, h,
                 [(str(cell), head_fs if ri == 0 else fs, ri == 0 or ci == 0, col)],
                 align=PP_ALIGN.CENTER if ci else PP_ALIGN.LEFT,
                 anchor=MSO_ANCHOR.MIDDLE)
            xx += col_w[ci]
        if ri:
            box(slide, x, yy + h, w, 0.008, fill=RULE)
        yy += h
    return yy


def kpi(slide, x, y, w, h, big, unit, label, *, color=BLUE):
    box(slide, x, y, w, h, fill=WHITE, line=RULE, lw=1.0)
    box(slide, x, y, w, 0.05, fill=color)
    text(slide, x + 0.2, y + 0.18, w - 0.35, 0.46,
         [(big, 23, True, color), ("  " + unit, 10.5, False, GRAY)],
         anchor=MSO_ANCHOR.MIDDLE)
    text(slide, x + 0.2, y + 0.66, w - 0.35, 0.5,
         [(label, 9.8, False, SLATE)], spacing=1.18)


def chevrons(slide, x, y, w, h, items, *, fs=10, sub_fs=8.2):
    n = len(items)
    gap = 0.07
    cw = (w - gap * (n - 1)) / n
    for i, (t, sub) in enumerate(items):
        xx = x + i * (cw + gap)
        shape = MSO_SHAPE.PENTAGON if i == 0 else MSO_SHAPE.CHEVRON
        s = box(slide, xx, y, cw, h, fill=(BLUE if i % 2 == 0 else SLATE),
                shape=shape)
        s.line.fill.background()
        tip = h * 0.5
        paras = [[(t, fs, True, WHITE)]]
        paras += [[(ln, sub_fs, False, C("CFE0EC"))] for ln in sub.splitlines()]
        text(slide, xx + (tip if i else 0.13), y + 0.05,
             cw - tip * (2.0 if i else 1.1), h - 0.10, paras,
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, spacing=1.0)


def band(slide, y, txt, *, fill=BLUE_F, color=SLATE, h=0.62, fs=12, bold=True):
    """그 장에서 실제로 말할 결론 한 줄."""
    box(slide, 0.45, y, SW - 0.9, h, fill=fill)
    box(slide, 0.45, y, 0.055, h, fill=BLUE)
    text(slide, 0.72, y, SW - 1.44, h, [(txt, fs, bold, color)],
         anchor=MSO_ANCHOR.MIDDLE)


def picture(slide, path, x, y, w=None, h=None):
    kw = {}
    if w is not None:
        kw["width"] = Emu(emu(w))
    if h is not None:
        kw["height"] = Emu(emu(h))
    return slide.shapes.add_picture(str(path), Emu(emu(x)), Emu(emu(y)), **kw)


def picture_fit(slide, path, x, y, w, h):
    from PIL import Image

    with Image.open(path) as im:
        iw, ih = im.size
    scale = min(w / iw, h / ih)
    pw, ph = iw * scale, ih * scale
    return picture(slide, path, x + (w - pw) / 2, y + (h - ph) / 2, w=pw)


def blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


# ── 그림(matplotlib)도 같은 톤으로 ─────────────────────────────────────────
MPL = {"ink": "#" + INK_H, "slate": "#" + SLATE_H, "blue": "#" + BLUE_H,
       "blue_pale": "#" + BLUE_P_H, "paper": "#" + PAPER_H,
       "rule": "#" + RULE_H, "orange": "#" + ORANGE_H, "red": "#" + RED_H,
       "green": "#" + GREEN_H, "gray": "#" + GRAY_H}


def use_mpl_style() -> None:
    """그림을 발표자료와 같은 서체·색으로 — 없으면 조용히 맑은 고딕으로 물러선다."""
    from matplotlib import font_manager, rcParams

    # 'Paperlogy 5 Medium' 을 그대로 쓰면 굵게가 없어 400 으로 되돌아간다. 기본
    # 가족명 'Paperlogy' 는 Regular/Bold 가 짝지어져 있어 굵게가 제대로 나온다.
    # 다만 Paperlogy 에 위첨자 ²(U+00B2) 가 없어 'R²' 가 깨진다 — 뒤에 Pretendard 를
    # 세워 글자 단위로 물려받게 한다(matplotlib 3.6+ 의 글꼴 폴백).
    have = {f.name for f in font_manager.fontManager.ttflist}
    # Noto Sans KR 은 이 환경에 가변 글꼴 하나로만 깔려 있어 matplotlib 이 굵기를
    # 못 고르고 경고를 쏟는다. Pretendard 가 이미 빈칸을 메우므로 사슬에서 뺀다.
    chain = [f for f in ("Paperlogy", "Pretendard",
                         "Malgun Gothic", "맑은 고딕") if f in have]
    if chain:
        rcParams["font.family"] = chain
    rcParams["axes.unicode_minus"] = False
    rcParams["axes.edgecolor"] = MPL["rule"]
    rcParams["axes.labelcolor"] = MPL["slate"]
    rcParams["text.color"] = MPL["slate"]
    rcParams["xtick.color"] = MPL["gray"]
    rcParams["ytick.color"] = MPL["gray"]
    rcParams["grid.color"] = MPL["rule"]
