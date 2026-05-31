# F 단계 — SARvey 처리 (WSL2/Linux)

inframon 의 InSAR 데이터 선별(A~E)이 만든 **레시피 번들**을 받아, WSL2/Linux 에서
SLC 다운로드 → 코레지스트레이션 → MiaplPy → **SARvey** PS/DS 시계열을 산출하고,
그 결과를 다시 inframon 으로 인제스트(G)하는 단계다.

> ⚠️ **실행 환경**: ISCE2·MiaplPy·SARvey 는 Windows 네이티브 불가 → **WSL2(Ubuntu) 또는 Linux**.
> 이 문서의 스크립트(`scripts/wsl_sarvey/`)는 manifest→도구 매핑과 흐름을 담은 **템플릿**이며,
> 도구 CLI/옵션은 **버전마다 다르므로 `# TODO(verify)` 부분은 본인 설치 버전과 대조**해 조정한다.

## 전체 흐름

```
[Windows / inframon]                 [WSL2 / Linux]                       [Windows / inframon]
A~E 선별 → 레시피 4종  ──복사──▶  10 다운로드 → 20 ISCE2 → 30 MiaplPy   ──복사──▶  G 인제스트
  + SARvey 번들 2종                  → 40 SARvey → 50 변환(Track H5)            (project.h5)
```

레시피 번들(`data/insar_recipe/`)에서 F 가 쓰는 두 파일:
- **`processing_manifest.json`** — 상류 스택 생성용(궤도방향·track·frame·VV·**reference_date=master**·장면목록·bbox·공간 baseline)
- **`sarvey_config.json`** — SARvey MTI 시계열 추정 파라미터(분석 기간·시간 baseline 네트워크 등)

## 0. 사전 준비

### 0.1 WSL2 (Windows)
```powershell
wsl --install -d Ubuntu          # 최초 1회, 재부팅
wsl                              # Ubuntu 진입
```

### 0.2 conda 환경 (WSL2 안에서)
도구마다 의존성 충돌이 있어 **환경을 분리**하는 걸 권장한다.
```bash
# Miniforge 설치 후
conda create -n isce2   -c conda-forge isce2 asf_search sentineleof sardem -y
conda create -n miaplpy -c conda-forge miaplpy mintpy -y          # 또는 소스 설치
conda create -n sarvey  -c conda-forge python=3.10 -y && conda activate sarvey && pip install sarvey
# inframon 코어(변환·인제스트용, 가벼움)
pip install -e /mnt/d/프로그램            # numpy/h5py/pydantic 만
```

### 0.3 NASA Earthdata 인증 (SLC 다운로드)
`~/.netrc` 에:
```
machine urs.earthdata.nasa.gov login <USER> password <PASS>
```

### 0.4 레시피 복사 (Windows → WSL)
```bash
mkdir -p ~/jeongjagyo/recipe
cp /mnt/d/프로그램/data/insar_recipe/*.json ~/jeongjagyo/recipe/
```

## 1. 단계별 실행

스크립트는 `scripts/wsl_sarvey/`. 각 단계는 `<recipe_dir> <work_dir>` 인자를 받는다.
환경이 분리돼 있으면 단계마다 `conda activate` 후 개별 실행한다.

```bash
cd /mnt/d/프로그램/scripts/wsl_sarvey
RECIPE=~/jeongjagyo/recipe
WORK=~/jeongjagyo/work

conda activate isce2   && ./10_download.sh   "$RECIPE" "$WORK"   # SLC + 궤도(EOF) + DEM
conda activate isce2   && ./20_stack_isce.sh "$RECIPE" "$WORK"   # stackSentinel → coreg SLC 스택
conda activate miaplpy && ./30_miaplpy.sh    "$RECIPE" "$WORK"   # load_data → inputs/{slcStack,geometryRadar}.h5
conda activate sarvey  && ./40_sarvey.sh     "$RECIPE" "$WORK"   # SARvey MTI (steps 0..4)
```

> `ISCE_STACK` 환경변수에 topsStack 경로를 지정해야 한다(예: `export ISCE_STACK=$CONDA_PREFIX/share/isce2/topsStack`).
> AOI 버퍼는 `AOI_BUFFER_DEG`(기본 0.05°)로 조정.

한 환경에 다 설치돼 있으면 `./run_all.sh "$RECIPE" "$WORK"` 로 일괄 실행.

## 1.5 보조 데이터 & 대기(트로포) 보정

InSAR 처리에 필요한 보조 입력과 대기 지연 보정:

| 입력 | 역할 | 처리 단계 | 비고 |
|---|---|---|---|
| **orbits** (POEORB/EOF) | 정밀 궤도 | `10_download` (sentineleof) | 자동 |
| **DEM** (COP-30) | 지형 위상 제거·지오코딩 | `10_download` (sardem) | AOI SNWE |
| **aux_cal** (S1 AUX_CAL) | 센서 보정 | `10_download` → `20_stack -a` | 자주 안 바뀜. 안 받아지면 [sar-mpc.eu](https://sar-mpc.eu)에서 수동 배치 |
| **ERA5** | ① master 선정 ② 트로포 보정 | E(완료) / `45_atmo era5` | 보정은 PyAPS+CDS 키 필요 |
| **GACOS** | 트로포 지연 보정 | `45_atmo gacos` | [gacos.net](http://www.gacos.net) 날짜별 수동 주문 |
| **weather** | 기상모델 APS | SARvey 내장 / `45_atmo` | 아래 3경로 |

**대기 보정 3경로** (`45_atmo.sh <recipe> <work> [era5|gacos|none]`):
- **(a) SARvey 내장 APS 필터링** — `sarvey_config.filtering.apply_aps_filtering=true`. 경험적 시공간 필터, 추가 데이터 불필요(기본).
- **(b) ERA5 + PyAPS** — 기상모델 기반. MintPy `tropo_pyaps3`. `~/.cdsapirc`(CDS API 키) 필요.
- **(c) GACOS** — gacos.net에서 AOI·취득일로 `.ztd` 주문 → MintPy `tropo_gacos`.

> 우리 master 선정(E)도 ERA5(강수·습도)를 쓰지만, 그건 **장면 선택용**이고 여기 (b)는 **변위 시계열의 지연 보정**으로 목적이 다르다.

## 2. 결과 변환 (F → G 다리)

SARvey 산출(`work/sarvey/outputs/p2_*_ts.h5`)을 inframon Track H5 스키마로 변환:
```bash
python3 50_export_to_inframon.py \
  --sarvey-h5 "$WORK/sarvey/outputs/p2_coh80_ts.h5" \
  --out "$WORK/track_jeongjagyo.h5"
# 데이터셋 이름이 다르면: --disp-key ... --lat-key ... --lon-key ... --date-key ... --coh-key ...
```
변환 결과 Track H5 스키마: `pixel_lonlat[N,2]` · `epochs[M]`(YYYYMMDD) · `los_mm[N,M]` · `coh[N]`.

## 3. inframon 으로 인제스트 (G, Windows)

```powershell
# (a) 빠른 변환만 — /insar 계약 직접 적재
python -m inframon --import-track-h5 D:\...\track_jeongjagyo.h5 --out data\project.h5

# (b) 전체 파이프라인 — InSAR real 핫스왑 (CV 정합 + PINN/FRAM 까지)
python -m inframon --demo --engine insar=real --insar-source D:\...\track_jeongjagyo.h5
```
이후 대시보드 ① InSAR 탭에서 실데이터 변위 시계열·LOS 맵을 확인.

## 4. manifest 필드 → 도구 매핑

| manifest 필드 | 쓰는 단계 / 도구 | 용도 |
|---|---|---|
| `stack.reference_date` (ERA5 master) | 20 stackSentinel `-m` | 코레지스트레이션 기준 영상 |
| `stack.orbit_direction` / `relative_orbit` / `frame` | 10 asf_search 필터 | 선택 트랙 장면만 다운로드 |
| `stack.polarization` (VV) | 10/20 `-p vv` | VV 만 사용 |
| `stack.scene_dates` | 10 다운로드 | 그 취득일 장면만 |
| `aoi.bbox_lonlat` (+버퍼) | 20/30 SNWE, subset | 처리 영역 |
| `baseline.max_perp_baseline_m` (150) | (MiaplPy/네트워크 단계) | 공간 baseline ≤150m 페어 |
| `sarvey_config.preparation.*` | 40 SARvey | 시계열 추정 네트워크/기간 |

## 5. 주의점 · 트러블슈팅

- **버전 차이**: `stackSentinel.py`·`miaplpyApp.py`·`sarvey` 의 옵션/스텝 이름은 버전마다 다르다.
  각 스크립트의 `# TODO(verify)` 와 `sarvey -g config.json`(템플릿 생성)으로 키를 대조하라.
- **SARvey 입력**: SARvey 는 MiaplPy `inputs/slcStack.h5` + `geometryRadar.h5` 를 기대한다.
  30단계 산출 경로를 40단계에서 심볼릭 링크로 연결한다.
- **공간 baseline 150m**: SARvey config 자체에는 수직 baseline 상한 키가 없을 수 있다 — 이는
  스택/네트워크(MiaplPy/ISCE) 단계에서 거른다(매니페스트의 `max_perp_baseline_m`).
- **디스크/시간**: 1년치 SLC + 스택은 수십~수백 GB, 처리는 수 시간~하루. WORK 를 큰 디스크에 둔다.
- **좌표**: 변환 H5 는 lon/lat(EPSG:4326). inframon `run_insar_real` 1차 증분은 점 좌표를
  CV 픽셀 프레임으로 간주하므로, 실 CV(Phase 2) 결합 시 geo→pixel affine 등록이 필요하다.

## 6.5 기존 insar_unified 파이프라인 연결 (Track D, MiaplPy)

이 저장소(WSL `~/insar_processing_bridge/insar_unified/`)에는 이미 정자교 ISCE2 stack +
4-Track(A:StaMPS / B:MintPy SBAS / C:MintPy QPS / **D:MiaplPy Phase-Linking**) 처리가 있다.
**SARvey 대신 이 파이프라인의 Track D 산출을 inframon으로 인제스트**한다(중복 처리 불필요).
보조 데이터(SLC/orbits/aux_cal/DEM/ERA5/GACOS/weather)는 `/home/insar/insar_data/`에 있음.

Track D(MiaplPy) 시계열이 나오면 — `scripts/wsl_sarvey/52_miaplpy_to_inframon.py`:
```bash
conda activate isce2_mintpy
python3 scripts/wsl_sarvey/52_miaplpy_to_inframon.py \
  --timeseries <miaplpy>/network_delaunay/timeseries.h5 \
  --geometry   <miaplpy>/inputs/geometryRadar.h5 \
  --coherence  <miaplpy>/network_delaunay/temporalCoherence.h5 \
  --out track_d_jeongjagyo.h5 \
  --coh-thresh 0.6 \
  --bbox 127.10058 37.35939 127.12098 37.37802     # 정자교 ROI
```
→ coherence ≥ γ_t(0.6) + ROI 안의 점만 추출 → Track H5. 그 뒤(Windows):
```powershell
python -m inframon --import-track-h5 track_d_jeongjagyo.h5 --out data\project.h5
# 또는 전체 파이프라인: python -m inframon --demo --engine insar=real --insar-source track_d_jeongjagyo.h5
```
> 정자교 실제 설정: ROI bbox 위 값, master **20211025**(ERA5), IW2/VV, multilook 2×1.

**Track B/C (MintPy SBAS/QPS, 지오코딩 래스터)** — `scripts/wsl_sarvey/54_mintpy_to_inframon.py`:
좌표가 per-pixel 배열이 아니라 격자 affine 속성(`X_FIRST/Y_FIRST/X_STEP/Y_STEP`)으로 정의되므로,
affine 으로 픽셀별 lon/lat 를 계산해 coherence ≥ 임계(MintPy 기본 0.7) + ROI 안의 픽셀만 점으로 뽑는다.
```bash
python3 scripts/wsl_sarvey/54_mintpy_to_inframon.py \
  --timeseries SBAS/geo/geo_timeseries.h5 \
  --coherence  SBAS/geo/geo_temporalCoherence.h5 \
  --out track_b.h5 --coh-thresh 0.7 \
  --bbox 127.10058 37.35939 127.12098 37.37802
# 레이더 좌표(geometryRadar.h5 lat/lon 배열)면: --geometry geometryRadar.h5
```
어댑터 선택: **D(점구름)=52_**, **B/C(지오코딩 래스터)=54_**. 무효(전 시점 0) 픽셀은 자동 제외.

## 6. 관련 파일
- 스크립트: `scripts/wsl_sarvey/{00_setup_env.sh,_manifest.sh,10_download.sh,20_stack_isce.sh,30_miaplpy.sh,40_sarvey.sh,45_atmo.sh,50_export_to_inframon.py,52_miaplpy_to_inframon.py,54_mintpy_to_inframon.py,run_all.sh}`
- 기존 파이프라인 연결: `52_`(Track D, MiaplPy 점구름) · `54_`(Track B/C, MintPy 래스터), §6.5
- 레시피 생성: 대시보드 ① InSAR 탭 또는 `python -m inframon --make-sarvey-config data/insar_recipe`
- 인제스트: `src/inframon/insar/{track_reader.py,real_engine.py}`
