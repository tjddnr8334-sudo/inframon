# inframon ↔ Pontifex 역할 분담 (2026-10-16)

| | **inframon** (강원대) | **Pontifex 창고** (스마트인사이드에이아이 `pontifex-addin`) |
|---|---|---|
| 한 줄 | 위성으로 **관측하고 판단**하는 프로그램 | 판단 결과를 **IFC 위에 쌓아 두고 내보내는** 창고 |
| 하는 일 | InSAR 처리 · 위험도(CRI) · **법정 근거표** · **SLC 보관 현황** · 교량별 전체 상세 내역 | IFC 원본 보관 · 부재(GlobalId) 단위 결과 결합 · 결과 넣은 IFC 내보내기 · **KICT B-Maps Add-in** |
| 화면 | 데스크톱/Streamlit 대시보드 ⓪~⑧ | 웹(`/warehouse/`, 교량 상세, B-Maps 탭) |
| 데이터 | `project.h5`, `bridge.json`, `*_elements.json`, `*.ifc`, SLC `.zip/.SAFE` | PostGIS(교량 33,120 · 시계열 · 부재 상태) + `data/warehouse/ifc/` |
| 위치 | `E:\프로그램` | `E:\스인 소프트웨어\pontifex-addin` (원본 `pontifex-1.0` 은 손대지 않음) |

```
 Sentinel-1 SLC ─► inframon ──────────────────────────────► Pontifex 창고 ───────► KICT B-Maps
   (⑦ 보관소)      ① InSAR  ③ CRI  ⑥ 법정 근거  ⑧ 상세      IFC 보관·GUID 결합        교량관리번호로
                    └─ --pontifex-push (감사 게이트) ──────►  /warehouse/bridge/<id>  ─► 탭(iframe/SDK)
```

## 1. 각 프로그램 보는 법

**inframon**
- `inframon_app.bat` (데스크톱 창) 또는 `python start.py --dashboard` → http://localhost:8501
- 새 섹션: **⑥ 법정 근거** · **⑦ SLC 보관소** · **⑧ 교량 상세 내역**(산출물·h5 구조·Pontifex 전송)
- CLI
  - `python -m inframon --slc-inventory E:\SLC [--slc-inventory-csv slc.csv]`
  - `python -m inframon --legal-table docs\bridges\정자교\bridge.json`

**Pontifex 창고** — Docker 가 없어도 WSL Ubuntu 에서 뜬다
```bash
# WSL: PostgreSQL+PostGIS 설치(처음 한 번) 후
cd "/mnt/e/스인 소프트웨어/pontifex-addin" && bash scripts/run_local_nodocker.sh
# → http://127.0.0.1:38000/   창고: /warehouse/   B-Maps 탭: /addin/bmaps/tab/<교량관리번호>/
```
Docker 가 있으면 원래대로 `./install.sh` (창고 앱은 마이그레이션으로 함께 올라간다).

## 2. inframon → 창고 보내기

```bash
python -m inframon --pontifex-push docs\bridges\성수대교\project.h5 \
    --pontifex-bridge-id 40001 --pontifex-bmaps-id <교량관리번호> [--pontifex-dry-run]
```
한 번에 네 가지를 보낸다 — project.h5 옆 폴더에서 자동으로 찾는다.

| 보내는 것 | 출처 | 창고에서 |
|---|---|---|
| CRI 시계열 (시점별) | `project.h5` `/fram/CRI` + `/insar/date_labels` | 시계열 차트·최신 등급 |
| 부재 GUID 결과 | `twin_cri.glb.meta.json`(측점→GlobalId) + `*_elements.json` | 부재마다 색·CRI (결합 = `guid`) |
| 법정 근거표 | `bridge.json` + `legal_inputs.json`(⑥ 탭에서 저장) | 창고·B-Maps 탭의 '법정 근거표' |
| IFC 원본 | `*_proxy.ifc` 또는 `*.ifc` | 보관(sha256 중복 제거)·3D 뷰어·결과 넣은 IFC |

감사 '보고 불가' 산출물은 막는다(`--pontifex-force` 로만 우회).

## 3. 법정 근거표가 말하는 것과 말하지 않는 것

규칙 원문: `src/inframon/legal/facility_safety_law.yaml` — 항목마다 출처(law.go.kr 조문·별표)와 `확인` 여부.

- **말한다**: 종별(시행령 별표 1 수치 기준), 안전등급 정의(별표 8), 법정 점검·진단·성능평가 주기와
  다음 기한(별표 3, '완료한 달의 말일' 기준점), 위성 관측값이 닿는 세부지침 조항.
- **말하지 않는다**: InSAR 값으로 법정 등급을 매기지 않는다. 세부지침 교량 편에는 교대·교각·기초의
  기울기·침하, 거더 처짐의 **수치 임계값이 없다**(서술 기준 d·e 뿐). 그래서 위성 행은 모두
  '법정 수치 기준 없음' 으로 표시된다.
- 원격탐사가 법정 점검을 대체한다는 조항은 법·시행령·시행규칙 어디에도 없다(2026-10-16 원문 검색).
- 미확인: 세부지침 2021.12판의 현행 여부, 종합평가 결정 방식(5.3절), 계측 필수/선택 구분.

## 4. B-Maps Add-in (창고 쪽)

- `GET /addin/bmaps/manifest.json` — 탭 이름·키(교량관리번호)·진입점
- `<script src="…/addin/bmaps/sdk.js">` → `PontifexAddin.mount(el, {bmapsId})` 로 B-Maps 화면에 탭을 끼운다
- 조회 API: `/addin/bmaps/api/v1/bridges/<교량관리번호>` (`/elements`, `/timeseries`, `/ifc[?enriched=1]`)
- 보안: `PONTIFEX_ADDIN_TOKEN`(조회 토큰), `PONTIFEX_ADDIN_ORIGINS`(CORS), `PONTIFEX_ADDIN_FRAME_ANCESTORS`(iframe 허용 출처) —
  prod 기본은 닫혀 있고 B-Maps 도메인을 지정해야 열린다.

기존 `examples/bmaps_tab/`(inframon `--serve-api` 직결)은 남겨 두지만, B-Maps 연동의 정식 경로는 이제 창고 Add-in 이다.

## 지역 전수 대장 (2026-10-16 추가)

강원 4,255개처럼 지역 전체를 위성 계산보다 먼저 서류 기준으로 정리한다.

```
python scripts/region_inventory.py --sido 강원 --out data/gangwon          # 교량 + 버스트 배정
python -m inframon --region-registry data/gangwon/bridges.csv \
       --region-registry-json data/gangwon/registry.json \
       --region-registry-push --pontifex-base http://127.0.0.1:38000
```

- 교량마다: 법정 종별(공식 코드 우선, 99=종외), 별표 3 다음 기한, **자료 기준일에 이미 지난 기한**,
  위성 관측 가능성(연장 ≥100 m 기대 · 50–100 m 제한적 · <50 m 원리상 어려움), 상승·하강 버스트.
- 기한 경과는 오늘이 아니라 공공데이터 **자료 기준일**(2025-12-31)로 판단한다. 오늘로 비교하면 그 뒤 점검이 빠져 거의 전부 '초과'가 된다.
- 계산 종별은 최대경간장이 없어 하한이다. 공식 종별이 계산보다 **낮을 때만** '종별 확인'으로 표시한다.
- 위성 계산을 마친 버스트는 `--region-registry-computed 054_114061_IW2 ...` 로 넘기면 그 교량이 '계산됨'이 된다.
- 코드: `src/inframon/legal/registry.py`, 창고 `backend/warehouse/registry.py`, 화면 `/warehouse/region/`.
