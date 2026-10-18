"""프레임 자동선정·취득 — 중심성·순위·burst 검증 스킵(네트워크 격리)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from inframon.insar import snap_acquire as sa
from inframon.insar.snap_acquire import (
    AcquireError,
    _centrality_km,
    search_frames,
)
from inframon.insar.snap_backend import BurstLoc

BLAT, BLON = 37.3219, 127.1083


def _zip(path: Path) -> Path:
    """온전한 zip — 취득이 '끝까지 받았는지'로 판정하므로 더미도 진짜 zip 이어야 한다."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("measurement.dat", b"slc")
    return path


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    """보관 폴더 설정을 테스트마다 끊는다.

    안 하면 `~/.inframon/config.json` 의 `slc_dir`(사용자가 고른 실제 폴더)로
    다운로드가 떨어진다 — 실제로 E:/SLC 에 테스트 zip 이 쌓여 있었다.
    """
    from inframon.insar import slc_store
    monkeypatch.setattr(slc_store, "_CONFIG_FILE", tmp_path / "없는config.json")
    monkeypatch.delenv("INFRAMON_SLC_DIR", raising=False)


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
        for u in urls:
            name = u.rsplit("/", 1)[-1]
            _zip(Path(out_dir) / name)
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


def _one_frame_scenes(dates=("2024-01-07", "2024-01-19", "2024-01-31")):
    return [_scene(f"A_{d}", d, 127, 120, _poly_around(BLAT, BLON, 0.4)) for d in dates]


def test_acquire_redownloads_truncated_existing(monkeypatch, tmp_path):
    """끊긴 조각을 '이미 받음'으로 건너뛰던 회귀 — 실제로 SNAP 이 BadZipFile 로 죽었다."""
    scenes = _one_frame_scenes()
    slc = tmp_path / "SLC"
    slc.mkdir(parents=True)
    _zip(slc / "A_2024-01-07.zip")                       # 기준영상은 온전
    (slc / "A_2024-01-19.zip").write_bytes(b"PK" + b"0" * 40)   # 조각

    downloaded = []

    def fake_dl(urls, out_dir, session):
        for u in urls:
            name = u.rsplit("/", 1)[-1]
            _zip(Path(out_dir) / name)
            downloaded.append(name)

    monkeypatch.setattr(sa, "find_bridge_burst",
                        lambda z, la, lo: BurstLoc("IW2", 1, 5.0, la, lo, contained=True))
    res = sa.acquire(BLAT, BLON, tmp_path, count=3, start="2024-01-01", end="2024-02-01",
                     min_scenes=3, search_fn=lambda *a: scenes, download_fn=fake_dl,
                     session=object())

    assert "A_2024-01-19.zip" in downloaded, "조각은 지우고 다시 받아야 한다"
    assert "A_2024-01-07.zip" not in downloaded, "온전한 것은 다시 받지 않는다"
    assert len(res.downloaded) == 3 and res.damaged == []


def test_acquire_drops_scene_that_stays_damaged(monkeypatch, tmp_path):
    """다시 받아도 깨졌으면 조용히 넘기지 말고 빼고 보고한다 — 처리로 흘려보내지 않는다."""
    scenes = _one_frame_scenes()

    def fake_dl(urls, out_dir, session):
        for u in urls:
            name = u.rsplit("/", 1)[-1]
            p = Path(out_dir) / name
            if "01-31" in name:
                p.write_bytes(b"PK" + b"0" * 40)   # 계속 조각
            else:
                _zip(p)

    monkeypatch.setattr(sa, "find_bridge_burst",
                        lambda z, la, lo: BurstLoc("IW2", 1, 5.0, la, lo, contained=True))
    res = sa.acquire(BLAT, BLON, tmp_path, count=3, start="2024-01-01", end="2024-02-01",
                     min_scenes=3, search_fn=lambda *a: scenes, download_fn=fake_dl,
                     session=object())

    assert res.damaged == ["A_2024-01-31"]
    assert len(res.downloaded) == 2
    assert all("01-31" not in p for p in res.downloaded)


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
                   download_fn=lambda u, o, s: [_zip(Path(o, x.rsplit("/", 1)[-1])) for x in u],
                   session=object())
