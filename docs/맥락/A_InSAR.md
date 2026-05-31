# A구간 — InSAR · 변위 시계열 추출 · 상태: 선별 A~E + 인제스트 REAL / 코어 처리(F)는 WSL2

> ROI 기반으로 측정점의 변위 시계열(LOS·종방향)을 추출. **유일한 실데이터 원천**이라 하류(PINN/FRAM) 품질을 좌우한다.
> 공통 계약: [00_공통_계약과_파이프라인.md](00_공통_계약과_파이프라인.md) · 전체: [`../개발_맥락_맵.md`](../개발_맥락_맵.md) §5.3 · F 실행: [`../F_SARvey_WSL2.md`](../F_SARvey_WSL2.md)

## 역할
CV의 ROI/격자 가이드를 받아 각 측정점의 **LOS·종방향 변위 시계열**을 산출. PINN·FRAM의 입력 데이터를 제공.

## 입출력 계약 (변경 없음 — stub/real 공통)
- **공개 API**: `run_insar(store, cv, cfg) -> InSAROutput`(stub) · `run_insar_real(store, cv, cfg) -> InSAROutput`(real)
- **출력(InSAROutput)**: `los[N,M]`, `longitudinal[N,M]`, `xyz`, `member`, `dates`, `coherence`, `temporal_coherence` (real 은 `date_labels`도 기록)
- **하류**: PINN ← `longitudinal`, `dates`, `member` / FRAM ← `longitudinal`, `xyz`, `member`, `dates`, `los`
- 핫스왑: `orchestrator.engines` 가 `("insar","real")` 등록 → `--engine insar=real` 로 켠다.

---

## InSAR 실데이터 경로 — 전체 그림 (A~G)

무거운 SAR 처리(SLC→시계열)는 Windows 네이티브 불가 → **오프라인(WSL2/Linux)에서 SARvey**로 돌리고 inframon 은 그 **앞(선별)과 뒤(인제스트)**를 담당한다.

```
[Windows/inframon]                         [WSL2/Linux]                  [Windows/inframon]
A·B 교량(OSM) → C·D SLC·트랙(ASF) → E ERA5 master   F SARvey(SLC→시계열)        G 인제스트
        └─────── 레시피 4종 + SARvey 번들 2종 ───────▶  → Track H5 ──────────▶  run_insar_real
```

| 단계 | 모듈 | 상태 | 산출 |
|---|---|---|---|
| **A·B** 교량 타깃(지도+OSM 확인) | `insar/osm_bridge.py`, `recipe.BridgeTarget` | ✅ | `bridge_target.json`(bbox) |
| 선별 기준(공간 baseline ≤150m·VV·트랙) | `recipe.SelectionCriteria` | ✅ | `selection_criteria.json` |
| **C·D** SLC 검색·트랙 선별(ASF) | `insar/slc_search.py`, `recipe.TrackSelection` | ✅ | `track_selection.json` |
| **E** ERA5 master 선정(강수·습도) | `insar/era5_master.py`, `recipe.MasterSelection` | ✅ | `master_selection_era5.json` |
| **F준비** SARvey 번들 생성 | `insar/sarvey_config.py` | ✅ | `processing_manifest.json` + `sarvey_config.json` |
| **F** SARvey 처리(다운로드→ISCE2→MiaplPy→SARvey) | `scripts/wsl_sarvey/*` | 🟡 템플릿(WSL2 실행) | SARvey ts.h5 → Track H5 |
| **G** 인제스트 | `insar/real_engine.py`, `track_reader.py` | ✅ | `/insar` 계약 |

### A·B — 교량 타깃 (`osm_bridge.py`)
한국 지도에서 위치 선택 → Overpass API 로 `bridge` 요소 조회·확인 → `BridgeTarget`(이름/bbox/길이/OSM id). bbox 가 SLC 검색 영역. 키 불필요(stdlib urllib).

### C·D — SLC 검색·트랙 선별 (`slc_search.py`)
`search_slc(bbox, …, polarization="VV")` → ASF Sentinel-1 SLC 검색(VV 필터) → `select_track` 가 (궤도방향, path, frame)별 집계해 **취득 최다 트랙** 선택. `asf_search` 옵션 의존성(`.[search]`), 검색은 로그인 불필요. 네트워크는 `_asf_geo_search` 격리.

### E — ERA5 master 선정 (`era5_master.py`)
선별 트랙 취득일별 ERA5(총 강수·평균 상대습도)를 받아 `score = w_p·강수 + w_h·습도` 최소(=가장 건조·저습 → 대기지연 최소)인 날을 master 로. **소스: Open-Meteo ERA5 archive(키 불필요)**. `selected_master` 키는 `inventory.py` 의 `master_selection_era5.json` 과 호환.

### F준비 — SARvey 번들 (`sarvey_config.py`)
레시피 4종 → 두 파일: **`processing_manifest.json`**(상류 스택 생성: 궤도/track/frame/VV/**reference=master**/장면/bbox/공간 baseline) + **`sarvey_config.json`**(SARvey MTI 시계열 추정). 트랙/master/baseline 은 SARvey 자체 설정이 아니라 그 앞단(ISCE2/MiaplPy)을 좌우하므로 분리. CLI: `--make-sarvey-config <recipe_dir>`.

### F — SARvey 처리 (`scripts/wsl_sarvey/`)
WSL2 템플릿: `10_download`(SLC/궤도/DEM) → `20_stack_isce`(stackSentinel) → `30_miaplpy`(load_data) → `40_sarvey`(MTI) → `50_export_to_inframon.py`(✅검증됨, SARvey ts → Track H5). 도구 CLI 는 버전차가 커서 `# TODO(verify)` 표시. 자세히: [`../F_SARvey_WSL2.md`](../F_SARvey_WSL2.md).

### G — 인제스트 (`real_engine.py` / `track_reader.py`)
- `import_track_h5`(CLI `--import-track-h5`) — Track H5 → `/insar` 직접 적재(빠른 변환).
- `run_insar_real`(`--engine insar=real --insar-source <h5>`) — Track H5 를 읽어 **CV 픽셀 프레임 정합 + ROI 필터 + 부재 라벨 + 기하 분해** 후 `/insar` 계약. **1차 증분 가정: H5 점 좌표를 CV 픽셀로 간주(identity)** — 실 CV 결합 시 geo→pixel affine 으로 교체.

### inventory (`inventory.py`) — 실데이터 사전 점검(REAL)
`inspect_insar_data(root)` / `build_scene_manifest` — SLC zip/궤도/DEM/`master_selection*.json`/`bperp_filter.json`/`exclude_dates.txt` 파싱. CLI `--inspect-data`, `--ingest-data`.

---

## 교체 시 보존할 계약
- `InSAROutput`의 `los`/`longitudinal` `[N,M]`(N=점, M=시점) 차원·의미 유지. `dates`/`member`/`xyz` 유지(PINN/FRAM 의존).
- real 엔진은 계약을 그대로 채우므로 stub↔real 교체가 PINN/FRAM 에 무영향(골든 회귀로 보호).

## 이 엔진 고유 리스크
- ⚠️ **좌표 정합 seam**: 1차 증분은 Track H5 점을 **CV 픽셀 프레임으로 간주(identity)**. 실 CV(Phase 2) 결합 시 geo(lon/lat)→pixel affine 등록 필요(매니페스트 bbox 기준). ROI 밖 점은 제거(제거 수 로그).
- **F 도구 버전 의존**: `stackSentinel.py`/`miaplpyApp.py`/`sarvey` 옵션·스텝명이 버전마다 다름 → `TODO(verify)`.
- **ISCE2/MiaplPy/SARvey Windows 네이티브 불가** → WSL2/Linux. 디스크 수십~수백 GB, 처리 수 시간~하루.
- 공간 baseline ≤150m 는 SARvey config 가 아니라 스택/네트워크(MiaplPy/ISCE) 단계에서 적용.
- `coherence`/`temporal_coherence` 가 stub 에서 동일 배열 중복 — 실데이터(Track H5)는 분리된 값 사용.

## 핵심 함수/CLI 요약
- **F 통합(프로그램 진입점)**: `insar/processing.py` — `run(recipe, mode='demo'|'real')`. CLI `--insar-process demo|real --recipe <dir>`. **demo**=합성 시계열로 어디서나 end-to-end(/insar→PINN→FRAM), **real**=Linux/WSL 단계 plan. ⚠️ 사용자 실데이터(`/home/insar/insar_data`)·기존 파이프라인 사용 안 함.
- 선별: `osm_bridge.find_bridges_near` · `slc_search.search_slc/select_track` · `era5_master.select_master`
- 번들: `sarvey_config.write_sarvey_bundle` (CLI `--make-sarvey-config`)
- 인제스트: `track_reader.import_track_h5` (CLI `--import-track-h5`) · `real_engine.run_insar_real` (`--engine insar=real --insar-source`)
- 대시보드: ① InSAR 탭 — 🗺️교량 → ⚙️선별기준 → 🛰️SLC → 🌧️ERA5 master → 🧩SARvey 번들
- 레시피 산출 위치: `data/insar_recipe/`
