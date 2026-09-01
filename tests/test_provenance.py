"""산출물 출처 각인(provenance) — '이 파일 무엇으로 만들었나' 가 파일 안에 남는가.

기존에는 track.h5 를 몇 달 뒤 열었을 때 어느 교량·어느 엔진·어느 코드 산출인지
되짚을 방법이 없었다. 기록이 없으면 재현도 폐기도 판단할 수 없다.
"""

from __future__ import annotations

import h5py
import numpy as np

from inframon.insar import provenance


def _h5(path):
    with h5py.File(path, "w") as f:
        f.create_dataset("los_mm", data=np.zeros((3, 2), dtype=np.float32))
    return path


def test_stamp_and_read_roundtrip(tmp_path):
    p = _h5(tmp_path / "t.h5")
    assert provenance.stamp_h5(p, engine="sarvey", target_lat=37.0, target_lon=127.0)
    got = provenance.read_h5(p)
    assert got["engine"] == "sarvey"
    assert got["target_lat"] == 37.0
    assert got["created_utc"].startswith("20")       # ISO 시각
    assert "command" in got


def test_stamp_keeps_original_datasets(tmp_path):
    p = _h5(tmp_path / "t.h5")
    provenance.stamp_h5(p, engine="snap")
    with h5py.File(p, "r") as f:
        assert f["los_mm"].shape == (3, 2)           # 원 데이터는 그대로


def test_bbox_list_is_stored_as_numbers(tmp_path):
    p = _h5(tmp_path / "t.h5")
    provenance.stamp_h5(p, deck_bbox=[127.0, 37.0, 127.1, 37.1])
    assert provenance.read_h5(p)["deck_bbox"] == [127.0, 37.0, 127.1, 37.1]


def test_missing_file_does_not_raise(tmp_path):
    assert provenance.stamp_h5(tmp_path / "nope.h5") is False
    assert provenance.read_h5(tmp_path / "nope.h5") == {}


def test_unreadable_file_does_not_kill_the_run(tmp_path):
    """출처 각인 실패가 산출물을 죽이면 안 된다 — False 만 돌려준다."""
    bad = tmp_path / "bad.h5"
    bad.write_bytes(b"not-hdf5")
    assert provenance.stamp_h5(bad, engine="snap") is False
    assert provenance.read_h5(bad) == {}


def test_read_ignores_non_provenance_attrs(tmp_path):
    p = _h5(tmp_path / "t.h5")
    with h5py.File(p, "a") as f:
        f.attrs["HEADING"] = -13.1                   # 원래 있던 메타
    provenance.stamp_h5(p, engine="sarvey")
    got = provenance.read_h5(p)
    assert "HEADING" not in got and got["engine"] == "sarvey"
