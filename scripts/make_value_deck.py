#!/usr/bin/env python3
"""KICT B-Maps 협의용 **연계 가치** 발표자료 7장 — "왜 붙여야 하는가" 를 말한다.

기존 `make_kict_slides.py` 는 방법론과 성능을 설명하는 자료다. 이 자료는 목적이
다르다. Inframon 은 아직 완성되지 않았고, 그래서 정확도 지표는 낮다. 낮은 숫자를
감추면 발표는 한 번 넘어가지만 다음 회의에서 무너진다. 그러니 숫자는 그대로 내되,
**왜 그럼에도 지금 붙여야 하는가**를 자료의 축으로 삼는다.

  ① 연계 가치 ─ B-Maps 가 이미 가진 것 / 지금 비어 있는 칸
  ② 연계 가치 ─ 붙일 자리는 이미 맞춰져 있다(IFC · CRI · OSM · REST)
  ③ 성능 산출물 ─ GNSS 기반 InSAR 변위 정확도 검증(현재 수준을 있는 그대로)
  ④ 성능 산출물 ─ 한강 교량 7개소 적용 현황
  ⑤ 성능 산출물 ─ 교량별 IFC 모델과 평면·입면 산출물
  ⑥ 성능 산출물 ─ 변위 속도 3D · FRAM 공명 위험 지수 3D
  ⑦ 결론 ─ B-Maps 제공 서비스 × Inframon 결합 시너지

모양새는 `2026 KAIA 자문회의 ppt_v2.pptx` 의 톤을 그대로 쓴다(`kaia_theme`).
표와 숫자는 `docs/bridges/<교량>/` 산출물에서 직접 읽는다 — 손으로 옮겨 적지 않는다.

    python scripts/make_value_deck.py

산출: docs/KICT_Bmaps_연계가치_7p.pptx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pptx import Presentation
from pptx.enum.text import MSO_ANCHOR
from pptx.util import Inches

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kaia_theme import (                                                # noqa: E402
    BLUE, BLUE_F, BLUE_P, GRAY, GREEN, INK, ORANGE, PAPER, RED, RULE, SH, SLATE,
    SW, WHITE, band, blank, box, bullets, chevrons, footer, header, kpi, panel,
    picture_fit, reset_pages, table, text,
)
from make_kict_slides import MAT_KO, TYPE_KO, read_bridge               # noqa: E402

GRAY_DIM = GRAY          # 값이 없는 칸은 옅게 — 없는 것과 0 을 눈으로 구별한다
IMG = ROOT / "docs" / "img"
VAL = IMG / "value"
BR = ROOT / "docs" / "bridges"
TAG = "Inframon × B-Maps"

# GNSS 현장 계측이 보고서에 실린 한강 교량 — 대조가 가능한 곳이 곧 실증 대상이다.
SEVEN = ["가양대교", "원효대교", "올림픽대교", "청담대교", "천호대교",
         "암사대교", "월드컵대교"]


def n_elements(folder: Path, name: str) -> int:
    ej = folder / f"{name}_elements.json"
    if not ej.exists():
        return 0
    try:
        return len(json.loads(ej.read_text(encoding="utf-8")).get("elements", []))
    except (OSError, json.JSONDecodeError):
        return 0


# ── ① 연계 가치 ───────────────────────────────────────────────────────────
def slide_value1(prs):
    s = blank(prs)
    header(s, "왜 결합해야 하는가",
           "B-Maps 는 '무엇이 어디에 있는가'를 덮고, Inframon 은 "
           "'그것이 그동안 어떻게 움직였는가'를 채운다", TAG)

    w = (SW - 0.9 - 0.3) / 2
    y0 = panel(s, 0.45, 1.52, w, 1.86, "B-Maps 가 이미 가진 것 — 공간", tab=SLATE)
    bullets(s, 0.72, y0 + 0.06, w - 0.54, 1.5, [
        ("전국 교량 대장·도면 ", "— 어디에 무엇이 있는지"),
        ("IFC 기반 자산 모델 ", "— 부재 단위로 식별되는 구조"),
        ("좌표계·플랫폼·사용자 ", "— 결과를 받아 쓸 자리가 이미 있다"),
    ], fs=10.8, spacing=1.42)

    x2 = 0.45 + w + 0.3
    y0 = panel(s, x2, 1.52, w, 1.86, "지금 비어 있는 칸 — 시간", tab=BLUE)
    bullets(s, x2 + 0.27, y0 + 0.06, w - 0.54, 1.5, [
        ("지어진 뒤 무슨 일이 있었나 ", "— 준공 도면은 그날의 기록이다"),
        ("점검과 점검 사이 ", "— 사람이 간 날만 관측이 있다"),
        ("계측기가 없는 교량 ", "— 전국 교량 대부분이 여기 해당한다"),
    ], fs=10.8, spacing=1.42)

    kw = (SW - 0.9 - 0.3) / 4
    for i, (big, unit, lab) in enumerate([
            ("2018", "년 6월~", "위성 관측이 이미 쌓여 있다\n소급해서 과거를 볼 수 있다"),
            ("12", "일", "재방문 주기\n사람이 가지 않아도 갱신된다"),
            ("0", "개", "현장 설치 장비\n교량에 손대지 않는다"),
            ("전량", "무상", "Sentinel-1 공개 자료\n관측 비용이 교량 수에 비례하지 않는다")]):
        kpi(s, 0.45 + i * (kw + 0.1), 3.58, kw, 1.22, big, unit, lab,
            color=(BLUE if i % 2 == 0 else SLATE))

    table(s, 0.45, 4.96, SW - 0.9,
          [["", "지금", "결합하면"],
           ["계측기가 달린 교량", "상시 감시 — 붙인 지점만", "위성으로 교차검증 · 나머지 구간까지"],
           ["계측기가 없는 교량", "점검 간 것으로 판단", "12일마다 전 구간 변위가 갱신된다"],
           ["과거 이력", "점검 기록이 전부", "2018년 6월까지 소급해 다시 본다"]],
          [1.5, 2.0, 2.9], row_h=0.325, fs=10.8, head_fs=10.8, head_h=0.33)

    band(s, 6.33, "둘은 겹치지 않는다 — 서로가 없으면 반쪽이다", h=0.52, fs=12)
    footer(s, "※ 관측 기간·주기는 실제 처리한 스택에서 옮겼다(2018-06-19 ~ 2025-12-27). "
              "현장 설치 0 은 InSAR 가 기존 산란체를 쓰기 때문이며, 그래서 산란체가 "
              "없는 교량은 점이 적다 — 장점과 한계가 같은 뿌리다.")
    return s


def slide_value2(prs):
    s = blank(prs)
    header(s, "붙일 자리는 이미 맞춰져 있다",
           "새로 만들 것이 아니라 잇는 것 — IFC · 3D Tiles · REST 로 이미 "
           "내보내고 있다", TAG)

    chevrons(s, 0.45, 1.32, SW - 0.9, 0.86, [
        ("좌표 1개", "위경도만 입력"),
        ("Sentinel-1 SLC", "내려받기·정합\n자동"),
        ("PS/DS 시계열", "점별 변위\nmm 단위"),
        ("PINN 역산", "강성·처짐\n물리식 구속"),
        ("IFC · CRI 트윈", "부재에 값이 붙는다"),
        ("B-Maps", "REST 로 그대로"),
    ], fs=10.2, sub_fs=8.0)

    rows = [["접점", "Inframon 이 내는 것", "B-Maps 가 받는 방식"],
            ["자산 모델", "IfcBridge proxy (.ifc) · 부재 GUID", "기존 IFC 파이프라인 그대로"],
            ["3D 타일", "tileset.json · glTF(twin.glb)", "지도 위 3D 레이어로 적재"],
            ["좌표", "CRI 전역 좌표 · 교축 측점(m)", "대장 좌표와 직접 대응"],
            ["도로망", "OSM 500 m 반경 도로 링크", "교통·우회 분석에 연결"],
            ["시계열", "REST /api/v1 · 점·부재 단위 변위", "대시보드·경보 규칙에 투입"]]
    table(s, 0.45, 2.58, 7.55, rows, [1.05, 2.25, 2.2], row_h=0.35, fs=10,
          head_fs=10)

    y0 = panel(s, 8.35, 2.58, SW - 0.45 - 8.35, 2.16, "역할이 겹치지 않는다")
    bullets(s, 8.6, y0 + 0.06, 4.1, 1.6, [
        ("KICT B-Maps ", "— 자산·도면·플랫폼·사용자"),
        ("Inframon ", "— 시계열 변위·물성 역산·위험 지수"),
        ("공통 ", "— IFC 부재 GUID 하나로 묶인다"),
    ], fs=10.5, spacing=1.4)

    box(s, 0.45, 5.05, SW - 0.9, 1.02, fill=PAPER, line=RULE, lw=1.0)
    box(s, 0.45, 5.05, 0.055, 1.02, fill=ORANGE)
    text(s, 0.72, 5.12, SW - 1.44, 0.9, [
        [("왜 지금인가", 11.5, True, ORANGE)],
        [("Sentinel-1 아카이브는 이미 2018년부터 쌓여 있다. 지금 붙이면 "
          "'앞으로 쌓을 자료'가 아니라 '이미 쌓인 7년치'를 B-Maps 가 바로 갖게 된다. "
          "계측기를 새로 달아 7년을 기다리는 길과, 있는 자료를 읽는 길의 차이다.",
          10.8, False, SLATE)]], spacing=1.3)

    band(s, 6.22, "남은 것은 어느 필드를 어느 화면에 붙일지 정하는 일이다")
    footer(s, "※ 엔드포인트·산출 파일명은 코드와 실제 교량 폴더에서 확인한 것이다. "
              "합의가 필요한 항목(갱신 주기·경보 임계·권한)은 별도 협의 안건으로 둔다.")
    return s


# ── ③ 성능 산출물: 정확도 검증 ─────────────────────────────────────────────
def slide_perf_gnss(prs, fix: dict):
    s = blank(prs)
    header(s, "성능 산출물 ① — GNSS 기반 InSAR 변위 정확도 검증",
           "교면 점만 남기고 지반 성분까지 뺐다 — 그래도 계절이 어긋난다", TAG)

    fig = VAL / "교면전용_GNSS대조.png"
    if fig.exists():
        box(s, 0.45, 1.28, SW - 0.9, 3.16, fill=WHITE, line=RULE, lw=1.0)
        picture_fit(s, fig, 0.5, 1.33, SW - 1.0, 3.06)

    got = [v for v in fix.values() if "phase_diff_months" in v]
    dif = sorted(abs(v["phase_diff_months"]) for v in got)
    same = sum(1 for v in got if v.get("same_seasonal_behaviour"))
    n_pt = [v["n_deck"] for v in fix.values() if "n_deck" in v]

    y0 = panel(s, 0.45, 4.62, SW - 0.9, 1.3, "지금 수준을 있는 그대로", tab=RED)
    cw = (SW - 1.44 - 0.5) / 2
    bullets(s, 0.72, y0 + 0.04, cw, 1.2, [
        ("한 것 ", "① 코히런스로 지오로케이션 밀림 되돌리기 ② 회랑을 ±30 m 에서 "
               "±폭/2 로 ③ 100~400 m 밖 지반 공통성분 빼기"),
        ("연주기 위상 ", f"{dif[0]:.1f} ~ {dif[-1]:.1f}개월 어긋남 · "
                    f"같은 계절 거동 {same}/{len(got)}개소"),
    ], fs=10.2, spacing=1.34)
    bullets(s, 0.72 + cw + 0.25, y0 + 0.04, cw, 1.2, [
        ("근본 원인 ", "한강 15개소 중 11개소에서 교량 위 점의 계절 위상이 "
                  "100~200 m 떨어진 맨땅과 1.5개월 안으로 같다"),
        ("즉 ", f"교면 점이 {min(n_pt)}~{max(n_pt)}개뿐이고 그마저 주변 지반과 "
              "섞여 있다 — 통계가 아니라 분해능 문제다"),
    ], fs=10.2, spacing=1.34)

    band(s, 6.02,
         "Sentinel-1 은 지상 5×20 m, 데크 폭은 1~2 화소다 — 지금 낸 것은 '맞는다' 가 "
         "아니라 '무엇이 있어야 맞출 수 있는가' 다", fill=BLUE_F, fs=11.5)
    footer(s, "※ 오프셋은 평균 코히런스가 가장 높은 자리로 정했다 — GNSS 를 보지 않고 "
              "정하는 기준이라 '맞도록 고른' 값이 아니다. 판정 근거: "
              "scripts/make_deck_vs_ground.py · make_deck_fix.py · 교량별 교면전용.json. "
              "원자료: 24년 한강온라인 최종보고(서울시·㈜유신·㈜일신이앤씨).")
    return s


# ── ④ 7개소 적용 현황 ──────────────────────────────────────────────────────
def slide_seven(prs, bs, fix):
    s = blank(prs)
    header(s, "성능 산출물 ② — 한강 교량 7개소 적용 현황",
           "보고서에 GNSS 현장 계측이 실린 교량만 골랐다 — 답을 맞춰 볼 수 있는 곳",
           TAG)

    # 값은 전부 '교면 전용 재선별'(회랑 ±폭/2 · 지반 공통성분 제거) 결과다.
    # 예전 ±30 m 선별 값은 강변 지반이 섞여 있어 쓰지 않는다.
    rows = [["교량", "연장", "형식", "교면 PS", "속도(지반보정)", "연주기",
             "GNSS 대조"]]
    colors = {}
    for i, b in enumerate(bs, start=1):
        m = b.get("meta", {})
        f = fix.get(b["name"], {})
        if f.get("skipped") or "velocity_mm_yr" not in f:
            vel, ann = "—", "—"
            colors[(i, 4)] = GRAY_DIM
        else:
            vel = f"{f['velocity_mm_yr']:+.2f} ± {f['velocity_ci95']:.2f}"
            ann = f"{f['annual_amp_mm']:.1f} mm · {f['annual_peak_month']:.1f}월"
            colors[(i, 4)] = RED if f["velocity_significant"] else GREEN
        if "phase_diff_months" in f:
            cmp_ = f"{f['phase_diff_months']:+.1f}개월"
            colors[(i, 6)] = GREEN if f["same_seasonal_behaviour"] else RED
        else:
            cmp_ = "계측 없음"
            colors[(i, 6)] = GRAY_DIM
        rows.append([
            b["name"],
            f"{(m.get('length_m') or 0):,.0f} m",
            TYPE_KO.get(m.get("bridge_type"), m.get("bridge_type") or "—"),
            str(f.get("n_deck", "—")),
            vel, ann, cmp_])
    table(s, 0.45, 1.36, 7.7, rows, [1.25, .8, .8, .75, 1.45, 1.25, 1.0],
          row_h=0.335, fs=9.4, head_fs=9.4, head_h=0.34, colors=colors)

    mp = IMG / "hangang_지도_화면.png"
    if mp.exists():
        box(s, 8.35, 1.36, SW - 0.45 - 8.35, 2.86, fill=WHITE, line=RULE, lw=1.0)
        picture_fit(s, mp, 8.42, 1.43, SW - 0.45 - 8.42 - 0.07, 2.72)

    y0 = panel(s, 0.45, 4.23, 7.7, 1.78,
               "교량 1개소당 자동으로 나오는 것 — 사람이 손대는 단계가 없다")
    w2 = 3.6
    bullets(s, 0.72, y0 + 0.06, w2, 1.3, [
        ("project.h5 ", "— 점별 변위 시계열 · 속도 · CRI · PINN"),
        ("<교량>_proxy.ifc ", "— 부재 GUID 가 붙은 IFC"),
        ("twin.glb · tileset.json ", "— 3D 타일"),
    ], fs=9.8, spacing=1.34)
    bullets(s, 0.72 + w2 + 0.3, y0 + 0.06, w2, 1.3, [
        ("교면전용.json · png ", "— 재선별 결과와 GNSS 대조"),
        ("osm_roads_500m.json ", "— 반경 도로망"),
        ("결과.md ", "— 출처·적용값·감사 판정을 문장으로"),
    ], fs=9.8, spacing=1.34)

    y0 = panel(s, 8.35, 4.55, SW - 0.45 - 8.35, 1.46, "7개소를 고른 기준", tab=BLUE)
    bullets(s, 8.6, y0 + 0.04, 4.0, 1.0, [
        "보고서에 GNSS 현장 계측이 실린 교량",
        "즉, 답을 맞춰 볼 수 있는 곳만 골랐다",
    ], fs=10, spacing=1.32)

    band(s, 6.2, "교면만 남기면 7개소 중 6개소의 속도 95% 구간이 0 을 품는다 — "
                 "유의한 추세가 없다. 숨기지 않고 그대로 싣는다",
         fill=PAPER, fs=11.5)
    footer(s, "※ 표의 값은 '교면 전용 재선별'(회랑 ±폭/2 · 코히런스로 밀림 되돌림 · "
              "지반 공통성분 제거) 결과다 — 교량별 교면전용.json 에서 직접 읽었다. "
              "예전 ±30 m 선별 값은 강변 지반이 섞여 있어 쓰지 않는다. "
              "속도는 초록이 '0 을 품음', GNSS 대조는 |1.5개월| 안이면 같은 계절 거동.")
    return s


# ── ⑤ IFC · 평면/입면 ─────────────────────────────────────────────────────
def slide_ifc(prs, bs, example: str = "올림픽대교"):
    s = blank(prs)
    header(s, "성능 산출물 ③ — 교량별 IFC 모델과 평면·입면 산출물",
           "도면이 없어도 모델이 선다 — 위경도 1개에서 부재까지", TAG)

    order = sorted(bs, key=lambda b: b["name"] != example)
    pick = next((b for b in order
                 if (VAL / f"IFC_평면입면_{b['name']}.png").exists()), None)
    if pick is not None:
        box(s, 0.45, 1.36, 8.1, 3.3, fill=WHITE, line=RULE, lw=1.0)
        picture_fit(s, VAL / f"IFC_평면입면_{pick['name']}.png",
                    0.5, 1.41, 8.0, 3.2)

    y0 = panel(s, 8.75, 1.36, SW - 0.45 - 8.75, 3.3, "무엇이 자동으로 서는가",
               tab=BLUE)
    bullets(s, 8.98, y0 + 0.06, 3.6, 2.8, [
        ("입력 ", "위경도 1개 — 제원은 공공데이터·OSM 에서 자동 수집"),
        ("출력 ", "IfcBridge proxy · glTF · 3D Tiles · 평면/종단 도면"),
        ("결합 ", "PS 점을 부재 GlobalId 에 묶는다 — 좌표가 아니라 부재로 말한다"),
        ("검증 ", "실측 CSV·표준데이터와 대조해 차이를 결과.md 에 남긴다"),
        ("한계 ", "준공도면 대체가 아니다 — 실제 IFC 가 있으면 그것을 쓴다"),
        ("쓸모 ", "도면이 없는 교량의 빈칸을 메운다 — 전국 단위에서 이 차이가 크다"),
    ], fs=10, spacing=1.36)

    rows = [["교량", "IFC 부재", "형식(PDE)", "재질", "경간", "폭"]]
    for b in bs:
        m = b.get("meta", {})
        rows.append([b["name"], f"{n_elements(b['folder'], b['name']):,}",
                     TYPE_KO.get(m.get("bridge_type"), "—"),
                     MAT_KO.get(m.get("material"), m.get("material") or "—"),
                     str(m.get("n_spans") or "—"),
                     f"{(m.get('width_m') or 0):.0f} m"])
    table(s, 0.45, 4.76, SW - 0.9, rows, [1.6, .9, 1.0, .7, .6, .6],
          row_h=0.252, fs=9.6, head_fs=9.6, head_h=0.3)

    footer(s, "※ proxy 는 준공도면 대체물이 아니라 '위성 점을 부재에 붙이기 위한 기하'다. "
              "실제 IFC 가 B-Maps 에 있으면 그것을 쓰는 편이 낫고, 없을 때 빈칸을 "
              "메우는 용도다 — 전국 단위로 갈 때 이 차이가 크다.")
    return s


# ── ⑥ 3D 시각화 ───────────────────────────────────────────────────────────
def slide_3d(prs, name: str = "올림픽대교"):
    s = blank(prs)
    header(s, "성능 산출물 ④ — 변위 속도 · FRAM 공명 위험 지수 3D 시각화",
           "같은 기하 위에 색만 바꿔 얹는다 — B-Maps 지도에 레이어 하나 더", TAG)

    w = (SW - 0.9 - 0.3) / 2
    pairs = [(VAL / f"3D_변위속도_{name}.png", "교량 변위 속도 — 색은 LOS 속도"),
             (VAL / f"3D_FRAM_CRI_{name}.png", "FRAM 공명 위험 지수 — 색은 CRI")]
    for i, (p, cap) in enumerate(pairs):
        x = 0.45 + i * (w + 0.3)
        box(s, x, 1.34, w, 0.32, fill=BLUE_P)
        text(s, x + 0.16, 1.34, w - 0.32, 0.32, [(cap, 10.8, True, SLATE)],
             anchor=MSO_ANCHOR.MIDDLE)
        box(s, x, 1.66, w, 2.72, fill=WHITE, line=RULE, lw=1.0)
        if p.exists():
            picture_fit(s, p, x + 0.05, 1.71, w - 0.1, 2.62)

    y0 = panel(s, 0.45, 4.72, SW - 0.9, 1.42, "왜 3D 여야 하나")
    cw = (SW - 1.44 - 0.5) / 2
    bullets(s, 0.72, y0 + 0.06, cw, 1.3, [
        ("부재에 붙는다 ", "— 점이 어느 경간·어느 주탑인지 좌표가 아니라 "
                      "부재 이름으로 말할 수 있다"),
        ("두 장이 같은 기하 ", "— 속도는 잠잠한데 공명 지수만 높은 구간이 "
                         "눈으로 잡힌다"),
    ], fs=10.2, spacing=1.36)
    bullets(s, 0.72 + cw + 0.25, y0 + 0.06, cw, 1.3, [
        ("B-Maps 화면 그대로 ", "— glTF·3D Tiles 로 내보내므로 지도 위에 "
                          "레이어 하나 더 얹는 일이다"),
        ("CRI 는 등급이 아니다 ", "— '같이 움직이는 구간'을 짚어 주는 지표이며, "
                           "관측 조건이 다르면 잠정으로 표시된다"),
    ], fs=10.2, spacing=1.36)

    footer(s, f"※ {name} 예시 — 보여 주는 것은 '값을 부재에 얹는 방식' 이다. "
              "이 두 그림의 점은 ③④장과 달리 **예전 ±30 m 선별**이라 교면 전용이 아니다"
              "(교면만 남기면 점이 10~122개로 줄어 3D 로는 성기다). "
              "두 그림 모두 project.h5 의 insar/velocity_mm_yr · fram/CRI 를 그대로 읽었고, "
              "회색 윤곽은 같은 좌표계의 IFC 부재다.")
    return s


# ── ⑦ 결론 ────────────────────────────────────────────────────────────────
def slide_conclusion(prs):
    s = blank(prs)
    header(s, "결론 — B-Maps 제공 서비스에 Inframon 이 더하는 것",
           "성능 지표보다 먼저 합의할 값: 계측기 없는 교량까지 같은 잣대로", TAG)

    cols = [
        ("노후도 평가 및 예측", BLUE,
         [("지금 ", "준공연도·점검 이력·재질로 추정"),
          ("더해지는 것 ", "7년치 실제 변위 속도와 그 신뢰구간"),
          ("달라지는 것 ", "'몇 년 됐으니 노후'가 아니라 "
                      "'실제로 얼마나 움직였는가'로 바뀐다"),
          ("근거 파일 ", "project.h5 · insar/velocity_mm_yr + 95% CI")]),
        ("교량 안정성 추정", SLATE,
         [("지금 ", "설계 제원과 점검 등급"),
          ("더해지는 것 ", "PINN 역산 강성·처짐 · FRAM 공명 지수"),
          ("달라지는 것 ", "전 구간을 같은 잣대로 훑고, 이상 구간만 "
                      "사람이 확인하러 간다"),
          ("근거 파일 ", "project.h5 · pinn/EI · fram/CRI")]),
        ("유지관리 시나리오", ORANGE,
         [("지금 ", "주기·예산 기준의 계획"),
          ("더해지는 것 ", "교량 간 비교 가능한 변위 순위와 추세"),
          ("달라지는 것 ", "어디를 먼저 볼지 근거로 정한다 — "
                      "순서가 바뀌면 같은 예산으로 더 막는다"),
          ("근거 파일 ", "결과.md 의 감사 판정 · 16개소 전수 대조표")]),
    ]
    w = (SW - 0.9 - 0.5) / 3
    for i, (t, c, items) in enumerate(cols):
        x = 0.45 + i * (w + 0.25)
        y0 = panel(s, x, 1.55, w, 2.32, t, tab=c, title_fs=12)
        bullets(s, x + 0.22, y0 + 0.06, w - 0.44, 1.85, items, fs=10.2,
                spacing=1.34)

    box(s, 0.45, 4.12, SW - 0.9, 1.3, fill=PAPER, line=RULE, lw=1.0)
    box(s, 0.45, 4.12, 0.055, 1.3, fill=BLUE)
    text(s, 0.72, 4.2, SW - 1.44, 1.16, [
        [("결합 시너지 — 한 문장으로", 11.5, True, BLUE)],
        [("B-Maps 는 전국 교량을 '어디에 무엇이 있는가'로 이미 덮고 있고, Inframon 은 "
          "그 위에 '그동안 어떻게 움직였는가'를 계측기 없이 얹는다. 둘이 붙으면 "
          "계측기가 달린 소수의 교량만 감시하던 체계가, 계측기가 없는 대다수 교량까지 "
          "같은 잣대로 들여다보는 체계로 바뀐다.",
          11, False, SLATE)]], spacing=1.32)

    band(s, 5.72, "지금 성능 지표는 낮다 — 완성 전이니 당연하다. 그러나 목적과 "
                 "붙일 자리는 이미 분명하다", fill=INK, color=WHITE, fs=12)
    footer(s, "※ 협의 제안: ① 시범 대상 교량 선정 ② B-Maps 화면에 붙일 필드 확정 "
              "③ 갱신 주기·경보 임계 합의 ④ 검증용 계측 위치·부호 규약 공유. "
              "④ 가 되면 정확도 검증이 지금보다 크게 올라간다.")
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/KICT_Bmaps_연계가치_7p.pptx")
    ap.add_argument("--example", default="올림픽대교")
    a = ap.parse_args()

    bs = [read_bridge(BR / n) for n in SEVEN]
    fix = {b["name"]: b for b in json.loads(
        (BR / "deck_fix_all.json").read_text(encoding="utf-8"))["bridges"]}

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(SW), Inches(SH)
    reset_pages()
    slide_value1(prs)
    slide_value2(prs)
    slide_perf_gnss(prs, fix)
    slide_seven(prs, bs, fix)
    slide_ifc(prs, bs, a.example)
    slide_3d(prs, a.example)
    slide_conclusion(prs)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    print(f"wrote {out}  ({len(prs.slides._sldIdLst)}장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
