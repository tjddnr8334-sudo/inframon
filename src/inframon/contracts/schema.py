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
# 1.2: PINNOutput 에 가상센싱(상부거더 전체 변위장) Optional 필드 추가 — 1.0/1.1 파일과 호환.
# 1.3: RemainingLifeOutput(/life 그룹) 추가 — 4엔진 계약 불변, 잔존수명은 opt-in 후처리.
SCHEMA_VERSION = "1.3"

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
    # ── 가상센싱(virtual sensing): 상부거더 전체 변위장 ──
    # InSAR 관측점(N개·희소·불규칙)에서 학습한 PINN 연속장을 거더 종축을 따라 촘촘한
    # 가상센서 격자(V개)로 재평가한 결과 — 관측이 없는 위치까지 포함한 거더 전체 변위량.
    # 모두 Optional: real 엔진만 채우고, stub·구버전 파일은 None(계약 검증 생략).
    n_virtual: int | None = None
    vsens_x_ds: str | None = None             # [V] 정규화 거더축 위치 [0,1]
    vsens_l_from_fixed_ds: str | None = None  # [V] 고정단 거리 [m]
    vsens_total_ds: str | None = None         # [V,M] 전체 변위량(종축·연직 벡터합 크기, mm)
    vsens_deflection_ds: str | None = None    # [V,M] 처짐(연직 하중, mm)
    vsens_thermal_ds: str | None = None       # [V,M] 열팽창(종축, mm)
    vsens_settle_ds: str | None = None        # [V,M] 침하(연직, mm)
    vsens_anomaly_ds: str | None = None       # [V,M] 이상(mm)
    # ── 가상센싱 2D: 교량 상판(deck) 전체 면 변위 지도 ──
    # PCA 로 세운 상판 격자(G=n_long×n_trans) 위의 전체 변위량 — 관측점이 없는 상판
    # 위치까지 포함(가상센싱). 점<3·stub·구버전이면 None(계약 검증 생략).
    n_deck: int | None = None
    deck_xy_ds: str | None = None             # [G,2] 격자 world 좌표(EPSG:5179)
    deck_s_ds: str | None = None              # [G] 종축(길이) 투영 [m]
    deck_w_ds: str | None = None              # [G] 횡축(폭) 투영 [m]
    deck_total_ds: str | None = None          # [G,M] 상판 전체 변위량(mm)
    deck_deflection_ds: str | None = None     # [G,M] 상판 처짐(mm)


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


# ───────────────────── 후처리: 잔존수명 (RSL, opt-in) ─────────────────────
# 4엔진(ENGINE_NAMES)에 5번째를 추가하지 않는다 — FRAM 뒤에 붙는 후처리 스테이지이고
# `--remaining-life` 로만 동작한다. 미사용 시 /life 그룹 자체가 생기지 않아 기존
# 파이프라인 수치·골든 회귀는 완전히 불변이다. 설계: docs/잔존수명_설계.md

# 잔존수명 채널(한계상태). 물리적으로 다른 한계는 다른 시간을 주므로 뭉개지 않는다.
RSL_CHANNELS = ("serviceability", "stiffness", "fatigue", "durability")
# 채널 성격 — 측정기반(위성 관측이 근거) / 가정기반(설계코드·점검자료 추정).
RSL_KINDS = ("measured", "model_based")
# 사용성 채널의 하위 한계 — 점별 지배 한계를 이 인덱스로 기록한다.
#   0=검열(열화 신호 없음) · 1=절대 변위 · 2=부등침하(각변위)
RSL_SUBLIMITS = ("censored", "absolute", "differential")


class RSLChannel(BaseModel):
    """한 한계상태 채널의 잔존수명 결과. 비활성이면 사유를 반드시 남긴다."""
    name: str                          # RSL_CHANNELS
    kind: str                          # RSL_KINDS — UI 가 '측정/추정'을 구분 표기
    active: bool
    inactive_reason: str | None = None  # 비활성 사유(관측기간 부족 등) — 침묵 금지
    rsl_years: float | None = None      # 점추정(검열이면 None)
    rsl_lower_years: float | None = None  # 보수적 하한 — 보고 기본값
    censored: bool = False              # 유효 열화 군집 없음 → "> horizon"
    detail: dict = Field(default_factory=dict)


class RemainingLifeOutput(BaseModel):
    """문서 docs/잔존수명_설계.md — /life 그룹."""
    schema_version: str = SCHEMA_VERSION
    n_points: int
    as_of: str                    # 기준일 YYYY-MM-DD (관측 마지막 취득일)
    observed_years: float         # 관측 구간 길이[년] — 채널 게이팅 근거
    horizon_years: float          # 이 값을 넘으면 검열(">horizon")
    # 점별 결과 [N]
    rsl_point_ds: str             # 잔존수명[yr] — 검열점은 inf
    rsl_lower_ds: str             # 보수적 하한[yr] — 검열점은 inf
    rate_ds: str                  # 열화율(변위 |mm/yr|)
    rate_sigma_ds: str            # 유효 표준오차 — 신뢰도 표기에 필수
    sublimit_ds: str              # 점별 지배 하위한계 (RSL_SUBLIMITS 인덱스)
    channels: list[RSLChannel] = Field(default_factory=list)
    # 교량 대표값 — 최솟값이 아니라 공간 응집 군집 규칙으로 뽑는다(고립 노이즈점 배제)
    rsl_years: float | None = None
    rsl_lower_years: float | None = None
    governing: str | None = None       # 지배 채널명
    censored_fraction: float = 1.0     # 검열된 점 비율(1.0 = 유의한 열화 없음)
    confidence: str = "low"            # high | medium | low
    confidence_reason: str = ""
    assumptions: dict = Field(default_factory=dict)  # 임계값·출처·변위원 전부
