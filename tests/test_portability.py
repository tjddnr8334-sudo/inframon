"""크로스플랫폼(Windows/Linux) 이식성 회귀 테스트.

여기서 잡는 실제 버그들:
  * gpt 그래프(.xml)가 패키지 데이터로 동봉되지 않아 wheel/frozen 설치에서만 사라지는 문제
  * 한글 Windows(cp949)에서 UTF-8 출력을 디코딩하다 UnicodeDecodeError 로 죽는 문제
  * `wsl` 유무로만 판단해 네이티브 리눅스를 "환경 없음"으로 오판하는 문제
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from inframon.insar import snap_backend as sb


# ── 패키지 데이터: gpt 그래프 동봉 ────────────────────────────────────────
def test_graphs_ship_with_the_package():
    """그래프는 패키지 안에 있어야 한다 — 리포 루트(scripts/)를 더듬으면 안 된다.

    `parents[3]` 로 리포 루트를 추정하던 옛 방식은 소스 체크아웃에서만 동작해,
    wheel 설치·PyInstaller 번들에서 "gpt rc=1" 로만 실패했다.
    """
    gdir = sb._default_graph_dir()
    assert gdir.is_dir(), f"그래프 디렉터리가 없습니다: {gdir}"
    assert (gdir / sb._GRAPH_TC).is_file()
    assert (gdir / sb._GRAPH_AMP).is_file()
    # 패키지 내부에 있어야 위치와 무관하게 따라다닌다.
    assert gdir.parent == Path(sb.__file__).resolve().parent


def test_resolve_graph_names_the_packaging_problem():
    """그래프가 없으면 gpt 를 돌리기 전에, 패키징 문제임을 알 수 있게 실패해야 한다."""
    with pytest.raises(FileNotFoundError, match="graphs"):
        sb._resolve_graph("/nonexistent-graph-dir", "coreg_ifg_tc.xml")


# ── 인코딩: 한글 Windows(cp949) ───────────────────────────────────────────
def test_run_pair_decodes_gpt_output_as_utf8(monkeypatch):
    """gpt 출력은 UTF-8 로 디코딩해야 한다(locale 기본값이면 cp949 → 크래시)."""
    seen = {}

    class _P:
        returncode = 0

    def fake_run(args, **kw):
        seen.update(kw)
        return _P()

    monkeypatch.setattr("subprocess.run", fake_run)
    sb.run_pair("gpt", "graph.xml", "S1A_IW_SLC__1SDV_20240107T093202_x.zip",
                "S1A_IW_SLC__1SDV_20240119T093202_x.zip",
                sb.BurstLoc("IW3", 9, 2.6, 37.33, 127.13),
                "SRTM 1Sec HGT", "out.tif")
    assert seen.get("encoding") == "utf-8"
    assert seen.get("errors") == "replace"


def test_toolchain_runner_decodes_as_utf8(monkeypatch):
    """WSL/conda 출력도 UTF-8 — 한글 Windows 에서 UnicodeDecodeError 가 나면 안 된다."""
    from inframon.insar import toolchain

    seen = {}

    class _P:
        returncode = 0
        stdout = "ok"

    monkeypatch.setattr(toolchain.sys, "platform", "linux")
    monkeypatch.setattr(toolchain.subprocess, "run",
                        lambda argv, **kw: (seen.update(kw), _P())[1])
    toolchain.default_runner("echo hi")
    assert seen.get("encoding") == "utf-8"


# ── 리눅스 셸 탐지: 네이티브 vs WSL ──────────────────────────────────────
def test_linux_shell_uses_bash_directly_on_linux(monkeypatch):
    """네이티브 리눅스에서는 WSL 을 거치지 않고 bash 를 직접 쓴다.

    회귀 방지: 예전엔 `wsl` 실행파일 유무만 봐서, 정작 리눅스에서
    "WSL2 가 없어 실 SAR 처리 불가" 라고 안내했다.
    """
    from inframon.dashboard import app

    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.setattr(app.shutil, "which", lambda n: "/bin/bash" if n == "bash" else None)
    prefix, mode = app.linux_shell()
    assert mode == "native"
    assert prefix == ["/bin/bash", "-lc"]
    assert "wsl" not in prefix


def test_linux_shell_uses_wsl_on_windows(monkeypatch):
    from inframon.dashboard import app

    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(app.shutil, "which", lambda n: r"C:\Windows\wsl.exe" if n == "wsl" else None)
    prefix, mode = app.linux_shell()
    assert mode == "wsl"
    assert prefix[0] == "wsl"


def test_linux_shell_ignores_git_bash_on_windows(monkeypatch):
    """Windows 에 WSL 이 없으면 도달 불가 — Git Bash 로 빠지면 안 된다.

    Git Bash 도 `bash` 로 잡히지만 ISCE2/conda 가 없는 별개 환경이라 오탐이 된다.
    """
    from inframon.dashboard import app

    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(app.shutil, "which",
                        lambda n: r"C:\Program Files\Git\bin\bash.exe" if n == "bash" else None)
    assert app.linux_shell() is None


def test_linux_status_reports_unreachable_without_a_shell(monkeypatch):
    from inframon.dashboard import app

    monkeypatch.setattr(app, "linux_shell", lambda: None)
    st = app.linux_status()
    assert st["linux"] is False
    assert st["mode"] == ""
    assert st["detail"], "왜 도달 불가인지 알려줘야 한다"


# ── 동봉 스크립트 경로 ────────────────────────────────────────────────────
def test_bundled_script_is_found_regardless_of_cwd(tmp_path, monkeypatch):
    """dl_urls.py 는 CWD 가 아니라 패키지 기준으로 찾아야 한다(더블클릭 실행 대비)."""
    from inframon.dashboard import app

    monkeypatch.chdir(tmp_path)  # 리포와 무관한 폴더에서 실행하는 상황
    p = app.bundled_script("dl_urls.py")
    assert p.is_file(), f"동봉 스크립트를 찾지 못했습니다: {p}"


# ── 에러 진단: "gpt rc=1" 대신 원인 ──────────────────────────────────────
def test_explain_gpt_failure_surfaces_the_error_line(tmp_path):
    log = tmp_path / "pair.log"
    log.write_text("INFO: 시작\nSEVERE: java.lang.OutOfMemoryError: Java heap space\n",
                   encoding="utf-8")
    msg = sb.explain_gpt_failure(1, log)
    assert "OutOfMemory" in msg
    assert "-Xmx" in msg, "흔한 원인은 조치까지 알려줘야 한다"
    assert msg != "gpt rc=1"


def test_explain_gpt_failure_points_at_the_log_when_opaque(tmp_path):
    log = tmp_path / "empty.log"
    log.write_text("", encoding="utf-8")
    msg = sb.explain_gpt_failure(3, log)
    assert str(log) in msg, "원인을 못 찾겠으면 최소한 로그 위치는 알려줘야 한다"


def test_explain_gpt_failure_survives_a_missing_log():
    assert "rc=7" in sb.explain_gpt_failure(7, None)


def test_explain_gpt_failure_reads_cp949_hostile_bytes(tmp_path):
    """로그에 UTF-8 이 아닌 바이트가 섞여도 진단이 죽으면 안 된다."""
    log = tmp_path / "mojibake.log"
    log.write_bytes(b"ERROR: \xb0\xa1\xb3\xaa cannot read file\n")
    msg = sb.explain_gpt_failure(1, log)
    assert "cannot read" in msg


# ── 데스크톱 첫 실행 UX: 스플래시·에러 진단 화면 ──────────────────────────
def test_splash_html_is_self_contained_and_localized():
    """더블클릭 즉시 뜨는 로딩 화면 — 외부 리소스 없이 완결되어야 한다."""
    from inframon import desktop

    html = desktop._splash_html()
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "inframon" in html
    assert "준비" in html  # 한글 안내 문구
    # 오프라인/CSP 환경에서도 떠야 하므로 외부 http 리소스를 참조하면 안 된다.
    assert "http://" not in html and "https://" not in html


def test_error_html_shows_the_log_path():
    """서버 기동 실패 화면은 로그 '위치'를 반드시 알려줘야 한다(에러 진단)."""
    from pathlib import Path

    from inframon import desktop

    log = Path("/tmp/inframon/inframon_app.log")
    html = desktop._error_html(log)
    assert str(log) in html
    assert "시작하지 못했습니다" in html


def test_log_path_matches_app_entry_convention():
    """desktop 이 안내하는 로그 경로가 _app_entry 가 실제로 쓰는 경로와 같아야 한다."""
    import tempfile
    from pathlib import Path

    from inframon import desktop

    expected = Path(tempfile.gettempdir()) / "inframon" / "inframon_app.log"
    assert desktop._log_path() == expected
