# BIM / 디지털 트윈 정합 — 위성 관측을 IFC 부재에 붙이기

> **상태: 정합 코어 + IFC 읽기/쓰기 모두 구현·검증됨.** `--bim-align` 으로 동작한다.
> IFC I/O 는 `ifcopenshell` 선택 의존(`pip install -e ".[bim]"`)이고, 없으면 부재
> 테이블(JSON/CSV)로 전 과정이 그대로 동작한다.

---

## 0. 무엇이 문제인가

"BIM 이랑 합친다"의 90% 는 **좌표계 정합**이다.

| | 좌표계 | 원점 | 회전 | 표고 기준 |
|---|---|---|---|---|
| InSAR (inframon) | 지리/투영 (EPSG:4326·5179·5186) | 지구 | 없음(북향) | DEM — 타원체고 또는 지오이드고 |
| BIM (IFC) | **로컬 엔지니어링** | 현장 임의 지점 | 교량 축 방향 | 수직기준면(예 인천만 평균해면) |

이 변환이 틀리면 뒤의 부재 연결·Pset 주입은 **전부 조용히 틀린다**. 결과 JSON 은 정상처럼
생겼고 뷰어에도 색이 칠해지므로 아무도 눈치채지 못한다 — 디지털 트윈에서 가장 위험한
실패 방식이다. 그래서 이 구현의 설계 원칙은 **정합 실패를 크게 내는 것**이다.

---

## 1. 파이프라인

```
project.h5 (InSAR · FRAM CRI · 잔존수명)
    │
    │ ① 좌표 정합   bim/georef.py
    │     · IfcMapConversion 이 있으면 그대로 (IFC4 표준)
    │     · 없으면 측량 기준점 쌍으로 Helmert 적합 + RMS 게이트
    │
    │ ② 부재 연결   bim/elements.py
    │     · 점 → GUID (AABB 내부 / 최근접, max_dist 초과는 미연결)
    │     · 동률은 InSAR 부재 라벨 → 부재 구체성 순으로 해소
    │
    │ ③ 부재 집계   bim/psets.py
    │     · 강건 통계(중앙값) + 위험 최댓값 병기 → Pset 페이로드
    ▼
  *_elements.json (부재별 상태) + *_pset.json (IFC 주입용)
    │
    └─ ④ 선택: bim/ifc_io.py 로 원본 IFC **사본**에 Pset 주입
```

---

## 2. ① 좌표 정합

### 2.1 IfcMapConversion (IFC4 표준)

```
E = Eastings        + Scale·(x·XAxisAbscissa − y·XAxisOrdinate)
N = Northings       + Scale·(x·XAxisOrdinate + y·XAxisAbscissa)
H = OrthogonalHeight + Scale·z
```
`(XAxisAbscissa, XAxisOrdinate)` 는 로컬 X축의 지도상 방향(= cos θ, sin θ)이다.
`MapConversion.to_map` / `.to_local` 이 이 식과 그 정확한 역변환이다(왕복 오차 1e-9).

### 2.2 IfcMapConversion 이 없을 때 — 기준점 정합

국내 실무 모델에는 지오레퍼런싱이 없는 경우가 흔하다. 이때는 **측량 기준점 쌍**으로
2D 상사변환을 최소자승 적합한다.

```json
{"target_crs": "EPSG:5186",
 "points": [{"name":"BM1","local":[0,0,0],"map":[200000,550000,10]},
            {"name":"BM2","local":[100,0,0],"map":[200100,550000,10]}]}
```

**축척은 기본적으로 1 로 고정한다**(`fix_scale=True`). BIM 도 측량도 미터계이므로 축척은
1 이어야 하고, 자유롭게 두면 **기준점 오차를 축척이 흡수해** 잔차가 작아지면서 실제로는
틀어진다. 잔차가 작다고 정합이 맞는 게 아니다.

**RMS 게이트** — 잔차가 `--bim-max-rms`(기본 0.5m)를 넘으면 값을 내지 않고 점별 잔차와
함께 실패한다. 조용히 틀린 정합보다 실패가 낫다.

### 2.3 표고는 기본적으로 쓰지 않는다

IFC `OrthogonalHeight` 는 보통 수직기준면 기준이고 InSAR z 는 DEM 에서 온다. 한국의
지오이드고는 **~25m** 라 그냥 합치면 수십 미터가 어긋나고, 상판(고도 8m)과 교각(0m)의
구분이 통째로 뒤집힌다. 그래서:

- 기본은 **2D 평면 연결**.
- 기준점에 표고가 있어 오프셋이 적합된 경우에만(`fit.height_fitted`) `--bim-use-z` 허용.
- 검증 없이 3D 를 쓰려 하면 `AlignmentError` 로 막는다.

---

## 3. ② 부재 연결

부재는 IFC 로컬 좌표계의 **AABB(축정렬 경계상자)** 로 근사한다. 규칙:

1. 점이 AABB 안(허용오차 `tol_m` 포함) → 그 부재, 거리 0
2. 아니면 표면까지 최단거리가 `max_dist_m` 이내인 최근접 부재
3. 둘 다 아니면 **미연결** — 억지로 붙이지 않는다. 틀린 부재에 값을 넣는 것보다 낫다.

### 3.1 2D 투영의 본질적 모호성

상판 AABB 는 평면에서 교각을 **통째로 포함**한다. 두 부재 모두 거리 0 이 되어 동률이고,
배열 순서로 아무거나 고르면 **부재 배치 순서에 따라 결과가 달라진다**. 해소 순서:

1. **InSAR 부재 라벨**(CV 가 준 독립 증거)과 맞는 후보 우선
2. 그래도 동률이면 **더 작은(구체적인) 부재** — 교각이 상판보다 특정적이다

그리고 모호했다는 사실 자체를 `n_ambiguous` 로 남긴다. 근본 해결은 3D 연결이고, 그건
표고 검증이 선행되어야 한다(2.3).

### 3.2 부재 라벨 불일치는 버리지 않는다

InSAR 라벨(deck)과 BIM 타입(pier)이 어긋나도 값을 버리지 않고 `member_mismatch` 로
표시만 한다. 어긋나는 이유는 대개 **정합 오차이거나 CV 라벨 오류**이고, 어느 쪽인지는
사람이 판단해야 한다.

---

## 4. ③ 부재 집계와 Pset

### 4.1 시계열은 IFC 에 넣지 않는다

`IfcPropertySingleValue` 는 스칼라 상태를 담는 그릇이다. 201시점 × 수천 점을 밀어 넣으면
IFC 가 뷰어에서 열리지 않는다. 그래서 **IFC 에는 현재 상태 + 출처 키**만 넣고, 시계열은
`project.h5`(트윈 데이터 레이어)에 남긴다.

```
SourceProject : /path/to/project.h5     ← 시계열이 여기 있다
SourceGroups  : /insar,/fram,/life
UpdatedAt     : 2026-07-22
```

### 4.2 집계는 강건 통계 + 위험 병기

부재당 점 수가 적고 이상치가 섞이므로 평균이 아니라 **중앙값**을 쓴다. 다만 중앙값 하나로
줄이면 국소 이상이 사라지므로 **최댓값도 함께** 낸다. 잔존수명은 그 부재에서 **가장 이른**
값이 지배한다(가장 약한 곳이 부재를 지배).

주입되는 속성(`Inframon_Monitoring`):

| 속성 | 의미 |
|---|---|
| `PointCount` · `Sparse` | 점 수, 3점 미만이면 통계 신뢰 낮음 |
| `VelocityMedian_mm_per_yr` · `VelocityMaxAbs_mm_per_yr` | 변위 속도(중앙·최악) |
| `CumulativeMaxAbs_mm` | 관측 구간 누적 변위 최댓값 |
| `CoherenceMedian` | 관측 품질 |
| `CRIMax` | FRAM 공명 위험 최댓값 |
| `RemainingLifeLower_yr` · `RemainingLifeCensoredFraction` | 잔존수명 하한, 검열 비율 |
| `DegradationRateMaxAbs_mm_per_yr` | 열화율 |
| `AssociationDistanceMedian_m` · `MemberMismatchPointCount` | **정합 품질**(결과 신뢰도 판단용) |

정합 품질을 Pset 에 같이 넣는 이유: BIM 쪽에서 값만 보는 사람이 그 값이 얼마나 믿을
만한지 알 수 있어야 하기 때문이다.

---

## 5. 사용법

```bash
# 부재 테이블(JSON/CSV) + IfcMapConversion(JSON) 으로 정합
python -m inframon --bim-align project.h5,elements.json,out/bridge \
  --bim-map-conversion mc.json --bim-source-crs EPSG:4326 --bim-max-dist 5

# IfcMapConversion 이 없는 모델 — 측량 기준점으로 정합
python -m inframon --bim-align project.h5,elements.json,out/bridge \
  --bim-control-points control.json --bim-max-rms 0.3

# 실 IFC (pip install -e ".[bim]") — 사전점검 후 정합·주입
python -m inframon --bim-inspect model.ifc
python -m inframon --bim-align project.h5,model.ifc,out/bridge \
  --bim-use-z --bim-write-ifc out/model_monitored.ifc
```

ELEMENTS 가 `.ifc` 면 부재 테이블과 `IfcMapConversion` 을 모두 그 파일에서 읽는다
(`--bim-map-conversion` 을 주면 그쪽이 우선). 실 IFC 는 `OrthogonalHeight` 로 표고 기준이
정의돼 있으므로 `--bim-use-z` 가 정당하고, 그래야 상판과 그 아래 교각이 갈린다.

부재 테이블 형식(둘 다 UTF-8/cp949 자동 인식 — 국내 BIM 산출물은 cp949 가 흔하다):

```csv
guid,name,ifc_type,xmin,ymin,zmin,xmax,ymax,zmax
DECK1,상판,IfcSlab,0,-5,8,100,5,9
PIER1,교각1,IfcColumn,30,-2,0,34,2,8
```

원본 IFC 는 **덮어쓰지 않는다** — BIM 원본은 다른 팀의 산출물이므로 주입은 사본에 한다
(같은 경로를 주면 거부한다).

---

## 6. 검증 상태 (정직하게)

| 부분 | 검증 |
|---|---|
| IfcMapConversion 변환·역변환 | ✅ IFC4 정의식 대조 + 왕복 1e-9 |
| 기준점 Helmert 적합 | ✅ 알려진 변환 복원(1e-6) · 오대응 시 실패 |
| CRS 재투영(WGS84→EPSG:5186) | ✅ 실 pyproj, 거리 보존 확인 |
| 부재 연결·동률 해소·미연결 | ✅ 배치 순서 무관성 포함 |
| 부재 집계·Pset 평탄화 | ✅ 스칼라만 나오는지 포함 |
| 오케스트레이션(실 project.h5) | ✅ 정자교 2661점 → 부재 4개, 100% 연결 |
| **IFC 읽기** (`IfcMapConversion`·부재 AABB·타입 추론) | ✅ 실 IFC 왕복 |
| **IFC 쓰기** (Pset 주입·재주입·원본 보존) | ✅ 실 IFC 왕복 |

IFC 왕복은 `tests/test_bim_ifc_roundtrip.py` 가 **ifcopenshell 로 교량 IFC 를 만들어**
검증한다(지오레퍼런싱 + 박스 형상 부재 4개 + 형상 없는 부재). 실 교량 IFC 가 없어도
실제로 겪을 문제(단위, 형상 AABB, GUID 매칭, 재주입 누적)를 대부분 만난다.

### 6.1 실 IFC 에서 실제로 걸린 것들

- **단위**: IFC 길이 단위를 지정하지 않으면 밀리미터가 된다. 100(m 의도)이 0.1m 로 읽힌다.
  가장 흔한 함정이라 테스트가 이 축을 고정한다. 투입 전 `--bim-inspect` 로 확인할 것.
- **정수/실수**: 점 개수를 `IfcReal` 로 넣으면 뷰어에 `13.0` 으로 보인다 → `IfcInteger` 사용.
- **재주입 누적**: 모니터링은 주기적으로 다시 도는데 덧붙이기만 하면 동명 Pset 이 쌓여
  뷰어에서 최신을 구분할 수 없다 → 같은 이름의 기존 Pset 을 지우고 새로 넣는다(`n_replaced`).
- **허용오차가 점을 뺏는 문제**: 상판 바닥(z=8)과 교각 상단(z=8)은 맞닿는다. 정합 오차
  흡수용 허용오차를 교각에도 적용하면 상판 안의 점이 교각과 동률이 되어 넘어간다.
  → **허용오차 없이도 안에 있는 후보를 우선**한다. 허용오차는 아무 부재에도 안 걸리는
  점을 구제하려는 것이지 이미 안에 있는 점을 뺏으라는 게 아니다.
- `inside` 는 허용오차를 뺀 **엄밀 포함**을 뜻한다(확장 박스 기준이면 허용오차를 키울수록
  "내부"가 늘어나 신뢰도 지표로 못 쓴다).

실 IFC 투입 시 반드시 `--bim-inspect` 로 `IfcMapConversion` 유무·부재 수·타입 분포·
**길이 단위**를 먼저 확인할 것.

---

## 7. 남은 것

- **실 교량 IFC 투입** — 합성 IFC 로는 못 만나는 것들: 대형 모델 성능, `IfcBridge`/
  `IfcBridgePart`(IFC4.3) 타입 매핑, 복잡 형상의 AABB 품질, 좌표계가 여러 개인 모델.
- **3D 연결 상시화** — 수직기준면 변환(지오이드 모델 KNGeoid) 연결.
- **부재 단위 시계열 API** — IFC 에서 `SourceProject` 를 따라와 시계열을 조회하는 엔드포인트
  (현재는 project.h5 직접 접근).
- **IfcAlignment 연계** — 선형(station) 기준 부재 매핑. 교량 종축 station 은 이미
  InSAR 쪽에 있으므로(`l_from_fixed`) 연결 가능.
