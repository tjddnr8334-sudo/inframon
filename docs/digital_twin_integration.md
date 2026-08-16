# 디지털 트윈 통합 설계 — 포맷 전략 (InSAR·PINN·FRAM → BIM 교량 트윈)

> 질문: 향후 BIM 디지털 트윈 교량과 합쳐질 때 IFC로? FBX로? 다른 포맷으로?
> 결론: **단일 포맷 선택 문제가 아니다.** 트윈은 세 층으로 나뉘고, 각 층의 포맷이 다르다.
> SHM 값(CRI·변위·속도)은 **GlobalId로 부재에 묶여** 층 사이를 이동한다.

---

## 1. 핵심 프레이밍 — "IFC vs FBX"는 잘못된 이분법

IFC와 FBX는 **경쟁 포맷이 아니라 다른 목적**이다.

| | IFC | FBX |
|---|---|---|
| 본질 | 의미(semantic) BIM — 부재 정체성·유형·관계·속성 | 지오메트리+머티리얼+애니메이션 |
| 부재 식별 | IfcBeam/IfcSlab… + **GlobalId**(전역 고유) | 없음(메시 노드만) |
| 속성(Pset) | 있음(자산관리·SHM 부착 지점) | 없음(BIM 의미 없음) |
| 좌표계 | IfcMapConversion(측지 georef) | 로컬 좌표(단위 모호) |
| 표준 | openBIM·ISO 16739 (buildingSMART) | 독점(Autodesk) |
| 강점 | **자산 기록·데이터 결합** | **실시간 렌더링**(Unreal/Unity) |

→ SHM 데이터는 **의미적으로 IFC에 속한다**(변위·CRI를 "그 부재"에 붙임). FBX는 렌더링용 파생물이지 기록 매체가 아니다.

---

## 2. 3층 아키텍처 — 각 층의 포맷

```
[의미 층 · 기록]         IFC 4.3        ← 부재 정체성·georef·자산관리 (단일 진실원)
        │  GlobalId 로 결합
[시간축 데이터 층]   project.h5 / VLM 패키지 / 시계열 DB   ← CRI·변위 시계열 (BIM은 정적이라 분리 필수)
        │  파생 렌더
[시각화·런타임 층]  glTF·3D Tiles / USD / (FBX)   ← 웹뷰어·Cesium·Omniverse·Unreal
```

### (A) 의미 층 = **IFC 4.3** (권장 기록 포맷)
- **IFC 4.3 = ISO 16739-1:2024** 에서 **인프라(IfcBridge·IfcBridgePart·IfcAlignment·선형참조)** 가 정식 추가됨. 이전 IFC 2x3/4.0 은 건물 전용이라 교량 클래스가 없었다 → **교량 트윈은 반드시 IFC 4.3**.
- SHM 값은 **부재별 Pset**(`Pset_InSAR{변위·속도·CRI·밴드·색}`)로 GlobalId에 붙인다(현 `--bim-align` 이 이 페이로드를 이미 생성).
- georef: **IfcMapConversion**(EPSG:5186 한국 중부원점) — 현 파이프라인이 이미 처리. IFC에 없으면 측량 기준점쌍(`--bim-control-points`)으로 주입.

### (B) 시간축 데이터 층 = 별도 (BIM의 근본 한계 대응)
- **BIM/지오메트리 포맷은 대부분 정적 스냅샷**이다. SHM은 본질적으로 시계열 → 지오메트리 안에 넣으면 안 된다.
- IFC의 `IfcPropertySet`은 스냅샷 1개만 자연스럽다. 시계열은:
  - (i) **GlobalId 키 사이드카**(project.h5/CSV/시계열 DB) — 트윈 플랫폼이 라이브 데이터를 여기서 읽음. **가장 견고한 패턴.**
  - (ii) IfcTimeSeries — 표준이나 무겁고 지원 적음.
- inframon의 **VLM 핸드오프 패키지(자기기술 manifest+CSV)가 이미 이 사이드카 역할**을 한다. GlobalId만 키로 추가하면 됨.

### (C) 시각화·런타임 층 = 대상 플랫폼이 결정
| 트윈 플랫폼 | 런타임 포맷 | SHM 값 전달 |
|---|---|---|
| **웹/Cesium/KICT Bmaps** | **glTF 2.0 / 3D Tiles** | 정점색 + `_FEATURE` 메타(GlobalId) |
| NVIDIA Omniverse | **USD(OpenUSD)** | USD 속성·프리미티브 |
| Unreal/Unity 저작 | **FBX / Datasmith** | 정점색·머티리얼 파라미터 |
| Autodesk Tandem | IFC/Revit→내부 | Tandem 파라미터 API |
| Bentley iTwin | iModel(IFC 파생) | iTwin 속성 |

→ **FBX는 트윈 플랫폼이 Unreal/Unity일 때만.** 그 경우도 기록은 IFC, FBX는 뷰 파생물.

---

## 3. 결합의 축 = **GlobalId 계약** (포맷 무관 불변식)

어떤 런타임 포맷을 쓰든 변하지 않는 것:
- IFC 부재의 **GlobalId** 가 SHM 시계열의 외래키.
- inframon 산출물(변위·CRI)은 이미 point_id·member 를 가짐 → **부재 매칭(`--bim-align`) 시 GlobalId 를 부여**하면, 이후 glTF/USD/FBX 어디로 렌더하든 값이 따라간다.
- 즉 "합쳐지는 포맷"을 지금 하나로 못 박을 필요 없음 — **GlobalId 결합만 확립하면 렌더 타깃은 나중에 교체 가능**.

---

## 4. inframon 다음 단계 (현 IFC-Pset 기반 위에서)

1. **IFC 4.3 교량 클래스 인지** — `--bim-align` 이 IfcBridge/IfcBridgePart/IfcBeam 을 읽고 GlobalId 를 SHM 산출에 심기(현재 부재 테이블 매칭 → GlobalId 보존으로 확장).
2. **GlobalId 키 사이드카 규격** — VLM 패키지 manifest 에 `element_globalid` 컬럼 추가(시계열↔부재 영구 결합).
3. **glTF/3D Tiles 내보내기** — 웹 트윈·Bmaps 용 파생 뷰(정점색=CRI, 피처 메타=GlobalId). 기록은 IFC 유지.
4. (선택) **USD/FBX 어댑터** — Omniverse/Unreal 대상일 때만. glTF 에서 변환 경로 확보.
5. **LOD·부재 매칭 정밀도** — PS점↔부재 최근접의 애매성(캔틸레버 sub-pixel 등, 정자교 교훈)을 GlobalId 매칭 신뢰도로 기록.

---

## 5. 한 줄 요약

**IFC 4.3(의미·기록) + GlobalId 결합(불변 축) + 대상 플랫폼별 런타임 포맷(glTF 기본, USD/FBX는 필요시).**
FBX 하나로 합치는 게 아니라, IFC로 정체성을 묶고 시계열은 사이드카로 분리한 뒤, 보는 화면만 플랫폼에 맞춰 렌더한다. 시간축이 있는 SHM이라 "정적 BIM 포맷 안에 다 넣기"는 애초에 불가 — 이 분리가 설계의 핵심.
