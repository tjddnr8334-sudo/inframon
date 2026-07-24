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
- Dashboard pipeline-progress badges and a remaining-life What-if panel — the header now shows which engines have run (`✅① InSAR · ② PINN · ③ FRAM · ④ 잔존수명`) and points at the next step, so a user can see how far the data got. The remaining-life tab gains sliders for the serviceability limits (settlement, deflection, angular distortion, prior displacement) that recompute the answer live **without touching `project.h5`** (`estimate_remaining_life(..., write=False)`), show the delta vs the saved result and which sub-limit governs, and print the CLI command to make it permanent. · 대시보드 진행 배지 + 잔존수명 파라미터 What-if(파일 미수정 미리보기).
- Step-by-step testing guide (`docs/테스트_가이드.md`) with a clickable run-order flowchart (Mermaid) — installation → build `project.h5` → dashboard/GNSS/BIM/remaining-life, each box linking to its section; covers the dashboard, direct CLI, and bringing your own IFC/SAR data. · 테스트 가이드 — 클릭 가능한 실행 순서 순서도.
- BIM / digital-twin alignment (`--bim-align`) — IFC local ↔ map CRS coordinate alignment via `IfcMapConversion` or a Helmert fit from surveyed control points (scale fixed to 1 by default, RMS-gated), point→element association with ambiguity resolution and explicit non-assignment, robust per-element aggregation, and an IFC Pset payload carrying current state plus source keys back to `project.h5`. Optional `ifcopenshell` adapter (`.[bim]` extra) for reading and writing real IFC (`--bim-inspect`, `--bim-write-ifc`), verified by a round-trip test that builds a bridge IFC, reads its georeferencing and element bounding boxes back, and injects Psets. · BIM/디지털 트윈 정합 — 좌표 정합·부재 연결·Pset. IFC I/O 는 `.[bim]` 선택 의존, IFC 왕복 테스트로 검증.
- AOI extension to enclose GNSS stations (`--gnss-extend-aoi`) plus co-located validation — widening the processing footprint to cover a station puts InSAR points *at* it, turning validation from a bridge-area median compared against a station kilometres away into a point-to-point comparison, and allowing the reference point to sit on the station. The added processing area is quantified before committing (area, pixel counts, ratio) and stations are skipped rather than silently exceeding the budget. `validate_insar_vs_gnss` now uses points within 500 m of a station when they exist and labels the comparison `co-located` vs `regional`. · AOI 를 GNSS 관측소까지 확장 + 공동위치 대조 — 지역 대조를 점 대 점 대조로.
- `--bim-inspect` is now a readiness gate for real BIM exports — it reports the schema, **length unit** (millimetre models are common and a wrong unit fails silently at 1000×), georeferencing, how many elements carry geometry vs fall back to placement, how many map to a member type, and a verdict listing exactly what must still be supplied. IFC4.3 bridge types (`IfcBridgePart`, `IfcDeepFoundation`, `IfcCaisson`) map to members, and `IfcSite` latitude/longitude is surfaced as a location hint when `IfcMapConversion` is absent. · `--bim-inspect` 를 실 IFC 투입 준비도 판정으로 — 단위·지오레퍼런싱·형상 보유율·부재 추론율과 판정.
- BIM data-requirements guide (`docs/BIM_필요데이터.md`) — what to supply for IFC integration: the IFC file, and survey control points when it lacks `IfcMapConversion`; element-table fallback when no IFC can be shared. · BIM 연계 필요 데이터 문서.
- GNSS reference anchor as ground evidence for SLC processing (`--gnss-anchor`, `--make-sarvey-config --gnss-anchor-km`) — nearby NGL continuous-GNSS stations are scored by record length, vertical stability, scatter and distance, and the best one is carried into `processing_manifest.json` as `gnss_reference` so reference-point selection rests on observation rather than a per-bridge-type heuristic. An absolute tie is only permitted when a station lies inside the InSAR footprint (≤2 km); beyond that the anchor is regional datum context and the tie is refused. A lookup failure never blocks bundle generation. · GNSS 기준앵커 — SLC 처리 레시피에 기준점 선정의 지상 근거를 싣는다(발자국 밖이면 절대 타이 거부).
- Stiffness-degradation channel for remaining life (P2) — PINN now keeps its per-epoch absolute-EI identification as `EI_series` (schema 1.5) instead of averaging the time axis away, and the channel regresses `log EI(t)` for time-to-`EI/EI₀ < 0.8`, behind four gates (identification saturation, instability, observation length, significance). Verified against both the synthetic demo and the real Jeongja track: `EI` saturates at its clip bound on both, so the channel correctly stays **inactive with the reason stated** rather than fitting a numerical artefact. · 강성열화 채널 — PINN 시간분해 EI + 4겹 게이트. 실 데이터로 확인 결과 EI 포화라 비활성(사유 명시).
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
- **GNSS validation judged on an invalid comparison.** It subtracted absolute GNSS LOS velocities — which include ~30 mm/yr of Korean plate motion — from the *relative* InSAR LOS and reported the difference as a pass/fail, so a sound result read as "⚠️ deviation large" (22.8 mm/yr RMS on the real Jeongja track). The verdict now uses the vertical component, which plate motion barely touches; LOS residuals remain visible but labelled as frame-affected, and vertical MAD outliers (residual equipment steps) are excluded and listed. Same data now reports a 0.30 mm/yr vertical residual RMS against three stations — the project's first agreement with ground truth. · GNSS 검증이 절대 GNSS LOS 와 상대 InSAR LOS 를 직접 빼서 판정하던 오류 — 판정을 플레이트-무관 수직으로 이동(정자교 실측 22.8 → 0.30 mm/yr).
- IFC placement fallback ignored the placement chain — an element without geometry was read at its `RelativePlacement` only, so on a model whose site placement carries a survey origin the element landed kilometres away. It now resolves the full chain and applies the project length-unit scale (millimetre models are common). · 형상 없는 부재의 배치 폴백이 부모 체인을 무시해 수 km 어긋나던 문제 + 단위 배율 적용.
- BIM association: the alignment tolerance could steal points already strictly inside another element — a deck point at the deck/pier interface (deck soffit and pier top touch) was assigned to the pier. Strict containment now outranks tolerance-assisted containment, and `inside` reports strict containment rather than the expanded box. · 허용오차가 이미 부재 안에 있는 점을 뺏던 문제(상판/교각 접촉면).
- BIM Pset injection wrote counts as `IfcReal` (`13.0`) and accumulated duplicate same-named Psets on re-run; now `IfcInteger` and replace-in-place. `--bim-write-ifc` also lost the source IFC path when elements were read from the IFC. · Pset 정수 타입·재주입 누적·원본 경로 유실 수정.
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
