"""4-Track CS 융합 — 방법 합의(가중중앙값) 융합 + 트랙 간 일치도(검증) 검증."""

from __future__ import annotations

import numpy as np

from inframon.insar.track_fusion import (
    fuse_tracks,
    fusion_report,
    write_fused_track_h5,
)
from inframon.insar.track_reader import TrackData

DATES = ["20230112", "20230124", "20230205", "20230217", "20230301"]


def _track(los, coh=None, lonlat=None):
    N, M = los.shape
    ll = lonlat if lonlat is not None else np.column_stack([
        np.linspace(127.108, 127.110, N), np.full(N, 37.3685)])
    labels = np.array(DATES[:M], dtype="S8")
    return TrackData(
        lonlat=ll.astype(np.float64), los=los.astype(np.float32),
        dates=np.arange(M, dtype=float) * 12.0, date_labels=labels,
        coherence=np.full(N, 0.8 if coh is None else coh, dtype=np.float32))


def test_cs_fusion_rejects_outlier():
    N, M = 6, 5
    base = np.linspace(0, -5, M)[None, :] * np.ones((N, 1))      # 공통 침하 신호
    a = _track(base + np.random.default_rng(1).normal(0, 0.1, (N, M)))
    b = _track(base + np.random.default_rng(2).normal(0, 0.1, (N, M)))
    c = _track(base.copy())
    c.los[0] += 50.0                                            # 점0: 한 방법만 이상치(스파이크)

    res = fuse_tracks([a, b, c])
    assert res.los_mm.shape == (N, M)
    # 가중 중앙값 → 점0 의 50mm 스파이크가 융합에서 억제됨(합의값 근처)
    assert np.abs(res.los_mm[0] - base[0]).max() < 5.0
    # 점0 은 트랙 불일치 큼 → agreement_mm 최대, 신뢰도 최저
    assert res.agreement_mm[0] == res.agreement_mm.max()
    assert res.confidence[0] < res.confidence[1:].mean()
    assert (res.n_used == 3).all()


def test_fusion_requires_two_tracks():
    import pytest
    with pytest.raises(ValueError):
        fuse_tracks([_track(np.zeros((4, 5)))])


def test_fusion_report_keys():
    a = _track(np.ones((5, 5)))
    b = _track(np.ones((5, 5)) * 1.1)
    rep = fusion_report(fuse_tracks([a, b]))
    for k in ("n_points", "n_tracks", "agreement_mm_median", "confident_frac",
              "mean_tracks_per_point", "matched_frac"):
        assert k in rep
    assert rep["n_tracks"] == 2


def test_fused_h5_ingests_to_insar(tmp_path):
    from inframon.contracts.io import ProjectStore
    from inframon.insar.track_reader import import_track_h5, read_track_h5

    a = _track(np.linspace(0, -3, 5)[None, :] * np.ones((6, 1)))
    b = _track(np.linspace(0, -3, 5)[None, :] * np.ones((6, 1)) + 0.2)
    out = write_fused_track_h5(fuse_tracks([a, b]), str(tmp_path / "fused.h5"))

    td = read_track_h5(out)                          # 표준 Track H5 로 읽힘
    assert td.los.shape == (6, 5)
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        meta = import_track_h5(store, out)           # → /insar 계약
    assert meta.n_points == 6 and meta.n_dates == 5
