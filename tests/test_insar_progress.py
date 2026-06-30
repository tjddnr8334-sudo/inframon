"""InSAR 처리 진행판 — WORK 산출물 스캔·단계 상태·렌더."""

from __future__ import annotations

import json

from inframon.insar.progress import DONE, PENDING, RUNNING, render_board, scan_progress


def _touch(p, content="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _stage(prog, sid):
    return next(s for s in prog["stages"] if s["id"] == sid)


def test_empty_work_all_pending(tmp_path):
    prog = scan_progress(tmp_path)
    assert all(s["status"] == PENDING for s in prog["stages"])
    assert prog["overall"] == 0.0


def test_slc_only_stage10_running(tmp_path):
    for i in range(5):
        _touch(tmp_path / "SLC" / f"s{i}.zip")
    prog = scan_progress(tmp_path)
    s10 = _stage(prog, "10")
    assert s10["status"] == RUNNING        # SLC 있으나 궤도·DEM 없음
    assert "5/5" in s10["detail"]
    assert prog["expected_scenes"] == 5    # 분모 = 실제 SLC 수


def test_download_complete_stage10_done(tmp_path):
    for i in range(3):
        _touch(tmp_path / "SLC" / f"s{i}.zip")
        _touch(tmp_path / "orbits" / f"o{i}.EOF")
    _touch(tmp_path / "DEM" / "dem.wgs84")
    _touch(tmp_path / "aux" / "a.xml")
    prog = scan_progress(tmp_path)
    assert _stage(prog, "10")["status"] == DONE


def test_isce_partial_coreg_running(tmp_path):
    for i in range(5):
        _touch(tmp_path / "SLC" / f"s{i}.zip")
        _touch(tmp_path / "orbits" / f"o{i}.EOF")
    _touch(tmp_path / "DEM" / "dem.wgs84")
    # ISCE 코레지 3/5 진행
    for i in range(3):
        _touch(tmp_path / "stack" / "merged" / "SLC" / f"d{i}" / "x.slc.full")
    prog = scan_progress(tmp_path)
    s20 = _stage(prog, "20")
    assert s20["status"] == RUNNING and "3/5" in s20["detail"]


def test_full_chain_done(tmp_path):
    for i in range(2):
        _touch(tmp_path / "SLC" / f"s{i}.zip")
        _touch(tmp_path / "orbits" / f"o{i}.EOF")
        _touch(tmp_path / "stack" / "merged" / "SLC" / f"d{i}" / "x.slc.full")
    _touch(tmp_path / "DEM" / "dem.wgs84")
    _touch(tmp_path / "stack" / "merged" / "geom_reference" / "hgt.rdr.full")
    _touch(tmp_path / "miaplpy" / "inputs" / "slcStack.h5")
    _touch(tmp_path / "miaplpy" / "inputs" / "geometryRadar.h5")
    _touch(tmp_path / "sarvey" / "outputs" / "p2_coh80_ts.h5")
    _touch(tmp_path / "track.h5")
    prog = scan_progress(tmp_path)
    assert all(s["status"] == DONE for s in prog["stages"])
    assert prog["overall"] == 1.0
    board = render_board(prog, title="시험교", now="00:00:00")
    assert "✅" in board and "100%" in board and "시험교" in board


def test_expected_from_manifest_before_download(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    (recipe / "processing_manifest.json").write_text(
        json.dumps({"stack": {"num_scenes": 41}}), encoding="utf-8")
    prog = scan_progress(tmp_path / "work", recipe)   # SLC 아직 없음 → manifest 41
    assert prog["expected_scenes"] == 41
