#!/usr/bin/env python3
"""대시보드 **조작 순서**를 화면 그대로 찍는다 — 발표에서 "따라 해 보세요" 가 되게.

설명만으로는 처음 보는 사람이 못 따라온다. 어느 칸에 무엇을 넣고 어느 버튼을 누르면
무엇이 바뀌는지가 **화면으로** 있어야 한다. 그래서 실제로 띄운 대시보드를 사람이 누르는
순서대로 눌러 가며 단계마다 한 장씩 남긴다(사람이 찍으면 매번 달라진다 — 이건 다시 돌릴 수 있다).

명령줄은 한 줄도 나오지 않는다. 화면에서 시작해 화면에서 끝난다:

  W1 ⓪ 시작 · ① 이 컴퓨터 준비 상태   — 무엇이 준비됐는지(SNAP·snaphu·토큰·SLC 폴더)
  W2 ⓪ 시작 · ② 교량 선택            — 교량명 '성수대교' 입력 → 🔎 찾기
  W3 ⓪ 시작 · ② 교량 선택            — 검색 결과 고르고 '이 교량으로 설정' → 좌표 자동 입력
  W4 ⓪ 시작 · ③ 전 과정 실행          — 엔진 · **SLC 장면 수** · 조회 기간을 정한다
  W5 ⓪ 시작 · ③ 전 과정 실행          — 📋 계획 보기(몇 초) 결과 — 몇 장을 쓸 수 있는지
  W6~W9 ① InSAR · ② PINN · ③ FRAM · ④ 잔존수명 — 결과를 보는 탭들

    streamlit run src/inframon/dashboard/app.py --server.port 8501
    python scripts/capture_workflow.py --name 성수대교 --lat 37.537353 --lon 127.035055 \
        --project docs/bridges/성수대교/project.h5 --slc 51

산출: docs/img/ui/w*.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img" / "ui"

SECTIONS = ["⓪ 시작", "① InSAR", "② PINN", "③ FRAM", "④ 잔존수명"]


def settle(page, ms: int = 2200) -> None:
    """Streamlit 은 rerun 이 끝나야 화면이 완성된다 — 실행 표시가 사라질 때까지 기다린다."""
    page.wait_for_timeout(500)
    for _ in range(60):
        try:
            if page.locator('[data-testid="stStatusWidget"]').count() == 0:
                break
        except Exception:                        # noqa: BLE001 — 위젯이 없으면 끝난 것
            break
        page.wait_for_timeout(500)
    page.wait_for_timeout(ms)


def shot(page, name: str, *, anchor: str | None = None, pad: int = 120,
         height: int = 900) -> None:
    """`anchor` 텍스트가 보이도록 스크롤한 뒤 그 자리만 잘라 찍는다.

    전체 페이지를 찍으면 발표 슬라이드에서 글씨가 안 보인다 — 사람이 눌러야 하는 **그 부분**만.
    """
    if anchor:
        try:
            # 제목(h1~h4)을 먼저 찾는다 — 본문 안내 문구에도 '② 교량 선택' 같은 글자가
            # 들어 있어서 텍스트 일치로 잡으면 엉뚱한 자리를 찍는다(실제로 그랬다).
            el = page.get_by_role("heading", name=anchor, exact=False).first
            if el.count() == 0:
                el = page.get_by_text(anchor, exact=False).first
            el.scroll_into_view_if_needed(timeout=8000)
            page.wait_for_timeout(700)
            box = el.bounding_box()
            if box:
                # 제목이 화면 위쪽에 오도록 한 번 더 스크롤 — 그 아래가 찍혀야 한다.
                page.mouse.wheel(0, max(0.0, box["y"] - pad))
                page.wait_for_timeout(600)
                box = el.bounding_box() or box
                y = max(0.0, box["y"] - pad)
                page.screenshot(path=str(OUT / f"{name}.png"), clip={
                    "x": 0, "y": y, "width": page.viewport_size["width"],
                    "height": min(height, page.viewport_size["height"] - 4)})
                print("  wrote", f"{name}.png", flush=True)
                return
        except Exception as e:                   # noqa: BLE001 — 못 찾으면 화면 그대로
            print(f"  (anchor '{anchor}' 못 찾음: {type(e).__name__}) 화면 그대로 찍음")
    page.screenshot(path=str(OUT / f"{name}.png"))
    print("  wrote", f"{name}.png", flush=True)


def pick_section(page, label: str) -> bool:
    """상단 섹션 라디오 — 진행 안내 문구에도 같은 글자가 있어 라디오그룹 안에서만 찾는다."""
    for make in (
        lambda: page.locator('[role="radiogroup"]').get_by_text(label, exact=True).first,
        lambda: page.get_by_role("radio", name=label),
    ):
        try:
            make().click(timeout=6000)
            settle(page)
            return True
        except Exception:                        # noqa: BLE001 — 다음 선택기로
            continue
    print(f"  ! 섹션 '{label}' 을 누르지 못했습니다")
    return False


def fill_by_label(page, label: str, value: str, *, index: int = 0) -> bool:
    """정확한 aria-label 로 찾는다.

    부분일치(`get_by_label`)는 사이드바의 '교량명 (예: 한강대교, 정자교)' 같은 **숨은**
    위젯을 먼저 물어 와 클릭이 타임아웃된다 — 실제로 그래서 캡처가 중간에 멈췄다.
    """
    sel = f'input[aria-label="{label}"], textarea[aria-label="{label}"]'
    try:
        box = page.locator(sel).nth(index)
        box.scroll_into_view_if_needed(timeout=8000)
        box.click(timeout=8000)
        box.fill("")
        box.fill(value)
        box.press("Enter")
        settle(page)
        return True
    except Exception as e:                       # noqa: BLE001
        print(f"  ! '{label}' 입력 실패: {type(e).__name__} {str(e)[:70]}")
        return False


def click_button(page, name: str, *, wait: int = 3000) -> bool:
    try:
        btn = page.get_by_role("button", name=name).first
        btn.scroll_into_view_if_needed(timeout=8000)
        btn.click(timeout=8000)
        settle(page, wait)
        return True
    except Exception as e:                       # noqa: BLE001
        print(f"  ! 버튼 '{name}': {type(e).__name__} {str(e)[:70]}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8501")
    ap.add_argument("--project", default="docs/bridges/성수대교/project.h5")
    ap.add_argument("--name", default="성수대교")
    ap.add_argument("--slc", default="51", help="SLC 장면 수 — 화면에서 고르는 값")
    ap.add_argument("--start", default="2018-06-19")
    ap.add_argument("--end", default="2025-12-27")
    ap.add_argument("--plan", action="store_true", help="📋 계획 보기까지 눌러 결과를 찍는다")
    ap.add_argument("--width", type=int, default=1500)
    ap.add_argument("--height", type=int, default=1000)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        page = br.new_page(viewport={"width": a.width, "height": a.height},
                           device_scale_factor=1.6)
        print("goto", a.url, flush=True)
        page.goto(a.url, timeout=90000)
        settle(page, 4000)

        # 결과를 볼 산출물 지정(사이드바) — 발표에서는 이미 돌려 둔 결과를 본다.
        fill_by_label(page, "project.h5 경로", str(Path(a.project).resolve()))

        pick_section(page, SECTIONS[0])
        print("W1 ⓪ 시작 · ① 이 컴퓨터 준비 상태", flush=True)
        shot(page, "w1_ready", anchor="① 이 컴퓨터 준비 상태", height=620)

        # ② 교량 선택 — 이름으로 찾기
        print("W2 ⓪ 시작 · ② 교량 선택 — 이름 입력", flush=True)
        fill_by_label(page, "교량명", a.name)
        shot(page, "w2_search", anchor="② 교량 선택", height=520)

        click_button(page, "찾기", wait=5000)
        print("W3 ⓪ 시작 · ② 교량 선택 — 검색 결과·좌표", flush=True)
        shot(page, "w3_hits", anchor="② 교량 선택", height=640)
        click_button(page, "이 교량으로 설정", wait=4000)
        shot(page, "w3b_picked", anchor="② 교량 선택", height=640)

        # ③ 전 과정 실행 — 장면 수·기간
        print("W4 ⓪ 시작 · ③ 전 과정 실행 — SLC 장면 수·기간", flush=True)
        fill_by_label(page, "SLC 장면 수", a.slc)
        fill_by_label(page, "조회 시작일", a.start)
        fill_by_label(page, "조회 종료일", a.end)
        shot(page, "w4_run_setup", anchor="③ 전 과정 실행", height=760)

        if a.plan:
            print("W5 📋 계획 보기", flush=True)
            click_button(page, "계획 보기", wait=6000)
            for _ in range(60):                  # 조회가 끝날 때까지(네트워크 수십 초)
                if page.get_by_text("교량선정", exact=False).count():
                    break
                page.wait_for_timeout(3000)
            settle(page, 2500)
            shot(page, "w5_plan", anchor="교량선정", pad=220, height=900)

        # 결과 탭들
        for i, (sec, nm) in enumerate(zip(SECTIONS[1:], ["insar", "pinn", "fram", "life"]), 6):
            if pick_section(page, sec):
                print(f"W{i} {sec}", flush=True)
                shot(page, f"w{i}_{nm}", anchor=sec.split(" ", 1)[-1], pad=260, height=980)

        br.close()
    print("완료 —", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
