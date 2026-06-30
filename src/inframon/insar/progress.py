"""InSAR 처리(F단계) 진행판 — WORK 폴더 산출물을 읽어 단계별 진행을 시각화.

WSL2 에서 SLC→ISCE2→MiaplPy→SARvey 가 도는 동안, **사람이 한눈에 어디까지 됐는지**
보게 한다. 터미널 로그는 흐르지만 이건 단계별 ✅/🔄/⬜ + 진행바 + 카운트로 요약한다.
파일시스템만 읽으므로 Windows/WSL 어느 쪽에서 켜도 같은 WORK 를 본다(별도 터미널 권장).

  python -m inframon --insar-progress <work_dir> [--recipe <recipe_dir>] [--watch 5]

단계↔산출물(scripts/wsl_sarvey/ 기준):
  10 다운로드  : WORK/SLC/*.zip · orbits/*.EOF · DEM/dem.wgs84 · aux/
  20 ISCE2     : WORK/stack/merged/SLC/*/ (코레지) · merged/interferograms/*/ · geom_reference/hgt.rdr.full
  30 MiaplPy   : WORK/miaplpy/inputs/{slcStack,geometryRadar}.h5
  40 SARvey    : WORK/sarvey/outputs/*_ts.h5
  50 Track H5  : WORK/track.h5
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PENDING, RUNNING, DONE = "pending", "running", "done"
_ICON = {PENDING: "⬜", RUNNING: "🔄", DONE: "✅"}


def _n(path: Path, pattern: str) -> int:
    return sum(1 for _ in path.glob(pattern)) if path.exists() else 0


def _exists(path: Path) -> bool:
    return path.exists() and (path.is_file() or any(path.iterdir()) if path.is_dir() else path.exists())


def _expected_scenes(work: Path, recipe: Path | None) -> int:
    """기대 장면 수 = 실제 처리할 SLC 수. WORK/SLC 의 zip 이 있으면 그 수(=이번 처리 대상),
    아직 없으면 레시피 manifest 의 num_scenes(전체 트랙 목표)로 폴백."""
    n = _n(work / "SLC", "*.zip")
    if n:
        return n
    if recipe:
        man = recipe / "processing_manifest.json"
        if man.exists():
            try:
                return int(json.loads(man.read_text(encoding="utf-8"))["stack"]["num_scenes"])
            except (KeyError, ValueError, json.JSONDecodeError):
                pass
    return 1


def _bar(frac: float, width: int = 14) -> str:
    frac = max(0.0, min(1.0, frac))
    fill = round(frac * width)
    return "█" * fill + "░" * (width - fill)


def scan_progress(work_dir: str | Path, recipe_dir: str | Path | None = None) -> dict[str, Any]:
    """WORK 폴더를 스캔해 단계별 진행 상태 dict 반환."""
    work = Path(work_dir)
    recipe = Path(recipe_dir) if recipe_dir else None
    exp = _expected_scenes(work, recipe)

    # 산출물 카운트
    n_slc = _n(work / "SLC", "*.zip")
    n_orbit = _n(work / "orbits", "*.EOF") + _n(work / "orbits", "*.eof")
    dem_ok = (work / "DEM" / "dem.wgs84").exists() or _n(work / "DEM", "*.wgs84") > 0
    aux_ok = _n(work / "aux", "*") > 0
    stack = work / "stack"
    n_coreg = _n(stack / "merged" / "SLC", "*") if stack.exists() else 0
    n_ifg = _n(stack / "merged" / "interferograms", "*") if stack.exists() else 0
    geom_ok = (stack / "merged" / "geom_reference" / "hgt.rdr.full").exists()
    miaplpy_in = work / "miaplpy" / "inputs"
    mp_slc = (miaplpy_in / "slcStack.h5").exists()
    mp_geo = (miaplpy_in / "geometryRadar.h5").exists()
    n_ts = _n(work / "sarvey" / "outputs", "*_ts.h5")
    track_ok = (work / "track.h5").exists()

    def status(done: bool, started: bool) -> str:
        return DONE if done else (RUNNING if started else PENDING)

    stages = [
        {"id": "10", "name": "다운로드",
         "status": status(n_slc >= exp and dem_ok and n_orbit >= exp, n_slc > 0),
         "frac": min(n_slc, exp) / exp,
         "detail": f"SLC {n_slc}/{exp} · 궤도 {n_orbit} · DEM {'✓' if dem_ok else '—'} · aux {'✓' if aux_ok else '—'}"},
        {"id": "20", "name": "ISCE2 스택",
         "status": status(geom_ok and n_coreg >= exp, stack.exists()),
         "frac": (min(n_coreg, exp) / exp) if exp else 0.0,
         "detail": f"코레지 {n_coreg}/{exp} {_bar(min(n_coreg, exp) / exp, 8)} · 간섭도 {n_ifg} · geom {'✓' if geom_ok else '—'}"},
        {"id": "30", "name": "MiaplPy",
         "status": status(mp_slc and mp_geo, mp_slc or mp_geo or (work / 'miaplpy').exists()),
         "frac": (mp_slc + mp_geo) / 2,
         "detail": f"slcStack {'✓' if mp_slc else '—'} · geometryRadar {'✓' if mp_geo else '—'}"},
        {"id": "40", "name": "SARvey",
         "status": status(n_ts > 0, (work / "sarvey").exists()),
         "frac": 1.0 if n_ts > 0 else 0.0,
         "detail": f"시계열 ts.h5 {n_ts}개 {'✓' if n_ts else '(대기)'}"},
        {"id": "50", "name": "Track H5",
         "status": status(track_ok, False),
         "frac": 1.0 if track_ok else 0.0,
         "detail": f"track.h5 {'✓ 인제스트 준비됨' if track_ok else '(대기)'}"},
    ]
    overall = sum(s["frac"] for s in stages) / len(stages)
    return {"work": str(work), "expected_scenes": exp, "stages": stages, "overall": overall}


def render_board(prog: dict[str, Any], *, title: str = "", now: str = "") -> str:
    """진행 상태 dict → 사람이 보는 보드 문자열."""
    W = 60
    lines = ["=" * W,
             f"  InSAR 처리 진행{(' — ' + title) if title else ''}",
             f"  work: {prog['work']}",
             "=" * W]
    for s in prog["stages"]:
        lines.append(f"  {_ICON[s['status']]} {s['id']} {s['name']:10} {s['detail']}")
    lines.append("-" * W)
    pct = round(prog["overall"] * 100)
    lines.append(f"  전체: {_bar(prog['overall'])} {pct}%" + (f"   (갱신 {now})" if now else ""))
    lines.append("=" * W)
    return "\n".join(lines)
