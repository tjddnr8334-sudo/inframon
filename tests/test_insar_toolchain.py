"""InSAR F코어 처리도구 감지·프로비저닝 안내 — runner 주입으로 서브프로세스 격리."""

from __future__ import annotations

from inframon.insar import toolchain
from inframon.insar.toolchain import (
    PROBES,
    REQUIRED_KEYS,
    SETUP_STAMPS,
    SETUP_CONDA,
    SETUP_CONTAINER,
    WSL_INSTALL_CMD,
    check_toolchain,
    format_report,
    format_wsl_report,
    provision_toolchain,
    wsl_status,
)


def test_all_found_ready():
    status = check_toolchain(runner=lambda cmd: (0, "found"))
    assert status["ready"] is True
    assert status["missing"] == []
    assert status["provision"] is None
    assert len(status["tools"]) == len(PROBES)
    assert all(t["found"] for t in status["tools"])


def test_none_found_provision_guidance():
    status = check_toolchain(runner=lambda cmd: (127, ""))
    assert status["ready"] is False
    assert set(status["missing"]) == set(REQUIRED_KEYS)
    pv = status["provision"]
    assert pv["conda"] == SETUP_CONDA
    assert pv["container"] == SETUP_CONTAINER
    assert "리포" in pv["note"]              # 재현 레시피가 리포에 저장됨을 명시


def test_partial_found():
    # conda·isce2 만 있고 나머지 없음 — env 별 감지(conda run -n <env>)를 명령문으로 구분.
    def runner(cmd):
        if "--version" in cmd:              # conda 자체 프로브(_conda_probe)
            return (0, "conda 26.1.0")
        if "-n isce2" in cmd:               # isce2 환경 프로브
            return (0, "isce")
        return (1, "")                      # miaplpy·sarvey 미설치
    status = check_toolchain(runner=runner)
    assert status["ready"] is False
    assert "sarvey" in status["missing"] and "miaplpy" in status["missing"]
    assert "conda" not in status["missing"] and "isce2" not in status["missing"]


def test_empty_stdout_counts_as_missing():
    # rc=0 이지만 출력이 비면(명령은 성공했으나 도구 없음) 미발견 처리
    status = check_toolchain(runner=lambda cmd: (0, "  "))
    assert status["ready"] is False
    assert set(status["missing"]) == set(REQUIRED_KEYS)


def test_format_report_marks():
    ready = format_report(check_toolchain(runner=lambda cmd: (0, "x")))
    assert "준비 완료" in ready and "✅" in ready
    missing = format_report(check_toolchain(runner=lambda cmd: (1, "")))
    assert "❌" in missing and SETUP_CONDA in missing and SETUP_CONTAINER in missing


# ── WSL 0단계 게이트 — 타 컴퓨터에서 "WSL 미설치"가 원인 불명 ❌ 4개로 보이던 문제 ──
def test_wsl_status_not_required_on_linux(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    st = wsl_status(run=lambda argv: (127, ""))
    assert st["required"] is False and st["ready"] is True


def test_wsl_status_missing_exe_names_install_cmd(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(toolchain.shutil, "which", lambda name: None)
    st = wsl_status(run=lambda argv: (0, "Ubuntu"))
    assert st["ready"] is False and st["reason"] == "wsl_exe_missing"
    assert st["install_cmd"] == WSL_INSTALL_CMD


def test_wsl_status_no_distro(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(toolchain.shutil, "which", lambda name: "wsl")
    st = wsl_status(run=lambda argv: (0, "  "))          # wsl.exe 는 있으나 배포판 0개
    assert st["ready"] is False and st["reason"] == "no_distro"
    assert st["install_cmd"] == WSL_INSTALL_CMD


def test_wsl_status_ready_lists_distros(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(toolchain.shutil, "which", lambda name: "wsl")
    st = wsl_status(run=lambda argv: (0, "Ubuntu-22.04\ndocker-desktop"))
    assert st["ready"] is True and "Ubuntu-22.04" in st["distros"]


def test_format_wsl_report_guides_install_and_alternatives(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(toolchain.shutil, "which", lambda name: None)
    text = format_wsl_report(wsl_status(run=lambda argv: (127, "")))
    assert WSL_INSTALL_CMD in text                        # 정확한 설치 명령
    assert "--insar-tools-install" in text                # 재부팅 후 다음 단계
    assert "--snap-auto" in text and "--hyp3-insar" in text   # WSL 없는 대안 레인


# ── 강제 설치(provision) — Popen 을 가짜로 격리 ──
class _FakePopen:
    def __init__(self, rc: int):
        self.stdout = iter([">> env: isce2\n", "done\n"])
        self._rc = rc

    def wait(self):
        return self._rc


def test_provision_ok_when_setup_succeeds_and_isce2_detected(monkeypatch):
    monkeypatch.setattr(toolchain.subprocess, "Popen", lambda *a, **k: _FakePopen(0))
    logs: list[str] = []
    r = provision_toolchain(runner=lambda cmd: (0, "found"), stream=logs.append)
    assert r["ok"] is True and r["setup_rc"] == 0 and r["error"] is None
    assert any("수 GB" in ln or "다운로드" in ln for ln in logs)   # 비용 경고를 미리 알린다


def test_provision_forces_isce2(monkeypatch):
    """스크립트가 0으로 끝나도 ISCE2 가 감지 안 되면 실패 — ISCE2 는 필수."""
    monkeypatch.setattr(toolchain.subprocess, "Popen", lambda *a, **k: _FakePopen(0))

    def runner(cmd):
        if "--version" in cmd:
            return (0, "conda 26.1.0")
        return (1, "")                                    # isce2 포함 전부 미감지
    r = provision_toolchain(runner=runner, stream=lambda s: None)
    assert r["ok"] is False and "ISCE2" in r["error"]


def test_provision_reports_setup_failure(monkeypatch):
    monkeypatch.setattr(toolchain.subprocess, "Popen", lambda *a, **k: _FakePopen(1))
    r = provision_toolchain(runner=lambda cmd: (0, "found"), stream=lambda s: None)
    assert r["ok"] is False and "00_setup_env.sh" in r["error"]


# ── StaMPS 는 선택 도구 — 없다고 F코어를 미준비로 판정하면 안 된다 ──
def test_stamps_는_필수가_아니다():
    """SARvey 가 논문 인용이 마땅치 않아 갈아 끼울 길로 둔 것이지, 없으면 못 도는 게
    아니다. MATLAB 기반이라 conda 로 깔 수도 없다."""
    assert "stamps" not in REQUIRED_KEYS
    assert "stamps" in {p[0] for p in PROBES}


def test_stamps_만_없으면_준비완료다():
    def runner(cmd):
        return (1, "") if "$STAMPS" in cmd else (0, "found")
    st = check_toolchain(runner=runner)
    assert st["ready"] is True                    # 선택 도구는 ready 를 막지 않는다
    assert st["missing"] == []
    assert st["optional_missing"] == ["stamps"]
    assert st["provision"] is None


def test_stamps_만_있고_필수가_없으면_미준비다():
    def runner(cmd):
        return (0, "stamps") if "$STAMPS" in cmd else (1, "")
    st = check_toolchain(runner=runner)
    assert st["ready"] is False
    assert set(st["missing"]) == set(REQUIRED_KEYS)
    assert st["optional_missing"] == []
    assert st["provision"]["stamps"] == SETUP_STAMPS


def test_stamps_프로브는_환경변수와_실행파일을_본다():
    probe = next(p[2] for p in PROBES if p[0] == "stamps")
    assert "$STAMPS" in probe and "mt_prep_snap" in probe


def test_리포트는_선택_도구를_실패로_표시하지_않는다():
    def runner(cmd):
        return (1, "") if "$STAMPS" in cmd else (0, "found")
    text = format_report(check_toolchain(runner=runner))
    assert "준비 완료" in text
    assert "선택(없어도 됨)" in text and "stamps" in text
