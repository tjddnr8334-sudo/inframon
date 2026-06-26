"""모듈 간 데이터 계약 (Interface Contract).

설계 문서 2.4 / 3.6 / 4.4 / 5.7 의 출력 구조를 Pydantic 모델로 박제한다.
- 큰 배열([H,W], [N,M] 등)은 HDF5 안에 저장하고, 여기서는 *경로/메타데이터*만 들고 다닌다.
- 4개 모듈은 따로 개발되더라도 이 계약을 통해서만 데이터를 주고받는다.

규칙:
  배열 필드 이름이 `*_ds`(dataset) 이면 그 값은 project.h5 안의 데이터셋 경로다.
  예: roi_mask_ds = "/cv/roi_mask"
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# 계약 스키마 버전. major 가 바뀌면 옛 project.h5 와 호환되지 않는다(읽을 때 raise).
# 하위호환되는 Optional 필드 추가는 minor 만 올린다.
# 1.1: InSAROutput.vertical_ds 추가(asc+desc 융합 연직 성분, Optional — 1.0 파일과 호환).
SCHEMA_VERSION = "1.1"

# 부재 종류 (CV → InSAR → PINN → FRAM 전체에서 공유하는 표준 라벨)
MEMBER_TYPES = ("deck", "pier", "abutment", "bearing")

# FRAM 기능 (설계 문서 5.3)
FRAM_FUNCTIONS = ("thermal", "load", "bearing", "foundation")


# ───────────────────────────── 모듈 3: CV ─────────────────────────────
class CVGeometry(BaseModel):
    centerline: list[tuple[float, float]] = Field(default_factory=list)  # 축선 좌표
    azimuth_angle: float = 0.0       # 축선-위성 방위각 (deg)
    bridge_length: float = 0.0       # length_unit 단위 (geo 있으면 m, 없으면 픽셀)
    bridge_width: float = 0.0        # length_unit 단위
    length_unit: str = "pixel"       # "m"(geo_transform 으로 환산) | "pixel"(미환산)
    # 지오레퍼런스 (선택) — InSAR real 이 Track 점(world 좌표)을 CV 픽셀에 정합할 때 사용.
    # 둘 다 None 이면 정합 불가로 간주해 identity(좌표=픽셀) 폴백한다(stub 기본).
    crs: str | None = None           # 예: "EPSG:5179". geo_transform 의 (x,y) 좌표계.
    geo_transform: tuple[float, float, float, float, float, float] | None = None
    # GDAL 6-tuple (c,a,b,f,d,e): x=c+a*col+b*row, y=f+d*col+e*row (픽셀→world)


class CVOutput(BaseModel):
    """문서 4.4 cv_output."""
    schema_version: str = SCHEMA_VERSION
    roi_mask_ds: str                       # [H,W] 이진 마스크
    member_label_ds: dict[str, str]        # {member: dataset 경로} [H,W] 각각
    geometry: CVGeometry
    shadow_ds: str | None = None           # 레이더 음영
    layover_ds: str | None = None          # 겹침
    grid_density_ds: str | None = None     # [H,W] 부재별 측정점 밀도
    image_shape: tuple[int, int]           # (H, W)


# ──────────────────────────── 모듈 1: InSAR ───────────────────────────
class InSAROutput(BaseModel):
    """문서 2.4 insar_output."""
    schema_version: str = SCHEMA_VERSION
    n_points: int
    n_dates: int
    point_id_ds: str          # [N]
    xyz_ds: str               # [N,3] EPSG:5179
    member_ds: str            # [N] 정수 라벨 (MEMBER_TYPES 인덱스)
    coherence_ds: str         # [N]
    l_from_fixed_ds: str      # [N] 고정단까지 거리 (m, 열팽창용)
    # ★ 변위 단위 규약: los/longitudinal/vertical 모두 **밀리미터(mm)**.
    #   생산자 전부 mm(합성 엔진·import_track_h5 의 los_mm·real_engine 융합)이고
    #   소비자(PINN 성분·Bmaps API)도 mm 로 다룬다 — 추가 환산(×1000) 금지.
    los_ds: str               # [N,M] LOS 변위 (mm)
    longitudinal_ds: str      # [N,M] 종방향 분해 변위 (mm, 열팽창 등 수평 축방향)
    dates_ds: str             # [M] (epoch days)
    temporal_coherence_ds: str  # [N]
    # 연직 변위 [N,M] (mm) — asc+desc 융합 시에만 채워진다(처짐·침하). 단일 궤도면 None.
    # PINN 이 있으면 처짐/침하 분리에 쓰고, Bmaps 가 연직 레이어로 노출한다.
    vertical_ds: str | None = None


# ───────────────────────────── 모듈 2: PINN ────────────────────────────
class PINNOutput(BaseModel):
    """문서 3.6 pinn_output."""
    schema_version: str = SCHEMA_VERSION
    n_points: int
    n_dates: int
    # displacement_components
    comp_thermal_ds: str      # [N,M]
    comp_load_ds: str
    comp_settle_ds: str
    comp_anomaly_ds: str
    # structural_response
    strain_ds: str            # [N,M]
    stress_ds: str            # [N,M]
    deflection_ds: str        # [N,M]
    natural_freq_ds: str      # [K]
    # physical_params (역산값)
    EI_ds: str                # [N]
    alpha_ds: str             # [N]
    # variability  (★ FRAM 입력)
    V_thermal_ds: str         # [N]
    V_load_ds: str
    V_settle_ds: str
    V_anomaly_ds: str
    # variability 시계열 [n_func, M]  — FRAM 공명 계산용
    V_func_series_ds: str
    func_names: list[str] = Field(default_factory=lambda: list(FRAM_FUNCTIONS))


# ───────────────────────────── 모듈 4: FRAM ────────────────────────────
class FRAMWarning(BaseModel):
    level: str                # 정상 / 주의 / 경고 / 위험
    lead_time_days: float | None = None   # (후방) CRI 가 임계 넘은 뒤 경과 시간
    critical_members: list[str] = Field(default_factory=list)
    function_states: dict[str, str] = Field(default_factory=dict)  # 기능별 상태(정상/주의/위험)
    lead_time_forecast_days: float | None = None  # (전방) 위험 임계 도달 예측까지 일수
    basis: str = "cri"        # 경보 근거: "cri" 또는 "calibrated_probability"


class FRAMOutput(BaseModel):
    """문서 5.7 fram_output."""
    schema_version: str = SCHEMA_VERSION
    n_points: int
    n_dates: int
    resonance_Rij_ds: str     # [n_func, n_func, M]
    amplification_ds: str     # [N,M]  증폭률 A
    spatial_prop_ds: str      # [N,M]
    divergence_ds: str        # [N,M]
    CRI_ds: str               # [N,M]  ★ 핵심 공명 위험 지수
    network_resonance_ds: str | None = None  # [M] 함수망 시스템 공명 강도(스펙트럼 반경)
    calibrated_risk_ds: str | None = None    # [N,M] isotonic 보정 붕괴확률(캘리브레이터 있을 때)
    cri_global_max: float     # 전체 최대 CRI (요약 스칼라)
    warning: FRAMWarning
