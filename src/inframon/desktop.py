"""데스크톱 런처 — Streamlit 대시보드를 전용 창에 띄운다.

`python -m inframon --app` (또는 더블클릭용 `inframon_app.bat` / 패키징된 `inframon.exe`)로
실행하면:
  1. Streamlit 대시보드를 백그라운드(headless)로 띄우고,
  2. 브라우저 대신 pywebview 전용 창에 감싸서 보여준다(주소창 없는 앱 느낌).
  3. 창을 닫으면 백그라운드 Streamlit 도 함께 종료한다.

소스 실행과 PyInstaller 패키징(.exe) 양쪽을 지원한다:
  * 소스: 자기 자신을 `python -m inframon --_run-streamlit PORT` 로 재실행해 서버를 띄움.
  * frozen(.exe): `sys.executable` 가 곧 앱이므로 `inframon.exe --_run-streamlit PORT` 로 재실행.
두 경우 모두 Streamlit 을 `streamlit run` 과 동일하게 in-process(`cli._main_run`)로 기동한다.

지금은 로컬 단독 실행용. 같은 대시보드를 서버에서 `streamlit run` 으로 띄우면
여러 사용자가 브라우저로 접속하는 형태로 그대로 확장된다(UI 코드 재사용).

의존성: `pip install pywebview` (+ 대시보드 의존성 `.[dashboard]`).
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_TITLE = "inframon — 통합 인프라 모니터링"

#: 런처가 서버 자식 프로세스를 띄울 때 쓰는 내부 플래그(사용자용 아님).
RUN_SERVER_FLAG = "--_run-streamlit"


def _free_port() -> int:
    """OS 가 비어 있는 TCP 포트를 하나 골라준다."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _dashboard_path() -> Path:
    """패키지에 동봉된 dashboard/app.py 의 절대경로(소스/frozen 모두)."""
    if getattr(sys, "frozen", False):  # PyInstaller 번들: 데이터로 동봉됨
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "inframon" / "dashboard" / "app.py"
    return Path(__file__).resolve().parent / "dashboard" / "app.py"


def _run_streamlit_server(port: int) -> None:
    """이 프로세스에서 Streamlit 대시보드를 in-process 로 기동(블록).

    `streamlit run` 의 내부 동작(`cli._main_run`)을 click 컨텍스트 의존부 없이 재현한다:
    `_main_script_path` 설정 → `load_config_options` → `bootstrap.run`. 프로세스를 점유하므로,
    런처는 이 함수를 자식 프로세스에서 실행한다.
    """
    import os

    from streamlit import config as st_config
    from streamlit.web import bootstrap

    app_py = os.path.abspath(str(_dashboard_path()))
    flag_options = {  # 키는 CLI 플래그형(언더스코어) — 내부에서 '.' 로 치환됨
        "server_address": "127.0.0.1",
        "server_port": int(port),
        "server_headless": True,
        "browser_gatherUsageStats": False,
        "global_developmentMode": False,
    }
    # config/secret 파일 탐색 기준이 되는 메인 스크립트 경로(load 전에 설정해야 함)
    st_config._main_script_path = app_py
    bootstrap.load_config_options(flag_options=flag_options)
    bootstrap.run(app_py, is_hello=False, args=[], flag_options=flag_options)


def _server_command(port: int) -> list[str]:
    """서버 자식 프로세스를 띄울 커맨드(소스/frozen 분기)."""
    if getattr(sys, "frozen", False):  # PyInstaller 등으로 패키징된 경우
        return [sys.executable, RUN_SERVER_FLAG, str(port)]
    return [sys.executable, "-m", "inframon", RUN_SERVER_FLAG, str(port)]


def _wait_until_up(url: str, proc: subprocess.Popen, timeout: float = 90.0) -> bool:
    """Streamlit 서버가 응답할 때까지 (또는 죽을 때까지) 대기."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:  # 서버가 떠보기도 전에 종료됨
            return False
        try:
            with urllib.request.urlopen(url, timeout=1):  # noqa: S310 — 로컬 고정 URL
                return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.3)
    return False


def run_app() -> int:
    """대시보드를 전용 창에 띄운다. 종료 코드를 반환."""
    try:
        import webview  # pywebview
    except ImportError:
        print("데스크톱 창에는 pywebview 가 필요합니다: `pip install pywebview`", file=sys.stderr)
        return 1

    if not _dashboard_path().exists():
        print(f"대시보드를 찾을 수 없습니다: {_dashboard_path()}", file=sys.stderr)
        return 1

    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    print(f"[inframon] 대시보드 시작 중 …  {url}")
    proc = subprocess.Popen(_server_command(port))

    try:
        if not _wait_until_up(url, proc):
            print("[inframon] 대시보드 서버 시작 실패 (로그를 확인하세요).", file=sys.stderr)
            proc.terminate()
            return 1

        print(f"[inframon] 준비 완료 — 창을 엽니다: {url}")
        webview.create_window(APP_TITLE, url, width=1280, height=860, min_size=(900, 600))
        webview.start()  # 창이 닫힐 때까지 블록
        return 0
    finally:
        # 창이 닫히면 Streamlit 백그라운드도 정리
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("[inframon] 종료.")


if __name__ == "__main__":
    raise SystemExit(run_app())
