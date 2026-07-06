"""InSAR F코어 처리도구(ISCE2/MiaplPy/SARvey) 감지·프로비저닝 안내 — 프로그램 관리.

바이너리 자체는 파이썬 패키지에 담을 수 없다(각 수 GB·ISCE2 컴파일·시스템 라이브러리).
대신 inframon 이 **도구 존재를 감지**하고, 없으면 리포에 저장된 **재현가능 레시피**(conda
`scripts/wsl_sarvey/00_setup_env.sh` 또는 컨테이너 `scripts/wsl_sarvey/Dockerfile`)로 구축하도록
안내한다 — "프로그램 내 저장"은 이 재현 레시피 + 감지/구동 계층으로 실현한다.

감지 명령(WSL/컨테이너 셸에서 실행)은 `probe_commands()` 로 노출하고, 실제 실행은 주입
가능한 `runner(cmd)->(rc, out)` 로 격리한다(테스트에서 가짜 runner 주입).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

# (키, 사람이름, 프로브 셸명령) — conda 환경 안/밖 어디서든 존재를 확인.
PROBES: list[tuple[str, str, str]] = [
    ("conda", "conda/mamba (환경관리자)", "command -v conda || command -v mamba"),
    ("isce2", "ISCE2 (스택·코레지스트레이션)",
     "python3 -c 'import isce' 2>/dev/null && echo isce || command -v stackSentinel.py"),
    ("miaplpy", "MiaplPy/MintPy (위상연결)",
     "python3 -c 'import miaplpy' 2>/dev/null && echo miaplpy || "
     "python3 -c 'import mintpy' 2>/dev/null && echo mintpy"),
    ("sarvey", "SARvey (MTI 시계열)",
     "python3 -c 'import sarvey' 2>/dev/null && echo sarvey || command -v sarvey"),
]

SETUP_CONDA = "bash scripts/wsl_sarvey/00_setup_env.sh"
SETUP_CONTAINER = "docker build -t inframon-insar -f scripts/wsl_sarvey/Dockerfile ."


@dataclass
class ToolStatus:
    key: str
    label: str
    found: bool
    detail: str


def default_runner(cmd: str) -> tuple[int, str]:
    """기본 runner — WSL(기본 배포판) 로그인 셸에서 셸명령 실행 → (rc, stdout).

    WSL 이 없으면(예: 이미 Linux/컨테이너 안) 현재 셸에서 직접 실행한다.
    """
    if shutil.which("wsl"):
        argv = ["wsl", "--", "bash", "-lc", cmd]
    else:
        argv = ["bash", "-lc", cmd]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        return p.returncode, (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def check_toolchain(runner=default_runner) -> dict:
    """F코어 도구 4종을 감지해 준비상태·부족분·프로비저닝 명령을 돌려준다."""
    statuses: list[ToolStatus] = []
    for key, label, probe in PROBES:
        rc, out = runner(probe)
        found = rc == 0 and bool(out.strip())
        statuses.append(ToolStatus(key, label, found, out.strip()[:120]))
    missing = [s.key for s in statuses if not s.found]
    ready = not missing
    return {
        "ready": ready,
        "tools": [{"key": s.key, "label": s.label, "found": s.found, "detail": s.detail}
                  for s in statuses],
        "missing": missing,
        "provision": None if ready else {
            "conda": SETUP_CONDA,
            "container": SETUP_CONTAINER,
            "note": "도구 바이너리는 리포에 담기 불가(수 GB·컴파일). 위 레시피로 1회 구축하면 "
                    "이후 재사용된다. 컨테이너 정의(Dockerfile)는 리포에 버전 저장됨.",
        },
    }


def format_report(status: dict) -> str:
    """check_toolchain 결과를 사람이 읽는 리포트 문자열로."""
    lines = ["=" * 56, "  InSAR F코어 처리도구 상태 (ISCE2/MiaplPy/SARvey)", "=" * 56]
    for t in status["tools"]:
        mark = "✅" if t["found"] else "❌"
        lines.append(f"  {mark} {t['label']}"
                     + (f"  [{t['detail']}]" if t["found"] and t["detail"] else ""))
    lines.append("-" * 56)
    if status["ready"]:
        lines.append("  준비 완료 — WSL2 F코어 실행 가능")
    else:
        pv = status["provision"]
        lines.append(f"  미설치: {status['missing']}")
        lines.append("  구축(둘 중 하나, 리포에 저장된 재현 레시피):")
        lines.append(f"    · conda     : {pv['conda']}")
        lines.append(f"    · container : {pv['container']}")
        lines.append(f"  ⚠️ {pv['note']}")
    lines.append("=" * 56)
    return "\n".join(lines)
