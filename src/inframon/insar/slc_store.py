"""사용자 지정 SLC 보관 폴더 — "이미 받아둔 SLC 를 어디에 두든 알아서 인식".

사용자가 SLC(.zip)를 원하는 폴더(예: E:\\SLC)에 모아두면, 취득(--snap-auto 등)이
다운로드 전에 이 보관 폴더를 먼저 뒤져 **있는 장면은 하드링크/복사로 재사용**하고
없는 장면만 내려받는다 — 장당 4~8GB 재다운로드를 없애는 계층.

위치 결정 우선순위: 환경변수 `INFRAMON_SLC_DIR` > `~/.inframon/config.json` 의
`slc_dir`(CLI `--slc-dir DIR` 로 저장 — 대시보드 config 와 같은 파일, 병합 저장).
어느 쪽도 없으면 조용히 no-op(기존 동작 그대로).

보관 폴더가 설정돼 있으면 **새 다운로드도 그곳(`<보관>/<궤도 프레임>/`)에 떨어진다** —
장당 4~8 GB 라 C: 가 아니라 사용자가 고른 큰 드라이브에 쌓여야 하고, 한 번 받은 장면이
다음 교량에서 그대로 재사용되려면 다운로드가 보관 폴더를 채워야 한다(`download_target`).

하드링크는 같은 드라이브에서 디스크 추가 사용 0 — 다른 드라이브면 복사로 폴백한다.
"""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path

_CONFIG_FILE = Path.home() / ".inframon" / "config.json"
_SLC_GLOB = "S1*_IW_SLC*.zip"


def _config_load() -> dict:
    try:
        return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get_slc_dir() -> Path | None:
    """보관 폴더 — env > config. 설정돼 있어도 폴더가 사라졌으면 None(경고는 호출측)."""
    raw = os.environ.get("INFRAMON_SLC_DIR") or _config_load().get("slc_dir")
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def set_slc_dir(path: str | Path, *, create: bool = False) -> Path:
    """보관 폴더를 config 에 저장(다른 키 보존·병합). 없으면 ValueError — create=True 면 만든다."""
    p = Path(path).resolve()
    if not p.is_dir():
        if not create:
            raise ValueError(f"SLC 보관 폴더가 없습니다: {p}")
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ValueError(f"SLC 보관 폴더를 만들 수 없습니다: {p} ({e})") from e
    cfg = _config_load()
    cfg["slc_dir"] = str(p)
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def drives() -> list[dict]:
    """쓸 수 있는 드라이브와 여유 공간(GB) — 사용자가 큰 드라이브를 고르게. 여유 큰 순."""
    import string
    if os.name == "nt":
        roots = [Path(c + ":\\") for c in string.ascii_uppercase if Path(c + ":\\").exists()]
    else:
        roots = [Path("/"), Path.home()]
    out = []
    for r in roots:
        try:
            u = shutil.disk_usage(r)
        except OSError:
            continue
        out.append({"root": str(r), "free_gb": round(u.free / 1e9, 1), "total_gb": round(u.total / 1e9, 1)})
    return sorted(out, key=lambda d: -d["free_gb"])


def suggest_dir() -> Path:
    """추천 보관 위치 — 여유가 가장 큰 드라이브의 SLC 폴더."""
    d = drives()
    return Path(d[0]["root"]) / "SLC" if d else Path.home() / "SLC"


def download_target(frame_label: str, fallback: str | Path) -> Path:
    """다운로드가 떨어질 폴더 — 보관 폴더가 있으면 `<보관>/<프레임>/`, 없으면 fallback."""
    root = get_slc_dir()
    if root is None:
        return Path(fallback)
    sub = root / frame_label.replace(" ", "_")
    sub.mkdir(parents=True, exist_ok=True)
    return sub


def scan(root: str | Path | None = None) -> list[Path]:
    """보관 폴더를 재귀 탐색해 SLC zip 목록(파일명 순). root 미지정이면 get_slc_dir()."""
    root = Path(root) if root else get_slc_dir()
    if root is None or not Path(root).is_dir():
        return []
    return sorted(Path(root).rglob(_SLC_GLOB), key=lambda p: p.name)


def zip_complete(path: str | Path) -> bool:
    """받아둔 SLC `.zip` 이 **끝까지** 받아졌는지 — 재개 판정의 단일 기준.

    존재·크기>0 만 보던 예전 판정은 중간에 끊긴 조각을 '받은 것'으로 남겼다. 실제로
    다운로드를 Ctrl+C 로 끊자 0.84 GB 조각이 남았고, 재개가 그것을 건너뛰어 SNAP 이
    BadZipFile 로 죽었다. zip 의 중앙디렉터리(EOCD)는 파일 맨 끝에 있어 절단되면 반드시
    깨지므로 끝부분만 읽어 판정한다 — 7 GB 라도 즉시 끝난다.

    내용이 상한(CRC) 경우까지는 못 잡는다. 전체 CRC 검사는 장당 수 분이라 하지 않는다.
    """
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size <= 0:
            return False
        return zipfile.is_zipfile(p)
    except OSError:
        return False


def discard_incomplete(*paths: str | Path) -> list[str]:
    """끝까지 안 받아진 zip 을 지운다 → 다음 다운로드가 **실제로** 다시 받는다.

    지우지 않으면 asf_search 가 "파일이 이미 있다"며 건너뛴다(download.py 의 isfile
    검사). 보관 폴더와 작업 폴더가 하드링크로 같은 파일을 가리키므로 두 경로를 함께
    받는다 — 한쪽만 지우면 다른 쪽이 남아 같은 일이 반복된다.
    """
    gone: list[str] = []
    for path in paths:
        p = Path(path)
        if p.exists() and not zip_complete(p):
            try:
                p.unlink()
                gone.append(str(p))
            except OSError:                      # 다른 프로세스가 쥐고 있으면 다음 실행에
                pass
    return gone


def provide(names: list[str], dest: str | Path, root: str | Path | None = None) -> list[str]:
    """필요한 장면(names, .zip 유무 무관)을 보관 폴더에서 dest 로 끌어온다.

    같은 드라이브면 하드링크(디스크 0), 아니면 복사. dest 에 이미 있으면 그대로 둔다.
    반환: 보관 폴더에서 충족된 장면 이름 목록(다운로드가 필요 없는 것들).
    """
    files = scan(root)
    if not files:
        return []
    index = {p.stem: p for p in files}          # "S1A_..._XXXX" → 경로
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    satisfied: list[str] = []
    for name in names:
        stem = name[:-4] if name.lower().endswith(".zip") else name
        src = index.get(stem)
        if src is None:
            continue
        tgt = dest / f"{stem}.zip"
        if zip_complete(tgt):
            satisfied.append(stem)              # 이미 온전히 있다
            continue
        # 작업 폴더의 조각은 우리가 만든 것 — 지워야 다운로더가 다시 받는다.
        discard_incomplete(tgt)
        if not zip_complete(src):
            continue                            # 보관본도 조각 → 다운로드에 맡긴다
        try:
            os.link(src, tgt)                   # 같은 드라이브 → 디스크 추가 사용 0
        except OSError:
            shutil.copy2(src, tgt)              # 다른 드라이브/권한 → 복사 폴백
        satisfied.append(stem)
    return satisfied


# ── 보관 현황(인벤토리) ──────────────────────────────────────────────────
# 보관 폴더에 무엇이 얼마나 있는지를 한 장으로 — 어느 궤도가 언제부터 언제까지 몇 장,
# 빈 기간이 어디인지, 받다 만 조각이 몇 개인지. 12일 주기 감시를 하려면 이 표가 먼저다.
INDEX_NAME = ".inframon_slc_index.json"

# Sentinel-1 절대궤도 → 상대궤도(track). S1A·S1B 는 ESA 공식 식. S1C 는 공식 오프셋을
# 확인하지 못해 비워 둔다(파일명에 상대궤도가 없으므로 추정하지 않는다).
_REL_ORBIT_OFFSET = {"S1A": 73, "S1B": 27}


def parse_scene(name: str) -> dict | None:
    """`S1A_IW_SLC__1SDV_20180619T093402_20180619T093429_022412_026D7A_9F3C(.zip)` → 필드."""
    stem = name[:-4] if name.lower().endswith(".zip") else name
    parts = stem.split("_")
    # 'SLC__1SDV' 는 '_' 두 개라 빈 조각이 생긴다 → 빈 조각 제거
    p = [x for x in parts if x != ""]
    if len(p) < 9 or not p[0].startswith("S1") or p[2] != "SLC":
        return None
    mission, mode, _slc, level_pol, start, stop, abs_orbit, datatake, uid = p[:9]
    try:
        absn = int(abs_orbit)
    except ValueError:
        return None
    off = _REL_ORBIT_OFFSET.get(mission)
    return {"scene": stem, "mission": mission, "mode": mode,
            "polarisation": level_pol[2:] if len(level_pol) >= 4 else level_pol,
            "start": f"{start[:4]}-{start[4:6]}-{start[6:8]}T{start[9:11]}:{start[11:13]}:{start[13:15]}",
            "date": f"{start[:4]}-{start[4:6]}-{start[6:8]}",
            "abs_orbit": absn,
            "track": ((absn - off) % 175) + 1 if off is not None else None,
            "datatake": datatake, "uid": uid}


def _scan_products(root: Path):
    """SLC .zip 과 조립된 .SAFE 폴더를 찾는다. .SAFE 안으로는 내려가지 않는다(파일 수천 개)."""
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in dirnames:
            if d.startswith("S1") and "_SLC_" in d and d.endswith(".SAFE"):
                yield Path(dirpath) / d, "safe"
            elif not d.startswith("."):
                keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            if fn.startswith("S1") and "_SLC_" in fn and fn.lower().endswith(".zip"):
                yield Path(dirpath) / fn, "zip"


def _dir_size(d: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(d):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


def inventory(roots: list[str | Path] | None = None, *, check_zip: bool = True,
              write_index: bool = True) -> dict:
    """보관 폴더(들)의 SLC 현황.

    roots 미지정이면 get_slc_dir(). check_zip=True 면 zip 끝(EOCD)만 읽어 완결 여부를
    판정한다(장당 즉시). 결과를 각 root 에 `.inframon_slc_index.json` 으로도 남긴다.
    """
    from datetime import date as _date

    if not roots:
        r = get_slc_dir()
        roots = [r] if r else []
    scenes: list[dict] = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        local = []
        for f, kind in _scan_products(root):
            info = parse_scene(f.name[:-5] if kind == "safe" else f.name)
            if info is None:
                continue
            try:
                size = f.stat().st_size if kind == "zip" else _dir_size(f)
            except OSError:
                continue
            if kind == "zip":
                ok = zip_complete(f) if check_zip else None
            else:                                 # 조립된 .SAFE — manifest 가 있어야 온전하다
                ok = (f / "manifest.safe").is_file()
            info.update({"path": str(f), "root": str(root), "format": kind,
                         "folder": str(f.parent.relative_to(root)) if f.parent != root else ".",
                         "size_gb": round(size / 1e9, 2), "complete": ok})
            local.append(info)
        scenes.extend(local)
        if write_index:
            try:
                (root / INDEX_NAME).write_text(json.dumps(
                    {"schema": "inframon.slc_index/1", "root": str(root),
                     "generated": _date.today().isoformat(), "scenes": local},
                    ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass                              # 읽기 전용 보관소면 색인만 생략

    # 같은 장면이 여러 폴더에 있으면(하드링크·복사) 한 번만 센다
    uniq: dict[str, dict] = {}
    for s in scenes:
        uniq.setdefault(s["scene"], s)
    tracks: dict[str, dict] = {}
    for s in uniq.values():
        key = f"{s['mission'][:2]}-T{s['track']:03d}" if s["track"] else f"{s['mission']}-T?"
        t = tracks.setdefault(key, {"track": s["track"], "dates": set(), "n": 0, "size_gb": 0.0,
                                    "incomplete": 0})
        t["dates"].add(s["date"])
        t["n"] += 1
        t["size_gb"] += s["size_gb"]
        t["incomplete"] += int(s["complete"] is False)
    track_rows = []
    for key, t in sorted(tracks.items()):
        ds = sorted(t["dates"])
        gaps = []
        for a, b in zip(ds, ds[1:]):
            dd = (_date.fromisoformat(b) - _date.fromisoformat(a)).days
            if dd > 24:                           # 재방문 12일 × 2 를 넘는 공백
                gaps.append({"from": a, "to": b, "days": dd})
        track_rows.append({"key": key, "track": t["track"], "n_scenes": t["n"],
                           "n_dates": len(ds), "first": ds[0], "last": ds[-1],
                           "size_gb": round(t["size_gb"], 1), "incomplete": t["incomplete"],
                           "gaps_over_24d": len(gaps), "largest_gap": max(gaps, key=lambda g: g["days"]) if gaps else None})
    return {
        "roots": [str(r) for r in roots],
        "n_files": len(scenes), "n_scenes": len(uniq),
        "size_gb": round(sum(s["size_gb"] for s in uniq.values()), 1),
        "incomplete": sum(1 for s in uniq.values() if s["complete"] is False),
        "tracks": track_rows,
        "scenes": sorted(uniq.values(), key=lambda s: (s["track"] or 0, s["date"])),
    }
