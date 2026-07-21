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


def _log_path() -> Path:
    """windowed(.exe)에서 stdout/stderr 가 향하는 로그 파일 경로(_app_entry 와 동일 규칙)."""
    import tempfile
    return Path(tempfile.gettempdir()) / "inframon" / "inframon_app.log"


def _splash_html(message: str = "대시보드를 준비하고 있어요…",
                 sub: str = "위성 데이터·물리엔진을 불러오는 중입니다. 잠시만요.") -> str:
    """첫 실행 즉시 보여줄 로딩 화면(자체 완결 HTML — 서버 준비 전 빈 창 방지)."""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {{ color-scheme: light dark;
    --bg:#e9eef3; --card:#fff; --ink:#152230; --muted:#5d7385; --accent:#0d8ca3; --line:#d0dbe4; }}
  @media (prefers-color-scheme: dark) {{ :root {{
    --bg:#0c131b; --card:#121d28; --ink:#e7eef4; --muted:#859bad; --accent:#45c8de; --line:#253544; }} }}
  * {{ box-sizing:border-box; }}
  html,body {{ height:100%; margin:0; }}
  body {{ background:var(--bg); color:var(--ink); display:grid; place-items:center;
    font-family:system-ui,"Malgun Gothic","Apple SD Gothic Neo",sans-serif; }}
  .box {{ text-align:center; padding:40px; max-width:440px; }}
  .brand {{ font-family:ui-monospace,"Cascadia Code",Consolas,monospace; letter-spacing:.28em;
    text-transform:uppercase; font-size:13px; color:var(--accent); margin-bottom:24px; }}
  .ring {{ width:52px; height:52px; margin:0 auto 26px; border-radius:50%;
    border:3px solid var(--line); border-top-color:var(--accent); animation:spin 1s linear infinite; }}
  @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
  @media (prefers-reduced-motion:reduce) {{ .ring {{ animation:none; border-top-color:var(--accent); }} }}
  h1 {{ font-size:19px; font-weight:650; margin:0 0 8px; letter-spacing:-.01em; }}
  p {{ font-size:13.5px; color:var(--muted); margin:0; line-height:1.55; }}
</style></head>
<body><div class="box">
  <div class="brand">🛰 inframon</div>
  <div class="ring"></div>
  <h1>{message}</h1>
  <p>{sub}</p>
</div></body></html>"""


def _error_html(log: Path) -> str:
    """서버 기동 실패 시 창에 띄울 진단 화면(로그 위치를 직접 안내)."""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {{ color-scheme: light dark;
    --bg:#e9eef3; --card:#fff; --ink:#152230; --muted:#5d7385; --accent:#c85f31; --line:#d0dbe4; }}
  @media (prefers-color-scheme: dark) {{ :root {{
    --bg:#0c131b; --card:#121d28; --ink:#e7eef4; --muted:#859bad; --accent:#ef9061; --line:#253544; }} }}
  * {{ box-sizing:border-box; }}
  html,body {{ height:100%; margin:0; }}
  body {{ background:var(--bg); color:var(--ink); display:grid; place-items:center;
    font-family:system-ui,"Malgun Gothic","Apple SD Gothic Neo",sans-serif; padding:24px; }}
  .box {{ max-width:560px; background:var(--card); border:1px solid var(--line);
    border-radius:12px; padding:30px 32px; }}
  .brand {{ font-family:ui-monospace,Consolas,monospace; letter-spacing:.2em; text-transform:uppercase;
    font-size:12px; color:var(--accent); margin-bottom:16px; }}
  h1 {{ font-size:20px; margin:0 0 12px; letter-spacing:-.01em; }}
  p {{ font-size:14px; color:var(--muted); line-height:1.6; margin:0 0 14px; }}
  code {{ font-family:ui-monospace,Consolas,monospace; font-size:12.5px; background:var(--bg);
    border:1px solid var(--line); border-radius:6px; padding:2px 6px; word-break:break-all; }}
  ol {{ font-size:13.5px; color:var(--muted); line-height:1.7; padding-left:20px; margin:0; }}
</style></head>
<body><div class="box">
  <div class="brand">⚠ inframon</div>
  <h1>대시보드를 시작하지 못했습니다</h1>
  <p>서버가 제한시간 안에 응답하지 않았습니다. 아래 로그에서 원인을 확인할 수 있어요:</p>
  <p><code>{log}</code></p>
  <ol>
    <li>이미 다른 inframon 창이 열려 있는지 확인하세요.</li>
    <li>백신·방화벽이 로컬 포트를 막지 않는지 확인하세요.</li>
    <li>계속 실패하면 위 로그 파일을 첨부해 문의해 주세요.</li>
  </ol>
</div></body></html>"""


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

    # 창을 '먼저' 스플래시로 띄운다 — 더블클릭 즉시 반응이 보이므로, 서버가 뜨는
    # 수십 초 동안 빈 화면(→ 사용자가 앱이 죽은 줄 알고 재실행)이 생기지 않는다.
    window = webview.create_window(APP_TITLE, html=_splash_html(),
                                   width=1280, height=860, min_size=(900, 600))
    state = {"code": 0}

    def _boot() -> None:
        """GUI 가 뜬 뒤 별도 스레드에서 서버 준비를 기다렸다가 대시보드로 전환."""
        if _wait_until_up(url, proc):
            print(f"[inframon] 준비 완료 — 대시보드로 전환: {url}")
            try:
                window.load_url(url)
            except Exception as exc:  # noqa: BLE001 — 전환 실패해도 창은 유지
                print(f"[inframon] 창 전환 실패: {exc!r}", file=sys.stderr)
                state["code"] = 1
        else:
            log = _log_path()
            print(f"[inframon] 대시보드 서버 시작 실패 — 로그: {log}", file=sys.stderr)
            state["code"] = 1
            try:
                window.load_html(_error_html(log))
            except Exception:  # noqa: BLE001
                pass

    try:
        webview.start(_boot)  # 창이 닫힐 때까지 블록(_boot 은 내부 스레드로 실행)
        return state["code"]
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
