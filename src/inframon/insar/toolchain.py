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
    """기본 runner — WSL(기본 배포판) 로그인 셸에서 셸명령 실행 → (rc, stdout).

    WSL 이 없으면(예: 이미 Linux/컨테이너 안) 현재 셸에서 직접 실행한다. 프로브가 conda
    실행파일을 직접 경로로 찾으므로 별도 환경 활성화(prelude)는 필요 없다.
    """
    if shutil.which("wsl"):
        argv = ["wsl", "--", "bash", "-lc", cmd]
    else:
        argv = ["bash", "-lc", cmd]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=120)
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


WSL_INSTALL_CMD = "wsl --install -d Ubuntu-22.04"


def _run_argv(argv: list[str], timeout: int = 60) -> tuple[int, str]:
    """argv 실행 → (rc, 출력). wsl.exe 는 UTF-16 출력이라 NUL 을 걷어낸다."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        out = ((p.stdout or "") + (p.stderr or "")).replace("\x00", "")
        return p.returncode, out.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def wsl_status(run=_run_argv) -> dict:
    """0단계 게이트 — 도구(ISCE2 등) 이전에 **WSL 자체**가 있는지부터 판정한다.

    타 컴퓨터에서 가장 흔한 첫 오류가 "WSL 미설치"인데, 도구 감지만 돌리면 4종 전부
    ❌ 로만 나와 원인을 알 수 없다. 여기서 원인(reason)과 정확한 설치 명령을 돌려준다.
    reason: ok | not_windows(불필요) | wsl_exe_missing | no_distro
    """
    import sys
    if not sys.platform.startswith("win"):
        return {"required": False, "ready": True, "reason": "not_windows",
                "detail": "리눅스/컨테이너 — WSL 불필요, 현재 셸에서 직접 실행"}
    if not shutil.which("wsl"):
        return {"required": True, "ready": False, "reason": "wsl_exe_missing",
                "install_cmd": WSL_INSTALL_CMD,
                "detail": "wsl.exe 없음 — Windows 기능 'Linux용 Windows 하위 시스템' 미설치"}
    rc, out = run(["wsl", "-l", "-q"])
    distros = [d.strip() for d in out.splitlines() if d.strip()]
    if rc != 0 or not distros:
        return {"required": True, "ready": False, "reason": "no_distro",
                "install_cmd": WSL_INSTALL_CMD, "detail": out[:200]}
    return {"required": True, "ready": True, "reason": "ok", "distros": distros}


def format_wsl_report(st: dict) -> str:
    """wsl_status 결과를 사람이 읽는 안내로 — 미설치면 설치 명령을 크게 보여준다."""
    if st["ready"]:
        tail = f" (배포판: {', '.join(st['distros'])})" if st.get("distros") else ""
        return f"  ✅ WSL 준비됨{tail}" if st["required"] else f"  ✅ {st['detail']}"
    lines = ["=" * 56,
             "  ⛔ WSL 이 설치되어 있지 않습니다 — F코어(SARvey 레인)의 전제조건",
             "=" * 56,
             f"  원인: {st['detail']}",
             "  설치(관리자 PowerShell 에서 1회, 이후 재부팅):",
             f"    {st['install_cmd']}",
             "  재부팅 후 Ubuntu 첫 실행에서 사용자 생성 → 아래로 도구 구축:",
             "    python -m inframon --insar-tools-install",
             "-" * 56,
             "  ⓘ WSL 없이 쓰려면: --snap-auto(Windows 네이티브) 또는 --hyp3-insar(클라우드)",
             "    같은 Track H5 가 나와 하류는 동일합니다. docs/시작_Windows_SNAP.md 참고.",
             "=" * 56]
    return "\n".join(lines)


def provision_toolchain(runner=default_runner, *, stream=print) -> dict:
    """WSL 안에 툴체인을 **실제로 구축**한다 — 00_setup_env.sh 실행 후 재감지.

    ISCE2 는 필수로 강제한다: 설치 스크립트가 isce2 실패 시 중단하고(exit 1),
    여기서도 재감지 결과에 isce2 가 없으면 ok=False 로 돌려준다. 수 GB 다운로드·
    수십 분 소요 — 진행 출력은 stream 으로 흘린다.
    """
    from pathlib import Path
    repo = Path(__file__).resolve().parents[3]
    drive, tail = repo.drive[0].lower(), repo.as_posix()[2:]
    wsl_repo = f"/mnt/{drive}{tail}"        # E:\프로그램 → /mnt/e/프로그램
    cmd = f"cd '{wsl_repo}' && bash scripts/wsl_sarvey/00_setup_env.sh"

    stream(f">> WSL 에서 툴체인 구축 시작 (리포: {wsl_repo})")
    stream(">> ⚠️ 수백 MB~수 GB 다운로드, 수십 분 소요될 수 있습니다.")
    if shutil.which("wsl"):
        argv = ["wsl", "--", "bash", "-lc", cmd]
    else:
        argv = ["bash", "-lc", cmd]
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace")
        for line in p.stdout:                       # 설치 진행을 실시간으로 보여준다
            stream("  " + line.rstrip().replace("\x00", ""))
        rc = p.wait()
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "setup_rc": 127, "error": str(exc), "status": None}

    status = check_toolchain(runner=runner)         # 구축 후 재감지로 검증
    isce2_ok = "isce2" not in status["missing"] and "conda" not in status["missing"]
    error = None
    if rc != 0:
        error = "00_setup_env.sh 가 실패했습니다(위 출력 참고)."
    elif not isce2_ok:
        error = "설치는 끝났지만 ISCE2 가 감지되지 않습니다 — 재실행하거나 위 출력에서 원인을 확인하세요."
    return {"ok": rc == 0 and isce2_ok, "setup_rc": rc, "status": status, "error": error}


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
