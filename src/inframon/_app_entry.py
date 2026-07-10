"""패키징(.exe) 전용 진입점 — 더블클릭하면 데스크톱 앱이 뜬다.

PyInstaller 가 이 모듈을 진입점으로 빌드한다(`inframon.spec`).
  * 인자 없음(더블클릭)            → 데스크톱 창(run_app).
  * `--_run-streamlit PORT`(내부)  → 자식 프로세스가 Streamlit 서버 기동.

소스 실행은 기존처럼 `python -m inframon --app` 을 쓰면 된다(이 파일 불필요).
"""

from __future__ import annotations

import sys


def main() -> int:
    # windowed(.exe, console=False) 에선 sys.stdout/stderr 가 None 이라 어떤 print() 든
    # AttributeError 로 죽는다(PyInstaller GUI 앱의 전형적 함정). None 이면 로그파일로
    # 돌려 모든 출력을 안전하게 하고(창 경로의 print 들도 동작), 크래시도 파일로 남긴다.
    if sys.stdout is None or sys.stderr is None:
        import os
        import tempfile
        logdir = os.path.join(tempfile.gettempdir(), "inframon")
        os.makedirs(logdir, exist_ok=True)
        _logf = open(os.path.join(logdir, "inframon_app.log"), "a",
                     encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _logf
        if sys.stderr is None:
            sys.stderr = _logf

    # frozen 콘솔(Windows cp949)에서도 한글·특수문자(—, ·) 출력이 깨지지 않도록 UTF-8 강제.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    if "--_selftest" in sys.argv:  # 번들 의존성 점검(옵션 기능 포함). desktop import 불필요
        ok = True
        for mod in ("asf_search", "shapely", "dateparser", "networkx",
                    "requests", "urllib3", "streamlit", "h5py"):
            try:
                __import__(mod)
                print(f"OK    {mod}")
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"FAIL  {mod}: {exc!r}")
        return 0 if ok else 1

    if "--_run-streamlit" in sys.argv:   # 내부: Streamlit 서버 기동(대시보드용)
        from inframon.desktop import _run_streamlit_server
        idx = sys.argv.index("--_run-streamlit")
        _run_streamlit_server(int(sys.argv[idx + 1]))
        return 0

    # 인자가 있으면(예: inframon_full.exe --snap-auto …) 처리엔진 CLI 로 위임(풀 exe).
    # GUI 라이브러리(pywebview) 없이 도는 처리 파이프라인 겸용. 무인자면 대시보드 창.
    if len(sys.argv) > 1:
        from inframon.__main__ import main as cli_main
        return cli_main() or 0

    from inframon.desktop import run_app
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
