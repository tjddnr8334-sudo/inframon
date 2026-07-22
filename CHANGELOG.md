# Changelog

All notable changes to **inframon** are documented in this file. ·
inframon 의 주요 변경사항을 기록한다.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). ·
형식은 Keep a Changelog, 버전 규칙은 SemVer 를 따른다.

> ⚠️ Research prototype — outputs are pipeline results, not validated diagnoses (see README Status banner). ·
> 연구용 프로토타입 — 출력은 파이프라인 결과이지 검증된 진단이 아니다(README Status 배너 참조).

## [Unreleased]

### Added
- BIM / digital-twin alignment (`--bim-align`) — IFC local ↔ map CRS coordinate alignment via `IfcMapConversion` or a Helmert fit from surveyed control points (scale fixed to 1 by default, RMS-gated), point→element association with ambiguity resolution and explicit non-assignment, robust per-element aggregation, and an IFC Pset payload carrying current state plus source keys back to `project.h5`. Optional `ifcopenshell` adapter for reading/writing real IFC (`--bim-inspect`, `--bim-write-ifc`) — not yet verified against a real IFC file. · BIM/디지털 트윈 정합 — 좌표 정합·부재 연결·Pset. IFC I/O 는 선택 의존·미검증.
- Remaining-service-life estimation (`--remaining-life`, opt-in) — serviceability channel from InSAR displacement rates, with Theil–Sen robust regression, censoring of statistically insignificant trends, conservative lower bounds, differential-settlement (angular distortion) sub-limit, spatial-cohesion aggregation and a reported hotspot cluster; written to a new `/life` group (schema 1.3) plus a dashboard tab. The four engines and golden regression are unchanged. · 잔존수명 추정(opt-in) — 사용성 채널·강건회귀·검열·하한·부등침하·공간 응집·핫스팟, `/life` 기록 + 대시보드 탭.
- InSAR accuracy corrections wired into real ingest (`--insar-corrections`, opt-in) — common-mode/APS removal from high-coherence stable points (benefit-guarded), height-correlated stratified troposphere, and thermal-expansion separation (`--insar-thermal` with `--insar-temp-csv` or ERA5 `--insar-fetch-temp`); corrected rate stored at `/insar/velocity_mm_yr`. · InSAR 정확도 보정을 실 인제스트에 연결(opt-in·이득 가드) — 공통성분·고도상관·열팽창 + 보정 속도 저장.
- Real DEM `z` connection — `import_track_h5` samples a DEM GeoTIFF (`--insar-dem`) and the SARvey→inframon converter exports per-point elevation, so the height-correlated correction can engage. · DEM z 실연결 — DEM 샘플링 + 58 변환기 점별 고도 export.
- Support-zone (ZONE) monitoring mode — pier/abutment point extraction + settlement rate + dashboard, with support CLI. · 지지부(ZONE) 모니터링 모드 — 교각·교대 점 추출 + 침하 속도 + 대시보드(지지부 CLI 포함).
- Ascending+Descending dashboard enhancement — asc/desc UNION (more points) + vertical/EW decomposition. · 대시보드 Asc+Desc 강화 — 상승·하강 궤도 UNION(점 증가) + 연직/EW 분해.
- README: repository-structure and reproducibility sections (EN/KR). · README 디렉터리 구조 + 재현성 섹션(영/한).
- Citation systematization — `CITATION.cff` `date-released`/`url`, README "How to cite" BibTeX, GitHub "Cite this repository". · 인용 체계화.
- Risk map rendered over an OpenStreetMap basemap (contextily). · risk map 을 OSM 베이스맵 위에 표시.
- Data-availability advisor; Korean font for report figures. · 가용성 advisor · 리포트 한글 폰트.

### Changed
- Merged README into a single bilingual (English + 한국어) file. · README 이중언어 단일 파일 통합.
- `CITATION.cff` version `0.0.1` → `0.1.0` (aligned with badge and tag). · CITATION.cff 버전 통일.
- Preview honesty — synthetic demo labeled as such; sparse deck PS on real data made explicit. · 미리보기 정직화.

### Fixed
- **Remaining life compared LOS displacement against vertical limits.** Settlement (25 mm) and angular-distortion (1/500) limits are vertical quantities, but single-orbit InSAR measures only the line-of-sight component, so remaining life was optimistic by 1/cos θ — 29% at a 39° incidence. LOS is now projected to vertical using the incidence angle, which is preserved through ingest as `/insar/incidence_deg` (schema 1.4) instead of being consumed and discarded. Real Jeongja re-run: 1.7 yr → 1.2 yr. · 잔존수명이 연직 한계에 LOS 를 그대로 비교하던 오류 — 입사각 투영 추가(39°에서 29%), 입사각을 인제스트에서 보존.
- Ingest-applied thermal correction was invisible to the remaining-life estimator on the `--import-track-h5` path, which records provenance under `track_source` while the estimator read only `insar_source` — confidence was wrongly downgraded to "low". · 인제스트 열보정이 `--import-track-h5` 경로에서 무시되던 문제(출처 attr 이름 불일치).
- Dashboard InSAR tab crashed on project switch when a stale slider value exceeded the new project's epoch/point count. · 프로젝트 전환 시 슬라이더 범위 초과로 InSAR 탭이 크래시하던 문제.
- Dashboard tabs rendered a stray `None` — a conditional expression used as a statement, which Streamlit's magic displays. · 조건식을 문장으로 써서 탭 하단에 `None` 이 찍히던 문제.
- FRAM CRI heat map dominated the tab at large point counts — now collapsible, risk-sorted, band-downsampled (block max) and color-mapped. · CRI 히트맵이 탭을 압도하던 문제 — 접기·위험순 정렬·밴드 다운샘플·컬러맵.
- LOS heading unit correction. · heading 단위 보정.

## [0.1.0] - 2026-07-01

First public release (GPLv3). · 첫 공개 릴리스.

### Added
- **Pipeline** — InSAR → PINN → CV → FRAM four-engine integration; Pydantic + HDF5 data-contract skeleton; per-engine stub→real hot-swap (`--engine X=real`) protected by golden-regression tests. · 4엔진 통합·데이터 계약 골격·엔진 핫스왑 + 골든 회귀.
- **InSAR** — Track H5 ingest; asc/desc fusion & vertical-displacement separation; bridge-type SARvey configs; DEM elevation fallback (GLO-30); ERA5 master selection; pre-ingest validation (`--check-track`). · Track H5 인제스트·궤도 융합·형식별 config·DEM 폴백·ERA5 master·사전검증.
- **PINN** — component decomposition + Euler-Bernoulli PDE + absolute EI; bridge-type PDEs (girder/cable-stayed/arch/suspension); temperature & traffic exogenous auto-collection (Open-Meteo / public API); custom orchestration (`--custom-pinn`). · 성분분해·형식별 PDE·외생 자동수집·맞춤 오케스트레이션.
- **FRAM** — pointwise resonance; 6-facet function network (N-K); Composite Resonance Index (CRI); alerts, correction probability, lead-time forecast; FastAPI real-time serving (`--serve`). · 점별 공명·6측면 함수망·CRI·경보·실시간 서빙.
- **Dashboard** — Streamlit FRAM/PINN/InSAR tabs; OSM-basemap risk map; desktop app (PyInstaller + pywebview). · Streamlit 탭·OSM risk map·데스크톱 앱.
- **Handoff** — KAIA CSV export; VLM input package; Bmaps REST API tab. · KAIA CSV·VLM 패키지·Bmaps API.
- **Ops** — environment doctor (`--doctor`); Prefect monitoring schedule (`--schedule`); incremental resume. · 환경 진단·스케줄·증분 재개.
- **Release prep** — GPLv3 `LICENSE`; `NOTICE` attribution (SARvey · MiaplPy · MintPy · ISCE2); `CITATION.cff`; `CONTRIBUTING`; CI (pytest, Python 3.11); GitHub Pages landing; Status & Limitations honesty banner; preview figures/GIF. · 공개 준비 일체.

### Fixed
- Bmaps API over-reporting displacement by 1000× (`los_ds` is already in mm). · Bmaps API 변위 단위 1000배 과대보고 수정.

[Unreleased]: https://github.com/tjddnr8334-sudo/inframon/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tjddnr8334-sudo/inframon/releases/tag/v0.1.0
