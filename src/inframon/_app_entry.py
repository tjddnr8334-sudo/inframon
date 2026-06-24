"""패키징(.exe) 전용 진입점 — 더블클릭하면 데스크톱 앱이 뜬다.

PyInstaller 가 이 모듈을 진입점으로 빌드한다(`inframon.spec`).
  * 인자 없음(더블클릭)            → 데스크톱 창(run_app).
  * `--_run-streamlit PORT`(내부)  → 자식 프로세스가 Streamlit 서버 기동.

소스 실행은 기존처럼 `python -m inframon --app` 을 쓰면 된다(이 파일 불필요).
"""

from __future__ import annotations

import sys


def main() -> int:
    # frozen 콘솔(Windows cp949)에서도 한글·특수문자(—, ·) 출력이 깨지지 않도록 UTF-8 강제.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    from inframon.desktop import RUN_SERVER_FLAG, _run_streamlit_server, run_app

    if "--_selftest" in sys.argv:  # 번들 의존성 점검(특히 옵션 기능 asf_search/SLC 검색)
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

    if RUN_SERVER_FLAG in sys.argv:
        idx = sys.argv.index(RUN_SERVER_FLAG)
        _run_streamlit_server(int(sys.argv[idx + 1]))
        return 0
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
