#!/usr/bin/env bash
# processing_manifest.json 에서 값을 읽어 환경변수로 export.
# 사용:  source _manifest.sh <recipe_dir>
set -euo pipefail

RECIPE_DIR="${1:-./recipe}"
MANIFEST="$RECIPE_DIR/processing_manifest.json"
[ -f "$MANIFEST" ] || { echo "manifest 없음: $MANIFEST" >&2; return 1 2>/dev/null || exit 1; }

# 작은 python3 헬퍼로 JSON 경로를 평가 (jq 불필요)
_j() { python3 -c "import json;print(json.load(open('$MANIFEST'))$1)"; }

export AOI_NAME="$(_j "['aoi']['name']")"
export LAT="$(_j "['aoi']['lat']")"
export LON="$(_j "['aoi']['lon']")"
export BBOX_MINLON="$(_j "['aoi']['bbox_lonlat'][0]")"
export BBOX_MINLAT="$(_j "['aoi']['bbox_lonlat'][1]")"
export BBOX_MAXLON="$(_j "['aoi']['bbox_lonlat'][2]")"
export BBOX_MAXLAT="$(_j "['aoi']['bbox_lonlat'][3]")"
export ORBIT_DIR="$(_j "['stack']['orbit_direction']")"
export REL_ORBIT="$(_j "['stack']['relative_orbit']")"
export FRAME="$(_j "['stack']['frame']")"
export POL="$(_j "['stack']['polarization']")"
export REF_DATE="$(_j "['stack']['reference_date']")"
export DATE_START="$(_j "['stack']['date_range'][0]")"
export DATE_END="$(_j "['stack']['date_range'][1]")"
export MAX_PERP="$(_j "['baseline']['max_perp_baseline_m']")"

# AOI 에 약간의 버퍼(deg)를 둬 SNWE 계산 (교량 bbox 는 매우 작음)
BUF="${AOI_BUFFER_DEG:-0.05}"
export SNWE_S="$(python3 -c "print($BBOX_MINLAT - $BUF)")"
export SNWE_N="$(python3 -c "print($BBOX_MAXLAT + $BUF)")"
export SNWE_W="$(python3 -c "print($BBOX_MINLON - $BUF)")"
export SNWE_E="$(python3 -c "print($BBOX_MAXLON + $BUF)")"
export POL_LC="$(python3 -c "print('$POL'.lower())")"

echo "[manifest] $AOI_NAME | $ORBIT_DIR path$REL_ORBIT frame$FRAME $POL | ref $REF_DATE | $DATE_START~$DATE_END"
echo "[manifest] SNWE = $SNWE_S $SNWE_N $SNWE_W $SNWE_E (buffer ${BUF}deg) | perp<=${MAX_PERP}m"
