"""InSAR 처리 레시피 — 데이터 선별 단계의 산출물(SARvey 가 먹을 입력 명세).

A·B(교량 타깃) → C·D(SLC/트랙 선별) → E(master) 로 점점 채워지는 명세를 JSON 으로
저장한다. 1차 증분은 BridgeTarget(A·B)만 다룬다. 이후 SceneSelection/MasterSelection 으로
확장하며, 최종적으로 `insar/inventory.py` 가 읽는 형식(master_selection.json 등)과 맞춘다.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .osm_bridge import Bridge


class BridgeTarget(BaseModel):
    """A·B 산출물 — 지도에서 고르고 OSM 으로 확인한 교량 타깃."""

    name: str
    name_ko: str | None = None
    selected_lat: float          # 사용자가 지도에서 고른 지점
    selected_lon: float
    osm_type: str
    osm_id: int
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat) — SLC 검색 영역
    length_m: float | None = None
    distance_m: float | None = None          # 선택 지점 ↔ 교량 거리(확인 신뢰도)
    tags: dict[str, str] = Field(default_factory=dict)
    confirmed: bool = True

    @property
    def osm_url(self) -> str:
        return f"https://www.openstreetmap.org/{self.osm_type}/{self.osm_id}"

    @classmethod
    def from_bridge(cls, bridge: Bridge, selected_lat: float, selected_lon: float) -> BridgeTarget:
        return cls(
            name=bridge.name,
            name_ko=bridge.name_ko,
            selected_lat=selected_lat,
            selected_lon=selected_lon,
            osm_type=bridge.osm_type,
            osm_id=bridge.osm_id,
            bbox=bridge.bbox,
            length_m=bridge.length_m,
            distance_m=bridge.distance_m,
            tags=bridge.tags,
            confirmed=True,
        )


class SelectionCriteria(BaseModel):
    """C·D 단계 SLC/페어 선별 기준 — C/D 구현 전에 미리 박제해 둔다.

    공간(수직) baseline 은 ≤ perp_baseline_max_m 인 것만 사용한다(기본 150 m).
    시간 baseline 상한은 temporal_baseline_max_days(None=제한 없음)로 둔다.
    """

    perp_baseline_max_m: float = 150.0          # 공간(수직) baseline 상한 [m]
    temporal_baseline_max_days: float | None = None  # 시간 baseline 상한 [일], None=무제한
    polarization: str = "VV"                    # VV 만 사용
    orbit_direction: str | None = None          # "ASCENDING"|"DESCENDING"|None(자동: 최다)
    prefer_most_data_track: bool = True          # track/frame 중 취득 최다 조합 선택


class TrackSelection(BaseModel):
    """D 산출물 — 선별된 트랙(방향/path/frame)과 그 장면 목록(SARvey 처리 대상)."""

    flight_direction: str
    path: int
    frame: int
    polarization: str = "VV"
    n_scenes: int
    first_date: str
    last_date: str
    scene_dates: list[str] = Field(default_factory=list)
    scene_names: list[str] = Field(default_factory=list)

    @classmethod
    def from_selection(cls, group, scenes, polarization: str = "VV") -> TrackSelection:
        return cls(
            flight_direction=group.flight_direction,
            path=group.path,
            frame=group.frame,
            polarization=polarization,
            n_scenes=group.n_scenes,
            first_date=group.first_date,
            last_date=group.last_date,
            scene_dates=[s.date for s in scenes],
            scene_names=[s.scene_name for s in scenes],
        )


class SceneWeather(BaseModel):
    """master 후보 1개의 점수 구성요소 (baseline + 대기)."""

    date: str               # YYYYMMDD
    precip_mm: float        # 그날 총 강수 [mm]
    humidity_pct: float     # 그날 평균 상대습도 [%]
    rho: float = 0.0        # baseline 기대 coherence (다른 장면들과의 시·공간 baseline 기반)
    dry_score: float = 0.0  # 건조도 (강수·습도, 높을수록 건조)
    combined: float = 0.0   # 최종 = norm(rho) × dry_score, 높을수록 master 적합


class MasterSelection(BaseModel):
    """E 산출물 — baseline(기대 coherence) × ERA5(강수·습도)로 고른 SARvey master.

    `selected_master` 키는 insar/inventory.py 가 읽는 master_selection_era5.json 형식과 호환.
    종합 점수: combined = norm(rho) × dry_score (rho=시·공간 baseline 기대 coherence).
    """

    selected_master: str            # master 취득일 YYYYMMDD
    master_scene: str | None = None
    lat: float
    lon: float
    source: str = "baseline(expected coherence) × ERA5(precip,humidity)"
    used_baseline: bool = False     # 수직 baseline 사용 여부(없으면 시간 baseline 만)
    temporal_crit_days: float = 365.0
    perp_crit_m: float = 300.0
    scenes: list[SceneWeather] = Field(default_factory=list)


def save_bridge_target(path: str | Path, target: BridgeTarget) -> Path:
    return _save_json(path, target)


def load_bridge_target(path: str | Path) -> BridgeTarget:
    return BridgeTarget.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_selection_criteria(path: str | Path, criteria: SelectionCriteria) -> Path:
    return _save_json(path, criteria)


def load_selection_criteria(path: str | Path) -> SelectionCriteria:
    return SelectionCriteria.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_track_selection(path: str | Path, track: TrackSelection) -> Path:
    return _save_json(path, track)


def load_track_selection(path: str | Path) -> TrackSelection:
    return TrackSelection.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_master_selection(path: str | Path, master: MasterSelection) -> Path:
    return _save_json(path, master)


def load_master_selection(path: str | Path) -> MasterSelection:
    return MasterSelection.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _save_json(path: str | Path, model: BaseModel) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    return path
