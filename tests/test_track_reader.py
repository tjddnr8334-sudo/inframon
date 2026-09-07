"""Track H5 읽기 — heading 단위 방어."""

from __future__ import annotations

import numpy as np

# ── heading 단위 방어 ──────────────────────────────────────────────────────
# SARvey/MintPy 트랙의 HEADING −0.2312 는 라디안(−13.25°)이다. 도로 읽으면 LOS 가 13°
# 틀어져 쉬프트의 교축 직각 성분이 0.01 m 로 계산됐다(정자교). 라디안으로 고치면 1.5 m,
# 데크 안 점 4 → 7.

def test_radian_heading_is_converted_to_degrees():
    from inframon.insar.track_reader import normalize_heading_deg
    import math
    assert abs(normalize_heading_deg(-0.2312377) - (-13.249)) < 0.01
    assert abs(normalize_heading_deg(-2.91) - math.degrees(-2.91)) < 1e-9   # desc


def test_degree_heading_is_untouched():
    from inframon.insar.track_reader import normalize_heading_deg
    assert normalize_heading_deg(-13.13) == -13.13
    assert normalize_heading_deg(-167.0) == -167.0
    assert normalize_heading_deg(193.4) == 193.4


def test_missing_heading_stays_none():
    from inframon.insar.track_reader import normalize_heading_deg
    assert normalize_heading_deg(None) is None


def test_read_track_h5_normalizes_radian_heading(tmp_path):
    """트랙 attr 에 라디안이 들어 있어도 TrackData.heading 은 도(°)다."""
    import h5py
    from inframon.insar.track_reader import read_track_h5
    p = tmp_path / "t.h5"
    with h5py.File(p, "w") as f:
        f["pixel_lonlat"] = np.array([[127.0, 37.0], [127.001, 37.0]])
        f["epochs"] = np.array([b"20200101", b"20200113"])
        f["los_mm"] = np.zeros((2, 2), np.float32)
        f["coh"] = np.array([0.8, 0.8], np.float32)
        f.attrs["HEADING"] = -0.2312377
    td = read_track_h5(p)
    assert abs(td.heading - (-13.249)) < 0.01
