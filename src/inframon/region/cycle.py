"""광역 감시 설정(``configs/<region>/region.json``)과 엔진 실행.

region.json 예::

    {"title": "강원특별자치도 강릉시",
     "engine_dir": "/mnt/f/bridge-insar-monitor",        # bridge-insar-monitor 체크아웃
     "engine_config": "config/gangneung.yaml",           # 엔진 설정(지자체·궤도·경로)
     "engine_python": "/home/insar/miniforge3/envs/b2s/bin/python",
     "results_dir": "/mnt/e/InSAR_DATA/bim/gangneung",   # 엔진 산출물(state.json 등)
     "projects_dir": "data/region/gangneung",            # 교량별 project.h5 내보내기 위치
     "update_days": 12}

엔진은 GPL 도구(ISCE2·StaMPS)를 쓰므로 **CLI 로만** 호출한다(CONTRIBUTING).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def load_region(path) -> dict:
    c = json.loads(Path(path).read_text(encoding="utf-8"))
    for k in ("engine_dir", "engine_config", "results_dir"):
        if k not in c:
            raise ValueError(f"region.json 에 '{k}' 가 없습니다: {path}")
    c.setdefault("update_days", 12)
    c.setdefault("engine_python", "python")
    c["_path"] = str(path)
    return c


def run_engine(c: dict, command: str = "update", *, log=None) -> int:
    """엔진 한 주기(``update``: 새 영상 → 정합(보간) → 판정 → 알람) 또는 ``report``/``health``."""
    if command not in ("update", "report", "health", "download", "status"):
        raise ValueError(command)
    cmd = [c["engine_python"], "-m", "bim", command, "--config", c["engine_config"]]
    out = open(log, "a", encoding="utf-8") if log else None
    try:
        return subprocess.run(cmd, cwd=c["engine_dir"], stdout=out, stderr=subprocess.STDOUT if out else None).returncode
    finally:
        if out:
            out.close()


def npz_for(c: dict, row: dict) -> str | None:
    """교량 행 → 엔진 StaMPS npz 경로(엔진 state 의 r.npz). 없으면 None."""
    p = (row.get("r") or {}).get("npz")
    return p if p and Path(p).exists() else None
