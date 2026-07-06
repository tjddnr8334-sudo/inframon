"""InSAR F코어 처리도구 감지·프로비저닝 안내 — runner 주입으로 서브프로세스 격리."""

from __future__ import annotations

from inframon.insar.toolchain import (
    PROBES,
    SETUP_CONDA,
    SETUP_CONTAINER,
    check_toolchain,
    format_report,
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
    assert set(status["missing"]) == {p[0] for p in PROBES}
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
    assert set(status["missing"]) == {p[0] for p in PROBES}


def test_format_report_marks():
    ready = format_report(check_toolchain(runner=lambda cmd: (0, "x")))
    assert "준비 완료" in ready and "✅" in ready
    missing = format_report(check_toolchain(runner=lambda cmd: (1, "")))
    assert "❌" in missing and SETUP_CONDA in missing and SETUP_CONTAINER in missing
