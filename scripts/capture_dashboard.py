"""playwright 로 라이브 Streamlit 대시보드 탭별 스크린샷."""
import sys
from playwright.sync_api import sync_playwright

URL = "http://localhost:8501"
TABS = [("① InSAR", "dashboard_insar"), ("② PINN", "dashboard_pinn"), ("③ FRAM", "dashboard_fram")]
OUT = "docs/img"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 2000})
    print("goto", URL)
    page.goto(URL, timeout=60000)
    # Streamlit 초기 렌더 대기 (제목 등장)
    try:
        page.wait_for_selector("text=inframon", timeout=40000)
    except Exception as e:  # noqa: BLE001
        print("제목 대기 경고:", e)
    page.wait_for_timeout(9000)   # 위젯·차트 렌더

    for label, fn in TABS:
        clicked = False
        for sel in (lambda: page.get_by_role("tab", name=label),
                    lambda: page.get_by_text(label, exact=False).first):
            try:
                sel().click(timeout=12000)
                clicked = True
                break
            except Exception:  # noqa: BLE001
                continue
        print(f"{label}: click={'OK' if clicked else 'FAIL'}")
        page.wait_for_timeout(10000)   # 탭 콘텐츠 + folium iframe 렌더
        path = f"{OUT}/{fn}.png"
        page.screenshot(path=path, full_page=True)
        print("saved", path)
    browser.close()
print("DONE")
