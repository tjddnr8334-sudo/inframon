#!/usr/bin/env bash
# 30단계 — MiaplPy 로 ISCE 스택을 SARvey 입력(slcStack.h5 + geometryRadar.h5)으로 변환.
# 전제: conda activate miaplpy (MiaplPy + MintPy)
# 사용:  ./30_miaplpy.sh <recipe_dir> <work_dir>
set -euo pipefail
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
echo "완료: $MIAPLPY_DIR/inputs/{slcStack.h5,geometryRadar.h5}"
