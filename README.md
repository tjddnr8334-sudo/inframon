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

### Citation · License

Cite inframon **and the underlying tools (especially SARvey)** — see [`CITATION.cff`](CITATION.cff).
**GPLv3** ([`LICENSE`](LICENSE)); contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md).

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

### 기반 InSAR 도구 · 귀속

inframon 은 아래 도구를 **엔진으로 CLI 호출**한다(별도 설치, 소스 미포함): **SARvey**(기본,
GPLv3)·**MiaplPy**·**MintPy**·**ISCE2**. InSAR 엔진은 **플러그블**(SARvey 기본, 어댑터 제공)이고,
inframon 고유 기여는 **선별(OSM·ASF·ERA5)·PINN·FRAM·대시보드/검증/정확도** 계층이다.
데이터 출처·약관은 [`NOTICE.md`](NOTICE.md).

### 인용 · 라이선스

inframon 과 **기반 도구(특히 SARvey)를 함께 인용** — [`CITATION.cff`](CITATION.cff).
**GPLv3** ([`LICENSE`](LICENSE)) · 기여: [`CONTRIBUTING.md`](CONTRIBUTING.md).
