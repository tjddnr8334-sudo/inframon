"""CLI(__main__) 스모크 — argparse 분기·종료코드 검증(유일하게 무테스트였던 모듈).

실제 프로세스로 호출(`python -m inframon ...`)해 인자 파싱·분기·종료코드를 확인한다.
무거운 수치는 다른 테스트가 검증하므로 여기선 가볍게(작은 규모) 관통만 본다.
"""

from __future__ import annotations

import subprocess
import sys

import h5py
import numpy as np


def _run(*args, cwd=None):
    """python -m inframon <args> 실행 → (returncode, stdout)."""
    r = subprocess.run(
        [sys.executable, "-m", "inframon", *args],
        capture_output=True, encoding="utf-8", errors="replace", cwd=cwd, timeout=180,
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def test_cli_help():
    code, out = _run("--help")
    assert code == 0
    assert "inframon" in out and "--demo" in out and "--doctor" in out


def test_cli_doctor():
    code, out = _run("--doctor")
    assert code == 0                                  # 코어 의존성 있으면 0
    assert "readiness doctor" in out and "판정" in out


def test_cli_demo_creates_project(tmp_path):
    out_h5 = tmp_path / "p.h5"
    code, out = _run("--demo", "--points", "8", "--dates", "4", "--out", str(out_h5))
    assert code == 0
    assert out_h5.exists()
    assert "완료" in out
    with h5py.File(out_h5, "r") as f:
        assert "/fram/CRI" in f                        # 끝까지 관통해 FRAM 산출


def test_cli_requires_demo_or_action():
    # 아무 액션 없이 호출 → 친절한 에러로 종료(0 아님)
    code, out = _run()
    assert code != 0
    assert "--demo" in out


def test_cli_check_track_ready_and_not(tmp_path):
    good = tmp_path / "good.h5"
    with h5py.File(good, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack(
            [300000 + np.arange(10) * 2.0, 600000 + np.zeros(10)]))
        f.create_dataset("epochs", data=np.array([20230101, 20230113, 20230125, 20230206],
                                                 dtype=np.int32))
        f.create_dataset("los_mm", data=np.zeros((10, 4), dtype=np.float32))
        f.create_dataset("coh", data=np.full(10, 0.8, dtype=np.float32))
    code_ok, out_ok = _run("--check-track", str(good))
    assert code_ok == 0 and "투입 가능" in out_ok

    bad = tmp_path / "bad.h5"
    with h5py.File(bad, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.zeros((3, 2)))   # los/epochs/coh 없음
    code_bad, out_bad = _run("--check-track", str(bad))
    assert code_bad == 1 and "투입 불가" in out_bad


def test_cli_engine_flag_validation():
    # 잘못된 엔진 이름 → argparse 친절 에러(0 아님)
    code, out = _run("--demo", "--engine", "nosuch=real")
    assert code != 0
    assert "engine" in out.lower()
