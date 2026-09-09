#!/usr/bin/env python3
"""inframon 한 번에 시작 — 이 파일 하나만 실행하면 설치부터 결과까지 나온다.

    git clone https://github.com/tjddnr8334-sudo/inframon
    cd inframon
    python start.py

하는 일(순서대로, 이미 되어 있으면 건너뛴다):
  1. 파이썬 버전 확인(3.11+)
  2. 가상환경 `.venv` 생성 — 시스템 파이썬을 건드리지 않는다
  3. inframon 설치(코어 3개: numpy·h5py·pydantic)
  4. 데모 파이프라인 실행 → `demo.h5` (CV→InSAR→PINN→FRAM)
  5. 환경 진단 + "다음에 뭘 할 수 있는지" 안내

옵션:
    python start.py --dashboard   설치 후 대시보드까지 띄운다(브라우저 자동 열림)
    python start.py --full        **전부** — 대시보드·SLC 검색·HyP3·PINN·CV·트윈·그림·API (한 줄로 끝)
    python start.py --tools       외부 도구까지 — SNAP(1.1 GB 무인 설치)·snaphu(WSL)·Earthdata 토큰
    python start.py --no-demo     설치만 하고 데모는 건너뛴다

표준 라이브러리만 쓴다 — 이 파일을 돌리는 데 필요한 건 파이썬뿐이다.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import venv
from pathlib import Path

# 한국어 Windows 콘솔/파이프(cp949)에서 '—' 같은 글자로 죽지 않게 — --help 조차 못 찍던 것을 겪었다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
MIN_PY = (3, 11)
LINE = "=" * 62


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: int, total: int, msg: str) -> None:
    say(f"\n[{n}/{total}] {msg}")


def fail(msg: str, how: str = "") -> None:
    say(f"\n  ⛔ {msg}")
    if how:
        say(f"     → {how}")
    sys.exit(1)


def venv_python() -> Path:
    """가상환경의 파이썬 실행파일(OS 별 경로가 다르다)."""
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(args: list[str], *, quiet: bool = False, check: bool = True) -> int:
    """하위 프로세스 실행. 한국어 로캘에서도 깨지지 않게 UTF-8 을 강제한다."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    kw: dict = {"cwd": str(ROOT), "env": env}
    if quiet:
        kw["stdout"] = subprocess.DEVNULL
        kw["stderr"] = subprocess.STDOUT
    rc = subprocess.run(args, **kw).returncode
    if check and rc != 0:
        fail(f"명령이 실패했습니다(rc={rc}): {' '.join(args[:3])} …",
             "위 메시지를 확인하세요. 인터넷 연결·방화벽이 흔한 원인입니다.")
    return rc


def check_python() -> None:
    if sys.version_info < MIN_PY:
        fail(f"파이썬 {MIN_PY[0]}.{MIN_PY[1]} 이상이 필요합니다 "
             f"(지금 {platform.python_version()}).",
             "https://www.python.org/downloads/ 에서 최신 버전을 설치하세요"
             " (설치 화면에서 'Add python.exe to PATH' 체크).")
    say(f"  파이썬 {platform.python_version()} · {platform.system()} — 사용 가능")


def ensure_venv() -> Path:
    py = venv_python()
    if py.exists():
        say(f"  기존 가상환경 재사용: {VENV.name}")
        return py
    say(f"  가상환경 생성: {VENV.name} (한 번만 걸립니다)")
    venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
    if not py.exists():
        fail("가상환경을 만들지 못했습니다.",
             "python -m venv .venv 를 직접 실행해 오류를 확인하세요.")
    return py


def install(py: Path, extras: str | None) -> None:
    target = f".[{extras}]" if extras else "."
    say(f"  설치: {target}" + ("  (torch 포함 1~2 GB — 5~10분 걸릴 수 있습니다)" if extras == "full"
                            else "  (수백 MB — 몇 분)" if extras else ""))
    run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], quiet=True, check=False)
    run([str(py), "-m", "pip", "install", "-q", "-e", target])


def demo(py: Path) -> Path:
    out = ROOT / "demo.h5"
    say("  데모 파이프라인 실행 (CV→InSAR→PINN→FRAM)\n")
    run([str(py), "-m", "inframon", "--demo", "--out", str(out)])
    return out


def tools(py: Path) -> None:
    """SNAP·snaphu·Earthdata — 토큰은 사람이 붙여넣어야 하므로 stdin 을 그대로 물려준다."""
    say("")
    run([str(py), "-m", "inframon", "--setup-tools"], check=False)


def doctor(py: Path) -> None:
    say("")
    run([str(py), "-m", "inframon", "--doctor"], check=False)


def dashboard(py: Path) -> None:
    say("\n  대시보드를 띄웁니다 — 브라우저에서 http://localhost:8501")
    say("  (끄려면 이 창에서 Ctrl+C)\n")
    run([str(py), "-m", "streamlit", "run", str(ROOT / "src/inframon/dashboard/app.py"),
         "--server.port", "8501"], check=False)


def next_steps(py: Path, ran_demo: bool, tools_done: bool = False) -> None:
    p = py.relative_to(ROOT) if py.is_relative_to(ROOT) else py
    say(f"\n{LINE}\n  준비 완료 — 이제 할 수 있는 것\n{LINE}")
    if ran_demo:
        say("  · 방금 만든 결과      : demo.h5")
    say("  · 화면으로 보기        : python start.py --dashboard")
    say(f"  · 실 교량 시연 44초    : {p} scripts\\demo_4pm.py")
    say(f"  · 임의 교량 계획 보기  : {p} -m inframon --pipeline 37.5337,126.9366 "
        f"--pipeline-mode plan")
    say(f"  · 새 교량 끝까지       : {p} scripts\\bridge_run.py --name 마포대교 --lat 37.5337 --lon 126.9366")
    say(f"  · 이 PC 도구 상태      : {p} -m inframon --doctor")
    say("")
    if tools_done:
        say("  외부 도구(SNAP·snaphu·Earthdata 토큰·SLC 폴더)는 위 [외부 도구·자격] 줄대로입니다 —")
        say("  ❌ 가 남았으면        : python start.py --tools --no-demo  (빠진 것만 다시)")
    else:
        say("  실위성 데이터로 돌리려면 SNAP·snaphu·Earthdata 토큰·SLC 보관 폴더가 더 필요합니다 —")
        say("  · 전부 자동 준비      : python start.py --tools --no-demo")
    say("  단계별 안내: docs/시작하기.md")
    say(LINE)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="inframon 한 번에 시작 (설치 → 데모 → 안내)")
    ap.add_argument("--dashboard", action="store_true", help="설치 후 대시보드까지 띄운다")
    ap.add_argument("--full", action="store_true",
                    help="실데이터용 추가 패키지(dashboard·search·pinn)까지 설치")
    ap.add_argument("--tools", action="store_true",
                    help="외부 도구까지 — SNAP(1.1 GB)·snaphu(WSL)·Earthdata 토큰을 내려받아 준비")
    ap.add_argument("--no-demo", action="store_true", help="데모 실행을 건너뛴다")
    a = ap.parse_args()

    total = 4 + (1 if a.tools else 0) + (1 if a.dashboard else 0)
    say(f"{LINE}\n  inframon 시작 — 설치부터 결과까지\n{LINE}")

    step(1, total, "파이썬 확인")
    check_python()

    step(2, total, "가상환경 준비")
    py = ensure_venv()

    step(3, total, "inframon 설치")
    extras = "full" if a.full else ("dashboard" if a.dashboard else None)
    install(py, extras)
    n = 3

    if a.tools:
        n += 1
        step(n, total, "외부 도구 (SNAP · snaphu · Earthdata 토큰)")
        tools(py)

    ran = False
    n += 1
    if not a.no_demo:
        step(n, total, "데모 실행")
        demo(py)
        ran = True
    else:
        step(n, total, "환경 진단")
    doctor(py)

    if a.dashboard:
        step(n + 1, total, "대시보드")
        next_steps(py, ran, a.tools)
        dashboard(py)
        return
    next_steps(py, ran, a.tools)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\n  중단했습니다.")
        sys.exit(130)
