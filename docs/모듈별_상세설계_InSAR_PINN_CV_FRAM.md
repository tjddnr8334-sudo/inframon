# 모듈별 상세 설계 — InSAR · PINN · CV · FRAM
## 인프라 모니터링 통합 프로그램 4대 엔진 구체화

> 본 문서는 4개 핵심 모듈 각각의 역할·데이터·처리방법·결합체계를 구체적으로 설계한다.
> - **InSAR**: SARvey 상위호환 변위 추출 엔진
> - **PINN**: 구조 건전성 PDE/ODE 데이터 생성 엔진 (FEM 연계)
> - **CV**: ROI 산정 자동화 엔진
> - **FRAM**: PINN 결합 시스템 안전 진단 엔진
>
> 본 문서는 **설계 스펙(목표 모습)**이다. **현재 구현 현황·살아있는 상태**는
> [`개발_맥락_맵.md`](개발_맥락_맵.md)가 신뢰원이고, 아래 **0절**이 요약이다.

---

## 목차

0. [구현 현황 (2026-06)](#0-구현-현황-2026-06)
1. [전체 모듈 아키텍처](#1-전체-모듈-아키텍처)
2. [모듈 1: InSAR — SARvey 상위호환 엔진](#2-모듈-1-insar--sarvey-상위호환-엔진)
3. [모듈 2: PINN — 구조 건전성 PDE/ODE 엔진](#3-모듈-2-pinn--구조-건전성-pdeode-엔진)
4. [모듈 3: CV — ROI 산정 자동화 엔진](#4-모듈-3-cv--roi-산정-자동화-엔진)
5. [모듈 4: FRAM — PINN 결합 안전 진단 엔진](#5-모듈-4-fram--pinn-결합-안전-진단-엔진)
6. [모듈 간 데이터 인터페이스](#6-모듈-간-데이터-인터페이스)
7. [통합 실행 체계](#7-통합-실행-체계)

---

## 0. 구현 현황 (2026-06)

이 설계는 **4대 엔진 모두 실구현(real)으로 실현**됐다. 데이터 계약(Pydantic+HDF5)을
**성역**으로 두고 stub→real 로 채워, 핫스왑(`--engine X=real`)·골든 회귀로 보호한다.
합성·데모로 검증 완료(테스트 **164개**, GitHub CI ruff+pytest). 실데이터 수치
게이트(G2~G5)만 실 SLC·제원·가중치 대기.

### 설계 → 구현 매핑

| 모듈(본문) | 구현 파일 | 설계 실현 핵심 |
|---|---|---|
| InSAR(§2) | `insar/real_engine.py` + 선별 A~E(`osm_bridge`/`slc_search`/`era5_master`) + `track_preflight` | SARvey Track H5 인제스트 → CV 정합(world xyz·DEM z·고정단 거리). 코어 F(ISCE2/MiaplPy/SARvey)는 WSL2, 어댑터·계약 검증됨 |
| PINN(§3) | `pinn/real_engine.py` | PyTorch PINN + Euler-Bernoulli PDE(autograd 4차) + FEM 모달(해석해 5%내) + **절대 EI 식별**(비차원 PDE 균형) |
| CV(§4) | `cv/real_engine.py` | Otsu+CC / Transformer(SegFormer) 분할 → PCA 축선 → **형상증거 부재분할(SAM 폴백)** → geo_transform/crs 산출 |
| FRAM(§5) | `fram/real_engine.py` | 점별 공명 + 절대보정(sat) + **함수망 공명(N-K)** + **isotonic 캘리브**. §5.8 기능 공명 다이어그램 대시보드 구현 |

### 설계를 넘어 추가된 것 (이번 구현 신규)

- **오케스트레이션**: 핫스왑 셀렉터(`orchestrator/engines.py`)·증분 재개(fingerprint, `incremental.py`)·실행 매니페스트·골든 회귀 테스트.
- **CV↔InSAR 좌표 체인**(설계 6절 인터페이스 구체화): `geo_transform`/`crs` 정합 → world xyz(EPSG:5179) → DEM 고도 z → 고정단(abutment) 미터 거리. 픽셀↔world 아핀은 `geotransform.py`.
- **FRAM 고도화**(설계 5.5~5.6 실현+): 함수망 N-K 스펙트럼 공명, Morandi 합성 검증(ROC-AUC **0.946**), isotonic 캘리브(Brier 0.234→**0.059**).
- **실데이터 진입 게이트**: `inventory`(`--inspect-data`)·Track preflight(`--check-track`)·readiness doctor(`--doctor`)·[실데이터 런북](실데이터_런북.md).
- **거버넌스**: GitHub CI(`.github/workflows/tests.yml`), 테스트 89→**164**, 비공개 저장소 백업.

> 살아있는 상세 상태: [`개발_맥락_맵.md`](개발_맥락_맵.md) · 실행 절차: [`실데이터_런북.md`](실데이터_런북.md) · 엔진별: [`맥락/`](맥락/README.md)

---

## 1. 전체 모듈 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                   통합 인프라 모니터링 플랫폼                    │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐  │
│  │  모듈 3  │   │  모듈 1  │   │  모듈 2  │   │  모듈 4  │  │
│  │   CV     │──▶│  InSAR   │──▶│  PINN    │──▶│  FRAM    │  │
│  │ ROI산정  │   │ 변위추출 │   │ 구조건전 │   │ 안전진단 │  │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘  │
│       │              │              │              │          │
│       ▼              ▼              ▼              ▼          │
│  교량마스크    LOS변위시계열   PDE/ODE해      CRI/전조등급    │
│  부재라벨      등간격PS/DS    물리파라미터    공명위험맵      │
│                                                               │
└─────────────────────────────────────────────────────────────┘
       ↑                                              │
       └──────────────── 피드백 루프 ─────────────────┘
              (FRAM 진단 → CV/InSAR 파라미터 재조정)
```

### 데이터 흐름 한눈에 보기

```
[CV]    SAR/광학영상 → 교량 ROI 마스크 + 부재 라벨 + 축선
   ↓
[InSAR] SLC 스택 + ROI → 등간격 LOS 변위 시계열
   ↓
[PINN]  변위 + 온도 + FEM → PDE/ODE 해 + 구조 응답 + 변동 V
   ↓
[FRAM]  성분변위 + 변동 V → 공명 분석 + CRI + 붕괴 전조
```

---

## 2. 모듈 1: InSAR — SARvey 상위호환 엔진

### 2.1 설계 목표

SARvey의 모든 기능을 포함하되, 단점을 모두 극복한 **상위호환(superset)** 엔진.

| 기능 | SARvey | 본 엔진 (상위호환) |
|---|---|---|
| 입력 프로세서 | ISCE2 전용 | **SNAP + ISCE2 + GAMMA** 다중 |
| PS/DS | MiaplPy 의존 | **자체 PS/DS + MiaplPy 호환** |
| 등간격 측정점 | grid_size | grid_size + **CV 연동 적응형 격자** |
| 인터페이스 | CLI + JSON | **GUI + CLI + Python API** |
| 파라미터 | 수동 | **AI 자동 최적화 + 수동** |
| 플랫폼 | WSL2 강제 | **Windows 네이티브 + Linux** |
| 시각화 | QGIS 별도 | **내장 대시보드** |

### 2.2 입력 데이터

```
필수:
  - 코레지스터된 SLC 스택 (다중 포맷 어댑터)
    · SNAP 출력 (.dim/.data)        ← SARvey 불가, 본 엔진 가능
    · ISCE2 출력 (slcStack.h5)
    · GAMMA 출력
  - geometryRadar (입사각, 헤딩, 좌표)
  - 정밀 궤도 (POEORB)
  - 외부 DEM (Copernicus GLO-30/10)

선택:
  - CV 모듈의 ROI 마스크 (자동 연동)
  - 기상 데이터 (코히런스 영향 분석)
```

### 2.3 처리 파이프라인

```
[Step 0] 다중 포맷 입력 어댑터 ★ 핵심 차별점
   - SNAP/ISCE2/GAMMA → 통합 내부 포맷(HDF5) 변환
   - 메타데이터 표준화

[Step 1] 기선 분석 및 네트워크 구성
   - 시간/공간 기선 계산
   - |B⊥| < 150m 필터
   - 네트워크: Star / MST / Full / Small-temporal 선택
   - AI 자동 최적 네트워크 추천

[Step 2] 간섭쌍 생성 및 위상 처리
   - 간섭도 생성
   - 위상 성분 분리 (orbit/flat/topo)
   - Goldstein 필터링

[Step 3] PS/DS 추출
   - PS: ADI 임계값 기반
   - DS: phase linking (SHP 통계검정)
   - 시간적 위상 코히런스(TPC) 평가

[Step 4] 등간격 측정점 (CV 연동) ★
   - grid_size 기반 격자
   - CV 부재 라벨에 따라 적응형 밀도
     (교각 조밀, 바닥판 표준)
   - ROI 마스크로 교량 외부 차단

[Step 5] 위상 언래핑
   - 2단계 (시간적 + 공간적)
   - SNAPHU / PUMA(Graph Cuts)

[Step 6] APS 보정 및 시계열 역산
   - 시공간 필터링
   - Stable Reference Point 선정
   - LOS Displacement Time Series

[Step 7] LOS → 종방향 분해
   - Ascending + Descending 결합
   - 도로 축선 방향 투영
```

### 2.4 출력 데이터 (PINN 입력용)

```python
# 표준 출력 구조 (HDF5)
insar_output = {
    "points": {
        "id": [...],              # 측정점 ID
        "x, y, z": [...],         # 좌표 (EPSG:5179)
        "member": [...],          # 부재 라벨 (CV 연동: deck/pier/...)
        "coherence": [...],       # 시간적 코히런스
        "L_from_fixed": [...],    # 고정단까지 거리 (열팽창용)
    },
    "displacement": {
        "los": [N, M],            # LOS 변위 [측정점 N × 시점 M]
        "longitudinal": [N, M],   # 종방향 분해 변위
        "dates": [M],             # 취득 일자
    },
    "quality": {
        "temporal_coherence": [N],
        "aps_residual": [N, M],
        "dem_error": [N],
    }
}
```

### 2.5 SARvey 대비 핵심 신규 기능

1. **다중 프로세서 어댑터**: SNAP 사용자도 즉시 사용 (가장 큰 차별점)
2. **CV 연동 적응형 격자**: 부재별 측정점 밀도 자동 조절
3. **AI 파라미터 최적화**: 코히런스 임계값·grid_size 자동 튜닝
4. **내장 시각화**: QGIS 없이 변위 지도 표시
5. **PINN 직결 출력**: PINN이 바로 받을 수 있는 표준 포맷

---

## 3. 모듈 2: PINN — 구조 건전성 PDE/ODE 엔진

### 3.1 설계 목표

InSAR 변위를 입력으로, **구조역학 지배방정식(PDE/ODE)을 풀어 구조 건전성 데이터를 생성**한다. FEM과 연계하여 검증·보완한다.

```
핵심 역할:
  1. InSAR 변위 → 구조 응답(변형률, 응력, 처짐) 역산
  2. PDE/ODE 지배방정식을 물리 손실로 내재화
  3. FEM 결과와 상호 검증
  4. 변위 성분 분리 + 변동 V 산출 (FRAM 입력)
  5. 미래 변위 예측 (조기경보)
```

### 3.2 구조역학 지배방정식 (PDE/ODE)

#### (1) 보(Beam) 처짐 — Euler-Bernoulli PDE

교량 거더의 정적 처짐:

```
EI · d⁴w(x)/dx⁴ = q(x)

  E: 탄성계수, I: 단면 2차모멘트
  w(x): 처짐, q(x): 분포하중
```

#### (2) 동적 거동 — 보 진동 ODE/PDE

```
ρA · ∂²w/∂t² + EI · ∂⁴w/∂x⁴ = f(x,t)

  ρ: 밀도, A: 단면적
  → 고유진동수, 모드형상 도출 (손상 시 변화)
```

#### (3) 열-구조 연성 PDE

```
열전도:  ∂T/∂t = κ·∇²T + Q
열응력:  σ_thermal = E·α·ΔT (구속 시)
열변형:  ε_thermal = α·ΔT
```

#### (4) 침하 — 지반-구조 상호작용 ODE

```
압밀 침하:  dS/dt = f(σ', t)  (시간 의존)
  S: 침하량, σ': 유효응력
```

### 3.3 PINN 아키텍처

```
[입력층]
  좌표 x, 시간 t, 온도 T, 하중 w
       ↓
[은닉층] (8~10층, 각 64~128 뉴런, tanh/SiLU)
  공간-시간 좌표를 구조 응답으로 매핑
       ↓
[출력층]
  변위 w(x,t), 변형률 ε(x,t), 응력 σ(x,t)
       ↓
[물리 손실 계산]
  자동미분(autograd)으로 PDE 잔차 계산
```

### 3.4 PINN 손실함수 (PDE/ODE 내재화)

```
L_total = L_data + L_PDE + L_BC + L_IC + L_FEM

L_data : InSAR 변위 관측 일치
         Σ |w_PINN(x_i,t_k) - d_InSAR(x_i,t_k)|²

L_PDE  : 지배방정식 잔차 (Euler-Bernoulli 등)
         Σ |EI·∂⁴w/∂x⁴ - q|²

L_BC   : 경계조건 (받침: 처짐=0, 모멘트=0)
         받침 위치에서 w=0, ∂²w/∂x²=0

L_IC   : 초기조건 (t=0 상태)

L_FEM  : FEM 결과와의 일치 (있을 때)
         Σ |w_PINN - w_FEM|²
```

### 3.5 FEM 연계 방안

```
[FEM의 역할 — PINN 보완·검증]

방안 A: FEM을 학습 데이터 생성기로
  - ABAQUS/ANSYS/OpenSees로 교량 모델링
  - 다양한 하중·온도 시나리오 시뮬레이션
  - FEM 결과를 PINN 학습 데이터로 활용
  - InSAR 측정점이 적은 곳을 FEM으로 보완

방안 B: FEM을 검증 도구로
  - PINN 출력(응력·변형)을 FEM과 비교
  - 일치도로 PINN 신뢰도 평가

방안 C: 하이브리드 (FEM-informed PINN) ★
  - FEM의 강성행렬 K를 PINN 손실에 반영
  - K·u = F 관계를 물리 제약으로
  - 데이터 부족 시 FEM 물리가 보완
```

### 3.6 PINN 출력 데이터 (구조 건전성)

```python
pinn_output = {
    "displacement_components": {
        "thermal": [N, M],      # 열팽창 성분
        "load": [N, M],         # 하중 성분
        "settlement": [N, M],   # 침하 성분
        "anomaly": [N, M],      # 이상 성분
    },
    "structural_response": {     # ★ 구조 건전성 핵심
        "strain": [N, M],        # 변형률
        "stress": [N, M],        # 응력
        "deflection": [N, M],    # 처짐
        "natural_freq": [...],   # 고유진동수 (손상지표)
        "mode_shape": [...],     # 모드형상
    },
    "physical_params": {         # 역산된 물리 파라미터
        "EI": [...],             # 휨강성 (손상 시 저하)
        "alpha": [...],          # 열팽창계수
        "L_eff": [...],          # 유효 경간
        "settle_rate": [...],    # 침하 속도
    },
    "variability": {             # FRAM 입력용
        "V_thermal": [N],        # 열팽창 변동
        "V_load": [N],
        "V_settle": [N],
        "V_anomaly": [N],
    },
    "prediction": {              # 미래 예측
        "future_disp": [N, P],   # P 시점 예측
        "confidence": [N, P],    # 신뢰구간
    }
}
```

### 3.7 PDE/ODE 데이터 활용 — 손상 진단

```
건전성 지표 (PINN이 PDE/ODE에서 도출):

[휨강성 EI 저하]   → 균열, 단면 손실
[고유진동수 변화]  → 강성 변화 = 손상
[응력 집중]        → 위험 부위
[비선형 처짐]      → 받침/거더 이상
[침하 가속]        → 기초 문제

→ 이 모든 것이 FRAM의 "변동(V)" 입력이 됨
```

---

## 4. 모듈 3: CV — ROI 산정 자동화 엔진

### 4.1 설계 목표

교량 ROI를 **수동 설정이 아닌 자동으로** 산정하여, InSAR 처리의 정확도와 효율을 높인다.

```
핵심 역할:
  1. SAR/광학 영상에서 교량 자동 탐지
  2. 교량 경계·부재 자동 분할
  3. 교량 축선 방향 검출
  4. 음영/겹침 구간 식별
  5. InSAR 등간격 격자 가이드 생성
```

### 4.2 입력 데이터

```
필수:
  - SAR 진폭/강도 영상 (Reflectivity Map)
  - 광학 영상 (Sentinel-2, 고해상도 위성/항공)

보조:
  - 도로대장/OSM 교량 위치 (초기 가이드)
  - 교량 구조 도면 (부재 정보)
  - DEM (음영/겹침 분석)
```

### 4.3 CV 처리 방법

#### (1) 교량 탐지 (Detection)

```
방법 A: 객체 탐지 (YOLO 계열) ★ 본인 경험 활용
  - YOLOv8/v11로 교량 bounding box 검출
  - 광학 + SAR 융합 입력

방법 B: OSM 가이드 + 정밀화
  - OSM 교량 라인 → 초기 위치
  - CV로 정밀 경계 추출
```

#### (2) 부재 분할 (Segmentation)

```
방법: 시맨틱/인스턴스 분할
  - Mask R-CNN / SAM (Segment Anything)
  - 출력: 바닥판/교각/교대/받침 마스크

학습 데이터:
  - 교량 항공/위성 영상 + 부재 라벨
  - 합성 데이터 증강 (FEM 모델 렌더링)
```

#### (3) 축선 방향 검출

```
방법: 주성분분석(PCA) + Hough 변환
  - 교량 마스크의 주축 방향 산출
  - 위성 방위각(Azimuth)과의 각도 계산
  - PS 추출 적합성 평가
```

#### (4) 음영/겹침 식별

```
방법: DEM + 기하 시뮬레이션
  - 입사각 + 지형 → Radar Shadow 영역
  - 고가 구조 → Layover 영역
  - 해당 구간 ROI에서 제외/플래그
```

#### (5) 등간격 격자 가이드

```
방법: 부재별 적응형 격자
  - 바닥판: 표준 간격 (예: 10m)
  - 교각/받침: 조밀 간격 (예: 5m, 응력 집중부)
  - InSAR 모듈에 격자 전달
```

### 4.4 CV 출력 데이터 (InSAR 입력용)

```python
cv_output = {
    "roi_mask": [H, W],          # 교량 ROI 이진 마스크
    "member_labels": {           # 부재별 마스크
        "deck": [H, W],
        "pier": [H, W],
        "abutment": [H, W],
        "bearing": [H, W],
    },
    "geometry": {
        "centerline": [...],     # 교량 축선 좌표
        "azimuth_angle": float,  # 축선-위성 방위각
        "bridge_length": float,
        "bridge_width": float,
    },
    "exclusion": {
        "shadow": [H, W],        # 레이더 음영
        "layover": [H, W],       # 겹침
    },
    "grid_guide": {              # InSAR 격자 가이드
        "points": [...],         # 권장 측정점 위치
        "density_map": [H, W],   # 부재별 밀도
    }
}
```

### 4.5 CV → InSAR 연동 효과

```
[수동 ROI]  →  외부 산란체 혼입, 부재 구분 안됨
[CV 자동]   →  정밀 경계 + 부재 라벨 + 적응형 격자
              → InSAR PS 품질 향상 + 부재별 분석 가능
```

---

## 5. 모듈 4: FRAM — PINN 결합 안전 진단 엔진

### 5.1 설계 목표 — PINN과의 결합이 핵심

FRAM(정성적 시스템 안전 방법론)을 **PINN의 정량적 출력과 결합**하여, 교량 붕괴를 시스템적으로 진단·예측한다.

```
핵심 질문: "PINN 출력을 어떻게 FRAM 변동·공명으로 변환하는가?"
```

### 5.2 PINN → FRAM 결합 체계 (3단계)

```
┌─────────────────────────────────────────────────┐
│  [결합 1단계] PINN 출력 → FRAM 기능(Function) 매핑  │
├─────────────────────────────────────────────────┤
│  [결합 2단계] PINN 변동 → FRAM 변동(Variability)   │
├─────────────────────────────────────────────────┤
│  [결합 3단계] FRAM 공명(Resonance) → CRI 산출      │
└─────────────────────────────────────────────────┘
```

### 5.3 결합 1단계 — PINN 출력을 FRAM 기능으로 매핑

각 교량 기능을 PINN 출력의 6측면으로 정의:

| FRAM 기능 | Input | Output (PINN) | Resource (PINN) | Control | Time |
|---|---|---|---|---|---|
| 열팽창 거동 | 온도 ΔT | d_thermal | α, L_eff | V_thermal 한계 | 계절주기 |
| 하중 지지 | 교통하중 | d_load, 처짐 | EI (휨강성) | 처짐 한계 | 일주기 |
| 받침 거동 | 변위 | 신축량 | 받침 강성 | 신축 한계 | 응답지연 |
| 기초 지지 | 지반반력 | d_settle | 침하속도 | 침하 한계 | 장기 |
| 구조 강성 | 응력 | 변형률 ε | EI, 고유진동수 | 응력 한계 | — |

→ PINN의 PDE/ODE 출력(EI, 고유진동수, 응력)이 각 기능의 Resource·Output을 채운다.

### 5.4 결합 2단계 — PINN 변동을 FRAM 변동으로

```
FRAM 변동 = PINN 물리모델로부터의 이탈 정도

[변동 1] 잔차 기반
  V_func(x,t) = |d_observed - d_PINN^func(x,t)|

[변동 2] 물리 파라미터 이탈
  V_EI(x) = |EI_estimated(x) - EI_design| / EI_design
  → 휨강성 저하 = 손상 변동

[변동 3] 고유진동수 편차
  V_freq = |f_measured - f_design| / f_design
  → 진동수 변화 = 강성 손상

[변동 4] 비선형성 (열팽창 선형성 이탈)
  V_thermal(x) = 1 - R²(temp-disp)
```

### 5.5 결합 3단계 — 공명을 CRI로

```
[공명 1] 변동 동시성 (시간 상관)
  R_ij(t) = Corr(V_i(t), V_j(t))
  예: 받침 변동 ↑ 와 열응력 변동 ↑ 동시 → 위험

[공명 2] 변동 증폭률 ★
  A(x,t) = V_total / Σ V_i
  A > 1 → 공명 (상호작용 증폭)

[공명 3] 공간 전파
  R_spatial = ∇·V(x,t)
  한 교각 손상 → 인접 경간 전파

[공명 4] PINN 예측 발산
  R_div = ||d_PINN(t+Δ) - d_linear(t+Δ)||
  비선형 가속 = 붕괴 전조

[종합] 공명 위험 지수
  CRI(x,t) = w₁·A + w₂·ΣR_ij + w₃·R_spatial + w₄·R_div
```

### 5.6 FRAM 기능망 동역학 (PINN 시계열 활용)

```
PINN이 시계열을 제공하므로, FRAM 기능망이 시간에 따라 변하는
동적 분석이 가능 (기존 정적 FRAM의 한계 극복):

t=1: 모든 기능 정상 (V 낮음)
   ↓
t=2: 받침 거동 변동 ↑ (V_bearing 증가)
   ↓
t=3: 열응력 변동 동반 상승 (R_ij ↑, 공명 시작)
   ↓
t=4: 침하 변동 가세 (A > 1, 증폭)
   ↓
t=5: CRI 임계 초과 → 붕괴 전조 경보 ★
```

### 5.7 FRAM 출력 데이터

```python
fram_output = {
    "function_states": {         # 각 기능 상태 시계열
        "thermal": {"V": [M], "status": [...]},
        "load": {"V": [M], "status": [...]},
        "bearing": {"V": [M], "status": [...]},
        "foundation": {"V": [M], "status": [...]},
    },
    "resonance": {
        "R_ij": [n_func, n_func, M],  # 기능간 공명 행렬 시계열
        "amplification": [N, M],       # 증폭률 A
        "spatial_prop": [N, M],        # 공간 전파
        "divergence": [N, M],          # 예측 발산
    },
    "CRI": [N, M],               # 공명 위험 지수 (핵심)
    "warning": {
        "level": [...],          # 정상/주의/경고/위험
        "lead_time": float,      # 예상 붕괴까지 시간
        "critical_members": [...], # 위험 부재
    },
    "fram_diagram": {...}        # 육각형 시각화 데이터
}
```

### 5.8 FRAM 시각화 (인터랙티브 육각형)

```
- 각 기능 = 육각형 노드
- 변동 V 크기 = 색상 (녹색→노랑→빨강)
- 공명 R_ij = 연결선 굵기
- 공간 전파 = 애니메이션
- 시간 슬라이더로 동역학 재생
- 클릭 → 해당 기능 PINN 상세 출력
```

---

## 6. 모듈 간 데이터 인터페이스

### 6.1 표준 데이터 포맷 (HDF5 기반)

```
project.h5
├── /cv/                  # CV 모듈 출력
│   ├── roi_mask
│   ├── member_labels
│   ├── geometry
│   └── grid_guide
├── /insar/               # InSAR 모듈 출력
│   ├── points
│   ├── displacement
│   └── quality
├── /pinn/                # PINN 모듈 출력
│   ├── displacement_components
│   ├── structural_response
│   ├── physical_params
│   └── variability
└── /fram/                # FRAM 모듈 출력
    ├── function_states
    ├── resonance
    ├── CRI
    └── warning
```

### 6.2 인터페이스 계약 (Interface Contract)

| 송신 | 수신 | 전달 데이터 | 형식 |
|---|---|---|---|
| CV → InSAR | ROI 마스크, 부재 라벨, 격자 가이드, 축선 | GeoTIFF + JSON |
| InSAR → PINN | LOS/종방향 변위 시계열, 좌표, L(x) | HDF5 |
| FEM → PINN | 강성행렬, 시뮬레이션 결과 | HDF5/MAT |
| PINN → FRAM | 성분변위, 구조응답, 변동 V | HDF5 |
| FRAM → (피드백) | 위험 부재 → CV/InSAR 재조정 | JSON |

### 6.3 피드백 루프

```
FRAM이 위험 부재 식별
   ↓
해당 부재에 대해:
  - CV: ROI 정밀화 (더 조밀한 분할)
  - InSAR: 측정점 밀도 증가 (grid_size 축소)
  - PINN: 해당 부위 물리모델 정밀화
   ↓
재분석 → CRI 갱신
```

---

## 7. 통합 실행 체계

### 7.1 실행 모드

```
[모드 1] 전체 파이프라인 (배치)
  CV → InSAR → PINN → FRAM 순차 실행
  대규모 교량 정기 모니터링

[모드 2] 모듈 독립 실행
  각 모듈 단독 사용 가능 (API)
  예: InSAR만 SARvey 대체용

[모드 3] 실시간 모니터링
  신규 SAR 영상 도착 시 자동 갱신
  CRI 임계 초과 시 경보
```

### 7.2 소프트웨어 계층 구조

```
┌─────────────────────────────────────┐
│   GUI / 웹 대시보드 (PyQt6/Streamlit) │
├─────────────────────────────────────┤
│   오케스트레이션 레이어 (워크플로우)    │
├─────────────────────────────────────┤
│  CV  │  InSAR  │  PINN  │  FRAM      │ ← 4대 엔진
├─────────────────────────────────────┤
│   공통 데이터 레이어 (HDF5 I/O)        │
├─────────────────────────────────────┤
│   외부 연동 (FEM, 기상청, GIS, BIM)    │
└─────────────────────────────────────┘
```

### 7.3 개발 우선순위 (모듈별)

```
1순위: InSAR 엔진 (코어, SARvey 상위호환)
       → 다중 포맷 어댑터부터

2순위: CV ROI 자동화 (InSAR 정확도 직결)
       → YOLO 교량탐지부터 (본인 경험)

3순위: PINN 구조건전성 (핵심 신규성)
       → 열팽창 PDE부터 단계적

4순위: FRAM 결합 (최종 진단)
       → PINN 변동 매핑부터
```

### 7.4 검증 체계

```
[InSAR 검증]   GNSS 교차검증, RMSE/R²
[CV 검증]      수동 라벨 대비 IoU
[PINN 검증]    FEM 결과 대비, 계측 변형률 대비
[FRAM 검증]    붕괴교량 역추적 (붕괴 전 CRI 상승)
[통합 검증]    전체 파이프라인 → 실제 손상 사례
```

---

## 핵심 요약

### 4대 모듈의 역할

| 모듈 | 역할 | 핵심 차별점 | 주요 출력 |
|---|---|---|---|
| **CV** | ROI 자동 산정 | YOLO/SAM 부재 분할 | 마스크, 라벨, 격자 가이드 |
| **InSAR** | 변위 추출 | SARvey 상위호환 (다중포맷, GUI) | LOS/종방향 변위 시계열 |
| **PINN** | 구조 건전성 | PDE/ODE 해 + FEM 연계 | 응력·강성·변동 V |
| **FRAM** | 안전 진단 | PINN 결합 정량화 | CRI, 붕괴 전조 |

### 결합의 핵심 사슬

```
CV가 "어디를" 정하고
InSAR가 "얼마나 움직였나" 재고
PINN이 "구조적으로 무슨 의미인가" 풀고 (PDE/ODE)
FRAM이 "시스템이 위험한가" 판정한다 (공명 → CRI)
```

### PINN-FRAM 결합이 핵심 신규성

```
PINN 출력 → FRAM 기능 6측면 매핑
PINN 물리이탈 → FRAM 변동 V
PINN 변동 상호작용 → FRAM 공명 → CRI
PINN 시계열 → FRAM 동적 기능망 (정적 FRAM 한계 극복)
```

---

*문서 작성: InSAR · PINN · CV · FRAM 4대 모듈 상세 설계 및 결합 체계*
