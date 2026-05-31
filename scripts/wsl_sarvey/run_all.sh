#!/usr/bin/env bash
# 전체 오케스트레이터 — 10~40 단계를 순서대로 실행.
# 각 단계는 서로 다른 conda 환경을 쓰므로(이상적으로), 보통은 단계별로 따로 실행하는 걸 권장.
# 이 스크립트는 한 환경에 다 깔린 경우의 편의용. 환경 분리 시 각 .sh 를 개별 실행하세요.
#
# 사용:  ./run_all.sh <recipe_dir> <work_dir>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RECIPE_DIR="${1:?recipe_dir (processing_manifest.json/sarvey_config.json 위치)}"
WORK="${2:?work_dir (산출물 폴더)}"
mkdir -p "$WORK"

echo "######## 10 다운로드 (SLC/궤도/DEM) ########"; bash "$HERE/10_download.sh"    "$RECIPE_DIR" "$WORK"
echo "######## 20 ISCE2 스택 ####################"; bash "$HERE/20_stack_isce.sh"  "$RECIPE_DIR" "$WORK"
echo "######## 30 MiaplPy load_data #############"; bash "$HERE/30_miaplpy.sh"     "$RECIPE_DIR" "$WORK"
echo "######## 40 SARvey + 내보내기 #############"; bash "$HERE/40_sarvey.sh"      "$RECIPE_DIR" "$WORK"

echo
echo "다음(수동): SARvey 산출 ts.h5 를 변환 →"
echo "  python3 $HERE/50_export_to_inframon.py --sarvey-h5 $WORK/sarvey/outputs/<...>_ts.h5 --out $WORK/track.h5"
echo "그 뒤(Windows/inframon):"
echo "  python -m inframon --import-track-h5 $WORK/track.h5 --out data/project.h5"
