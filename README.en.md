# inframon — Bridge Infrastructure Monitoring Platform

[한국어](README.md) · **English**

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-GPLv3-green)
![Version](https://img.shields.io/badge/version-0.1.0-orange)
![Status](https://img.shields.io/badge/status-research%20prototype-yellow)

inframon chains four engines into one pipeline to monitor bridge safety from satellite SAR:
**InSAR** extracts displacement time series, **PINN** recovers structural meaning (physics), **CV**
auto-delineates the measurement region, and **FRAM** diagnoses system safety via a Composite
Resonance Index (CRI).

> ⚠️ **Status: Research prototype.**
> The full pipeline (real Sentinel-1 → SARvey → PINN → FRAM) and **analytic PINN/FEM validation
> (error < 0.1%)** have been demonstrated. However, validation against **field measurements
> (ambient vibration test / GNSS / leveling), commercial FEM (SAP2000, etc.), and real
> failure/inspection labels has NOT been performed**; the isotonic collapse probability is based on
> **synthetic Morandi-style labels**. Displacements, CRI, and collapse probabilities are **pipeline
> outputs, not validated structural diagnoses. Do not use for operational bridge-safety decisions.**

## Preview

> The visuals below are generated from actual pipeline output (`data/project.h5`) — Jeongja Bridge
> real Sentinel-1 data processed through download → ISCE2 coreg → MiaplPy → SARvey → PINN → FRAM
> (`scripts/make_readme_media.py`).

![Risk over time (demo)](docs/img/demo.gif)

*Bridge risk (CRI / collapse probability) evolving over time — PS/DS point cloud.*

![Result overview](docs/img/overview.png)

*InSAR LOS displacement · LOS velocity map · FRAM global CRI time series · PINN decomposition.*

### Live dashboard (Streamlit)

![Dashboard — FRAM risk](docs/img/dashboard_fram.png)

*FRAM tab — real SARvey result (1,072 pts × 201 epochs): alert DANGER·deck, max CRI 0.965, CRI series/heatmap.*

![Dashboard — InSAR](docs/img/dashboard_insar.png)

*InSAR tab — bridge selection (OSM) · SLC search · aux data · Asc+Desc · accuracy correction.*

![Dashboard — PINN](docs/img/dashboard_pinn.png)

*PINN tab — displacement decomposition · structural response · EI/α/natural-freq · validation · bridge-specific (profile·temperature·traffic).*

> Regenerate screenshots: `python scripts/capture_dashboard.py` (dashboard running + playwright).

## Architecture

Data contracts (Pydantic + HDF5) and the skeleton are **sacred**; each engine is swapped stub→real
independently (hot-swap `--engine X=real`), and golden-regression tests protect the contracts/values.

```
CV    → ROI / member segmentation / axis / georeference   (cv/{engine,real_engine}.py)   STUB + REAL
InSAR → Track H5 → CV registration · world xyz · series    (insar/{engine,real_engine}.py) STUB + REAL
PINN  → decomposition + Euler-Bernoulli PDE + absolute EI   (pinn/{engine,real_engine}.py)  STUB + REAL
FRAM  → pointwise resonance · function net (N-K) · CRI      (fram/{engine,real_engine}.py)  STUB + REAL
```

## Quick start

```bash
# core only (synthetic demo works with just this)
pip install -e ".[dev]"

python -m inframon --doctor     # environment / readiness check
python -m inframon --demo       # full pipeline (stubs) → data/project.h5 + CRI
pytest -q                       # tests

# dashboard (optional)
pip install -e ".[dashboard]"
streamlit run src/inframon/dashboard/app.py
```

## Real data (real Sentinel-1 → CRI)

Heavy SAR processing (ISCE2 / MiaplPy / SARvey) runs on **WSL2/Linux**; selection, ingest, analysis
and visualization run on Windows. See the runbook (`docs/실데이터_런북.md`).

```bash
python -m inframon --doctor <data-root>       # + InSAR inventory
python -m inframon --check-track track.h5      # pre-ingest validation (exit 0 = ready)
python -m inframon --demo --insar-source track.h5 --out data/project.h5 \
  --engine cv=real --engine insar=real --engine pinn=real --engine fram=real
```

## Layout

```
src/inframon/
  contracts/   # ★ inter-module data contract (schema.py, io.py = project.h5)
  cv/ insar/ pinn/ fram/   # four engines
  orchestrator/            # CV→InSAR→PINN→FRAM sequential run
  dashboard/               # Streamlit dashboard
```

## Underlying InSAR tools · Attribution

inframon **invokes** the following open-source tools as its InSAR engine (installed separately on
WSL2/Linux) via CLI — it does not embed their source.

- **SARvey** (default InSAR engine, PS/DS time series) — https://github.com/luhipi/sarvey · GPLv3
- **MiaplPy** (phase linking · SLC stack) — https://github.com/insarlab/MiaplPy · GPLv3
- **MintPy** (InSAR time series) — https://github.com/insarlab/MintPy · GPLv3
- **ISCE2** (Sentinel-1 topsStack coregistration) — https://github.com/isce-framework/isce2

> The InSAR engine is **pluggable** — SARvey is the default; MiaplPy/MintPy/StaMPS adapters are also
> provided (`scripts/wsl_sarvey/5x_*_to_inframon.py`). inframon's original contribution is the
> **data selection (OSM·ASF·ERA5), structural analysis (PINN), functional resonance (FRAM), and
> dashboard/validation/accuracy layers.**

Data: Copernicus Sentinel-1 (ASF) · GLO-30 DEM · ERA5 (Open-Meteo) · OpenStreetMap (ODbL). Follow
each provider's terms and citation requirements. See [`NOTICE.md`](NOTICE.md).

## Citation

If you use inframon in research, please cite inframon **and the underlying tools (especially
SARvey)**. See [`CITATION.cff`](CITATION.cff).

## License

**GPLv3** — see [`LICENSE`](LICENSE). Contribution guide: [`CONTRIBUTING.md`](CONTRIBUTING.md).
