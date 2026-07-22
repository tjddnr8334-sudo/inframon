# inframon — Bridge Infrastructure Monitoring Platform · 교량 인프라 모니터링 플랫폼

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-GPLv3-green)
![Version](https://img.shields.io/badge/version-0.1.0-orange)
![Status](https://img.shields.io/badge/status-research%20prototype-yellow)

**[English](#english) · [한국어](#한국어)**

> ⚠️ **Research prototype / 연구용 프로토타입.** Full pipeline + analytic PINN/FEM validation
> (error < 0.1%) demonstrated; field / commercial-FEM / real-failure-label validation **not yet done**.
> Outputs are pipeline results, **not** validated diagnoses — do not use for operational safety decisions. ·
> 전 파이프라인·해석해 검증은 실증됨, 현장·상용FEM·실 붕괴라벨 검증은 미수행 — **실무 안전 판정용 아님.**

**Concept — points ON a bridge deck (synthetic demo) / 개념 — 교량 데크 위 점 (합성 데모):**

![Bridge-deck monitoring points, CRI over time (synthetic)](docs/img/demo_bridge.gif)

*Illustrative **synthetic** demo — monitoring points along a modeled bridge deck (deck·pier·abutment),
CRI 🟢→🔴 over time. **Not real InSAR.** · 모델 교량 데크 위 점의 CRI 시간변화(합성, 실측 아님).*

**Real data (Jeongja Br., Sentinel-1) on OpenStreetMap / 실 데이터 (정자교) OSM 위:**

![Real LOS velocity on OSM](docs/img/velocity_map.png)

*Real SARvey result — PS/DS points sit on **surrounding buildings**; a smooth bridge deck over a river
has **few natural scatterers**, so deck points are sparse (needs corner reflectors / high-res SAR — see
Status). · 실 결과: 점이 주변 건물에 분포, 매끈한 교량 데크엔 산란체가 적어 데크 점은 희소(코너리플렉터/고해상도 SAR 필요).*

![Result overview](docs/img/overview.png)

![Dashboard — FRAM](docs/img/dashboard_fram.png)
![Dashboard — PINN](docs/img/dashboard_pinn.png)
![Dashboard — InSAR](docs/img/dashboard_insar.png)

*Real Jeongja Bridge Sentinel-1 result (1,072 pts × 201 epochs) — dashboard FRAM / PINN / InSAR tabs. ·
정자교 실 Sentinel-1 결과 — 대시보드 FRAM/PINN/InSAR 탭. Regenerate: `scripts/{make_readme_media,capture_dashboard}.py`.*

---

## English

inframon chains four engines into one pipeline to monitor bridge safety from satellite SAR:
**InSAR** extracts displacement time series, **PINN** recovers structural meaning (physics),
**CV** auto-delineates the measurement region, and **FRAM** diagnoses system safety via a Composite
Resonance Index (CRI).

### Architecture

Data contracts (Pydantic + HDF5) and the skeleton are **sacred**; each engine is swapped stub→real
independently (hot-swap `--engine X=real`), protected by golden-regression tests.

```
CV    → ROI / member segmentation / axis / georeference   (cv/{engine,real_engine}.py)   STUB + REAL
InSAR → Track H5 → CV registration · world xyz · series    (insar/{engine,real_engine}.py) STUB + REAL
PINN  → decomposition + Euler-Bernoulli PDE + absolute EI   (pinn/{engine,real_engine}.py)  STUB + REAL
FRAM  → pointwise resonance · function net (N-K) · CRI      (fram/{engine,real_engine}.py)  STUB + REAL
```

### Repository structure

```
inframon/
├── src/inframon/          # main package (installable: pip install -e .)
│   ├── contracts/         # Pydantic + HDF5 data contract — the "sacred" schema (schema, io, array_schema)
│   ├── cv/                # CV engine: ROI / segmentation / axis / georeference        (engine + real_engine)
│   ├── insar/             # InSAR engine: Track H5 → world xyz series, DEM, ERA5 master, fusion (engine + real_engine)
│   ├── pinn/              # PINN engine: decomposition + Euler-Bernoulli PDE + absolute EI (engine + real_engine, pde)
│   ├── fram/              # FRAM engine: pointwise resonance · function net (N-K) · CRI (engine + real_engine, network)
│   ├── orchestrator/      # pipeline wiring · hot-swap engine registry · incremental resume
│   ├── api/               # FastAPI service (--serve) + engine registry / transform
│   ├── dashboard/         # Streamlit app (FRAM / PINN / InSAR tabs)
│   ├── __main__.py        # CLI entry — --demo / --doctor / --check-track / --engine X=real
│   └── config.py · doctor.py · export.py · geotransform.py · schedule.py · weather.py · traffic.py …
├── tests/                 # 43 test files (304 tests) — golden regression, contract validation, per-engine *_real
├── docs/                  # design & context docs (KR) + GitHub Pages landing (index.html)
├── scripts/               # SLC download, WSL2 SARvey runners, dashboard / media capture
├── examples/              # runnable examples (bmaps_tab)
├── .github/workflows/     # CI — tests.yml (pytest on Python 3.11)
├── CITATION.cff · LICENSE (GPLv3) · NOTICE.md · CONTRIBUTING.md
└── pyproject.toml · environment.yml
```

> **System overview** — architecture, BMAPS synchronization, and originality vs. the underlying
> InSAR tools are summarized in [`docs/시스템_개요.md`](docs/시스템_개요.md).

### Quick start

```bash
pip install -e ".[dev]"
python -m inframon --doctor     # environment / readiness check
python -m inframon --demo       # full pipeline (stubs) → data/project.h5 + CRI
pytest -q                       # tests
pip install -e ".[dashboard]" && streamlit run src/inframon/dashboard/app.py   # dashboard
```

### Real data (real Sentinel-1 → CRI)

Heavy SAR processing (ISCE2 / MiaplPy / SARvey) runs on **WSL2/Linux**; selection, ingest, analysis
and visualization run on Windows. See `docs/실데이터_런북.md`.

```bash
python -m inframon --check-track track.h5      # pre-ingest validation (exit 0 = ready)
python -m inframon --demo --insar-source track.h5 --out data/project.h5 \
  --engine cv=real --engine insar=real --engine pinn=real --engine fram=real
```

**Accuracy corrections (opt-in).** `--insar-corrections` runs three steps on the LOS series
*before* PINN/FRAM consume it, and stores the corrected rate at `/insar/velocity_mm_yr`:

```bash
python -m inframon --import-track-h5 track.h5 --out data/project.h5 \
  --insar-corrections --insar-dem dem.tif \
  --insar-thermal --insar-temp-csv temps.csv    # or --insar-fetch-temp (ERA5, no key)
```

The same flags apply to the full chain (`--demo --engine insar=real --insar-source track.h5 …`),
but that path registers Track coordinates against the CV frame, so it needs a geo-referenced CV
input (GeoTIFF / override) — without one the points fall outside the ROI. `--import-track-h5`
is the verified route for a bare SARvey/MintPy product.

| Step | What it removes | Requires | Notes |
|---|---|---|---|
| Common-mode (APS) | epoch-wise atmospheric phase, as the median of high-coherence stable points | `coherence` | `--ref-min-coherence` (default 0.9). **Benefit-guarded**: kept only if temporal std actually drops — SARvey already APS-filters, so it self-skips there |
| Height-correlated | stratified troposphere ∝ elevation | point `z` (Track `height`, or `--insar-dem` GeoTIFF) | skipped when the z spread < 1 m |
| Thermal | seasonal expansion via `los = a + b·t + c·T` | `--insar-thermal` + a temperature source | CSV (`date,temp_C`) is deterministic and preferred; `--insar-fetch-temp` pulls ERA5 (Open-Meteo, no API key) |

Every step is opt-in and logs what it applied/skipped into the `insar_source` attribute, so the
default and golden-regression paths are unchanged.

### Reproducibility

| Aspect | Detail |
|---|---|
| Language / runtime | Python ≥ 3.11 (CI runs on 3.11) |
| Core deps | `numpy≥1.26` · `h5py≥3.10` · `pydantic≥2.6` — the stub demo runs on these alone |
| Heavy deps (optional) | `torch≥2.2` (PINN/CV) · `transformers≥4.40` (CV) · `mintpy`/`rasterio`/`gdal` (InSAR) · `streamlit`/`plotly`/`folium` (dashboard) — see `pyproject.toml` extras |
| InSAR toolchain | SARvey (default) · MiaplPy · MintPy · ISCE2 — run on **WSL2/Linux**, invoked via CLI (`docs/F_SARvey_WSL2.md`) |
| Data sources | Sentinel-1 SLC (ESA/Copernicus via ASF) · Copernicus GLO-30 DEM · ERA5 (Open-Meteo) · OpenStreetMap (ODbL) — see [`NOTICE.md`](NOTICE.md) |
| Deterministic demo | `python -m inframon --demo` → `data/project.h5` + CRI, no network/GPU, fixed seeds; numerics locked by golden-regression tests |
| Real case study | Jeongja Bridge, Sentinel-1 — **1,072 points × 201 epochs** |
| Analytic validation | PINN/FEM vs closed-form Euler-Bernoulli: **error < 0.1%** (`tests/test_pinn_real.py`, `tests/test_benchmark.py`) |
| Environment capture | `python -m inframon --doctor` reports versions/readiness; `environment.yml` pins the conda env |

Reproduce the demo end-to-end:

```bash
pip install -e ".[dev]"
python -m inframon --doctor            # environment / readiness report
python -m inframon --demo --out data/project.h5
pytest -q                              # golden regression confirms numerics are unchanged
```

**Not yet reproducible/validated:** field measurements, commercial-FEM cross-check, and real
failure-label evaluation are *not* done — outputs are pipeline results, not diagnoses (see the Status
banner at the top).

### Underlying InSAR tools · Attribution

inframon **invokes** these tools as its InSAR engine (installed separately, WSL2/Linux) via CLI —
it does not embed their source.

- **SARvey** (default InSAR engine, PS/DS) — https://github.com/luhipi/sarvey · GPLv3
- **MiaplPy** (phase linking / SLC stack) — https://github.com/insarlab/MiaplPy · GPLv3
- **MintPy** (InSAR time series) — https://github.com/insarlab/MintPy · GPLv3
- **ISCE2** (Sentinel-1 topsStack coregistration) — https://github.com/isce-framework/isce2

The InSAR engine is **pluggable** (SARvey default; MiaplPy/MintPy/StaMPS adapters provided).
inframon's own contribution is the **selection (OSM·ASF·ERA5), PINN structural analysis, FRAM
resonance, and dashboard/validation/accuracy** layers. Data: Sentinel-1 (ASF) · GLO-30 DEM · ERA5
(Open-Meteo) · OpenStreetMap (ODbL). See [`NOTICE.md`](NOTICE.md).

### How to cite

If you use inframon in academic work, please cite it **and the underlying tools (especially
SARvey)**. GitHub's **"Cite this repository"** button (built from [`CITATION.cff`](CITATION.cff))
exports APA/BibTeX automatically. Ready-to-paste BibTeX:

```bibtex
@software{inframon_2026,
  title   = {inframon: Bridge Infrastructure Monitoring Platform (Sentinel-1 InSAR $\to$ PINN $\to$ FRAM CRI)},
  author  = {{inframon contributors}},
  year    = {2026},
  version = {0.1.0},
  url     = {https://github.com/tjddnr8334-sudo/inframon},
  note    = {Software}
}
```

### License

**GPLv3** ([`LICENSE`](LICENSE)); contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md).
Data sources & terms: [`NOTICE.md`](NOTICE.md). Version history: [`CHANGELOG.md`](CHANGELOG.md).

---

## 한국어

inframon 은 InSAR · PINN · CV · FRAM 네 엔진을 하나의 파이프라인으로 묶어 위성 SAR 로 교량을
모니터링한다: **InSAR**(변위 시계열) → **PINN**(구조 물리 해석) → **CV**(측정 영역 자동 산정) →
**FRAM**(공명 위험 지수 CRI 로 시스템 안전 진단).

설계/맥락 문서: [`docs/개발_맥락_맵.md`](docs/개발_맥락_맵.md) · [`docs/맥락/`](docs/맥락/README.md)

### 구조

데이터 계약(Pydantic + HDF5)과 골격은 **성역**이고, 각 엔진을 stub→real 로 독립 교체(핫스왑
`--engine X=real`)하며 골든 회귀가 계약·수치를 보호한다.

```
CV    → ROI/부재 분할·축선·지오레퍼런스        (cv/{engine,real_engine}.py)   STUB + REAL
InSAR → Track H5 → CV 정합·world xyz·변위 시계열 (insar/{engine,real_engine}.py) STUB + REAL
PINN  → 성분분해 + Euler-Bernoulli PDE + 절대 EI  (pinn/{engine,real_engine}.py)  STUB + REAL
FRAM  → 점별 공명·함수망(N-K)·CRI + 경보·보정확률  (fram/{engine,real_engine}.py)  STUB + REAL
```

### 디렉터리 구조

```
inframon/
├── src/inframon/          # 메인 패키지 (설치: pip install -e .)
│   ├── contracts/         # Pydantic + HDF5 데이터 계약 — "성역" 스키마 (schema, io, array_schema)
│   ├── cv/                # CV 엔진: ROI/분할/축선/지오레퍼런스                 (engine + real_engine)
│   ├── insar/             # InSAR 엔진: Track H5 → world xyz 시계열, DEM, ERA5 master, 융합 (engine + real_engine)
│   ├── pinn/              # PINN 엔진: 성분분해 + Euler-Bernoulli PDE + 절대 EI (engine + real_engine, pde)
│   ├── fram/              # FRAM 엔진: 점별 공명 · 함수망(N-K) · CRI          (engine + real_engine, network)
│   ├── orchestrator/      # 파이프라인 배선 · 핫스왑 엔진 레지스트리 · 증분 재개
│   ├── api/               # FastAPI 서비스(--serve) + 엔진 레지스트리/변환
│   ├── dashboard/         # Streamlit 앱 (FRAM / PINN / InSAR 탭)
│   ├── __main__.py        # CLI 진입점 — --demo / --doctor / --check-track / --engine X=real
│   └── config.py · doctor.py · export.py · geotransform.py · schedule.py · weather.py · traffic.py …
├── tests/                 # 테스트 파일 43개 (테스트 304개) — 골든 회귀, 계약 검증, 엔진별 *_real
├── docs/                  # 설계·맥락 문서(한글) + GitHub Pages 랜딩(index.html)
├── scripts/               # SLC 다운로드, WSL2 SARvey 러너, 대시보드/미디어 캡처
├── examples/              # 실행 예제 (bmaps_tab)
├── .github/workflows/     # CI — tests.yml (Python 3.11 pytest)
├── CITATION.cff · LICENSE (GPLv3) · NOTICE.md · CONTRIBUTING.md
└── pyproject.toml · environment.yml
```

> **시스템 개요** — 체계 구조 · BMAPS 동기화 · 기반 도구 대비 독자성을
> [`docs/시스템_개요.md`](docs/시스템_개요.md) 에 정리.

### 빠른 시작

```bash
pip install -e ".[dev]"
python -m inframon --doctor     # 환경·준비도 진단
python -m inframon --demo       # 전체 파이프라인(stub) → data/project.h5 + CRI
pytest -q                       # 테스트
pip install -e ".[dashboard]" && streamlit run src/inframon/dashboard/app.py   # 대시보드
```

### 실데이터 (실 Sentinel-1 → CRI)

무거운 SAR 처리(ISCE2/MiaplPy/SARvey)는 **WSL2/Linux**, 선별·인제스트·해석·시각화는 Windows.
절차는 [`docs/실데이터_런북.md`](docs/실데이터_런북.md).

```bash
python -m inframon --check-track track.h5      # 투입 전 사전검증 (exit 0 = 가능)
python -m inframon --demo --insar-source track.h5 --out data/project.h5 \
  --engine cv=real --engine insar=real --engine pinn=real --engine fram=real
```

**정확도 보정(opt-in).** `--insar-corrections` 는 PINN/FRAM 이 소비하기 **전에** LOS 시계열에
3단계 보정을 적용하고, 보정된 속도를 `/insar/velocity_mm_yr` 에 저장한다.

```bash
python -m inframon --import-track-h5 track.h5 --out data/project.h5 \
  --insar-corrections --insar-dem dem.tif \
  --insar-thermal --insar-temp-csv temps.csv    # 또는 --insar-fetch-temp (ERA5, 키 불필요)
```

같은 플래그가 전체 체인(`--demo --engine insar=real --insar-source track.h5 …`)에도 적용되지만,
그 경로는 Track 좌표를 CV 프레임에 정합하므로 **지오참조된 CV 입력**(GeoTIFF/override)이 필요하다
— 없으면 점이 ROI 밖으로 떨어진다. 맨 SARvey/MintPy 산출물은 `--import-track-h5` 가 검증된 경로다.

| 단계 | 제거 대상 | 필요 | 비고 |
|---|---|---|---|
| 공통성분(APS) | 에폭별 대기위상 — 고결맞음 안정점의 **중앙값** | `coherence` | `--ref-min-coherence`(기본 0.9). **이득 가드**: 시간변동이 실제로 줄 때만 채택 — SARvey 출력은 이미 APS 필터링돼 자동 skip |
| 고도상관 | 고도에 비례하는 성층 대류권 | 점별 z (Track `height` 또는 `--insar-dem` GeoTIFF) | z 스프레드 < 1 m 이면 skip |
| 열팽창 | `los = a + b·t + c·T` 회귀로 계절 열변형 분리 | `--insar-thermal` + 온도원 | CSV(`date,temp_C`)가 결정론적이라 우선, 없으면 `--insar-fetch-temp` 로 ERA5(Open-Meteo) 조회 |

모두 opt-in 이고 적용/skip 내역을 `insar_source` attr 에 남긴다 — 기본 경로와 골든 회귀는 불변.

### 재현성

| 항목 | 내용 |
|---|---|
| 언어/런타임 | Python ≥ 3.11 (CI 3.11) |
| 코어 의존성 | `numpy≥1.26` · `h5py≥3.10` · `pydantic≥2.6` — stub 데모는 이것만으로 실행 |
| 무거운 의존성(선택) | `torch≥2.2`(PINN/CV) · `transformers≥4.40`(CV) · `mintpy`/`rasterio`/`gdal`(InSAR) · `streamlit`/`plotly`/`folium`(대시보드) — `pyproject.toml` extras |
| InSAR 툴체인 | SARvey(기본) · MiaplPy · MintPy · ISCE2 — **WSL2/Linux**에서 CLI 호출 (`docs/F_SARvey_WSL2.md`) |
| 데이터 출처 | Sentinel-1 SLC(ESA/Copernicus, ASF) · Copernicus GLO-30 DEM · ERA5(Open-Meteo) · OpenStreetMap(ODbL) — [`NOTICE.md`](NOTICE.md) |
| 결정론적 데모 | `python -m inframon --demo` → `data/project.h5` + CRI, 네트워크/GPU 불필요·고정 시드, 골든 회귀로 수치 고정 |
| 실 사례 | 정자교, Sentinel-1 — **1,072점 × 201에폭** |
| 해석해 검증 | PINN/FEM vs Euler-Bernoulli 해석해: **오차 < 0.1%** (`tests/test_pinn_real.py`, `tests/test_benchmark.py`) |
| 환경 기록 | `python -m inframon --doctor` 로 버전·준비도 출력, `environment.yml` 로 conda 환경 고정 |

데모 전체 재현:

```bash
pip install -e ".[dev]"
python -m inframon --doctor            # 환경·준비도 리포트
python -m inframon --demo --out data/project.h5
pytest -q                              # 골든 회귀로 수치 불변 확인
```

**아직 재현·검증 안 됨:** 현장 실측 · 상용 FEM 교차검증 · 실 붕괴라벨 평가는 미수행 — 출력은
파이프라인 결과이지 진단이 아니다(상단 Status 배너 참조).

### 기반 InSAR 도구 · 귀속

inframon 은 아래 도구를 **엔진으로 CLI 호출**한다(별도 설치, 소스 미포함): **SARvey**(기본,
GPLv3)·**MiaplPy**·**MintPy**·**ISCE2**. InSAR 엔진은 **플러그블**(SARvey 기본, 어댑터 제공)이고,
inframon 고유 기여는 **선별(OSM·ASF·ERA5)·PINN·FRAM·대시보드/검증/정확도** 계층이다.
데이터 출처·약관은 [`NOTICE.md`](NOTICE.md).

### 인용 방법

논문·학술 자료에 inframon 을 사용하면 inframon 과 **기반 도구(특히 SARvey)를 함께 인용**한다.
GitHub 저장소 페이지의 **"Cite this repository"** 버튼([`CITATION.cff`](CITATION.cff) 기반)이
APA/BibTeX 를 자동 생성한다. 바로 붙여넣는 BibTeX 는 [English › How to cite](#how-to-cite) 참조.

### 라이선스

**GPLv3** ([`LICENSE`](LICENSE)) · 기여: [`CONTRIBUTING.md`](CONTRIBUTING.md) ·
데이터 출처·약관: [`NOTICE.md`](NOTICE.md) · 변경 이력: [`CHANGELOG.md`](CHANGELOG.md).
