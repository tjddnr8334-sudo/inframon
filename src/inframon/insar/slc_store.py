"""사용자 지정 SLC 보관 폴더 — "이미 받아둔 SLC 를 어디에 두든 알아서 인식".

사용자가 SLC(.zip)를 원하는 폴더(예: E:\\SLC)에 모아두면, 취득(--snap-auto 등)이
다운로드 전에 이 보관 폴더를 먼저 뒤져 **있는 장면은 하드링크/복사로 재사용**하고
없는 장면만 내려받는다 — 장당 4~8GB 재다운로드를 없애는 계층.

위치 결정 우선순위: 환경변수 `INFRAMON_SLC_DIR` > `~/.inframon/config.json` 의
`slc_dir`(CLI `--slc-dir DIR` 로 저장 — 대시보드 config 와 같은 파일, 병합 저장).
어느 쪽도 없으면 조용히 no-op(기존 동작 그대로).

하드링크는 같은 드라이브에서 디스크 추가 사용 0 — 다른 드라이브면 복사로 폴백한다.
"""

from __future__ import annotations

import json
import os
import shutil
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


def set_slc_dir(path: str | Path) -> Path:
    """보관 폴더를 config 에 저장(다른 키 보존·병합). 폴더가 없으면 ValueError."""
    p = Path(path).resolve()
    if not p.is_dir():
        raise ValueError(f"SLC 보관 폴더가 없습니다: {p}")
    cfg = _config_load()
    cfg["slc_dir"] = str(p)
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def scan(root: str | Path | None = None) -> list[Path]:
    """보관 폴더를 재귀 탐색해 SLC zip 목록(파일명 순). root 미지정이면 get_slc_dir()."""
    root = Path(root) if root else get_slc_dir()
    if root is None or not Path(root).is_dir():
        return []
    return sorted(Path(root).rglob(_SLC_GLOB), key=lambda p: p.name)


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
        if not (tgt.exists() and tgt.stat().st_size > 0):
            try:
                os.link(src, tgt)               # 같은 드라이브 → 디스크 추가 사용 0
            except OSError:
                shutil.copy2(src, tgt)          # 다른 드라이브/권한 → 복사 폴백
        satisfied.append(stem)
    return satisfied
