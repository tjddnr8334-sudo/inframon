"""산출물 출처 각인(provenance) — "이 Track H5 는 무엇으로 어떻게 만들었나".

기존에는 track.h5·project.h5 가 산출물 폴더에 놓여 있어도 **어느 교량·어느 엔진·어느
입력·어느 코드**에서 나왔는지 되짚을 방법이 없었다. 몇 달 뒤 같은 폴더를 열었을 때
"이 파일 믿어도 되나" 를 판단할 근거가 없으면 재현도 폐기도 못 한다.

여기서는 HDF5 attrs 에 한 줄짜리 사실만 박는다(용량 영향 없음). 파이프라인 단위의
더 자세한 기록은 `pipeline_bridge.PipelineReport.write_json` 이 남긴다.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROV_PREFIX = "prov_"          # 원 데이터셋 attrs 와 섞이지 않게 접두어를 둔다


def git_commit(repo_root: str | Path | None = None) -> str | None:
    """산출물이 어느 코드에서 나왔는지. git 없는 배포본에서는 None."""
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(root),
                             capture_output=True, text=True, errors="replace", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout.strip() or None) if out.returncode == 0 else None


def provenance_fields(**extra: Any) -> dict[str, Any]:
    """공통 출처 필드 — 시각·명령·커밋·버전 + 호출자가 준 값."""
    fields: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": " ".join(sys.argv[:12]),
        "git_commit": git_commit(),
    }
    try:
        from importlib.metadata import version
        fields["inframon_version"] = version("inframon")
    except Exception:  # noqa: BLE001 — 설치 형태에 따라 없을 수 있다
        pass
    fields.update({k: v for k, v in extra.items() if v is not None})
    return fields


def stamp_h5(path: str | Path, **extra: Any) -> bool:
    """HDF5 에 prov_* attrs 를 박는다. 실패해도 산출물을 죽이지 않는다(False 반환)."""
    import h5py
    import numpy as np

    p = Path(path)
    if not p.exists():
        return False
    try:
        with h5py.File(p, "a") as f:
            for k, v in provenance_fields(**extra).items():
                if v is None:
                    continue
                if isinstance(v, (list, tuple)):
                    f.attrs[PROV_PREFIX + k] = np.asarray(v, dtype=np.float64)
                elif isinstance(v, (int, float, bool)):
                    f.attrs[PROV_PREFIX + k] = v
                else:
                    f.attrs[PROV_PREFIX + k] = str(v)
    except OSError:
        return False
    return True


def read_h5(path: str | Path) -> dict[str, Any]:
    """박아둔 출처를 읽어 돌려준다(없으면 빈 dict) — 산출물 감사에 쓴다."""
    import h5py

    p = Path(path)
    if not p.exists():
        return {}
    try:
        with h5py.File(p, "r") as f:
            out = {}
            for k, v in f.attrs.items():
                if not str(k).startswith(PROV_PREFIX):
                    continue
                val = v.decode() if isinstance(v, bytes) else v
                out[str(k)[len(PROV_PREFIX):]] = (val.tolist() if hasattr(val, "tolist")
                                                  else val)
            return out
    except OSError:
        return {}
