"""playwright 로 라이브 Streamlit 대시보드 탭별 스크린샷.

사용:
    streamlit run src/inframon/dashboard/app.py --server.port 8501
    python scripts/capture_dashboard.py [PROJECT_H5] [URL]

탭 선택기는 `st.tabs` 가 아니라 **라디오**다(rerun 시 첫 탭으로 리셋되는 문제 때문에
session_state 로 유지되는 라디오로 바꿨다). 그래서 role="tab" 이 아니라 라벨 텍스트를
클릭한다.
"""
import sys

from playwright.sync_api import sync_playwright

PROJECT = sys.argv[1] if len(sys.argv) > 1 else "data/project.h5"
URL = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8501"
OUT = "docs/img"

# (라디오 라벨, 파일명) — 라벨은 app.py 의 _SECTIONS 와 일치해야 한다.
TABS = [
    ("① InSAR", "dashboard_insar"),
    ("② PINN", "dashboard_pinn"),
    ("③ FRAM", "dashboard_fram"),
    ("④ 잔존수명", "dashboard_life"),
]


def _click(page, label: str) -> bool:
    for make in (lambda: page.get_by_text(label, exact=False).first,
                 lambda: page.get_by_role("radio", name=label),
                 lambda: page.get_by_role("tab", name=label)):
        try:
            make().click(timeout=8000)
            return True
        except Exception:  # noqa: BLE001 — 다음 선택기로
            continue
    return False


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 2000})
    print("goto", URL)
    page.goto(URL, timeout=60000)
    try:
        page.wait_for_selector("text=inframon", timeout=40000)
    except Exception as e:  # noqa: BLE001
        print("제목 대기 경고:", e)
    page.wait_for_timeout(9000)

    # 캡처 대상 프로젝트 지정 — 사이드바 경로 입력에 채운다.
    try:
        box = page.get_by_label("project.h5 경로")
        box.fill(PROJECT)
        box.press("Enter")
        print("project =", PROJECT)
        page.wait_for_timeout(9000)
    except Exception as e:  # noqa: BLE001 — 실패하면 기본 경로 그대로 캡처
        print("경로 입력 경고:", e)

    for label, fn in TABS:
        ok = _click(page, label)
        print(f"{label}: click={'OK' if ok else 'FAIL'}")
        page.wait_for_timeout(10000)   # 탭 콘텐츠 + folium iframe 렌더
        path = f"{OUT}/{fn}.png"
        page.screenshot(path=path, full_page=True)
        print("saved", path)
    browser.close()
print("DONE")
