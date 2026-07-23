# BIM 연계에 필요한 데이터 — 무엇을 주시면 되나

> inframon 의 위성 모니터링 결과를 BIM(IFC) 부재에 붙이려면 무엇이 필요한지 정리한 문서.
> 정합이 어떻게 동작하는지는 [`BIM_정합.md`](BIM_정합.md), 사용법은 README.

---

## 0. 한 줄 요약

**IFC 파일 하나면 시작할 수 있습니다.** 다만 그 IFC 에 **지오레퍼런싱(`IfcMapConversion`)이
있느냐**가 갈림길이고, 없으면 **측량 기준점 2점 이상**이 추가로 필요합니다.

```bash
# 먼저 이것부터 — 파일만 주시면 무엇이 더 필요한지 즉시 답이 나옵니다
python -m inframon --bim-inspect model.ifc
```

이 명령이 알려주는 것: IFC 스키마 버전 · **길이 단위** · `IfcMapConversion` 유무 ·
투영 좌표계 · 부재 수와 타입 분포. 여기 결과에 따라 아래 A/B 중 하나로 갑니다.

---

## 1. 필수 — 이것 없으면 시작 못 함

| # | 항목 | 형식 | 왜 필요한가 |
|---|---|---|---|
| 1 | **IFC 파일** | `.ifc` (IFC4 권장, IFC2X3 도 읽힘) | 부재 GUID·형상·타입의 원본 |
| 2 | **좌표 정합 근거** | 아래 A 또는 B | IFC 로컬 좌표 ↔ 지도 좌표 변환 |

### A. IFC 에 `IfcMapConversion` 이 있는 경우 → **추가 자료 불필요**

`--bim-inspect` 가 `IfcMapConversion: 있음` 이라고 하면 그걸 그대로 씁니다.
필요한 값(원점 E/N/H, 회전, 축척, 투영 좌표계)이 파일 안에 다 있습니다.

### B. 없는 경우 → **측량 기준점 쌍이 필요합니다** (국내 실무에서 더 흔함)

같은 지점을 **IFC 로컬 좌표**와 **측량 좌표(EPSG:5186 등)** 양쪽으로 아는 점이
**최소 2점**, 실무적으로 **3~4점 권장**(잔차 확인용)입니다.

```json
{
  "target_crs": "EPSG:5186",
  "points": [
    {"name": "BM1", "local": [0.0,   0.0,  0.0], "map": [200000.000, 550000.000, 12.500]},
    {"name": "BM2", "local": [120.0, 0.0,  0.0], "map": [200112.760, 550041.000, 12.480]},
    {"name": "BM3", "local": [120.0, 18.0, 4.0], "map": [200106.600, 550058.000, 16.470]}
  ]
}
```

- 보통 **교대·교각 중심, 받침 위치, 측량 기준점(BM)** 처럼 도면과 현장 양쪽에 있는 점을 씁니다.
- **표고(3번째 값)는 선택**이지만 넣으면 **3D 부재 연결**이 가능해집니다(§3 참조).
- 잔차 RMS 가 0.5 m 를 넘으면 정합을 **거부**합니다. 조용히 틀린 정합보다 실패가 낫습니다.

---

## 2. 있으면 크게 좋아지는 것

| 항목 | 없으면 | 있으면 |
|---|---|---|
| **표고 기준(수직기준면) 정보** | 2D 평면 연결만 — 상판 아래 교각이 안 갈림 | 3D 연결로 상판/교각/교대 정확히 분리 |
| **부재 명명 규칙** | IFC 타입으로만 추론(`IfcSlab`→상판) | 한글 부재명("교각 P3")도 인식 |
| **교량 지간(경간)** | 잔존수명이 상판에 L/800 을 적용 못 함 | 상판 사용성 한계 적용 가능 |
| **IFC4.3 교량 확장** | `IfcSlab`/`IfcColumn` 등 일반 타입으로 처리 | `IfcBridge`/`IfcBridgePart` 매핑(미구현) |

**표고가 왜 중요한가**: IFC `OrthogonalHeight` 는 수직기준면(예 인천만 평균해면) 기준이고
InSAR z 는 DEM(타원체고 또는 지오이드고)에서 옵니다. 한국의 지오이드고는 **약 25 m** 라
그냥 합치면 상판(고도 8 m)과 교각(0 m)의 구분이 통째로 뒤집힙니다. 그래서 표고 오프셋이
검증되기 전에는 **3D 연결을 막아 놓았습니다**.

---

## 3. IFC 없이도 되는 길 — 부재 테이블

IFC 를 못 주는 상황(보안·용량·미보유)이면 **부재 목록만으로도 전 과정이 동작합니다.**
`ifcopenshell` 도 필요 없습니다.

```csv
guid,name,ifc_type,xmin,ymin,zmin,xmax,ymax,zmax
DECK1,상판,IfcSlab,0,-5,8,100,5,9
PIER1,교각1,IfcColumn,30,-2,0,34,2,8
PIER2,교각2,IfcColumn,66,-2,0,70,2,8
ABUT1,교대,IfcFooting,-4,-5,0,0,5,8
```

- `guid` 는 나중에 IFC 로 되돌릴 식별자입니다(IFC GlobalId 를 그대로 쓰면 가장 좋습니다).
- `xmin..zmax` 는 **IFC 로컬 좌표계**의 경계상자[m]. 도면에서 뽑아도 됩니다.
- **UTF-8/cp949 자동 인식** — 국내 BIM 도구 산출물이 cp949 인 경우가 흔해서 둘 다 읽습니다.
- JSON 형식도 지원합니다(`{"elements": [...]}`).

---

## 4. 무엇을 돌려받나

부재 GUID 별로 아래가 나오고, IFC 사본에 `Inframon_Monitoring` Property Set 으로 주입됩니다.

| 속성 | 의미 |
|---|---|
| `PointCount` · `Sparse` | 그 부재에 붙은 InSAR 점 수(3점 미만이면 통계 신뢰 낮음) |
| `VelocityMedian_mm_per_yr` · `VelocityMaxAbs_mm_per_yr` | 변위 속도(중앙·최악) |
| `CumulativeMaxAbs_mm` | 관측 구간 누적 변위 최댓값 |
| `CoherenceMedian` | 관측 품질 |
| `CRIMax` | FRAM 공명 위험 지수 최댓값 |
| `RemainingLifeLower_yr` · `RemainingLifeCensoredFraction` | 잔존수명 하한, 검열 비율 |
| `DegradationRateMaxAbs_mm_per_yr` | 열화율 |
| `AssociationDistanceMedian_m` · `MemberMismatchPointCount` | **정합 품질**(값의 신뢰도 판단용) |
| `SourceProject` · `SourceGroups` · `UpdatedAt` | 시계열이 있는 `project.h5` 로 되돌아가는 키 |

**시계열은 IFC 에 넣지 않습니다.** `IfcPropertySingleValue` 는 스칼라를 담는 그릇이고,
수천 시점을 밀어 넣으면 IFC 가 뷰어에서 열리지 않습니다. IFC 에는 **현재 상태 + 출처 키**만
넣고 시계열은 `project.h5`(트윈 데이터 레이어)에 남깁니다.

**원본 IFC 는 덮어쓰지 않습니다** — 주입은 항상 사본에 합니다(같은 경로를 주면 거부).

---

## 5. 실행 순서

```bash
# 0) 사전점검 — 무엇이 더 필요한지 확인
python -m inframon --bim-inspect model.ifc

# 1-A) IfcMapConversion 이 있는 경우
python -m inframon --bim-align project.h5,model.ifc,out/bridge \
  --bim-use-z --bim-write-ifc out/model_monitored.ifc

# 1-B) 없는 경우 — 측량 기준점으로
python -m inframon --bim-align project.h5,model.ifc,out/bridge \
  --bim-control-points control.json --bim-max-rms 0.3 \
  --bim-write-ifc out/model_monitored.ifc

# 1-C) IFC 없이 부재 테이블로
python -m inframon --bim-align project.h5,elements.csv,out/bridge \
  --bim-control-points control.json
```

IFC 를 읽고 쓰려면 `pip install -e ".[bim]"` (ifcopenshell). 부재 테이블만 쓸 거면 불필요합니다.

---

## 5.5 실 BIM 산출물 호환 — 무엇이 확인됐나

실 교량 IFC 가 아직 없어서, ifcopenshell 로 여러 형태의 IFC 를 만들어 축을 고정했다.

| 축 | 확인 |
|---|---|
| **길이 단위** | 밀리미터 모델의 선언값 100 → **0.1 m** 로 읽힘. 형상은 `ifcopenshell.geom` 이 SI 미터로 변환하고, 배치 폴백에는 단위 배율을 직접 적용한다 |
| **중첩 배치** | 사이트(1000, 2000) 위에 부재(5, 0) → **1005, 2000**. `RelativePlacement` 만 읽으면 5, 0 이라 **1 km 어긋난다**(고침) |
| **지오레퍼런싱 없음** | 오류가 아니라 "기준점 필요"로 처리, `IfcSite` 위경도가 있으면 위치 힌트로 표시 |
| **형상 없는 부재** | 배치 원점(0크기 AABB)으로 들어가고 그 사실을 보고 |
| **IFC4.3 교량 타입** | `IfcBridgePart`·`IfcDeepFoundation`·`IfcCaisson`·`IfcBearing` 매핑 |
| **IfcBuildingElementProxy** | 타입으로는 못 정함 → 부재명("P3 교각")으로 인식 |

**아직 못 겪은 것**(실 모델이 필요): 대형 모델 성능, 좌표계가 여러 개인 모델, 복잡 형상의
AABB 품질, Revit/Civil3D/Tekla 별 특이사항.

## 6. 자주 걸리는 것 (실제로 겪은 것들)

- **길이 단위 미지정 → 밀리미터로 해석.** `100`(미터 의도)이 `0.1 m` 로 읽힙니다.
  가장 흔한 함정이라 `--bim-inspect` 로 먼저 확인하세요.
- **부재가 평면상 겹침.** 위에서 보면 상판 경계상자가 교각을 통째로 포함합니다.
  2D 로는 갈리지 않아 모호성을 숫자(`n_ambiguous`)로 남기고, InSAR 부재 라벨 →
  부재 구체성 순으로 해소합니다. **근본 해결은 3D 연결(=표고)** 입니다.
- **관측점이 하나도 없는 부재.** 매끈한 상판·레이더 음영 때문에 흔합니다.
  그 부재의 상태는 **"알 수 없음"이지 "정상"이 아닙니다** — 결과에 그렇게 표시됩니다.
- **부재 라벨 불일치.** InSAR 라벨(deck)과 BIM 타입(pier)이 다르면 값을 버리지 않고
  표시만 합니다. 정합 오차인지 CV 라벨 오류인지는 사람이 판단해야 합니다.

---

## 7. 요청 사항 정리 (그대로 전달하셔도 됩니다)

> 1. **IFC 파일** (`.ifc`) — IFC4 권장
> 2. IFC 에 `IfcMapConversion`(지오레퍼런싱)이 **없다면**, 아래를 아는 **측량 기준점 3~4점**
>    - 각 점의 **IFC 로컬 좌표** (x, y, z)
>    - 같은 점의 **측량 좌표** (EPSG:5186 등 E, N, 표고)
> 3. (선택) IFC 의 **길이 단위**와 **수직기준면**(예: 인천만 평균해면)
> 4. (선택) 교량 **지간(경간) 길이**
>
> IFC 를 줄 수 없으면 부재 목록 CSV(GUID·이름·타입·경계상자)로도 됩니다.
