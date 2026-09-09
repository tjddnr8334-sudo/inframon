"""외부 도구 **자동 준비** — SNAP · snaphu · Earthdata 토큰.

doctor 가 "프로그램이 대신 못 하는 것" 으로 분류하던 세 가지 중, 실제로 대신 못 하는 것은
**Earthdata 계정 가입** 하나다. 나머지는 내려받아 깔 수 있다:

  · SNAP    — ESA 공식 install4j 설치기(1.1 GB)를 받아 `-q -dir` 무인 설치. 관리자 권한을
              피하려고 사용자 폴더(%LOCALAPPDATA%\\Programs\\esa-snap)에 넣고, 그 gpt 경로를
              ~/.inframon/tools.json 에 적어 find_gpt 가 찾게 한다.
  · snaphu  — Windows 네이티브 빌드가 없다. WSL 이 있으면 `wsl -u root apt-get install snaphu`
              (root 는 기본적으로 비밀번호가 없다). WSL 자체가 없으면 관리자 승격으로
              `wsl --install` 을 걸고 재부팅 뒤 다시 오라고 한다.
  · Earthdata — 브라우저를 토큰 페이지로 열고, 사용자가 붙여넣은 토큰을 CMR 에 한 번 찔러
              (Bearer 유효성) 확인한 뒤 ~/.inframon/earthdata_token 에 저장한다.

`python -m inframon --setup-tools` · `python start.py --tools`. 네트워크·외부 프로세스는
모두 이 모듈 안의 작은 함수로 격리해 테스트에서 monkeypatch 한다.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

TOOLS_FILE = Path.home() / ".inframon" / "tools.json"
DOWNLOAD_DIR = Path.home() / ".inframon" / "downloads"

SNAP_VERSION = "14.0.0"
SNAP_URLS = {
    "Windows": f"https://download.esa.int/step/snap/14.0/installers/esa-snap_sentinel_windows-{SNAP_VERSION}.exe",
    "Linux": f"https://download.esa.int/step/snap/14.0/installers/esa-snap_sentinel_unix-{SNAP_VERSION}.sh",
}
SNAP_SIZE_GB = 1.1
EARTHDATA_TOKEN_URL = "https://urs.earthdata.nasa.gov/profile"      # 로그인 → Generate Token
EARTHDATA_SIGNUP_URL = "https://urs.earthdata.nasa.gov/users/new"
# 토큰 유효성: CMR 은 잘못된 Bearer 에 401 "Token does not exist", 맞으면 200.
CMR_PROBE_URL = "https://cmr.earthdata.nasa.gov/search/collections.json?page_size=1&short_name=SENTINEL-1A_SLC"

Log = Callable[[str], None]


def snap_install_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local")) / "Programs"
    else:
        base = Path.home() / ".local"
    return base / "esa-snap"


def gpt_path_in(install_dir: Path) -> Path:
    return install_dir / "bin" / ("gpt.exe" if platform.system() == "Windows" else "gpt")


# ── tools.json ────────────────────────────────────────────────────────────
def read_tools() -> dict:
    try:
        return json.loads(TOOLS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_tool(key: str, value: str) -> Path:
    d = read_tools()
    d[key] = value
    TOOLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOOLS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return TOOLS_FILE


# ── 격리된 바깥세상 ───────────────────────────────────────────────────────
def download(url: str, dest: Path, log: Log = print) -> Path:
    """이어받기 없이 통째로. 같은 크기의 파일이 이미 있으면 다시 받지 않는다."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
    if total and dest.exists() and dest.stat().st_size == total:
        log(f"    이미 받아둔 파일 사용: {dest} ({total / 1e9:.2f} GB)")
        return dest
    log(f"    내려받기: {url}\n    → {dest} ({total / 1e9:.2f} GB)")
    tmp = dest.with_suffix(dest.suffix + ".part")
    done, last_pct = 0, -1
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = int(done * 100 / total)
                if pct // 5 != last_pct // 5:
                    last_pct = pct
                    log(f"    … {pct:3d}%  ({done / 1e9:.2f} / {total / 1e9:.2f} GB)")
    if total and done != total:
        tmp.unlink(missing_ok=True)
        raise OSError(f"다운로드가 중간에 끊겼습니다 ({done}/{total} B) — 다시 실행하면 이어서 시도합니다.")
    tmp.replace(dest)
    return dest


def run(args: list[str], *, timeout: int | None = None, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, errors="replace",
                          timeout=timeout, input=input_text)


def probe_token(token: str) -> tuple[bool, str]:
    req = urllib.request.Request(CMR_PROBE_URL, headers={"Authorization": f"Bearer {token.strip()}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200, f"CMR {r.status}"
    except urllib.error.HTTPError as e:            # 401 = 토큰이 틀림
        return False, f"CMR {e.code}: {e.read(200).decode('utf-8', 'replace')}"
    except OSError as e:
        return False, f"네트워크 오류 {e}"


# ── 상태 ─────────────────────────────────────────────────────────────────
def status() -> dict[str, dict]:
    """세 도구의 현재 상태 — doctor 와 같은 탐지기를 쓴다(따로 판단하면 어긋난다)."""
    out: dict[str, dict] = {}
    from .insar.slc_download import find_earthdata_token
    tok, src = find_earthdata_token()
    out["earthdata"] = {"ok": bool(tok), "where": src}
    try:
        from .insar.snap_backend import find_gpt
        out["snap"] = {"ok": True, "where": find_gpt()}
    except Exception as e:                       # noqa: BLE001  (SnapError = 없음)
        out["snap"] = {"ok": False, "where": "없음", "why": str(e)}
    from .insar.snap_unwrap import find_snaphu
    t = find_snaphu()
    out["snaphu"] = {"ok": t is not None, "where": t.describe() if t else "없음"}
    return out


# ── SNAP ─────────────────────────────────────────────────────────────────
def setup_snap(log: Log = print, *, install_dir: Path | None = None) -> bool:
    system = platform.system()
    url = SNAP_URLS.get(system)
    if not url:
        log(f"    {system} 은 자동 설치 미지원 — https://step.esa.int/main/download/snap-download/")
        return False
    target = install_dir or snap_install_dir()
    gpt = gpt_path_in(target)
    if gpt.exists():
        write_tool("snap_gpt", str(gpt))
        log(f"    이미 설치됨: {gpt}")
        return True
    installer = download(url, DOWNLOAD_DIR / url.rsplit("/", 1)[-1], log)
    log(f"    무인 설치 → {target}  (3~10분, 창이 뜨지 않습니다)")
    if system != "Windows":
        installer.chmod(0o755)
    # install4j 표준 무인 옵션. 사용자 폴더라 관리자 승격이 필요 없다.
    r = run([str(installer), "-q", "-dir", str(target), "-Dinstall4j.suppressUnattendedReboot=true"],
            timeout=3600)
    if not gpt.exists():
        log(f"    설치 실패(rc={r.returncode}). {r.stdout[-400:]} {r.stderr[-400:]}")
        log(f"    수동: {installer} 를 더블클릭해 설치한 뒤 다시 실행하세요.")
        return False
    write_tool("snap_gpt", str(gpt))
    log(f"    ✅ SNAP {SNAP_VERSION} — {gpt}  (경로 기록: {TOOLS_FILE})")
    return True


# ── snaphu ───────────────────────────────────────────────────────────────
def _wsl_distros() -> list[str]:
    if not shutil.which("wsl"):
        return []
    try:
        r = subprocess.run(["wsl", "-l", "-q"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    # wsl.exe 는 UTF-16LE 로 찍는다 — text=True 로 읽으면 글자 사이에 NUL 이 낀다.
    txt = r.stdout.decode("utf-16-le", "replace") if b"\x00" in r.stdout else r.stdout.decode("utf-8", "replace")
    return [ln.strip() for ln in txt.splitlines() if ln.strip()]


def setup_snaphu(log: Log = print) -> bool:
    from .insar.snap_unwrap import find_snaphu
    if find_snaphu():
        log(f"    이미 있음: {find_snaphu().describe()}")
        return True
    system = platform.system()
    if system == "Linux":
        if shutil.which("apt-get"):
            r = run(["sudo", "-n", "apt-get", "install", "-y", "snaphu"], timeout=600)
            if find_snaphu():
                log("    ✅ snaphu (apt)")
                return True
            log(f"    sudo 없이 못 깔았습니다(rc={r.returncode}) — 직접: sudo apt-get install -y snaphu")
        else:
            log("    직접: conda install -c conda-forge snaphu")
        return False
    if system != "Windows":
        log("    직접: conda install -c conda-forge snaphu")
        return False

    distros = _wsl_distros()
    if not distros:
        log("    snaphu 는 Windows 빌드가 없어 WSL(Ubuntu) 안에 깝니다. WSL 이 없으므로 먼저 설치합니다 —")
        log("    관리자 승인 창이 뜨면 '예'. 끝나면 **재부팅** 후 같은 명령을 다시 실행하세요.")
        cmd = ("Start-Process wsl -ArgumentList '--install -d Ubuntu --no-launch' -Verb RunAs -Wait")
        r = run(["powershell", "-NoProfile", "-Command", cmd], timeout=1800)
        if r.returncode == 0:
            log("    WSL 설치 명령을 걸었습니다 → 재부팅 후 다시: python start.py --tools")
        else:
            log(f"    승격이 거부됐거나 실패(rc={r.returncode}). 직접 관리자 PowerShell 에서: wsl --install -d Ubuntu")
        return False
    log(f"    WSL {distros[0]} 에 설치: apt-get install snaphu (1~3분)")
    r = run(["wsl", "-d", distros[0], "-u", "root", "--", "bash", "-lc",
             "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq snaphu"],
            timeout=900)
    t = find_snaphu()
    if t:
        log(f"    ✅ snaphu — {t.describe()}")
        return True
    log(f"    실패(rc={r.returncode}): {(r.stderr or r.stdout)[-400:]}")
    log(f"    직접: wsl -d {distros[0]} -u root -- apt-get install -y snaphu")
    return False


# ── Earthdata 토큰 ────────────────────────────────────────────────────────
def setup_earthdata(log: Log = print, *, token: str | None = None,
                    ask: Callable[[str], str] | None = None, open_browser: bool = True) -> bool:
    from .insar.slc_download import find_earthdata_token, save_earthdata_token
    have, src = find_earthdata_token()
    if have and token is None:
        log(f"    이미 있음: {src}")
        return True
    if token is None:
        if ask is None:
            log("    토큰은 사람이 발급받아야 합니다 (비대화 모드라 건너뜀):")
            log(f"      1) 계정: {EARTHDATA_SIGNUP_URL}   2) 로그인 → {EARTHDATA_TOKEN_URL} → Generate Token")
            log("      3) python -m inframon --earthdata-save <토큰>")
            return False
        log("    Earthdata 토큰 — 계정만 있으면 30초:")
        log(f"      1) 계정이 없으면 가입: {EARTHDATA_SIGNUP_URL}")
        log(f"      2) 로그인 → {EARTHDATA_TOKEN_URL} → [Generate Token] → 긴 문자열 복사")
        if open_browser:
            try:
                import webbrowser
                webbrowser.open(EARTHDATA_TOKEN_URL)
            except Exception:                    # noqa: BLE001
                pass
        token = ask("      3) 여기 붙여넣고 Enter (건너뛰려면 그냥 Enter): ").strip()
        if not token:
            log("    건너뜀 — 나중에: python -m inframon --earthdata-save <토큰>")
            return False
    ok, why = probe_token(token)
    if not ok:
        log(f"    ❌ 토큰이 유효하지 않습니다 ({why}). 저장하지 않았습니다.")
        return False
    path = save_earthdata_token(token)
    log(f"    ✅ Earthdata 토큰 확인({why}) → {path}")
    return True


# ── 진입점 ───────────────────────────────────────────────────────────────
def run_setup(which: str = "all", *, log: Log = print, interactive: bool | None = None,
              token: str | None = None) -> dict[str, bool]:
    """which: 'all' 또는 'snap,snaphu,earthdata' 부분집합. 결과 {도구: 준비됨}."""
    if interactive is None:
        interactive = sys.stdin.isatty()
    wanted = {"snap", "snaphu", "earthdata"} if which in ("", "all") else {w.strip() for w in which.split(",")}
    st = status()
    res: dict[str, bool] = {}
    log("[외부 도구 준비]")
    if "snap" in wanted:
        log(f"  SNAP gpt    : {'✅ ' + st['snap']['where'] if st['snap']['ok'] else '❌ 없음 → 내려받아 설치 (%.1f GB)' % SNAP_SIZE_GB}")
        res["snap"] = st["snap"]["ok"] or setup_snap(log)
    if "snaphu" in wanted:
        log(f"  snaphu      : {'✅ ' + st['snaphu']['where'] if st['snaphu']['ok'] else '❌ 없음 → WSL 에 설치'}")
        res["snaphu"] = st["snaphu"]["ok"] or setup_snaphu(log)
    if "earthdata" in wanted:
        log(f"  Earthdata   : {'✅ ' + st['earthdata']['where'] if st['earthdata']['ok'] else '❌ 토큰 없음'}")
        res["earthdata"] = st["earthdata"]["ok"] or setup_earthdata(
            log, token=token, ask=(input if interactive else None))
    missing = [k for k, v in res.items() if not v]
    log("  결과: " + (", ".join(f"{k} {'✅' if v else '❌'}" for k, v in res.items())))
    if missing:
        log("  남은 것은 다시 실행하면 이어서 합니다: python start.py --tools")
    return res
