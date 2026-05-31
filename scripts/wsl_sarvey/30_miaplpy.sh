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
miaplpy.subset.lalo         = ${SNWE_S}:${SNWE_N},${SNWE_W}:${SNWE_E}
EOF
echo ">> MiaplPy 템플릿: $TEMPLATE"

# load_data 단계만 실행해 inputs/ 생성 (이후 SARvey 가 받는다)
miaplpyApp.py "$TEMPLATE" --dir "$MIAPLPY_DIR" --dostep load_data   # TODO(verify): step 이름

echo "완료: $MIAPLPY_DIR/inputs/{slcStack.h5,geometryRadar.h5}"
