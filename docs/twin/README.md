# 데모 디지털 트윈 — IFC 까지 붙은 실물

정자교(성남 분당) 기준으로 **IFC 생성 → 부재 결합 → 3D 트윈**을 통째로 만든 산출물이다.
저장소에 코드와 테스트는 있었지만 **열어 볼 파일**이 없었다. 여기 것이 그 실물이다.

| 파일 | 무엇 |
|---|---|
| `jeongjagyo_proxy.ifc` | **IFC4** 프록시 교량 — 부재 11개 + `IfcMapConversion`(EPSG:5186) |
| `jeongjagyo_elements.json` | 위 IFC 를 **되읽어** 얻은 부재 AABB·GlobalId |
| `twin.glb` | InSAR 점 12개, LOS 변위속도 채색, 부재 결합 포함 |
| `twin.viewer.html` | **더블클릭하면 브라우저에서 3D** — 서버·설치·**인터넷 불필요**(three.js 동봉). IFC 부재 11개가 반투명 박스로, 점은 그 위에. 점 클릭 = 값·부재·GlobalId |
| `tileset.json` | 3D Tiles — Cesium·BMAP 탑재용 |
| `twin.glb.meta.json` | 점별 값·GlobalId·범례·좌표 근거 |
| **`결과.md`** | **트윈 점으로 도출한 InSAR · PINN 가상센싱 · CRI** — 수치와 그림 |
| `twin_cri.glb` · `twin_cri.viewer.html` | 같은 점, **CRI(위험도) 채널** 3D |
| `twin_insar.png` | LOS 시계열 12점 × 201시점 · 점별 속도 |
| `twin_pinn.png` | 가상센싱 전체 변위장(교축 × 시간) · 성분 분해 · 점별 CRI |
| `twin_project.h5` | 위 결과의 프로젝트(12점) — 감사 **보고 가능** · 재현용 |

## 바로 보기

    docs/twin/twin.viewer.html 를 브라우저로 연다   ← 오프라인에서도 뜬다(헤드리스 Chrome 으로 file:// 확인)

## 다시 만들기

    python scripts/make_demo_twin.py

`jeongjagyo_proxy.ifc` 부터는 실측 제원과 OSM 만으로 만들어지지만, `twin.glb` 는
정자교 SARvey 산출물(`data/jeongjagyo_real.h5`, 2,661점 × 201에폭)이 있어야 한다.
없으면 스크립트가 사유를 말하고 멈춘다.

## 만드는 순서와 그 이유

1. **실측 제원 → 프록시 부재** — 전국교량표준데이터(data.go.kr 15081953) 정자교:
   연장 108 m · 5경간. 폭 26.5 m 는 추정이 아니라 OSM 양측 보도 중심선 간격
   23.5 m 에 보도 반폭을 더한 값이다.
2. **부재 → IFC4** (`bim.ifc_write`) — bbox 를 `IfcExtrudedAreaSolid` 로 쓴다.
   형상이 있어야 되읽을 때 배치 원점이 아닌 **형상 AABB** 가 나온다.
   `IfcMapConversion` 으로 로컬↔지도 변환을 파일 안에 박는다.
3. **IFC 를 되읽는다** (`bim.ifc_io`) — 여기가 요점이다. 부재 테이블에서 바로 트윈을
   만들면 IFC 는 장식이 된다. 되읽은 결과로 결합해야 "IFC 로 결합했다"가 참이 된다.
   왕복은 테스트로 고정돼 있다(`tests/test_bim_ifc_write.py`: GUID 11/11,
   bbox 오차 < 1 mm, 지오참조 보존).
4. **점을 데크 레벨로 올려 결합** — 지면 37.2 m → 데크 상단 44.6 m. 12점 전부
   GlobalId 로 부재에 붙는다.

## 이것은 실 BIM 이 아니다

`jeongjagyo_proxy.ifc` 는 **표준데이터 제원으로 세운 프록시**다. 측량 정합이 아니라
제원+OSM 방위로 배치했고, 그 사실이 파일 헤더와 각 부재 Description 에 적혀 있다.
실 IFC 가 있으면 당연히 그쪽을 쓴다 — `--ifc <파일>` 로 넣으면 같은 경로를 탄다.
실 설계 IFC(IFC2X3, 691부재)로 읽기 경로를 검증했고 그 과정에서 결함 5건을 고쳤다.
