"""프레임 자동선정·취득 — 중심성·순위·burst 검증 스킵(네트워크 격리)."""

from __future__ import annotations

import pytest

from inframon.insar import snap_acquire as sa
from inframon.insar.snap_acquire import (
    AcquireError,
    FrameCandidate,
    _centrality_km,
    search_frames,
)
from inframon.insar.snap_backend import BurstLoc

BLAT, BLON = 37.3219, 127.1083


def _poly_around(clat, clon, half=0.3):
    """(clat,clon) 중심 half°(위경도) 사각형 GeoJSON Polygon."""
    return {"coordinates": [[[clon - half, clat - half], [clon + half, clat - half],
                             [clon + half, clat + half], [clon - half, clat + half],
                             [clon - half, clat - half]]]}


def _scene(name, date, path, frame, geom, direction="ASCENDING"):
    return {"date": date, "name": name, "url": f"https://asf/{name}.zip", "bytes": int(7.8e9),
            "direction": direction, "path": path, "frame": frame, "geometry": geom}


def test_centrality_inside_outside():
    inside = _poly_around(BLAT, BLON, 0.3)     # 교량 중심 → 큰 +margin
    outside = _poly_around(BLAT - 1.0, BLON, 0.3)   # 남쪽 1° → 밖(음수)
    assert _centrality_km(BLAT, BLON, inside) > 20
    assert _centrality_km(BLAT, BLON, outside) < 0


def test_search_frames_ranks_by_centrality():
    # frameB: 교량 깊숙이(중심성↑), frameA: 살짝 걸침(중심성↓ 그러나 +)
    scenes = []
    for d in ("2024-01-07", "2024-01-19", "2024-01-31"):
        scenes.append(_scene(f"B_{d}", d, 127, 115, _poly_around(BLAT, BLON, 0.4)))
        scenes.append(_scene(f"A_{d}", d, 127, 120, _poly_around(BLAT - 0.35, BLON, 0.4)))
    cands = search_frames(BLAT, BLON, start="2024-01-01", end="2024-02-01",
                          search_fn=lambda *a: scenes)
    assert cands[0].frame == 115                # 중심성 최고가 1순위
    assert all(c.n_scenes == 3 for c in cands)


def test_acquire_skips_uncontained_frame(monkeypatch, tmp_path):
    # frameB 중심성↑지만 burst 밖(frame115 실제 상황), frameA 중심성↓지만 burst 포함.
    scenes = []
    for d in ("2024-01-07", "2024-01-19"):
        scenes.append(_scene(f"B_{d}", d, 127, 115, _poly_around(BLAT, BLON, 0.4)))
        scenes.append(_scene(f"A_{d}", d, 127, 120, _poly_around(BLAT - 0.35, BLON, 0.4)))

    downloaded = []

    def fake_dl(urls, out_dir, session):
        from pathlib import Path
        for u in urls:
            name = u.rsplit("/", 1)[-1]
            (Path(out_dir) / name).write_text("x")
            downloaded.append(name)

    def fake_burst(zip_path, lat, lon):
        contained = "A_" in str(zip_path)        # frameA(120)만 포함
        return BurstLoc("IW2", 1 if contained else 9, 5.0, lat, lon, contained=contained)

    monkeypatch.setattr(sa, "find_bridge_burst", fake_burst)
    res = sa.acquire(BLAT, BLON, tmp_path, count=2, start="2024-01-01", end="2024-02-01",
                     min_scenes=2, search_fn=lambda *a: scenes, download_fn=fake_dl,
                     session=object())
    assert res.frame.frame == 120 and res.contained is True
    assert res.burst.subswath == "IW2" and res.burst.burst_index == 1
    # frameB 기준영상만 받아보고 스킵, frameA 는 2장 다 받음
    assert any("B_2024-01-07" in n for n in downloaded)     # B 검증용 1장
    assert sum(1 for n in downloaded if n.startswith("A_")) == 2
    assert len(res.considered) == 2                          # B 건너뜀 + A 채택


def test_acquire_no_frames(monkeypatch, tmp_path):
    with pytest.raises(AcquireError):
        sa.acquire(BLAT, BLON, tmp_path, count=4, start="2024-01-01", end="2024-02-01",
                   min_scenes=5, search_fn=lambda *a: [], download_fn=lambda *a: None,
                   session=object())


def test_acquire_all_uncontained_raises(monkeypatch, tmp_path):
    scenes = [_scene(f"B_{d}", d, 127, 115, _poly_around(BLAT, BLON, 0.4))
              for d in ("2024-01-07", "2024-01-19")]
    monkeypatch.setattr(sa, "find_bridge_burst",
                        lambda z, la, lo: BurstLoc("IW2", 9, 40.0, la, lo, contained=False))
    with pytest.raises(AcquireError):
        sa.acquire(BLAT, BLON, tmp_path, count=2, start="2024-01-01", end="2024-02-01",
                   min_scenes=2, search_fn=lambda *a: scenes,
                   download_fn=lambda u, o, s: [__import__("pathlib").Path(o, x.rsplit("/", 1)[-1]).write_text("x") for x in u],
                   session=object())
