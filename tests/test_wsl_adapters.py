"""WSL F 처리 export 어댑터 검증 (환경 독립, 합성 도구 출력).

ISCE2/MiaplPy/SARvey 자체는 WSL2 전용이라 여기서 못 돌리지만, **도구 출력 → Track H5**
변환 어댑터(우리 코드, `scripts/wsl_sarvey/5x_*.py`)는 합성 도구 출력으로 검증한다.
각 어댑터 산출 Track H5 가 (1) preflight 를 통과하고 (2) import_track_h5 로 인제스트되는지
확인 — F→G 계약이 외부 도구 없이도 관통하는지 본다. 셸 스크립트는 bash -n 문법 검증.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import h5py
import numpy as np
import pytest

from inframon.contracts.io import ProjectStore
from inframon.insar.track_preflight import preflight_track_h5
from inframon.insar.track_reader import import_track_h5

WSL = Path(__file__).resolve().parents[1] / "scripts" / "wsl_sarvey"
EPOCHS = np.array([20230101, 20230113, 20230125, 20230206], dtype=np.int32)


def _load(stem: str):
    spec = importlib.util.spec_from_file_location(stem.replace(".", "_"), WSL / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ingestable(out_h5, tmp_path, expect_points):
    """산출 Track H5 가 preflight ready + import_track_h5 인제스트 가능한지."""
    rep = preflight_track_h5(out_h5)
    assert rep.is_ready, f"preflight 실패: {rep.errors}"
    assert rep.n_dates == len(EPOCHS)
    with ProjectStore(tmp_path / "proj.h5", mode="w") as store:
        ins = import_track_h5(store, out_h5)
    assert ins.n_points == expect_points and ins.n_dates == len(EPOCHS)


def test_adapter50_sarvey_export(tmp_path):
    """50: SARvey export(단일 H5, displacement[M,N] 전치 포함) → Track H5."""
    mod = _load("50_export_to_inframon")
    n, m = 15, len(EPOCHS)
    src = tmp_path / "sarvey_ts.h5"
    with h5py.File(src, "w") as f:
        f.create_dataset("displacement", data=np.random.default_rng(0).normal(0, 0.01, (m, n)))  # [M,N]
        f.create_dataset("latitude", data=37.36 + np.arange(n) * 1e-4)
        f.create_dataset("longitude", data=127.10 + np.arange(n) * 1e-4)
        f.create_dataset("date", data=EPOCHS.astype("S8"))
        f.create_dataset("temporalCoherence", data=np.full(n, 0.85))
    out = tmp_path / "track50.h5"
    np_pts, np_dates = mod.convert(str(src), str(out))
    assert (np_pts, np_dates) == (n, m)
    _ingestable(out, tmp_path, n)


def test_adapter52_miaplpy(tmp_path):
    """52: MiaplPy(timeseries+geometry+coherence 3파일) → Track H5."""
    mod = _load("52_miaplpy_to_inframon")
    m, L, W = len(EPOCHS), 4, 5
    ts = tmp_path / "ts.h5"
    geo = tmp_path / "geo.h5"
    coh = tmp_path / "coh.h5"
    rng = np.random.default_rng(1)
    with h5py.File(ts, "w") as f:
        f.create_dataset("timeseries", data=rng.normal(0, 0.01, (m, L, W)))
        f.create_dataset("date", data=EPOCHS.astype("S8"))
    lon = np.tile(127.10 + np.arange(W) * 1e-4, (L, 1))
    lat = np.tile((37.36 + np.arange(L) * 1e-4)[:, None], (1, W))
    with h5py.File(geo, "w") as f:
        f.create_dataset("latitude", data=lat)
        f.create_dataset("longitude", data=lon)
    with h5py.File(coh, "w") as f:
        f.create_dataset("temporalCoherence", data=np.full((L, W), 0.8))
    out = tmp_path / "track52.h5"
    n, _ = mod.convert(str(ts), str(geo), str(coh), str(out), coh_thresh=0.6)
    assert n == L * W
    _ingestable(out, tmp_path, n)


def test_adapter54_mintpy_affine(tmp_path):
    """54: MintPy(지오코딩 affine attrs 기반 좌표) → Track H5."""
    mod = _load("54_mintpy_to_inframon")
    m, L, W = len(EPOCHS), 4, 5
    ts = tmp_path / "ts.h5"
    coh = tmp_path / "coh.h5"
    rng = np.random.default_rng(2)
    with h5py.File(ts, "w") as f:
        d = f.create_dataset("timeseries", data=rng.normal(0, 0.01, (m, L, W)) + 0.02)  # 비영
        f.create_dataset("date", data=EPOCHS.astype("S8"))
        for k, v in {"X_FIRST": 127.10, "Y_FIRST": 37.37, "X_STEP": 1e-4, "Y_STEP": -1e-4}.items():
            d.attrs[k] = v
            f.attrs[k] = v
    with h5py.File(coh, "w") as f:
        f.create_dataset("temporalCoherence", data=np.full((L, W), 0.85))
    out = tmp_path / "track54.h5"
    n, _ = mod.convert(str(ts), str(coh), str(out), coh_thresh=0.7)
    assert n == L * W
    _ingestable(out, tmp_path, n)


def test_adapter56_stamps_mat(tmp_path):
    """56: StaMPS(.mat: lonlat/ph_mm/day/coh_ps) → Track H5."""
    pytest.importorskip("scipy")
    from scipy.io import savemat

    mod = _load("56_stamps_to_inframon")
    n, m = 18, len(EPOCHS)
    mat = tmp_path / "stamps.mat"
    savemat(str(mat), {
        "lonlat": np.column_stack([127.10 + np.arange(n) * 1e-4, 37.36 + np.arange(n) * 1e-4]),
        "ph_mm": np.random.default_rng(3).normal(0, 2.0, (n, m)),
        "day": EPOCHS.astype(np.float64),            # YYYYMMDD (mx>1e7 경로)
        "coh_ps": np.full(n, 0.8),
    })
    out = tmp_path / "track56.h5"
    k, _ = mod.convert(str(mat), str(out), unit="mm")
    assert k == n
    _ingestable(out, tmp_path, n)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash 없음")
def test_wsl_shell_scripts_syntax():
    """모든 WSL 셸 스크립트가 bash -n 문법 검증을 통과한다(F 실행 전 정합)."""
    scripts = sorted(WSL.glob("*.sh"))
    assert scripts, "셸 스크립트를 찾지 못함"
    for sh in scripts:
        r = subprocess.run([shutil.which("bash"), "-n", str(sh)], capture_output=True, text=True)
        assert r.returncode == 0, f"{sh.name} 문법 오류: {r.stderr}"


def test_plan_real_references_existing_scripts():
    """plan_real 시퀀스가 실제 존재하는 스크립트를 가리킨다."""
    from inframon.insar import processing

    joined = "\n".join(processing.plan_real("rec", "wrk"))
    for stem in ("10_download.sh", "20_stack_isce.sh", "30_miaplpy.sh", "40_sarvey.sh",
                 "50_export_to_inframon.py"):
        assert stem in joined and (WSL / stem).exists(), f"{stem} 누락/미참조"
