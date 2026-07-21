"""파이프라인 설정. Phase 0 에서는 데모 규모를 정하는 용도."""

from __future__ import annotations

from dataclasses import dataclass, field

# 핫스왑 스위치 — 엔진 이름과 선택 가능한 구현 모드.
# pipeline 은 cfg.engines 를 보고 각 단계의 stub/real 구현을 고른다.
ENGINE_NAMES = ("cv", "insar", "pinn", "fram")
ENGINE_MODES = ("stub", "real")


def _default_engines() -> dict[str, str]:
    # Phase 0 기본값: 전부 stub. real 구현이 붙으면 호출 측에서 켠다.
    return {name: "stub" for name in ENGINE_NAMES}


@dataclass
class PipelineConfig:
    # 데모 규모
    image_h: int = 256          # CV 영상 높이
    image_w: int = 512          # CV 영상 너비
    n_points: int = 200         # InSAR 측정점 수 N
    n_dates: int = 36           # 취득 시점 수 M (3년 월간 가정)
    n_future: int = 6           # PINN 미래 예측 시점 P

    # 재현성
    seed: int = 42

    # FRAM CRI 가중치 (문서 5.5: CRI = w1·A + w2·ΣR_ij + w3·R_spatial + w4·R_div)
    cri_weights: tuple[float, float, float, float] = (0.4, 0.3, 0.15, 0.15)

    # 경보 임계값 (정상/주의/경고/위험)
    cri_thresholds: tuple[float, float, float] = (0.3, 0.6, 0.85)

    # FRAM 이 asc+desc 융합 연직(InSAROutput.vertical_ds)을 CRI 에 반영할지.
    # 연직 침하 속도/공간기울기/발산을 종축항과 max 결합 → 연직우세 손상(침하·처짐) 직접 포착.
    # vertical_ds 가 없으면(단일궤도·합성·Morandi) 무영향 → 켜도 검증 게이트 안전.
    fram_use_vertical: bool = True

    # CRI 정상범위(reference range) — 건강 교량 코호트로 학습한 기준치(dict, ReferenceRange.to_dict).
    # 있으면 경보 등급을 임의 절대임계 대신 **건강 인구 대비 백분위/밴드**로 판독한다
    # (의료 검사수치의 reference range). None 이면 기존 절대임계(cri_thresholds) 경로 불변.
    fram_reference_range: dict | None = None

    # 엔진별 구현 선택 (핫스왑 스위치). 기본 전부 "stub".
    engines: dict[str, str] = field(default_factory=_default_engines)

    # InSAR real 엔진이 소비할 처리 결과 H5 (Track export). insar=stub 면 무시.
    # insar_source_h5 = 기준(주) 궤도. insar_source_desc_h5 가 있으면 asc+desc 융합을
    # 시도하고(연직+종축 분리), 융합 불가 시 자동으로 단일 궤도 처리로 폴백한다.
    insar_source_h5: str | None = None
    insar_source_desc_h5: str | None = None
    # Track 결과에 점별 고도가 없을 때, 이 DEM GeoTIFF(WSL2 1단계의 ISCE2용 DEM 등)에서
    # world 좌표로 z 를 샘플링한다. 없으면 z=0. 점별 고도가 있으면 이 값은 무시.
    insar_dem_geotiff: str | None = None

    # InSAR 정확도 보정(인제스트에서 LOS 시계열에 적용) — 기준점 정합 + 고도상관 성층대기 보정.
    # 결정론적(네트워크 불필요)이고 opt-in. 켜면 project.h5 의 los/longitudinal 이 보정된 값으로
    # 저장되고 /insar/velocity_mm_yr(보정 속도) + insar_source.corrections(이력)가 기록된다.
    # 열팽창 분리는 온도 시계열이 필요하므로 대시보드에서 별도로 한다(atmo.temporal_decompose).
    insar_apply_corrections: bool = False
    insar_ref_min_coherence: float = 0.9   # 기준점 후보 최소 시간결맞음(부족 시 최고 coh 폴백)

    # 열팽창(온도) 보정 — los=a+b·t+c·T 회귀로 계절 열변형(c·T)을 분리해 순 변형만 남긴다.
    # insar_apply_corrections 와 함께 켠다. 온도원: insar_temperature_csv(date,temp_C, 결정론적)
    # 우선, 없고 insar_fetch_temperature=True 면 ERA5(Open-Meteo, 키불필요·네트워크)로 취득일 온도.
    insar_thermal_correction: bool = False
    insar_temperature_csv: str | None = None
    insar_fetch_temperature: bool = False

    # 계약 강건화 (Phase 1 고도화)
    validate_contracts: bool = True   # 각 단계 출력의 배열 형상/dtype/심볼 검증
    write_manifest: bool = True       # 실행 출처(run_id/cfg/해시)를 project.h5 에 기록

    # 증분 재개 (축 B) — 기존 project.h5 에서 입력이 안 바뀐 단계는 재계산 생략
    resume: bool = False              # True 면 기존 파일을 열어 단계 fingerprint 비교
    force_stages: tuple[str, ...] = ()  # resume 중에도 강제 재계산할 단계(+하류 cascade)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Phase 0 데모 엔진이 수치적으로 동작 가능한 최소 설정을 강제한다."""
        if self.image_h < 24:
            raise ValueError("image_h must be >= 24")
        if self.image_w < 8:
            raise ValueError("image_w must be >= 8")
        if self.n_points < 2:
            raise ValueError("n_points must be >= 2")
        if self.n_dates < 2:
            raise ValueError("n_dates must be >= 2")
        if self.n_future < 1:
            raise ValueError("n_future must be >= 1")
        if len(self.cri_weights) != 4:
            raise ValueError("cri_weights must contain 4 values")
        if any(w < 0 for w in self.cri_weights):
            raise ValueError("cri_weights must be non-negative")
        if len(self.cri_thresholds) != 3:
            raise ValueError("cri_thresholds must contain 3 values")
        t_lo, t_mid, t_hi = self.cri_thresholds
        if not (0 <= t_lo <= t_mid <= t_hi <= 1):
            raise ValueError("cri_thresholds must satisfy 0 <= low <= mid <= high <= 1")
        if set(self.engines) != set(ENGINE_NAMES):
            raise ValueError(f"engines must have exactly keys {ENGINE_NAMES}, got {sorted(self.engines)}")
        for name, mode in self.engines.items():
            if mode not in ENGINE_MODES:
                raise ValueError(f"engines[{name!r}] must be one of {ENGINE_MODES}, got {mode!r}")
        bad = set(self.force_stages) - set(ENGINE_NAMES)
        if bad:
            raise ValueError(f"force_stages must be subset of {ENGINE_NAMES}, got extra {sorted(bad)}")
