"""SLC 보관 폴더 — 사용자가 어디에 두든(예: E:\\SLC) 취득이 알아서 인식·재사용.

핵심 계약: 보관 폴더에 있는 장면은 다운로드하지 않는다(하드링크/복사로 끌어옴).
단, **끝까지 받은** 것만 — 조각은 지우고 다시 받는다.
네트워크·find_bridge_burst 는 전부 monkeypatch.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from inframon.insar import slc_store
from inframon.insar.slc_store import (
    discard_incomplete,
    get_slc_dir,
    provide,
    scan,
    set_slc_dir,
    zip_complete,
)

S1 = "S1A_IW_SLC__1SDV_20240107T093202_20240107T093230_051000_062000_AAAA"
S2 = "S1A_IW_SLC__1SDV_20240119T093202_20240119T093230_051175_062100_BBBB"
S3 = "S1A_IW_SLC__1SDV_20240131T093202_20240131T093230_051350_062200_CCCC"


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    f = tmp_path / "config.json"
    monkeypatch.setattr(slc_store, "_CONFIG_FILE", f)
    monkeypatch.delenv("INFRAMON_SLC_DIR", raising=False)
    return f


def _zip(path: Path, payload: bytes = b"slc") -> Path:
    """온전한 zip — 완결성 판정이 계약이 된 뒤로 더미는 진짜 zip 이어야 한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("measurement.dat", payload)
    return path


def _truncate(path: Path, keep: int = 40) -> Path:
    """다운로드가 끊긴 조각 — 끝(EOCD)이 잘려 zip 으로 안 열린다."""
    head = path.read_bytes()[:keep]
    path.write_bytes(head)
    return path


def _store(tmp_path, *names) -> Path:
    root = tmp_path / "SLC보관"
    (root / "하위폴더").mkdir(parents=True)
    for i, n in enumerate(names):
        d = root if i % 2 == 0 else root / "하위폴더"     # 재귀 탐색 검증
        _zip(d / f"{n}.zip")
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
    _zip(dest / f"{S1}.zip", b"already-here")
    provide([S1], dest, root)
    with zipfile.ZipFile(dest / f"{S1}.zip") as z:                # 온전하면 덮어쓰지 않음
        assert z.read("measurement.dat") == b"already-here"


def test_zip_complete_rejects_truncated_and_empty(cfg, tmp_path):
    ok = _zip(tmp_path / "ok.zip")
    cut = _truncate(_zip(tmp_path / "cut.zip"))
    (tmp_path / "empty.zip").write_bytes(b"")
    assert zip_complete(ok) is True
    assert zip_complete(cut) is False, "끊긴 조각을 '받은 것'으로 보면 SNAP 이 죽는다"
    assert zip_complete(tmp_path / "empty.zip") is False
    assert zip_complete(tmp_path / "없다.zip") is False


def test_discard_incomplete_removes_both_hardlinks(cfg, tmp_path):
    """보관본과 작업본은 하드링크 — 한쪽만 지우면 다른 쪽이 남아 또 건너뛴다."""
    import os
    store = _truncate(_zip(tmp_path / "store.zip"))
    work = tmp_path / "work.zip"
    os.link(store, work)
    gone = discard_incomplete(work, store)
    assert len(gone) == 2 and not work.exists() and not store.exists()


def test_discard_incomplete_keeps_whole_zip(cfg, tmp_path):
    ok = _zip(tmp_path / "ok.zip")
    assert discard_incomplete(ok) == [] and ok.exists()


def test_provide_replaces_truncated_dest_from_store(cfg, tmp_path):
    """작업 폴더에 조각이 있으면 보관본으로 갈아끼운다(예전엔 조각을 그대로 뒀다)."""
    root = _store(tmp_path, S1)
    dest = tmp_path / "work" / "SLC"
    _truncate(_zip(dest / f"{S1}.zip"))
    assert provide([S1], dest, root) == [S1]
    assert zip_complete(dest / f"{S1}.zip")


def test_provide_skips_truncated_store_copy(cfg, tmp_path):
    """보관본 자체가 조각이면 재사용했다고 보고하지 않는다 → 다운로드로 넘어간다."""
    root = _store(tmp_path, S1)
    _truncate(next(iter(scan(root))))
    dest = tmp_path / "work" / "SLC"
    assert provide([S1], dest, root) == []
    assert not (dest / f"{S1}.zip").exists()


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
            _zip(Path(out_dir) / u.rsplit("/", 1)[1])

    monkeypatch.setattr(snap_acquire, "find_bridge_burst", lambda *a, **k: _Burst())
    res = snap_acquire.acquire(
        37.0, 127.0, tmp_path / "out", count=3, start="2024-01-01", end="2024-02-01",
        min_scenes=3, search_fn=lambda *a, **k: scenes, download_fn=fake_download,
        session=object())

    assert sorted(res.from_store) == sorted([S1, S2])
    assert downloaded == [f"http://x/{S3}.zip"]           # 없는 1장만 다운로드
    assert len(res.downloaded) == 3                       # SLC 폴더엔 3장 전부 정리됨


# ── 보관 폴더가 있으면 새 다운로드도 거기로 떨어진다 ──
def test_acquire_downloads_into_store_and_links_into_slc_dir(cfg, tmp_path, monkeypatch):
    from inframon.insar import snap_acquire

    root = tmp_path / "bigdrive" / "SLC"
    root.mkdir(parents=True)
    monkeypatch.setenv("INFRAMON_SLC_DIR", str(root))

    scenes = [{"date": f"2024-01-{d:02d}", "name": n, "url": f"http://x/{n}.zip",
               "bytes": 1, "direction": "ASCENDING", "path": 127, "frame": 115,
               "geometry": {"coordinates": [[[126, 36], [128, 36], [128, 38],
                                             [126, 38], [126, 36]]]}}
              for d, n in ((7, S1), (19, S2))]

    class _Burst:
        contained = True
        subswath, burst_index, distance_km = "IW2", 5, 3.0

    targets: list[str] = []

    def fake_download(urls, out_dir, session):
        targets.append(out_dir)
        for u in urls:
            _zip(Path(out_dir) / u.rsplit("/", 1)[1])

    monkeypatch.setattr(snap_acquire, "find_bridge_burst", lambda *a, **k: _Burst())
    res = snap_acquire.acquire(
        37.0, 127.0, tmp_path / "out", count=2, start="2024-01-01", end="2024-02-01",
        min_scenes=2, search_fn=lambda *a, **k: scenes, download_fn=fake_download,
        session=object())

    frame_dir = root / "ASC_path127_frame115"
    assert all(Path(t) == frame_dir for t in targets), "다운로드는 보관 폴더의 프레임 하위폴더로"
    assert sorted(p.name for p in frame_dir.glob("*.zip")) == sorted([f"{S1}.zip", f"{S2}.zip"])
    assert len(res.downloaded) == 2                                  # 처리용 SLC 폴더엔 링크/복사
    assert all(Path(p).parent == tmp_path / "out" / "SLC" for p in res.downloaded)
    assert len(scan(root)) == 2                                      # 다음 교량이 재사용할 수 있게 보관됨


def test_set_creates_dir_when_asked(cfg, tmp_path):
    new = tmp_path / "D" / "SLC"
    with pytest.raises(ValueError):
        set_slc_dir(new)
    assert set_slc_dir(new, create=True) == new.resolve()
    assert new.is_dir() and get_slc_dir() == new.resolve()


def test_drives_report_free_space_sorted():
    from inframon.insar.slc_store import drives, suggest_dir
    d = drives()
    assert d and all(x["free_gb"] >= 0 for x in d)
    assert [x["free_gb"] for x in d] == sorted((x["free_gb"] for x in d), reverse=True)
    assert suggest_dir().name == "SLC"


def test_download_target_falls_back_without_store(cfg, tmp_path):
    from inframon.insar.slc_store import download_target
    assert download_target("ASC path1 frame2", tmp_path / "SLC") == tmp_path / "SLC"
