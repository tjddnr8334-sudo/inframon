#!/usr/bin/env bash
# 20단계 — ISCE2 stackSentinel 로 코레지스트레이션 스택 생성.
# 전제: conda activate isce2 (ISCE2 + topsStack 경로 설정: $ISCE_STACK)
# 사용:  ./20_stack_isce.sh <recipe_dir> <work_dir>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RECIPE_DIR="${1:?recipe_dir}"; WORK="${2:?work_dir}"
source "$HERE/_manifest.sh" "$RECIPE_DIR"

SLC_DIR="$WORK/SLC"; ORBIT_DIR_PATH="$WORK/orbits"; DEM="$WORK/DEM/dem.wgs84"
STACK_DIR="$WORK/stack"; mkdir -p "$STACK_DIR"

# stackSentinel.py 위치(topsStack). 설치 경로에 맞게 ISCE_STACK 지정.
: "${ISCE_STACK:?ISCE_STACK 환경변수에 topsStack 경로를 지정하세요 (예: \$ISCE_HOME/contrib/stack/topsStack)}"

# reference 날짜는 ERA5 master (YYYYMMDD)
echo ">> stackSentinel: ref=$REF_DATE  SNWE=$SNWE_S $SNWE_N $SNWE_W $SNWE_E  pol=$POL_LC"
python3 "$ISCE_STACK/stackSentinel.py" \
  -s "$SLC_DIR" \
  -d "$DEM" \
  -o "$ORBIT_DIR_PATH" \
  -a "$WORK/aux" \
  -w "$STACK_DIR" \
  -b "$SNWE_S $SNWE_N $SNWE_W $SNWE_E" \
  -p "$POL_LC" \
  -m "$REF_DATE" \
  -c 1 \
  -W slc                      # SARvey/MiaplPy 입력은 coregistered SLC 스택
  # TODO(verify): -n '1 2 3'(서브스왓), --num_proc, --azimuth_looks/--range_looks 등 버전별 옵션

echo ">> run_files 실행 (순서대로)"
# stackSentinel 은 $STACK_DIR/run_files/ 에 단계별 스크립트를 만든다. 순서대로 실행:
for f in "$STACK_DIR"/run_files/run_*; do
  echo "   -- $f"
  bash "$f"                    # TODO(verify): 클러스터/병렬이면 제출 방식 조정
done
echo "완료: 코레지스트레이션 스택 -> $STACK_DIR"
