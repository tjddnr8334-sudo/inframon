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
    python start.py --full        실데이터용 추가 패키지까지(대시보드·위성조회·PINN)
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
    say(f"  설치: {target}" + ("  (수백 MB — 몇 분 걸릴 수 있습니다)" if extras else ""))
    run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], quiet=True, check=False)
    run([str(py), "-m", "pip", "install", "-q", "-e", target])


def demo(py: Path) -> Path:
    out = ROOT / "demo.h5"
    say("  데모 파이프라인 실행 (CV→InSAR→PINN→FRAM)\n")
    run([str(py), "-m", "inframon", "--demo", "--out", str(out)])
    return out


def doctor(py: Path) -> None:
    say("")
    run([str(py), "-m", "inframon", "--doctor"], check=False)


def dashboard(py: Path) -> None:
    say("\n  대시보드를 띄웁니다 — 브라우저에서 http://localhost:8501")
    say("  (끄려면 이 창에서 Ctrl+C)\n")
    run([str(py), "-m", "streamlit", "run", str(ROOT / "src/inframon/dashboard/app.py"),
         "--server.port", "8501"], check=False)


def next_steps(py: Path, ran_demo: bool) -> None:
    p = py.relative_to(ROOT) if py.is_relative_to(ROOT) else py
    say(f"\n{LINE}\n  준비 완료 — 이제 할 수 있는 것\n{LINE}")
    if ran_demo:
        say("  · 방금 만든 결과      : demo.h5")
    say("  · 화면으로 보기        : python start.py --dashboard")
    say(f"  · 임의 교량 계획 보기  : {p} -m inframon --pipeline 36.4507,126.8073 "
        f"--pipeline-mode plan")
    say(f"  · 이 PC 도구 상태      : {p} -m inframon --insar-tools")
    say(f"  · 산출물 품질 감사     : {p} -m inframon --audit-artifacts")
    say("")
    say("  실위성 데이터로 돌리려면 SNAP·snaphu·교량 CSV 가 더 필요합니다 —")
    say("  단계별 안내: docs/시작하기.md")
    say(LINE)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="inframon 한 번에 시작 (설치 → 데모 → 안내)")
    ap.add_argument("--dashboard", action="store_true", help="설치 후 대시보드까지 띄운다")
    ap.add_argument("--full", action="store_true",
                    help="실데이터용 추가 패키지(dashboard·search·pinn)까지 설치")
    ap.add_argument("--no-demo", action="store_true", help="데모 실행을 건너뛴다")
    a = ap.parse_args()

    total = 4 + (1 if a.dashboard else 0)
    say(f"{LINE}\n  inframon 시작 — 설치부터 결과까지\n{LINE}")

    step(1, total, "파이썬 확인")
    check_python()

    step(2, total, "가상환경 준비")
    py = ensure_venv()

    step(3, total, "inframon 설치")
    extras = "dashboard,search,pinn" if a.full else ("dashboard" if a.dashboard else None)
    install(py, extras)

    ran = False
    if not a.no_demo:
        step(4, total, "데모 실행")
        demo(py)
        ran = True
    else:
        step(4, total, "환경 진단")
    doctor(py)

    if a.dashboard:
        step(5, total, "대시보드")
        next_steps(py, ran)
        dashboard(py)
        return
    next_steps(py, ran)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\n  중단했습니다.")
        sys.exit(130)
