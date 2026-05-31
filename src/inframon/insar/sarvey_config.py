"""레시피 4종 → SARvey 처리 번들 생성 — InSAR 선별(A~E)과 처리(F) 사이의 다리.

레시피 항목은 두 군데로 나뉜다:
  • 트랙/프레임/궤도/편파/master/장면목록/공간 baseline → 상류 SLC **스택 생성**
    (ISCE2 stackSentinel + MiaplPy load_data)을 좌우 → `processing_manifest.json`
  • 시계열 추정 파라미터(분석 기간·시간 baseline 네트워크 등) → `sarvey_config.json`

SARvey config 키는 버전마다 다를 수 있으므로, 생성물에 `_README` 로 "본인 SARvey
버전의 `sarvey -g` 템플릿과 대조" 안내를 남긴다. 의존성 없이 JSON 으로 출력한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from .recipe import (
    BridgeTarget,
    MasterSelection,
    SelectionCriteria,
    TrackSelection,
    load_bridge_target,
    load_master_selection,
    load_selection_criteria,
    load_track_selection,
)

# 레시피 파일 표준 이름
F_TARGET = "bridge_target.json"
F_CRITERIA = "selection_criteria.json"
F_TRACK = "track_selection.json"
F_MASTER = "master_selection_era5.json"


def _ymd_to_iso(ymd: str) -> str:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


class RecipeBundle:
    """recipe_dir 의 4종 레시피를 모아 로드(일부 없으면 None)."""

    def __init__(self, recipe_dir: str | Path):
        d = Path(recipe_dir)
        self.target: BridgeTarget | None = (
            load_bridge_target(d / F_TARGET) if (d / F_TARGET).exists() else None
        )
        self.criteria: SelectionCriteria = (
            load_selection_criteria(d / F_CRITERIA)
            if (d / F_CRITERIA).exists() else SelectionCriteria()
        )
        self.track: TrackSelection | None = (
            load_track_selection(d / F_TRACK) if (d / F_TRACK).exists() else None
        )
        self.master: MasterSelection | None = (
            load_master_selection(d / F_MASTER) if (d / F_MASTER).exists() else None
        )

    def require(self) -> None:
        missing = []
        if self.target is None:
            missing.append(F_TARGET)
        if self.track is None:
            missing.append(F_TRACK)
        if missing:
            raise FileNotFoundError(
                "SARvey 번들 생성에 필요한 레시피가 없습니다: " + ", ".join(missing)
                + " (교량 타깃·트랙 선별을 먼저 저장하세요)"
            )


def build_processing_manifest(b: RecipeBundle) -> dict:
    """상류 SLC 스택 생성(ISCE2/MiaplPy)을 위한 매니페스트."""
    b.require()
    t, trk, crit, mst = b.target, b.track, b.criteria, b.master
    return {
        "_README": "ISCE2 stackSentinel + MiaplPy 스택 생성 파라미터. SARvey 실행 전 단계.",
        "aoi": {
            "name": t.name,
            "name_ko": t.name_ko,
            "lat": t.selected_lat,
            "lon": t.selected_lon,
            "bbox_lonlat": list(t.bbox),
            "osm": t.osm_url,
            "length_m": t.length_m,
        },
        "stack": {
            "mission": "SENTINEL-1",
            "product": "SLC",
            "beam_mode": "IW",
            "orbit_direction": trk.flight_direction,
            "relative_orbit": trk.path,
            "frame": trk.frame,
            "polarization": crit.polarization,
            "reference_date": mst.selected_master if mst else None,
            "num_scenes": trk.n_scenes,
            "date_range": [trk.first_date, trk.last_date],
            "scene_dates": list(trk.scene_dates),
            "scene_names": list(trk.scene_names),  # 정확한 granule — 다운로드는 이것으로
        },
        "baseline": {
            "max_perp_baseline_m": crit.perp_baseline_max_m,
            "max_temporal_baseline_days": crit.temporal_baseline_max_days,
        },
        "sources": {
            "bridge": "OSM (Overpass)",
            "slc": "ASF Sentinel-1",
            "reference_selection": (mst.source if mst else None),
        },
    }


def build_sarvey_config(b: RecipeBundle) -> dict:
    """SARvey MTI 시계열 추정 config(JSON). 버전별 키 차이는 _README 참조."""
    b.require()
    trk, crit = b.track, b.criteria
    max_tbase = int(crit.temporal_baseline_max_days) if crit.temporal_baseline_max_days else 100
    return {
        "_README": (
            "inframon 레시피에서 생성. 트랙/편파/master/공간baseline 은 processing_manifest.json"
            "(상류 스택 생성)에서 처리됩니다. 키 이름은 본인 SARvey 버전의 `sarvey -g` 템플릿과"
            " 대조해 조정하세요."
        ),
        "general": {
            "input_path": "inputs/",       # MiaplPy 산출(slcStack.h5, geometryRadar.h5)
            "output_path": "outputs/",
            "num_cores": 5,
            "num_patches": 1,
            "logging_level": "INFO",
        },
        "preparation": {
            "start_date": _ymd_to_iso(trk.first_date),
            "end_date": _ymd_to_iso(trk.last_date),
            "ifg_network_type": "sb",      # small baseline
            "num_ifgs": 3,
            "max_tbase": max_tbase,         # 시간 baseline 상한 [일]
            "filter_wdw_size": 9,
        },
        "consistency_check": {
            "coherence_p1": 0.9,
            "grid_size": 200,
            "num_nearest_neighbours": 30,
            "velocity_bound": 0.1,
            "dem_error_bound": 100.0,
            "arc_unwrapping_coherence_threshold": 0.6,
        },
        "unwrapping": {
            "use_arcs_from_temporal_unwrapping": True,
            "spatial_unwrapping_method": "puma",
        },
        "filtering": {
            "coherence_p2": 0.8,
            "apply_aps_filtering": True,
            "interpolation_method": "kriging",
        },
        "densification": {
            "coherence_threshold": 0.5,
            "num_connections_to_p1": 5,
            "max_distance_to_p1": 2000.0,
        },
    }


def write_sarvey_bundle(recipe_dir: str | Path, out_dir: str | Path | None = None) -> dict[str, Path]:
    """레시피 4종 → processing_manifest.json + sarvey_config.json 생성."""
    bundle = RecipeBundle(recipe_dir)
    out = Path(out_dir) if out_dir else Path(recipe_dir)
    out.mkdir(parents=True, exist_ok=True)

    manifest = build_processing_manifest(bundle)
    config = build_sarvey_config(bundle)
    manifest_path = out / "processing_manifest.json"
    config_path = out / "sarvey_config.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"manifest": manifest_path, "config": config_path}
