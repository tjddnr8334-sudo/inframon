"""InSAR F코어 처리도구(ISCE2/MiaplPy/SARvey) 감지·프로비저닝 안내 — 프로그램 관리.

바이너리 자체는 파이썬 패키지에 담을 수 없다(각 수 GB·ISCE2 컴파일·시스템 라이브러리).
대신 inframon 이 **도구 존재를 감지**하고, 없으면 리포에 저장된 **재현가능 레시피**(conda
`scripts/wsl_sarvey/00_setup_env.sh` 또는 컨테이너 `scripts/wsl_sarvey/Dockerfile`)로 구축하도록
안내한다 — "프로그램 내 저장"은 이 재현 레시피 + 감지/구동 계층으로 실현한다.

도구들은 각각 **별도 conda 환경**(isce2/miaplpy/sarvey)에 설치되고, non-interactive WSL
셸에서 conda 는 PATH·셸함수로 안 잡히므로 conda **실행파일 직접 경로 + `conda run -n <env>`**
로 각 환경 안에서 import 를 확인한다. 실제 셸 실행은 주입 가능한 `runner(cmd)->(rc, out)`
로 격리한다(테스트에서 가짜 runner 주입).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass

# conda 실행파일 후보(직접 경로). ⚠️ 이 WSL 호출 방식에선 **새 셸변수·for 루프가 빈 값**이
# 되므로($HOME·명령치환·&&/|| 만 정상) 변수 없이 직접 경로 || 체인으로만 탐색한다.
CONDA_BINS: tuple[str, ...] = (
    "$HOME/miniforge3/bin/conda",
    "$HOME/miniconda3/bin/conda",
    "$HOME/anaconda3/bin/conda",
    "/opt/conda/bin/conda",
    "conda",  # 이미 PATH 에 있으면(컨테이너/활성화된 셸)
)


def _conda_probe() -> str:
    """conda 실행파일 자체 존재 확인 — 어느 후보든 `--version` 이 되면 발견."""
    alts = " || ".join(f"{c} --version" for c in CONDA_BINS)
    return "{ " + alts + "; } 2>/dev/null"


def _env_probe(env: str, marker: str, *modules: str) -> str:
    """<env> 환경에서 modules 중 하나라도 import 되면 marker 를 출력(발견)."""
    alts = " || ".join(f"{c} run -n {env} python -c 'import {m}'"
                       for c in CONDA_BINS for m in modules)
    return "{ " + alts + f"; }} 2>/dev/null && echo {marker}"


# (키, 사람이름, 감지 셸명령) — 변수/루프 없이 self-contained. 발견 시 표식(버전/이름) 출력.
PROBES: list[tuple[str, str, str]] = [
    ("conda", "conda/mamba (환경관리자)", _conda_probe()),
    ("isce2", "ISCE2 (스택·코레지스트레이션)", _env_probe("isce2", "isce", "isce")),
    ("miaplpy", "MiaplPy/MintPy (위상연결)",
     _env_probe("miaplpy", "miaplpy", "miaplpy", "mintpy")),
    ("sarvey", "SARvey (MTI 시계열)", _env_probe("sarvey", "sarvey", "sarvey")),
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
    """기본 runner — 리눅스 로그인 셸에서 셸명령 실행 → (rc, stdout).

    리눅스/컨테이너 안이면 bash 로 직접, Windows 면 WSL(기본 배포판)을 경유한다.
    OS 를 먼저 보는 이유: Windows 의 Git Bash 도 `bash` 로 잡히지만 ISCE2/conda 가
    없는 별개 환경이라, WSL 대신 거기로 빠지면 오탐이 난다.
    프로브가 conda 실행파일을 직접 경로로 찾으므로 별도 환경 활성화(prelude)는 필요 없다.
    """
    if sys.platform != "win32":
        argv = ["bash", "-lc", cmd]
    elif shutil.which("wsl"):
        argv = ["wsl", "--", "bash", "-lc", cmd]
    else:
        return 127, ""  # Windows + WSL 없음 → 리눅스 툴체인 도달 불가
    try:
        # encoding 을 명시하지 않으면 한글 Windows 에서 locale(cp949)로 디코딩해
        # UTF-8 을 내보내는 WSL/conda 출력에 UnicodeDecodeError 가 난다.
        p = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def check_toolchain(runner=default_runner) -> dict:
    """F코어 도구 4종을 감지해 준비상태·부족분·프로비저닝 명령을 돌려준다."""
    statuses: list[ToolStatus] = []
    for key, label, probe in PROBES:
        rc, out = runner(probe)
        found = rc == 0 and bool(out.strip())
        # 표식은 항상 마지막 줄(발견 echo) — import 시 라이브러리가 찍는 배너는 버린다.
        detail = out.strip().splitlines()[-1][:120] if out.strip() else ""
        statuses.append(ToolStatus(key, label, found, detail))
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
