# Bmaps "InSAR 위성 변위 분석" 탭 — 프론트엔드 연동 예제

inframon API(`src/inframon/api`)를 KICT **Bmaps** 의 새 탭에 붙이기 위한 프론트엔드 예제.
서버 설계·계약은 [`../../docs/Bmaps_연동_인터페이스.md`](../../docs/Bmaps_연동_인터페이스.md).

## 파일

| 파일 | 용도 |
|---|---|
| `inframon-client.js` | **프레임워크 비의존 API 클라이언트**(순수 fetch, ES module). 7개 엔드포인트 + 색상 헬퍼. 어디서든 import. |
| `insar-tab.html` | **즉시 실행 가능한 완성 데모**(Leaflet 지도 + Chart.js). 빌드 도구 없이 브라우저로 열면 동작 — 시각 레퍼런스. |
| `InsarTab.jsx` | Bmaps 가 **React** 기반일 때의 컴포넌트 예제. 지도는 `renderMap` prop 으로 Bmaps 기존 GIS 라이브러리에 위임. |
| `vworld-adapter.js` | **VWorld(브이월드) 지도 어댑터**. OpenLayers + VWorld 타일로 측점 렌더링/클릭/툴팁 관리. Bmaps 의 기존 VWorld 지도에 측점 레이어만 얹을 수도 있음. |
| `insar-tab-vworld.html` | VWorld 베이스맵 버전의 즉시 실행 데모(OpenLayers 기반). |
| `VWorldInsarMap.jsx` | VWorld 어댑터를 감싼 React 컴포넌트 — `InsarTab` 의 `renderMap` 에 끼워 쓴다. |

## 1단계 — API 서버 띄우기

```powershell
# inframon 루트(D:\프로그램)에서
pip install -e ".[serve]"

# (a) 데모 데이터 단일 교량으로 빠르게
python -m inframon --serve-api --out data/project.h5

# (b) 다수 교량(운영) — 명부 작성 후
#     data/bridge_registry.example.json 을 복사·편집
python -m inframon --serve-api --registry data/bridge_registry.json
```
→ `http://127.0.0.1:8000/api/v1/health` 확인.

## 2단계 — 데모 열기

`insar-tab.html` 상단의 `API_BASE` 를 서버 주소로 맞춘 뒤, **로컬 정적 서버**로 연다
(ES module 은 `file://` 직접 열기가 막혀 있음):

```powershell
# 이 디렉터리(examples/bmaps_tab)에서
python -m http.server 5500
# 브라우저: http://127.0.0.1:5500/insar-tab.html
```

> 데모 `data/project.h5` 의 측점 좌표는 합성용 임의값이라 지도에 안 찍힐 수 있다(헤더에 안내 표시).
> 실데이터(올바른 EPSG:5179 → WGS84)면 정상 표시. 헤더 배지·시점 슬라이더·CRI 추세 차트·점 클릭 시계열은 데모에서도 동작.

## 3단계 — 실제 Bmaps 탭에 이식

- **클라이언트 재사용**: `inframon-client.js` 를 Bmaps 코드에 그대로 넣고 `new InframonClient(API_BASE)` 호출.
- **지도/차트**: Bmaps 가 이미 쓰는 GIS(OpenLayers/VWorld 등)·차트 라이브러리로 교체. `InsarTab.jsx` 의 `renderMap` 패턴 참고.
- **탭 등록**: 기존 9개 탭 옆에 "교량 InSAR 위성 변위 분석" 추가, 선택된 교량의 `bridge_id` 를 전달.

## VWorld(브이월드) 연동

KICT 환경에서 Bmaps 가 VWorld 지도를 쓰는 경우. VWorld Map API 2.0 은 **OpenLayers 기반**이라,
어댑터는 OpenLayers(`ol`) + VWorld 타일 소스로 동작한다.

**준비**: VWorld([vworld.kr](https://www.vworld.kr)) 에서 apiKey 발급 + **호출 도메인 등록**(미등록 도메인은
타일이 차단됨). `localhost`/내부망 도메인도 등록 대상.

**단독 데모**:
```powershell
cd examples/bmaps_tab
python -m http.server 5500
# http://127.0.0.1:5500/insar-tab-vworld.html  → 상단에 VWorld 키 입력 후 "지도 시작"
```

**Bmaps 이식 (이미 VWorld 지도가 있을 때)** — 측점 레이어만 얹는다:
```js
import { InsarPointLayer } from "./vworld-adapter.js";
// bmapsMap: Bmaps 가 쓰는 ol.Map (vw.ol3.Map 이면 내부 ol.Map)
const insar = new InsarPointLayer(bmapsMap, { onPick: (id) => openSeriesPanel(id) });
const data = await api.points(bridgeId, { metric: "los", date: "latest" });
insar.update(data.points, { colorBy: "cri" });  // colorBy: "cri" | "value"
insar.fit();
```

**React (VWorld + InsarTab)**:
```jsx
import InsarTab from "./InsarTab.jsx";
import VWorldInsarMap from "./VWorldInsarMap.jsx";

<InsarTab apiBase={API_BASE} bridgeId={selectedBridgeId}
  renderMap={(points, { onPick }) =>
    <VWorldInsarMap apiKey={VWORLD_KEY} points={points} colorBy="cri" onPick={onPick} />} />
```

> 좌표: API 가 WGS84(lat, lon)로 주고, 어댑터가 `ol.proj.fromLonLat` 로 지도 투영(EPSG:3857)에 맞춘다.
> 따라서 서버는 기본(`wgs84`)으로 띄우면 된다(`--srs 5179` 불필요).

## CORS

브라우저에서 직접 호출하므로 서버에 호출 출처를 허용해야 한다:

```powershell
python -m inframon --serve-api --registry data/bridge_registry.json `
  --cors-origin https://bmaps.kict.re.kr
```
(미지정 시 개발 편의상 전체 허용 `*`. 운영에선 Bmaps 도메인만 지정 권장.)

## API 요약 (`/api/v1`)

| 메서드 | 경로 | 반환 |
|---|---|---|
| GET | `/health` | 상태 + 교량 수 |
| GET | `/bridges` | 교량 목록(경보등급·최대CRI) |
| GET | `/bridges/{id}/insar/summary` | 경보·CRI·관측기간 |
| GET | `/bridges/{id}/insar/points?metric=los\|longitudinal&date=latest\|N` | 측점(위경도·변위mm·CRI) |
| GET | `/bridges/{id}/insar/points.geojson?...` | 동일 데이터 GeoJSON |
| GET | `/bridges/{id}/insar/points/{pid}/series` | 측점 시계열(변위·성분·CRI·EI) |
| GET | `/bridges/{id}/insar/cri` | CRI 추세(시점별 최대) |
| GET | `/bridges/{id}/insar/function-network` | 4기능 공명·결합행렬 |
| GET | `/bridges/{id}/insar/export.csv` | KAIA 변위 CSV(점×시점, text/csv 다운로드) |
| GET | `/bridges/{id}/insar/vlm-package.zip?figures=true\|false` | VLM 입력 패키지(manifest·csv·summary·narrative·figures) ZIP |

오류: 404(교량/산출물 없음) · 409(schema 불일치) · 400(파라미터 오류) · 503(파일 읽기 실패).

> KAIA 핸드오프: `export.csv`/`vlm-package.zip` 은 InSAR 수직변위 + PINN 가상센싱을 단위통일
> 패키지로 내려 VLM(타 팀) 입력으로 쓴다. 탭에 "CSV/VLM 패키지 다운로드" 버튼으로 연결.
