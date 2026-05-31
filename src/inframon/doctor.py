"""readiness doctor — 실데이터 투입 전 환경·데이터 준비도 진단.

선택 의존성이 무엇을 가능하게 하는지, (선택)데이터 루트/Track H5 가 인제스트 가능한지를
구조적 리포트로 돌려준다(부작용 없음). CLI `--doctor [PATH]`:
PATH 가 디렉터리면 InSAR 인벤토리, `.h5` 파일이면 Track preflight 를 포함한다.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# (모듈명, 필수여부, 가능하게 하는 것, 설치법)
_DEPS: tuple[tuple[str, bool, str, str], ...] = (
    ("numpy", True, "코어 수치 연산", "pip install -e ."),
    ("h5py", True, "project.h5·Track H5 입출력", "pip install -e ."),
    ("pydantic", True, "데이터 계약(스키마)", "pip install -e ."),
    ("scipy", False, "CV 형태학(거리변환)·StaMPS .mat 로드", "pip install -e .[cv]"),
    ("torch", False, "PINN real(PDE+FEM)·CV transformer 백엔드", "pip install -e .[pinn]"),
    ("transformers", False, "CV transformer 분할·SAM 부재 분할", "pip install -e .[cv]"),
    ("rasterio", False, "GeoTIFF 에서 geo_transform/crs 읽기", "conda install -c conda-forge rasterio"),
    ("pyproj", False, "Track CRS ≠ CV CRS 일 때 좌표 재투영", "pip install pyproj"),
    ("asf_search", False, "Sentinel-1 SLC 검색(트랙 선별)", "pip install -e .[search]"),
    ("streamlit", False, "대시보드 뷰어", "pip install -e .[dashboard]"),
)


@dataclass(frozen=True)
class DepCheck:
    name: str
    present: bool
    required: bool
    enables: str
    install: str


@dataclass(frozen=True)
class DoctorReport:
    deps: list[DepCheck]
    capabilities: dict[str, bool]
    data: dict[str, Any] | None = None       # 인벤토리 요약(선택)
    track: dict[str, Any] | None = None       # preflight 요약(선택)
    notes: list[str] = field(default_factory=list)

    @property
    def core_ok(self) -> bool:
        """필수 의존성이 모두 있으면 코어 동작 가능."""
        return all(d.present for d in self.deps if d.required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "core_ok": self.core_ok,
            "capabilities": self.capabilities,
            "deps": [vars(d) for d in self.deps],
            "data": self.data,
            "track": self.track,
            "notes": self.notes,
        }


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def run_doctor(path: str | Path | None = None) -> DoctorReport:
    """환경·(선택)데이터 준비도를 진단한다. path: 디렉터리→인벤토리, .h5→preflight."""
    deps = [DepCheck(n, _has(n), req, en, ins) for (n, req, en, ins) in _DEPS]
    have = {d.name: d.present for d in deps}

    capabilities = {
        "pipeline_demo": have["numpy"] and have["h5py"] and have["pydantic"],
        "insar_ingest": have["h5py"],                       # Track H5 → /insar
        "pinn_real": have["torch"],
        "cv_real_classical": have["numpy"] and have["scipy"],
        "cv_transformer": have["torch"] and have["transformers"],
        "cv_member_sam": have["torch"] and have["transformers"],
        "cv_geotiff_geo": have["rasterio"],
        "crs_reprojection": have["pyproj"],
        "slc_search": have["asf_search"],
        "dashboard": have["streamlit"],
    }

    notes: list[str] = []
    if not capabilities["pinn_real"]:
        notes.append("PINN real 미가용 → pinn=stub 로만 실행 가능(torch 설치 필요).")
    if not capabilities["crs_reprojection"]:
        notes.append("pyproj 없음 → Track 좌표가 CV CRS 와 다르면 정합 불가(같으면 무관).")
    if not capabilities["cv_geotiff_geo"]:
        notes.append("rasterio 없음 → GeoTIFF geo_transform 자동 읽기 불가(cfg override 로 주입 가능).")

    data = None
    track = None
    if path is not None and str(path):
        p = Path(path)
        if not p.exists():
            notes.append(f"경로가 없습니다: {p}")
        elif p.is_dir():
            data = _inventory_summary(p, notes)
        elif p.suffix.lower() in (".h5", ".hdf5"):
            track = _preflight_summary(p)
        else:
            notes.append(f"점검 대상이 디렉터리도 .h5 도 아닙니다: {p}")

    return DoctorReport(deps=deps, capabilities=capabilities, data=data, track=track, notes=notes)


def _inventory_summary(root: Path, notes: list[str]) -> dict[str, Any]:
    try:
        from .insar.inventory import build_scene_manifest, inspect_insar_data
        inv = inspect_insar_data(str(root))
        man = build_scene_manifest(str(root))
        return {
            "root": str(root),
            "slc_zip_count": inv.slc_zip_count,
            "slc_total_gb": round(inv.slc_total_gb, 2),
            "usable_scenes": man.get("usable_count"),
            "source_scenes": man.get("source_slc_count"),
            "selected_master": inv.selected_master,
            "ready_for_timeseries": inv.is_ready_for_timeseries,
            "missing_required": list(inv.missing_required),
        }
    except Exception as exc:  # noqa: BLE001 — 진단 도구는 죽지 않는다
        notes.append(f"인벤토리 점검 실패: {exc}")
        return {"root": str(root), "error": str(exc)}


def _preflight_summary(track_h5: Path) -> dict[str, Any]:
    from .insar.track_preflight import preflight_track_h5
    rep = preflight_track_h5(track_h5)
    return {
        "path": str(track_h5),
        "is_ready": rep.is_ready,
        "n_points": rep.n_points,
        "n_dates": rep.n_dates,
        "errors": rep.errors,
        "warnings": rep.warnings,
    }


def format_report(rep: DoctorReport) -> str:
    """CLI 출력용 사람이 읽는 진단 리포트."""
    lines = ["=" * 56, "  inframon readiness doctor", "=" * 56]
    lines.append("  [의존성]")
    for d in rep.deps:
        mark = "✅" if d.present else ("❌" if d.required else "⚪")
        tag = "필수" if d.required else "선택"
        lines.append(f"    {mark} {d.name:<13} ({tag}) — {d.enables}")
        if not d.present:
            lines.append(f"        설치: {d.install}")
    lines.append("  [가능한 기능]")
    for cap, ok in rep.capabilities.items():
        lines.append(f"    {'✅' if ok else '⚪'} {cap}")
    if rep.data:
        d = rep.data
        lines.append("  [데이터 인벤토리]")
        if "error" in d:
            lines.append(f"    ❌ {d['error']}")
        else:
            lines.append(f"    SLC {d['slc_zip_count']}개·{d['slc_total_gb']}GB · "
                         f"사용가능 {d['usable_scenes']}/{d['source_scenes']} · "
                         f"master {d['selected_master'] or '-'}")
            lines.append(f"    시계열 준비: {'✅' if d['ready_for_timeseries'] else '❌'}"
                         + ("" if d["ready_for_timeseries"]
                            else "  누락: " + ", ".join(d["missing_required"])))
    if rep.track:
        t = rep.track
        lines.append("  [Track preflight]")
        lines.append(f"    {'✅ 투입 가능' if t['is_ready'] else '❌ 투입 불가'} · "
                     f"N={t['n_points']} M={t['n_dates']}")
        for e in t["errors"]:
            lines.append(f"    ❌ {e}")
        for w in t["warnings"]:
            lines.append(f"    ⚠️  {w}")
    for n in rep.notes:
        lines.append(f"  ⚠️  {n}")
    lines.append(f"  판정: {'✅ 코어 동작 가능' if rep.core_ok else '❌ 필수 의존성 누락'}")
    lines.append("=" * 56)
    return "\n".join(lines)
