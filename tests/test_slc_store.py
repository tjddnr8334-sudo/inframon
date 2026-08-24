"""SLC 보관 폴더 — 사용자가 어디에 두든(예: E:\\SLC) 취득이 알아서 인식·재사용.

핵심 계약: 보관 폴더에 있는 장면은 다운로드하지 않는다(하드링크/복사로 끌어옴).
네트워크·find_bridge_burst 는 전부 monkeypatch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from inframon.insar import slc_store
from inframon.insar.slc_store import get_slc_dir, provide, scan, set_slc_dir

S1 = "S1A_IW_SLC__1SDV_20240107T093202_20240107T093230_051000_062000_AAAA"
S2 = "S1A_IW_SLC__1SDV_20240119T093202_20240119T093230_051175_062100_BBBB"
S3 = "S1A_IW_SLC__1SDV_20240131T093202_20240131T093230_051350_062200_CCCC"


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    f = tmp_path / "config.json"
    monkeypatch.setattr(slc_store, "_CONFIG_FILE", f)
    monkeypatch.delenv("INFRAMON_SLC_DIR", raising=False)
    return f


def _store(tmp_path, *names) -> Path:
    root = tmp_path / "SLC보관"
    (root / "하위폴더").mkdir(parents=True)
    for i, n in enumerate(names):
        d = root if i % 2 == 0 else root / "하위폴더"     # 재귀 탐색 검증
        (d / f"{n}.zip").write_bytes(b"x" * 10)
    return root


# ── 설정 저장/해석 ──
def test_set_get_roundtrip_and_merge(cfg, tmp_path):
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"data_root": "E:\\연구중\\inframon"}), encoding="utf-8")
    root = _store(tmp_path, S1)
    set_slc_dir(root)
    assert get_slc_dir() == root.resolve()
    kept = json.loads(cfg.read_text(encoding="utf-8"))
    assert kept["data_root"] == "E:\\연구중\\inframon"     # 기존 키(대시보드) 보존·병합


def test_set_rejects_missing_dir(cfg, tmp_path):
    with pytest.raises(ValueError):
        set_slc_dir(tmp_path / "없는폴더")


def test_env_overrides_config(cfg, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    set_slc_dir(a)
    monkeypatch.setenv("INFRAMON_SLC_DIR", str(b))
    assert get_slc_dir() == b


def test_get_none_when_dir_vanished(cfg, tmp_path):
    d = tmp_path / "사라질폴더"
    d.mkdir()
    set_slc_dir(d)
    d.rmdir()
    assert get_slc_dir() is None                          # 사라진 드라이브/폴더 → 조용히 무시


# ── 탐색·제공 ──
def test_scan_recursive_and_sorted(cfg, tmp_path):
    root = _store(tmp_path, S2, S1, S3)                   # 하위폴더 포함 3장
    names = [p.stem for p in scan(root)]
    assert names == sorted([S1, S2, S3])


def test_provide_links_matches_and_reports(cfg, tmp_path):
    root = _store(tmp_path, S1, S2)
    dest = tmp_path / "work" / "SLC"
    got = provide([S1, f"{S3}.zip"], dest, root)          # .zip 유무 무관, S3 는 보관에 없음
    assert got == [S1]
    assert (dest / f"{S1}.zip").exists() and (dest / f"{S1}.zip").stat().st_size > 0
    assert not (dest / f"{S3}.zip").exists()


def test_provide_keeps_existing_dest_file(cfg, tmp_path):
    root = _store(tmp_path, S1)
    dest = tmp_path / "SLC"
    dest.mkdir()
    (dest / f"{S1}.zip").write_bytes(b"already-here")
    provide([S1], dest, root)
    assert (dest / f"{S1}.zip").read_bytes() == b"already-here"   # 덮어쓰지 않음


def test_provide_noop_without_store(cfg, tmp_path):
    assert provide([S1], tmp_path / "SLC") == []          # 미설정 → 기존 동작 그대로


# ── 취득 통합: 보관 폴더에 있는 장면은 다운로드하지 않는다 ──
def test_acquire_reuses_store_and_downloads_only_missing(cfg, tmp_path, monkeypatch):
    from inframon.insar import snap_acquire

    root = _store(tmp_path, S1, S2)                       # 3장 중 2장은 보관 폴더에 있음
    monkeypatch.setenv("INFRAMON_SLC_DIR", str(root))

    scenes = [{"date": f"2024-01-{d:02d}", "name": n, "url": f"http://x/{n}.zip",
               "bytes": 1, "direction": "ASCENDING", "path": 127, "frame": 115,
               "geometry": {"coordinates": [[[126, 36], [128, 36], [128, 38],
                                             [126, 38], [126, 36]]]}}
              for d, n in ((7, S1), (19, S2), (31, S3))]

    class _Burst:
        contained = True
        subswath, burst_index, distance_km = "IW2", 5, 3.0

    downloaded: list[str] = []

    def fake_download(urls, out_dir, session):
        downloaded.extend(urls)
        for u in urls:
            (Path(out_dir) / u.rsplit("/", 1)[1]).write_bytes(b"dl")

    monkeypatch.setattr(snap_acquire, "find_bridge_burst", lambda *a, **k: _Burst())
    res = snap_acquire.acquire(
        37.0, 127.0, tmp_path / "out", count=3, start="2024-01-01", end="2024-02-01",
        min_scenes=3, search_fn=lambda *a, **k: scenes, download_fn=fake_download,
        session=object())

    assert sorted(res.from_store) == sorted([S1, S2])
    assert downloaded == [f"http://x/{S3}.zip"]           # 없는 1장만 다운로드
    assert len(res.downloaded) == 3                       # SLC 폴더엔 3장 전부 정리됨
