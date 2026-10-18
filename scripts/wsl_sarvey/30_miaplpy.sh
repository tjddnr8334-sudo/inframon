#!/usr/bin/env bash
# 30단계 — MiaplPy 로 ISCE 스택을 SARvey 입력(slcStack.h5 + geometryRadar.h5)으로 변환.
# 전제: conda activate miaplpy (MiaplPy + MintPy)
# 사용:  ./30_miaplpy.sh <recipe_dir> <work_dir>
set -euo pipefail
# 병렬은 프로세스(MiaplPy numProcessor·SARvey num_cores)로 한다 — 계산 라이브러리 스레드는 프로세스당 1개.
# 안 묶으면 프로세스마다 코어 수만큼 스레드를 띄워 부하가 140 을 넘고 수십 배 느려진다(강릉 2026-10-16 실측).
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}" OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}" \
       MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}" NUMBA_NUM_THREADS="${NUMBA_NUM_THREADS:-1}"
HERE="$(cd "$(dirname "$0")" && pwd)"
RECIPE_DIR="${1:?recipe_dir}"; WORK="${2:?work_dir}"
source "$HERE/_manifest.sh" "$RECIPE_DIR"

STACK_DIR="$WORK/stack"; MIAPLPY_DIR="$WORK/miaplpy"; mkdir -p "$MIAPLPY_DIR"
TEMPLATE="$WORK/miaplpy.txt"

# MiaplPy 템플릿 생성 (ISCE topsStack 산출을 가리킴). 경로/옵션은 버전 확인 필요.
cat > "$TEMPLATE" <<EOF
miaplpy.load.processor      = isce
miaplpy.load.slcFile        = $STACK_DIR/merged/SLC/*/*.slc.full
miaplpy.load.metaFile       = $STACK_DIR/reference/IW*.xml
miaplpy.load.baselineDir    = $STACK_DIR/baselines
miaplpy.load.demFile        = $STACK_DIR/merged/geom_reference/hgt.rdr.full
miaplpy.load.lookupYFile    = $STACK_DIR/merged/geom_reference/lat.rdr.full
miaplpy.load.lookupXFile    = $STACK_DIR/merged/geom_reference/lon.rdr.full
miaplpy.load.incAngleFile   = $STACK_DIR/merged/geom_reference/los.rdr.full
miaplpy.load.azAngleFile    = $STACK_DIR/merged/geom_reference/los.rdr.full
miaplpy.load.compression    = no
miaplpy.load.startDate      = None
miaplpy.load.endDate        = None
miaplpy.subset.lalo         = ${SNWE_S}:${SNWE_N},${SNWE_W}:${SNWE_E}
miaplpy.subset.yx           = auto

EOF
echo ">> MiaplPy 템플릿: $TEMPLATE"

# load_data 단계만 실행해 inputs/ 생성 (이후 SARvey 가 받는다)
# miaplpyApp 을 거치지 않고 load_data 의 실체인 load_slc_geometry.py 를 직접 호출한다 —
# miaplpyApp 은 작업폴더 cfg 를 재생성하며 compression 값을 'default'(h5py 미지원)로
# 되엎는 버그가 있다(0.2.x). 직접 호출이면 우리 템플릿의 compression=no 가 그대로 쓰인다.
LOAD_SLC="$(command -v load_slc_geometry.py)" || {
  echo "load_slc_geometry.py 없음 — conda env(miaplpy)에 'pip install miaplpy' 필요" >&2; exit 1; }
# load_slc_geometry 는 성공해도 main() 반환값(파일 목록)을 sys.exit 에 넘겨 비0 종료한다
# — 성공 판정은 아래 산출물 존재·에포크 검사로 하므로 종료코드는 무시.
"$LOAD_SLC" --template "$TEMPLATE" --project_dir "$WORK" --work_dir "$MIAPLPY_DIR" || true

# load_data 는 내부 오류를 삼키고 exit 0 할 수 있다 — 산출물 존재로 성공 판정
for f in "$MIAPLPY_DIR/inputs/slcStack.h5" "$MIAPLPY_DIR/inputs/geometryRadar.h5"; do
  [ -s "$f" ] || { echo "실패: $f 미생성(위 miaplpy 로그 확인)" >&2; exit 1; }
done
python3 - "$MIAPLPY_DIR/inputs/slcStack.h5" <<'PY'
import h5py, sys
with h5py.File(sys.argv[1]) as f:
    n, ny, nx = f["slc"].shape
    print(f"slcStack: {n} epochs x {ny} x {nx}")
assert n >= 2, "slcStack 에포크 부족"
PY
echo "load_data 완료: $MIAPLPY_DIR/inputs/{slcStack.h5,geometryRadar.h5}"

# ── 위상연결(phase linking) + 패치 결합 → inverted/phase_series.h5, maskPS.h5 ──
# SARvey 가 DS 점을 받으려면 이 두 산출물이 필요하다(sarvey_config.phase_linking).
# 창 크기는 교량용: 거리 15 × 방위 5 화소(내곡교 119장 스택에서 쓴 값). 방위창을 크게 잡으면 데크 폭(≈1 화소)이 주변과 섞인다.
cat >> "$TEMPLATE" <<EOT
miaplpy.inversion.patchSize                = 200
miaplpy.inversion.ministackSize            = 15
miaplpy.inversion.rangeWindow              = 15
miaplpy.inversion.azimuthWindow            = 5
miaplpy.inversion.shpTest                  = auto
miaplpy.inversion.phaseLinkingMethod       = auto
miaplpy.inversion.PsNumShp                 = auto
EOT
if [ ! -s "$MIAPLPY_DIR/inverted/phase_series.h5" ]; then
  echo ">> MiaplPy phase_linking (miaplpyApp --dostep phase_linking → concatenate_patches)"
  miaplpyApp "$TEMPLATE" --dir "$MIAPLPY_DIR" --dostep phase_linking      2>&1 | tail -20
  miaplpyApp "$TEMPLATE" --dir "$MIAPLPY_DIR" --dostep concatenate_patches 2>&1 | tail -20
fi
for f in "$MIAPLPY_DIR/inverted/phase_series.h5" "$MIAPLPY_DIR/maskPS.h5"; do
  [ -s "$f" ] || { echo "경고: $f 미생성 — SARvey 는 위상연결 없이(PS만) 돈다" >&2; }
done
echo "완료: $MIAPLPY_DIR/{inputs,inverted/phase_series.h5,maskPS.h5}"
