"""실제 InSAR 데이터 폴더 인벤토리.

Phase 1 실제 어댑터의 첫 단계로, WSL/로컬 데이터 루트에 있는 SLC, orbit,
DEM, ROI 메타파일을 가볍게 확인한다. 대용량 SLC zip 내부는 열지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..contracts.io import ProjectStore


SLC_DATE_RE = re.compile(r"_(\d{8})T")
DATASOURCES_SLC_COUNT_RE = re.compile(r"SLC[^\n]*\((\d+)(?:장|files|개)\)")


@dataclass(frozen=True)
class InSARDataInventory:
    root: Path
    slc_zip_count: int
    slc_first_date: str | None
    slc_last_date: str | None
    slc_dates: list[str]
    slc_total_bytes: int
    orbit_count: int
    dem_files: list[str]
    roi_files: list[str]
    selected_master: str | None
    selected_master_era5: str | None
    bperp_pass_count: int | None
    bperp_exclude_count: int | None
    declared_slc_count: int | None = None
    missing_required: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exclude_dates: list[str] = field(default_factory=list)

    @property
    def slc_total_gb(self) -> float:
        return self.slc_total_bytes / (1024**3)

    @property
    def is_ready_for_timeseries(self) -> bool:
        return not self.missing_required

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["root"] = str(self.root)
        data["slc_total_gb"] = self.slc_total_gb
        data["is_ready_for_timeseries"] = self.is_ready_for_timeseries
        return data


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_exclude_dates(path: Path) -> list[str]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8").replace("\n", ",")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _declared_slc_count(path: Path) -> int | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    match = DATASOURCES_SLC_COUNT_RE.search(text)
    return int(match.group(1)) if match else None


def _missing_required(root: Path) -> list[str]:
    required = [
        "SLC",
        "orbits",
        "DEM",
        "master_selection.json",
        "bperp_filter.json",
        "exclude_dates.txt",
    ]
    return [name for name in required if not (root / name).exists()]


def inspect_insar_data(root: str | Path) -> InSARDataInventory:
    """데이터 루트를 훑어 실제 처리 전 필요한 핵심 수량을 반환한다."""
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f"InSAR data root not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"InSAR data root is not a directory: {root}")

    slc_dir = root / "SLC"
    slc_files = sorted(slc_dir.glob("S1*.zip")) if slc_dir.exists() else []
    dates = []
    total_bytes = 0
    for path in slc_files:
        total_bytes += path.stat().st_size
        m = SLC_DATE_RE.search(path.name)
        if m:
            dates.append(m.group(1))
    dates.sort()

    orbit_dir = root / "orbits"
    orbit_count = len(list(orbit_dir.glob("*.EOF"))) if orbit_dir.exists() else 0
    dem_dir = root / "DEM"
    dem_files = sorted(p.name for p in dem_dir.rglob("*") if p.is_file()) if dem_dir.exists() else []
    roi_files = sorted(p.name for p in root.glob("*.kmz"))

    master = _read_json(root / "master_selection.json")
    master_era5 = _read_json(root / "master_selection_era5.json")
    bperp = _read_json(root / "bperp_filter.json")
    exclude_dates = _read_exclude_dates(root / "exclude_dates.txt")
    declared_count = _declared_slc_count(root / "data_sources.txt")
    missing_required = _missing_required(root)

    warnings = []
    if declared_count is not None and declared_count != len(slc_files):
        warnings.append(
            f"data_sources.txt SLC count ({declared_count}) != actual SLC zip count ({len(slc_files)})"
        )
    bperp_total = None
    if bperp.get("pass_count") is not None and bperp.get("exclude_count") is not None:
        bperp_total = int(bperp["pass_count"]) + int(bperp["exclude_count"])
    if bperp_total is not None and bperp_total != len(slc_files):
        warnings.append(f"bperp_filter total ({bperp_total}) != actual SLC zip count ({len(slc_files)})")
    if master.get("selected_master") and master["selected_master"] not in dates:
        warnings.append(f"selected_master {master['selected_master']} is not present in SLC dates")
    if master_era5.get("selected_master") and master_era5["selected_master"] not in dates:
        warnings.append(f"ERA5 selected_master {master_era5['selected_master']} is not present in SLC dates")
    exclude_missing = sorted(set(exclude_dates) - set(dates))
    if exclude_missing:
        warnings.append(f"{len(exclude_missing)} exclude_dates are not present in current SLC zip set")
    if orbit_count and slc_files and orbit_count != len(slc_files):
        warnings.append(f"orbit EOF count ({orbit_count}) != actual SLC zip count ({len(slc_files)})")

    return InSARDataInventory(
        root=root,
        slc_zip_count=len(slc_files),
        slc_first_date=dates[0] if dates else None,
        slc_last_date=dates[-1] if dates else None,
        slc_dates=dates,
        slc_total_bytes=total_bytes,
        orbit_count=orbit_count,
        dem_files=dem_files,
        roi_files=roi_files,
        selected_master=master.get("selected_master"),
        selected_master_era5=master_era5.get("selected_master"),
        bperp_pass_count=bperp.get("pass_count"),
        bperp_exclude_count=bperp.get("exclude_count"),
        declared_slc_count=declared_count,
        missing_required=missing_required,
        warnings=warnings,
        exclude_dates=exclude_dates,
    )


def build_scene_manifest(root: str | Path) -> dict[str, Any]:
    """현재 SLC 파일 기준으로 오래된 bperp/exclude 메타를 정규화한 처리 manifest를 만든다."""
    root = Path(root)
    inventory = inspect_insar_data(root)
    slc_dates = set(inventory.slc_dates)
    exclude_dates = set(inventory.exclude_dates)
    bperp = _read_json(root / "bperp_filter.json")
    bperp_entries = {
        str(entry["date"]): entry
        for entry in bperp.get("entries", [])
        if isinstance(entry, dict) and entry.get("date") is not None
    }

    rejected = []
    usable = []
    for date in inventory.slc_dates:
        reasons = []
        entry = bperp_entries.get(date)
        if entry is None:
            reasons.append("missing_bperp")
        elif not bool(entry.get("pass", False)):
            reasons.append("bperp_reject")
        if date in exclude_dates:
            reasons.append("manual_exclude")

        if reasons:
            rejected.append({"date": date, "reasons": reasons})
        else:
            usable.append(date)

    stale_bperp_dates = sorted(set(bperp_entries) - slc_dates)
    missing_bperp_dates = sorted(slc_dates - set(bperp_entries))
    excluded_present_dates = sorted(exclude_dates & slc_dates)
    excluded_missing_dates = sorted(exclude_dates - slc_dates)

    return {
        "root": str(root),
        "source_slc_count": inventory.slc_zip_count,
        "usable_count": len(usable),
        "rejected_count": len(rejected),
        "usable_dates": usable,
        "rejected_dates": rejected,
        "stale_bperp_dates": stale_bperp_dates,
        "missing_bperp_dates": missing_bperp_dates,
        "excluded_present_dates": excluded_present_dates,
        "excluded_missing_dates": excluded_missing_dates,
        "selected_master": inventory.selected_master,
        "selected_master_era5": inventory.selected_master_era5,
        "recommended_master": inventory.selected_master_era5 or inventory.selected_master,
    }


def write_inventory(project_path: str | Path, inventory: InSARDataInventory) -> None:
    """실제 데이터 인벤토리를 project.h5의 /insar attribute에 기록한다."""
    project_path = Path(project_path)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    with ProjectStore(project_path, mode="a") as store:
        store.write_json_attr("insar", "data_inventory", inventory.to_dict())
        store.write_json_attr("insar", "scene_manifest", build_scene_manifest(inventory.root))
