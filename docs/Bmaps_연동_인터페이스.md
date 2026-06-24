# Bmaps 연동 인터페이스 설계 — InSAR 위성 변위 분석 탭

KICT **Bmaps**(교량 SHM 플랫폼)에 inframon(InSAR·PINN·CV·FRAM → CRI)을
**새 탭 하나**로 직접 통합하기 위한 연동 인터페이스 설계.

> 대상 탭(가칭): **「교량 InSAR 위성 변위 분석」**
> 기존 탭들(성능평가·노후도·내하·내진·계측안전성·손상확산·보수보강·손상이미지·하부구조라우팅)은
> 모두 *현장 계측/점검 기반*이다. 이 탭은 빠진 축인 **위성 기반·비접촉·광역 변위 모니터링**을 채운다.

---

## 1. 통합 아키텍처 — 사이드카 REST 마이크로서비스

```
┌─────────────────────────── Bmaps ───────────────────────────┐
│  [성능평가][노후도]...[하부구조라우팅] [★ InSAR 위성 변위 분석]│   ← 새 탭(Bmaps 프론트)
│                                              │  REST/JSON      │
└──────────────────────────────────────────────┼───────────────┘
                                                │ (교량 ID로 조회)
                                   ┌────────────▼─────────────┐
                                   │  inframon-api (FastAPI)  │   ← 사이드카 서비스
                                   │  serve.py 확장           │
                                   └────────────┬─────────────┘
                                                │ 읽기 전용
                                   ┌────────────▼─────────────┐
                                   │ 교량별 project.h5 (계약)  │
                                   └──────────────────────────┘
```

**왜 사이드카인가**
- InSAR 처리 스택(GDAL·ISCE2·PyTorch·SARvey)은 무겁고 일부는 WSL2/Linux 전용 → Bmaps 본체(자바/웹)에 섞으면 배포·의존성이 오염된다.
- inframon은 이미 **읽기 전용 FastAPI 서빙 계층(`serve.py`)** 을 갖고 있다. 이를 확장하는 게 신규 작성보다 안전하다.
- "탭은 Bmaps 안에 진짜로 들어가되(프론트엔드 컴포넌트), 데이터/연산은 inframon이 책임진다"는 관심사 분리.
- 파이프라인이 `project.h5`를 갱신하면 API가 매 요청마다 새로 읽어 **즉시 반영**(serve.py 현재 동작).

> 대안: Bmaps DB에 결과를 적재(ETL)하거나 정적 GeoJSON으로 내보내는 방식도 가능하나,
> 실시간성·계약 일관성·구현량 면에서 REST 사이드카가 가장 유리. (§8 비교)

---

## 2. 교량 식별 / 매핑 — 가장 먼저 합의할 계약

현재 inframon은 **교량 1개 = project.h5 1개**(`--out data/project.h5`)이고 교량 식별자 개념이 없다.
Bmaps는 **다수 교량**을 교량관리번호로 식별한다. 따라서 **레지스트리**가 첫 연결 고리다.

`data/bridge_registry.json` (또는 Bmaps DB 테이블):

```json
{
  "bridges": [
    {
      "bridge_id": "KICT-2024-00137",      // Bmaps 교량관리번호 (PK)
      "name": "정자교",
      "project_h5": "data/jeongjagyo/project.h5",
      "wgs84_center": [37.3219, 127.1083], // 지도 초기 뷰
      "track_source": "track_2024H1.h5",
      "last_run_utc": "2026-06-01T03:00:00Z"
    }
  ]
}
```

- **합의 필요**: `bridge_id` 체계를 Bmaps의 교량관리번호와 1:1로 맞춘다. 이게 모든 엔드포인트의 경로 파라미터다.
- 한 교량의 재처리(track 갱신)는 같은 `project_h5`를 덮어쓰거나, 이력 보관 시 `runs/<date>/project.h5` + `last_run_utc`로 버전 관리.

---

## 3. REST API 계약

베이스: `GET /api/v1/...`, 응답 `application/json; charset=utf-8`. 모두 **읽기 전용(GET)**.
좌표는 지도 오버레이를 위해 **WGS84(lat, lon)** 로 변환해 제공한다
(원천 EPSG:5179 → `insar/geo.py::reproject(xy, "EPSG:5179", "EPSG:4326")`).

### 3.1 교량 목록 — 탭 진입/지도 핀
`GET /api/v1/bridges`
```json
{
  "bridges": [
    {
      "bridge_id": "KICT-2024-00137", "name": "정자교",
      "wgs84_center": [37.3219, 127.1083],
      "warning_level": "주의",          // 정상|주의|경고|위험
      "cri_global_max": 0.42,
      "last_run_utc": "2026-06-01T03:00:00Z",
      "has_insar": true
    }
  ]
}
```

### 3.2 탭 헤더 요약 — 한 교량 선택 시
`GET /api/v1/bridges/{bridge_id}/insar/summary`
```json
{
  "bridge_id": "KICT-2024-00137", "name": "정자교",
  "warning": {                          // FRAMWarning 계약 그대로
    "level": "주의", "basis": "cri",
    "critical_members": ["pier"],
    "function_states": {"thermal":"정상","load":"주의","bearing":"정상","foundation":"정상"},
    "lead_time_days": null, "lead_time_forecast_days": 38.0
  },
  "cri_global_max": 0.42,
  "n_points": 1280, "n_dates": 36,
  "date_range": ["2024-01-08", "2025-12-21"],
  "coherence_mean": 0.71
}
```
> 출처: `serve.py::read_monitor()` 거의 그대로 + 교량 메타·날짜 범위 추가.

### 3.3 변위 측점 (지도 레이어) — 탭의 핵심
`GET /api/v1/bridges/{bridge_id}/insar/points?metric=los|longitudinal&date=latest|<idx>`
```json
{
  "dates": ["2024-01-08", "...", "2025-12-21"],
  "metric": "los", "date_index": 35,
  "points": [
    {
      "point_id": 4021,
      "lat": 37.32188, "lon": 127.10841, "elev": 41.2,
      "member": "pier",                  // MEMBER_TYPES
      "coherence": 0.83,
      "value_mm": -3.7,                  // 선택 시점 변위(mm)
      "vertical_mm": -1.2,               // 연직 변위(asc+desc 융합 시), 없으면 null
      "cri": 0.41                        // 해당 측점 최신 CRI(색칠용)
    }
  ],
  "has_vertical": true                   // 연직(융합) 데이터 존재 여부
}
```
> 한 번에 수천 점 → GeoJSON `FeatureCollection`으로도 동시 제공(지도 라이브러리 직접 소비):
> `GET .../insar/points.geojson?...`

### 3.4 측점 시계열 — 점 클릭 시 상세
`GET /api/v1/bridges/{bridge_id}/insar/points/{point_id}/series`
```json
{
  "point_id": 4021, "member": "pier",
  "dates": ["2024-01-08", "..."],
  "los_mm":          [0.0, -0.6, ...],
  "longitudinal_mm": [0.0, -0.4, ...],   // 종축 수평(열팽창)
  "vertical_mm":     [0.0, -0.3, ...],   // 연직(처짐·침하), asc+desc 융합 시 / 없으면 null
  "components": {                        // PINN 성분분해(있으면)
    "thermal_mm": [...], "load_mm": [...],
    "settle_mm": [...], "anomaly_mm": [...]
  },
  "cri": [0.10, 0.12, ..., 0.41],
  "EI": 2.1e10, "alpha": 1.1e-5         // PINN 역산 절대 강성·열팽창
}
```

### 3.5 CRI 시계열 — 안전성 추세 차트
`GET /api/v1/bridges/{bridge_id}/insar/cri`
```json
{ "cri_global_max": 0.42, "n_points": 1280, "n_dates": 36,
  "dates": ["..."], "cri_max_series": [0.05, ..., 0.42] }
```
> `serve.py::/cri` 그대로 + dates 추가.

### 3.6 함수망 진단 — N-K 공명·임계경로 (선택 패널)
`GET /api/v1/bridges/{bridge_id}/insar/function-network`
```json
{ "func_names": ["thermal","load","bearing","foundation"],
  "variability": [..], "coupling": [[..],[..],[..],[..]],
  "network_resonance_max": 0.6, "driver": "load", "critical_path": ["load","bearing"] }
```
> `serve.py::/function-network` + `dashboard/data.py::fram_function_diagram()` 결합.

### 3.7 헬스
`GET /api/v1/health` → `{"status":"ok","bridges":12}`

**오류 규약**: 404=해당 bridge_id 없음/InSAR 미산출, 409=schema_version 불일치, 503=project.h5 읽기 실패.
응답 `{"error": {"code": "...", "message": "..."}}`.

---

## 4. InSAR 탭 화면 ↔ 엔드포인트 매핑

| 탭 UI 요소 | 데이터 | 엔드포인트 |
|---|---|---|
| 상단 경보 배지(정상/주의/경고/위험) | FRAM warning | `/insar/summary` |
| 지도 위 변위 측점 히트맵 | 측점 + 색=변위/CRI | `/insar/points(.geojson)` |
| 측점 클릭 → 시계열 팝업 | LOS/종방향/성분/CRI | `/insar/points/{id}/series` |
| CRI 안전성 추세 차트 | cri_max_series | `/insar/cri` |
| 기능 공명 다이어그램(레이더+결합) | 함수망 | `/insar/function-network` |
| 구조 지표(절대 EI·강성저하) | PINN | series 응답에 포함 |

이 구성은 기존 **「계측 데이터 안전성 분석」** 탭과 한 쌍을 이룬다 — *센서로 본 안전성* ↔ *위성으로 본 안전성*.

---

## 5. 좌표·단위 규약 (오해 방지)

- **좌표**: 내부 EPSG:5179 → API는 WGS84(lat,lon). 변환은 `insar/geo.py::reproject`. Bmaps 지도 SRS와 일치 확인 필수(Bmaps가 5179 타일이면 변환 생략 옵션 제공).
- **변위 단위**: 계약 내부는 m, API는 **mm**(현장 가독성). 부호 규약 명시: LOS 음수=위성에서 멀어짐(침하 경향).
- **날짜**: 계약은 epoch days(`dates_ds`) → API는 ISO `YYYY-MM-DD`.
- **member**: 정수 라벨 → 문자열(`deck|pier|abutment|bearing`)로 풀어서 제공.

---

## 6. 인증 · 배포 · 성능

- **인증**: 사이드카는 내부망 전용 + Bmaps 게이트웨이 뒤. 외부 노출 시 API Key 헤더(`X-API-Key`) 또는 Bmaps와 동일 SSO(reverse proxy).
- **배포**: `python -m inframon --serve`(현행) 확장 → `inframon-api` 진입점. Docker 권장(GDAL/torch 고정). Windows면 `dist/inframon`(PyInstaller) + 윈도 서비스.
- **성능**: `/points`는 수천 점 → (1) project.h5 읽기 결과 메모리 캐시(`last_run_utc` 키로 무효화), (2) gzip, (3) 큰 변위 행렬은 시점 슬라이스만 전송. 시계열 전체가 필요하면 `/series`로 점 단위 지연 로드.
- **CORS**: Bmaps 도메인만 허용.

---

## 7. 구현 작업 목록 (codebase 변경) — contracts는 성역, 건드리지 않음

1. `src/inframon/api/` 신설 — `serve.py`를 다중 교량으로 확장
   - `registry.py` — `bridge_registry.json` 로드/조회 (§2)
   - `transform.py` — h5 계약 → API DTO 변환 (5179→WGS84, m→mm, epoch→ISO, member 라벨)
   - `app.py` — §3 엔드포인트 (기존 `serve.py` 라우트 흡수)
2. `serve.py` — `read_monitor()` 재사용(단일교량 호환 유지), 신규 변환 함수 공유
3. `dashboard/data.py` — `fram_panel_data`/`fram_function_diagram` 재사용(중복 방지)
4. `__main__.py` — `--serve-api [--registry data/bridge_registry.json] [--port]` 플래그 추가
5. `tests/test_bmaps_api.py` — 각 엔드포인트 계약·좌표변환·단위·404/409 회귀
6. `pyproject.toml` — `[serve]` extra에 fastapi/uvicorn 확정(이미 선택 의존)
7. `docs/실데이터_런북.md` — "Bmaps 연동" 절에서 본 문서 링크

> 핵심 원칙: **`contracts/`(schema.py·io.py)는 성역.** API는 계약을 *읽어 변환*만 하고
> 새 계산을 넣지 않는다. 골든 회귀가 계약을 계속 보호한다.

---

## 8. 통합 방식 비교 (의사결정 근거)

| 방식 | 실시간성 | 구현량 | Bmaps 본체 오염 | 비고 |
|---|---|---|---|---|
| **REST 사이드카(채택)** | 높음(요청 시 최신) | 중(serve.py 확장) | 없음 | 권장 |
| Bmaps DB ETL 적재 | 배치 주기 | 중~고(스키마+ETL) | DB 스키마 추가 | 대용량 집계엔 유리 |
| 정적 GeoJSON 내보내기 | 낮음 | 소 | 없음 | 프로토타입/PoC용 |

---

## 9. 다음 액션 (합의 대기 항목)

1. **`bridge_id` 체계** — Bmaps 교량관리번호와 매핑 규칙 확정 (§2).
2. **Bmaps 지도 SRS** — WGS84인지 EPSG:5179 타일인지 (§5, 변환 생략 여부).
3. **인증 방식** — 내부망/API Key/SSO 중 택1 (§6).
4. 확정되면 §7 순서로 `src/inframon/api/` 구현 + 회귀 테스트.
