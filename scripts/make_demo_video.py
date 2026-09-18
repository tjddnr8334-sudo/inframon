#!/usr/bin/env python3
"""inframon 구동 순서 영상(.mp4) — 산출물에서 직접 만든다.

발표에서 "이 프로그램이 어떤 순서로 도는가" 를 말로만 하면 안 남는다. 이 스크립트는
`bridge_run.py` 가 **실제로 찍는 단계**(⓪ SLC→InSAR · ① 제원 … ⑩ 결과 문서)를 그대로
따라가며, 각 단계가 만든 그림을 교량 폴더에서 그대로 읽어 영상으로 잇는다. 그림을
발표용으로 다시 그리지 않는다 — 다시 돌리면 그날의 산출물로 갱신된다.

    python scripts/make_demo_video.py                      # 기본(암사대교)
    python scripts/make_demo_video.py --bridge docs/bridges/성수대교
    python scripts/make_demo_video.py --scale 0.5 --preview # 빠른 확인(960x540)

산출:
    docs/video/inframon_구동순서.mp4   자막을 화면에 구운 영상(발표용 · 그대로 재생)
    docs/video/inframon_구동순서.srt   같은 자막의 편집용 파일(유튜브·곰플레이어 등)
    docs/video/inframon_구동순서.txt   자막 원문(대본 — 발표 원고로 쓴다)

자막을 고치려면 이 파일의 SUBS 문자열만 고치면 된다. 영상과 .srt 가 같이 바뀐다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── 판형 ─────────────────────────────────────────────────────────────────────
W, H = 1920, 1080
FPS = 24

BG = (13, 24, 38)          # 바탕(짙은 남색)
PANEL = (21, 38, 58)       # 카드
PANEL_L = (28, 49, 73)     # 카드(밝은)
LINE = (44, 72, 103)       # 경계선
INK = (232, 240, 248)      # 본문
DIM = (146, 168, 190)      # 보조
BLUE = (78, 161, 247)
GREEN = (53, 192, 138)
ORANGE = (242, 153, 74)
RED = (235, 87, 87)
SUB_BG = (8, 15, 25)

# mono 는 **굴림체**(gulim.ttc index 1) — 한글이 있는 고정폭이다. Consolas 는 고정폭이지만
# 한글 글리프가 없어 로그가 통째로 네모가 된다. 반대로 원문자 ⓪(U+24EA)는 한글 글꼴에
# 없고 Consolas 에만 있어서, 단계 번호만 "circ" 로 따로 찍는다.
FONTS = {
    "bold": ("C:/Windows/Fonts/malgunbd.ttf", 0),
    "reg": ("C:/Windows/Fonts/malgun.ttf", 0),
    "mono": ("C:/Windows/Fonts/gulim.ttc", 1),
    "circ": ("C:/Windows/Fonts/consola.ttf", 0),
}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    if key not in _font.cache:
        p, idx = FONTS[kind]
        if not Path(p).exists():                     # 윈도우가 아니면 기본 글꼴
            _font.cache[key] = ImageFont.load_default()
        else:
            _font.cache[key] = ImageFont.truetype(p, size, index=idx)
    return _font.cache[key]


_font.cache = {}


# ── 그리기 도우미 ────────────────────────────────────────────────────────────
def text(d, xy, s, *, kind="reg", size=28, fill=INK, anchor="la"):
    d.text(xy, s, font=_font(kind, size), fill=fill, anchor=anchor)


def wrap(s: str, font, width: int) -> list[str]:
    """한국어는 단어 경계가 성기다 — 글자 단위로 재되, 공백에서 끊는다."""
    out, line = [], ""
    for ch in s:
        if ch == "\n":
            out.append(line); line = ""; continue
        trial = line + ch
        if font.getlength(trial) > width and line:
            cut = trial.rfind(" ")
            if cut > len(line) * 0.55:
                out.append(trial[:cut]); line = trial[cut + 1:]
            else:
                out.append(line); line = ch
        else:
            line = trial
    if line:
        out.append(line)
    return out


def card(d, box, *, fill=PANEL, outline=LINE, r=14):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=2)


def fit_image(img: Image.Image, box) -> tuple[Image.Image, tuple[int, int]]:
    """상자 안에 비율을 지켜 넣는다 — 잘라내지 않는다(그림이 근거라서)."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    s = min(bw / img.width, bh / img.height)
    w, h = max(1, int(img.width * s)), max(1, int(img.height * s))
    return img.resize((w, h), Image.LANCZOS), (x0 + (bw - w) // 2, y0 + (bh - h) // 2)


_img_cache: dict[str, Image.Image] = {}


def load(p: str | Path) -> Image.Image | None:
    p = str(p)
    if p not in _img_cache:
        try:
            im = Image.open(p).convert("RGB")
        except Exception:
            im = None
        _img_cache[p] = im
    return _img_cache[p]


# ── 장면 ─────────────────────────────────────────────────────────────────────
@dataclass
class Scene:
    dur: float
    bg: object                      # (img, draw) -> None
    cues: list = field(default_factory=list)   # [(t0, t1, "자막")]
    dyn: object = None              # (img, draw, t) -> None
    chapter: int | None = None      # 상단 레일에서 밝힐 단계 번호


STEPS = [
    ("⓪", "SLC → InSAR"),
    ("①", "제원"),
    ("②", "데크선"),
    ("③", "지면"),
    ("④", "점 선택"),
    ("⑤", "잔차고도"),
    ("⑥", "IFC 트윈"),
    ("⑦", "PINN·CRI"),
    ("⑧", "브리프"),
    ("⑨", "감사"),
    ("⑩", "결과 문서"),
]


def rail(d, active: int | None):
    """상단 단계 레일 — 지금 어디를 도는지 영상 내내 보인다."""
    d.rectangle((0, 0, W, 86), fill=(9, 18, 30))
    d.line((0, 86, W, 86), fill=LINE, width=2)
    x = 46
    for i, (num, name) in enumerate(STEPS):
        on = active is not None and i == active
        done = active is not None and i < active
        col = BLUE if on else (GREEN if done else (70, 92, 116))
        sz = 25 if on else 22
        f = _font("bold" if on else "reg", sz)
        fc = _font("circ", sz) if num == "⓪" else f
        wn, ws = fc.getlength(num + " "), f.getlength(name)
        if on:
            d.rounded_rectangle((x - 12, 22, x + wn + ws + 12, 64), radius=10,
                                fill=(26, 56, 92), outline=BLUE, width=2)
        d.text((x, 43), num, font=fc, fill=col, anchor="lm")
        d.text((x + wn, 43), name, font=f, fill=col, anchor="lm")
        x += wn + ws + 34
    return x


def progress(d, frac: float):
    d.rectangle((0, H - 6, W, H), fill=(20, 33, 50))
    d.rectangle((0, H - 6, int(W * frac), H), fill=BLUE)


def subtitle(d, s: str | None):
    if not s:
        return
    f = _font("bold", 36)
    lines = wrap(s, f, W - 320)[:2]
    h = 26 + 48 * len(lines)
    y0 = H - 40 - h
    d.rectangle((0, y0 - 8, W, H - 26), fill=SUB_BG)
    d.line((0, y0 - 8, W, y0 - 8), fill=(30, 52, 78), width=2)
    y = y0 + 12
    for ln in lines:
        d.text((W // 2, y), ln, font=f, fill=(255, 255, 255), anchor="ma")
        y += 48


BODY = (48, 118, W - 48, 862)      # 자막 위까지가 본문


def body_split(left_frac=0.42):
    x0, y0, x1, y1 = BODY
    xm = int(x0 + (x1 - x0) * left_frac)
    return (x0, y0, xm - 18, y1), (xm + 18, y0, x1, y1)


# ── 장면 만들기 ──────────────────────────────────────────────────────────────
def sc_title(meta: dict) -> Scene:
    def bg(img, d):
        d.rectangle((0, 0, W, H), fill=BG)
        for i, r in enumerate(range(0, 420, 14)):    # 옅은 궤도 곡선(장식)
            d.arc((W - 700 - r, -240 - r, W + 240 + r, 620 + r),
                  200, 340, fill=(18, 34, 54) if i % 2 else (22, 42, 66), width=3)
        text(d, (120, 300), "inframon", kind="bold", size=104, fill=INK)
        text(d, (120, 430), "좌표 하나로, 위성에서 교량까지", kind="bold", size=52, fill=BLUE)
        text(d, (120, 520),
             "Sentinel-1 SLC → InSAR → 교면 점 → PINN → IFC 디지털 트윈 → B-Maps",
             size=32, fill=DIM)
        d.line((120, 596, 1180, 596), fill=LINE, width=2)
        text(d, (120, 626),
             f"이 영상은 실제 산출물로 만들었습니다 — {meta['bridge']} 폴더의 그림 그대로",
             size=28, fill=DIM)
        text(d, (120, 674),
             f"Sentinel-1 ASC path127 · {meta['scenes']}장면 · {meta['span']}",
             size=28, fill=DIM)
        text(d, (120, H - 150), "※ 연구용 프로토타입 — 실무 안전판정이 아닙니다",
             size=26, fill=ORANGE)
    return Scene(7.0, bg, [
        (0.3, 3.4, "inframon 은 좌표 하나만 주면 위성 원자료부터 디지털 트윈까지 스스로 갑니다."),
        (3.6, 6.7, "이 영상은 프로그램이 실제로 도는 순서를 그대로 따라갑니다."),
    ])


CMD = "python scripts/bridge_run.py --name 암사대교 --lat 37.569152 --lon 127.131795"

LOG = [
    ("━━ 암사대교 (37.569152, 127.131795) → docs\\bridges\\암사대교", BLUE),
    ("  ① 제원", INK),
    ("  ② 데크선", INK),
    ("      · 닫힌 way → 주축 양 끝 절단 · 한쪽 차도 26점", DIM),
    ("  ③ 지면", INK),
    ("  ④ 점 선택", INK),
    ("      · 쉬프트 32.4 m(heading -13.3°) 보정 후 데크 ±30 m 안 237/20000", DIM),
    ("  ⑤ 잔차고도", INK),
    ("      · 잔차고도 집단평균 +8.5±2.6 m (z=3.33)", DIM),
    ("  ⑥ IFC 트윈", INK),
    ("      부재 77 · 점 237 · 결합 237", GREEN),
    ("  ⑦ PINN·CRI", INK),
    ("      CRI 0.809 · 경고", ORANGE),
    ("  ⑧ 브리프", INK),
    ("  ⑨ 감사", INK),
    ("      조건부", ORANGE),
    ("  ⑩ 결과 문서", INK),
]


def sc_cmd() -> Scene:
    dur, t_type, t_log0 = 19.0, 3.2, 4.2
    per = (dur - t_log0 - 1.2) / len(LOG)

    def bg(img, d):
        d.rectangle((0, 0, W, H), fill=BG)
        rail(d, None)
        text(d, (48, 112), "실행은 한 줄 — 교량을 갈아 끼우는 데 코드 수정이 없다",
             kind="bold", size=40, fill=INK)
        card(d, (48, 176, W - 48, 862), fill=(10, 20, 33))
        d.rounded_rectangle((48, 176, W - 48, 232), radius=14, fill=(18, 33, 51))
        for i, c in enumerate([(235, 96, 88), (240, 190, 80), (110, 200, 120)]):
            d.ellipse((76 + i * 30, 196, 92 + i * 30, 212), fill=c)
        text(d, (150, 204), "명령 프롬프트 — E:\\프로그램", kind="mono", size=22,
             fill=DIM, anchor="lm")

    def dyn(img, d, t):
        y = 268
        text(d, (80, y), "> ", kind="mono", size=26, fill=GREEN)
        n = len(CMD) if t > t_type else int(len(CMD) * max(t - 0.4, 0) / (t_type - 0.4))
        text(d, (110, y), CMD[:n], kind="mono", size=26, fill=INK)
        if t < t_type and int(t * 3) % 2 == 0:
            xx = 110 + _font("mono", 26).getlength(CMD[:n])
            d.rectangle((xx + 2, y + 2, xx + 13, y + 30), fill=INK)
        y = 330
        for i, (ln, col) in enumerate(LOG):
            if t < t_log0 + i * per:
                break
            text(d, (110, y), ln, kind="mono", size=25, fill=col)
            y += 31

    return Scene(dur, bg, [
        (0.2, 3.9, "명령은 한 줄입니다. 교량 이름과 위경도만 바꾸면 됩니다."),
        (4.1, 8.0, "프로그램은 열한 단계를 차례로 밟으며 각 단계를 화면에 찍습니다."),
        (8.2, 12.5, "괄호 안 줄은 그때그때의 판단입니다 — 무엇을 왜 그렇게 정했는지 남깁니다."),
        (12.7, 18.7, "여기부터 이 열한 단계를 하나씩 보겠습니다."),
    ], dyn=dyn)


def sc_step(i: int, *, head: str, bullets: list[str], out: str,
            image: str | None, cues: list, dur: float,
            note: str | None = None, note_col=DIM) -> Scene:
    num, name = STEPS[i]

    def bg(img, d):
        d.rectangle((0, 0, W, H), fill=BG)
        rail(d, i)
        L, R = body_split(0.40 if image else 0.52)
        fc = _font("circ", 54) if num == "⓪" else _font("bold", 54)
        d.text((L[0], L[1] + 6), num, font=fc, fill=BLUE)
        text(d, (L[0] + fc.getlength(num + " "), L[1] + 6), name,
             kind="bold", size=54, fill=BLUE)
        y = L[1] + 86
        for ln in wrap(head, _font("bold", 32), L[2] - L[0]):
            text(d, (L[0], y), ln, kind="bold", size=32, fill=INK); y += 44
        y += 14
        for b in bullets:
            d.ellipse((L[0] + 4, y + 13, L[0] + 13, y + 22), fill=BLUE)
            for k, ln in enumerate(wrap(b, _font("reg", 27), L[2] - L[0] - 30)):
                text(d, (L[0] + 28, y), ln, size=27, fill=(INK if k == 0 else DIM))
                y += 38
            y += 10
        if note:
            card(d, (L[0], y + 6, L[2], y + 6 + 46 + 38 *
                     max(0, len(wrap(note, _font("reg", 25), L[2] - L[0] - 40)) - 1)),
                 fill=PANEL_L)
            yy = y + 20
            for ln in wrap(note, _font("reg", 25), L[2] - L[0] - 40):
                text(d, (L[0] + 20, yy), ln, size=25, fill=note_col); yy += 38
        # 산출물
        text(d, (L[0], L[3] - 44), "산출", kind="bold", size=24, fill=DIM)
        text(d, (L[0] + 66, L[3] - 44), out, kind="mono", size=24, fill=GREEN)
        if image:
            im = load(image)
            if im is not None:
                card(d, R, fill=(245, 247, 250), outline=LINE)
                sm, pos = fit_image(im, (R[0] + 10, R[1] + 10, R[2] - 10, R[3] - 40))
                img.paste(sm, pos)
                text(d, ((R[0] + R[2]) // 2, R[3] - 28),
                     Path(image).as_posix().split("/")[-1] + "  (산출물 그대로)",
                     kind="mono", size=21, fill=DIM, anchor="ma")
            else:
                card(d, R, fill=PANEL)
                text(d, ((R[0] + R[2]) // 2, (R[1] + R[3]) // 2),
                     "(그림 없음)", size=28, fill=DIM, anchor="mm")
    return Scene(dur, bg, cues, chapter=i)


def sc_full(img_path: str, title: str, sub: str, cues: list, dur: float,
            foot: str) -> Scene:
    def bg(img, d):
        d.rectangle((0, 0, W, H), fill=BG)
        rail(d, None)
        text(d, (48, 104), title, kind="bold", size=40, fill=INK)
        text(d, (48, 154), sub, size=27, fill=DIM)
        box = (48, 196, W - 48, 838)
        im = load(img_path)
        if im is not None:
            card(d, box, fill=(245, 247, 250))
            sm, pos = fit_image(im, (box[0] + 10, box[1] + 10, box[2] - 10, box[3] - 10))
            img.paste(sm, pos)
        text(d, (48, 848), foot, kind="mono", size=22, fill=DIM)
    return Scene(dur, bg, cues)


def sc_result(md_lines: list[tuple[str, tuple]], cues: list, dur: float) -> Scene:
    def bg(img, d):
        d.rectangle((0, 0, W, H), fill=BG)
        rail(d, 10)
        fc = _font("circ", 40)
        d.text((48, 104), "⑩", font=fc, fill=INK)
        text(d, (48 + fc.getlength("⑩  "), 104),
             "결과 문서 — 숫자마다 출처를 적는다", kind="bold", size=40, fill=INK)
        text(d, (48, 154), "docs/bridges/암사대교/결과.md · 손으로 옮겨 적은 숫자가 없다",
             size=27, fill=DIM)
        card(d, (48, 196, W - 48, 846), fill=(10, 20, 33))
        y = 226
        for ln, col in md_lines:
            text(d, (84, y), ln, kind="mono", size=25, fill=col)
            y += 34
    return Scene(dur, bg, cues, chapter=10)


def sc_end(meta: dict) -> Scene:
    def bg(img, d):
        d.rectangle((0, 0, W, H), fill=BG)
        text(d, (120, 190), "다시 돌리면 그날의 산출물로 갱신된다",
             kind="bold", size=58, fill=INK)
        card(d, (120, 300, W - 120, 470), fill=(10, 20, 33))
        text(d, (160, 330), "한 교량", kind="bold", size=26, fill=DIM)
        text(d, (160, 372), "python scripts/bridge_run.py --name <교량> --lat <위도> --lon <경도>",
             kind="mono", size=27, fill=GREEN)
        text(d, (160, 416), "여러 교량", kind="bold", size=26, fill=DIM)
        text(d, (300, 416), "python scripts/bridge_run.py --batch docs/bridges/hangang16_batch.json",
             kind="mono", size=27, fill=GREEN)
        rows = [
            (f"{meta['n_bridges']} 개소", "실 SLC 로 관통한 교량(한강 16개소 포함)"),
            (f"{meta['n_points']} 점", "교면에 결합된 PS/DS 측점"),
            (f"{meta['agree']}", "현장 안전감시 보고서 판정과 일치한 교량"),
        ]
        x = 120
        for big, small in rows:
            card(d, (x, 510, x + 540, 660), fill=PANEL)
            text(d, (x + 30, 536), big, kind="bold", size=52, fill=BLUE)
            for k, ln in enumerate(wrap(small, _font("reg", 25), 480)):
                text(d, (x + 30, 604 + k * 32), ln, size=25, fill=DIM)
            x += 570
        text(d, (120, 706), "※ 연구용 프로토타입 — 전 파이프라인·해석해 검증 완료, "
                            "현장·상용FEM·실 붕괴라벨 검증 미수행.", size=27, fill=ORANGE)
        text(d, (120, 746), "   산출은 파이프라인 결과이며 실무 안전판정이 아닙니다.",
             size=27, fill=ORANGE)
    return Scene(9.0, bg, [
        (0.3, 4.2, "교량을 갈아 끼우는 데 코드 수정은 없습니다. 명령 한 줄이면 됩니다."),
        (4.4, 8.8, "다만 연구용 프로토타입입니다. 산출을 실무 안전판정으로 읽으면 안 됩니다."),
    ])


# ── 대본 ─────────────────────────────────────────────────────────────────────
def build_scenes(bd: Path, meta: dict) -> list[Scene]:
    b = bd.as_posix()
    S: list[Scene] = [sc_title(meta), sc_cmd()]

    S.append(sc_step(
        0, head="트랙이 없으면 여기서 만든다",
        bullets=["교량 ROI 로 Sentinel-1 SLC 를 골라 받는다 — 같은 궤도·같은 프레임만",
                 "SNAP 으로 코레지스트레이션·간섭도, snaphu 로 위상을 푼다",
                 "이미 받아 둔 기관 아카이브(.SAFE)면 그 자리에서 읽는다 — 재다운로드 없음"],
        out="track_<교량>.h5", image="docs/img/velocity_map.png",
        dur=11.0, cues=[
            (0.2, 4.4, "영 단계. 쓸 수 있는 트랙이 없으면 위성 원자료부터 만듭니다."),
            (4.6, 7.8, "같은 궤도, 같은 프레임의 장면만 골라 간섭도를 만들고 위상을 풉니다."),
            (8.0, 10.8, "이미 받아 둔 자료가 있으면 다시 받지 않고 그 자리에서 읽습니다."),
        ]))

    S.append(sc_step(
        1, head="제원은 지어내지 않는다 — 출처를 적는다",
        bullets=["파트너 실측 CSV · 전국교량표준데이터에서 연장·폭·경간·준공연도를 찾는다",
                 "이름이 비슷한 램프·접속교가 아니라 본교를 고른다",
                 "없으면 없다고 적고 규칙으로 가정한 값임을 남긴다"],
        out="bridge.json · 결과.md '무엇을 어디서 가져왔나'", image=None,
        note="암사대교 — CSV '구리암사대교' 576 m · 형식 아치교 → arch · "
             "경간수 없음 → 30 m 규칙으로 38 가정",
        dur=10.0, cues=[
            (0.2, 4.0, "일 단계. 제원은 실측 자료에서 찾고, 어디서 가져왔는지 함께 적습니다."),
            (4.2, 6.8, "이름이 비슷한 램프나 접속교가 아니라 본교를 고릅니다."),
            (7.0, 9.8, "없는 값은 없다고 적습니다. 가정한 값은 가정이라고 남깁니다."),
        ]))

    S.append(sc_step(
        2, head="OSM 에서 교면 중심선을 뽑는다",
        bullets=["이름과 연장이 함께 맞는 way 를 고른다 — 토막나 있으면 잇는다",
                 "양방향 차도를 도는 닫힌 선이면 주축 양 끝으로 잘라 한쪽 차도만 남긴다",
                 "굽은 접속램프는 잘라낸다 — 본교의 방위가 흐려지면 뒤 계산이 전부 흔들린다"],
        out="deck_polyline.json · 데크 방위", image=f"{b}/chain.png",
        dur=11.0, cues=[
            (0.2, 4.2, "둘째 단계. 오픈스트리트맵에서 교면 중심선을 뽑습니다."),
            (4.4, 8.0, "닫힌 선이면 한쪽 차도만 남기고, 굽은 접속램프는 잘라냅니다."),
            (8.2, 10.8, "왼쪽 위 그림의 회색 선이 그렇게 정리한 교면 중심선입니다."),
        ]))

    S.append(sc_step(
        3, head="지면 높이를 기준으로 잡는다",
        bullets=["Open-Meteo DEM 으로 교량 주변 지면 고도를 읽는다",
                 "처리 오프셋이 있는지 본다 — 도로선 양쪽 지면 점이 한쪽으로 치우쳤는가",
                 "유의하면 빼고, 아니면 산란체 위치(보도·난간)로 본다 — 지어서 빼지 않는다"],
        out="ground · ground_offset", image=None,
        note="암사대교 — 처리 오프셋 ≈ 0 (|B| 0.0 m < 3 m · 지면 점 50 · 도로 양쪽 대칭)",
        dur=9.5, cues=[
            (0.2, 3.8, "삼 단계. 높이의 기준이 되는 지면을 잡습니다."),
            (4.0, 9.3, "처리 과정에서 생긴 치우침이 있는지 보고, 유의할 때만 뺍니다. "
                       "근거 없이 빼면 뒤 숫자가 전부 거짓이 됩니다."),
        ]))

    S.append(sc_step(
        4, head="위성 점을 교면 위로 되돌려 고른다",
        bullets=["레이더는 높은 것을 옆으로 밀어 찍는다 — 형하고만큼 거리방향으로 밀린다",
                 "밀린 양(δh/tanθ)을 되돌린 뒤 교면 중심선에서 ±30 m 안만 남긴다",
                 "정밀도 기준에 못 미치는 점은 뺀다 — 뺀 점도 그림에 ×로 남긴다"],
        out="교면 결합 측점 · project.h5", image=f"{b}/chain.png",
        note="암사대교 — 쉬프트 32.4 m(heading -13.3°) 보정 후 237/20000 점 채택",
        dur=12.0, cues=[
            (0.2, 4.6, "사 단계. 레이더는 높은 구조물을 옆으로 밀어 찍습니다."),
            (4.8, 8.6, "밀린 만큼 되돌린 다음, 교면 중심선에서 삼십 미터 안쪽만 남깁니다."),
            (8.8, 11.8, "이만 개 점 중 이백삼십칠 개가 암사대교 교면 위 점입니다."),
        ]))

    S.append(sc_step(
        5, head="교면 위 점이 맞는지 스스로 검사한다",
        bullets=["교면 위 점의 잔차고도가 주변 지면 점보다 유의하게 높아야 한다(z>2)",
                 "낮게 나오면 경고를 찍는다 — 제방·교대 지면을 교면으로 착각했을 수 있다",
                 "이 관문을 통과하지 못하면 뒤 숫자를 믿으면 안 된다"],
        out="잔차고도 z 검정 · 결과.md 경고", image=None,
        note="암사대교 — 교면 위 - 밖 +8.5 ± 2.6 m (z=3.33) · 통과",
        note_col=GREEN,
        dur=10.5, cues=[
            (0.2, 4.4, "오 단계. 고른 점이 정말 다리 위 점인지 프로그램이 스스로 검사합니다."),
            (4.6, 8.0, "다리 위 점은 주변 지면보다 확실히 높아야 합니다."),
            (8.2, 10.3, "이 관문을 넘지 못하면 뒤 숫자를 믿으면 안 됩니다."),
        ]))

    S.append(sc_step(
        6, head="IFC 디지털 트윈을 세우고 점을 부재에 묶는다",
        bullets=["제원대로 교대·교각·슬래브를 세운다 — 사장교면 주탑·케이블까지",
                 "경간 배치는 등간격 또는 실측 주경간 중에서 고른다(--span-layout)",
                 "점마다 가장 가까운 부재를 찾아 GlobalId 로 묶는다 — 부재별 집계가 된다"],
        out="<교량>_proxy.ifc · twin.glb · twin.viewer.html", image=f"{b}/twin_ps.png",
        note="암사대교 — 부재 77 · 점 237 · 결합 237 (100 %)", note_col=GREEN,
        dur=12.0, cues=[
            (0.2, 4.4, "육 단계. 제원대로 디지털 트윈을 세웁니다."),
            (4.6, 8.4, "그리고 위성 점을 가장 가까운 부재에 묶습니다. 아이에프씨 아이디로요."),
            (8.6, 11.8, "이제 '어느 교각 위 점이 얼마나 움직였나' 를 말할 수 있습니다."),
        ]))

    S.append(sc_step(
        7, head="물리식을 지키는 신경망으로 역산한다",
        bullets=["열·하중·침하·이상 성분을 나눈다 — 데이터만 맞추지 않고 지배방정식을 함께 푼다",
                 "센서가 없는 지점의 거동을 가상센싱으로 채운다",
                 "공진위험지수(CRI)로 4단계 경보를 낸다"],
        out="PINN 결과 · CRI", image="docs/img/dashboard_pinn.png",
        note="암사대교 — CRI 0.809 · 경고 (등급은 잠정 — 아래 ⑨ 감사 참조)",
        note_col=ORANGE,
        dur=11.0, cues=[
            (0.2, 4.2, "칠 단계. 물리식을 함께 푸는 신경망으로 거동을 나눕니다."),
            (4.4, 7.8, "열에 의한 것, 하중에 의한 것, 침하, 그리고 설명되지 않는 이상 성분."),
            (8.0, 10.8, "그 결과로 공진위험지수와 네 단계 경보를 냅니다."),
        ]))

    S.append(sc_step(
        8, head="건기연 브리프 — 네 단으로 한 장에 담는다",
        bullets=["(a) 점을 교량 위에  (b) 교면 위가 맞나  (c) 어디가 얼마나  (d) 시간에 따라",
                 "판정은 점 하나가 아니라 교면 점 전체의 중앙값 ± 95 % 신뢰구간으로 말한다",
                 "발표용으로 다시 그리지 않는다 — 이 그림이 곧 산출물이다"],
        out="brief.png", image=f"{b}/brief.png",
        dur=11.0, cues=[
            (0.2, 4.0, "팔 단계. 네 단짜리 브리프 그림 한 장을 만듭니다."),
            (4.2, 8.2, "점 하나의 속도는 믿지 않습니다. 교면 점 전체의 중앙값과 신뢰구간까지만 말합니다."),
            (8.4, 10.8, "이 그림을 발표용으로 다시 그리지 않습니다. 이게 곧 산출물입니다."),
        ]))

    S.append(sc_step(
        9, head="스스로 감사한다 — 못 하는 것을 먼저 적는다",
        bullets=["관측조건이 학습 기준과 다르면 등급을 '잠정' 으로 내린다",
                 "EI·고유진동수는 관측값이 아니라 설계 제원 기반임을 명시한다",
                 "시점 수가 기준(≥100장)에 못 미치면 판정을 보류한다"],
        out="감사 판정 · 결과.md '이 파이프라인이 원리상 못 하는 것'", image=None,
        note="암사대교 — 조건부 · CRI 등급 잠정: 노이즈 20.7 mm(기준 10.0 mm의 2.1배) · "
             "관측기간 2748일(기준 552일). 등급을 구조 상태로 읽으면 안 된다",
        note_col=ORANGE,
        dur=11.5, cues=[
            (0.2, 4.4, "구 단계. 프로그램이 자기 결과를 감사합니다."),
            (4.6, 8.6, "관측 조건이 학습 기준과 다르면 등급을 잠정으로 내리고 그 이유를 적습니다."),
            (8.8, 11.3, "못 하는 것을 먼저 적는 것이 이 파이프라인의 규칙입니다."),
        ]))

    S.append(sc_result([
        ("# 암사대교 — 좌표 하나로 끝까지 (bridge_run)", BLUE),
        ("", INK),
        ("| 항목        | 출처                                                      |", DIM),
        ("| specs       | 파트너 실측 CSV '구리암사대교' (576 m)                     |", INK),
        ("| bridge_type | CSV '아치교' → arch                                       |", INK),
        ("| clearance   | 잔차고도 집단평균 +8.5±2.6 m (z=3.33)                      |", INK),
        ("| deck        | OSM '구리암사대교' 2363 m · 방위 125.2°                    |", INK),
        ("| points      | 쉬프트 32.4 m 보정 후 데크 ±30 m 안 237/20000              |", INK),
        ("", INK),
        ("| IFC 트윈   | 부재 77 · 점 237 · 결합 237                                |", GREEN),
        ("| 잔차고도   | 교면 위 - 밖 +8.5 ± 2.6 m (z=3.33) — 유의하게 높다          |", GREEN),
        ("| PINN · CRI | CRI 0.809 · 경고                                          |", ORANGE),
        ("| 감사       | 조건부 · CRI 등급은 잠정 — 관측조건이 기준과 다르다         |", ORANGE),
        ("", INK),
        ("## 이 파이프라인이 원리상 못 하는 것", RED),
        ("- EI(강성) 관측 식별 — InSAR 는 상대 변위라 자중 처짐을 못 본다", DIM),
        ("- 데크 위 PS 밀도 — Sentinel-1 화소 ~11 m. 프로그램으로 늘릴 수 없다", DIM),
    ], [
        (0.2, 4.2, "십 단계. 마지막으로 결과 문서를 씁니다."),
        (4.4, 8.4, "숫자마다 어디서 왔는지 적고, 못 하는 것도 같이 적습니다."),
        (8.6, 11.0, "손으로 옮겨 적은 숫자는 한 개도 없습니다."),
    ], 11.5))

    S.append(sc_full(
        "docs/img/hangang_지표_종합.png",
        "같은 코드로 한강 16개소 — 현장 안전감시 보고서와 전수 대조",
        "2024 한강교량 온라인 안전감시 최종보고 전 교량 · 교량만 갈아 끼웠다",
        [(0.2, 4.4, "같은 코드로 한강 열여섯 개소를 돌렸습니다. 교량만 갈아 끼웠습니다."),
         (4.6, 9.0, "현장 계측 보고서의 판정과 맞대 보면 열네 개소가 일치합니다."),
         (9.2, 13.6, "연한 띠는 교축을 여섯 구간으로 나눈 추세 범위입니다. "
                     "삼각형 표시는 전체 중앙값으로는 안 보이지만 한 구간은 유의한 교량입니다.")],
        14.0, "docs/img/hangang_지표_종합.png  ·  scripts/make_hangang_gnss_insar.py"))

    S.append(sc_full(
        "docs/img/hangang_트윈_3D.png",
        "디지털 트윈 위의 PS 점 — 교량마다, 부재에 묶어서",
        "16개소 · 1,179 점 · 부재 결합 94 %",
        [(0.2, 4.4, "열여섯 개 트윈 위에 천백칠십구 개 점이 부재에 묶여 올라갑니다."),
         (4.6, 8.4, "교대, 교각, 슬래브 단위로 집계할 수 있다는 뜻입니다.")],
        9.0, "docs/img/hangang_트윈_3D.png  ·  scripts/make_twin_ps_figure.py"))

    S.append(sc_full(
        "docs/img/bmaps_tab.png",
        "B-Maps 연동 — 읽기 전용 REST 사이드카",
        "기존 탭은 현장 계측·점검 기반 — 위성 InSAR 가 비어 있는 축을 채운다",
        [(0.2, 4.2, "마지막으로 비맵스에 읽기 전용으로 연결됩니다."),
         (4.4, 8.2, "기존 탭이 비워 둔 축을, 센서 없이 위성이 채웁니다.")],
        8.6, "docs/img/bmaps_tab.png  ·  12개 REST 엔드포인트(구현 완료 · 읽기 전용)"))

    S.append(sc_end(meta))
    return S


# ── 렌더링 ───────────────────────────────────────────────────────────────────
def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_subs(scenes: list[Scene], srt: Path, txt: Path) -> int:
    lines, script, n, t0 = [], [], 0, 0.0
    for sc in scenes:
        for a, b, s in sc.cues:
            n += 1
            lines.append(f"{n}\n{srt_time(t0 + a)} --> {srt_time(t0 + b)}\n{s}\n")
            script.append(f"[{srt_time(t0 + a)[3:8]}] {s}")
        t0 += sc.dur
    srt.write_text("\n".join(lines), encoding="utf-8")
    txt.write_text("\n".join(script) + "\n", encoding="utf-8")
    return n


def render(scenes: list[Scene], out: Path, fps: int, scale: float) -> None:
    import imageio_ffmpeg

    total = sum(s.dur for s in scenes)
    ow, oh = int(W * scale) // 2 * 2, int(H * scale) // 2 * 2
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-an",
           "-vf", f"scale={ow}:{oh}:flags=lanczos" if scale != 1.0 else "null",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    done_t, nf = 0.0, 0
    for si, sc in enumerate(scenes):
        base = Image.new("RGB", (W, H), BG)
        sc.bg(base, ImageDraw.Draw(base))
        n = max(1, int(round(sc.dur * fps)))
        for k in range(n):
            t = k / fps
            fr = base.copy()
            d = ImageDraw.Draw(fr)
            if sc.dyn:
                sc.dyn(fr, d, t)
            cue = next((s for a, b, s in sc.cues if a <= t < b), None)
            subtitle(d, cue)
            progress(d, (done_t + t) / total)
            if k < 5:                                   # 장면 전환 — 짧은 페이드
                fr = Image.blend(Image.new("RGB", (W, H), BG), fr, (k + 1) / 6)
            p.stdin.write(fr.tobytes())
            nf += 1
        done_t += sc.dur
        print(f"  {si + 1:2d}/{len(scenes)}  {sc.dur:5.1f}s  누적 {done_t:6.1f}s")
    p.stdin.close()
    if p.wait() != 0:
        raise SystemExit("ffmpeg 실패")
    print(f"wrote {out}  ({nf} 프레임 · {total:.1f}초 · {ow}x{oh} · {fps}fps)")


def read_meta(bd: Path) -> dict:
    meta = {"bridge": bd.name, "scenes": "51", "span": "2018-06-19 ~ 2025-12-27",
            "n_bridges": 23, "n_points": 1179, "agree": "14/16"}
    try:
        cmp_ = json.loads(Path("docs/bridges/hangang_gnss_insar.json")
                          .read_text(encoding="utf-8"))
        rows = cmp_["bridges"]
        done = [r for r in rows if r.get("insar")]
        meta["agree"] = (f"{sum(1 for r in done if str(r.get('agree','')).startswith('일치'))}"
                         f"/{len(done)}")
        meta["n_points"] = sum(r["insar"]["n_points"] for r in done)
    except Exception:
        pass
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="docs/bridges/암사대교",
                    help="단계별 그림을 가져올 교량 폴더")
    ap.add_argument("--out", default="docs/video/inframon_구동순서.mp4")
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="0.5 면 960x540 로 줄여 빠르게 확인")
    ap.add_argument("--preview", action="store_true",
                    help="앞 3개 장면만 만든다(확인용)")
    ap.add_argument("--subs-only", action="store_true", help=".srt·대본만 쓴다")
    a = ap.parse_args()

    bd = Path(a.bridge)
    if not bd.exists():
        print(f"교량 폴더가 없다: {bd}", file=sys.stderr)
        return 2

    scenes = build_scenes(bd, read_meta(bd))
    if a.preview:
        scenes = scenes[:3]

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = write_subs(scenes, out.with_suffix(".srt"), out.with_suffix(".txt"))
    print(f"자막 {n}줄 — {out.with_suffix('.srt')} · {out.with_suffix('.txt')}")
    if a.subs_only:
        return 0
    render(scenes, out, a.fps, a.scale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
