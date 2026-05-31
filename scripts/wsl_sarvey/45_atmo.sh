#!/usr/bin/env bash
# 45단계(선택) — 대기(트로포) 지연 보정. SARvey 시계열에 기상모델 보정을 더한다.
# 세 가지 경로:
#   (a) SARvey 내장 APS 필터링 — sarvey_config.filtering.apply_aps_filtering=true (경험적 시공간 필터)
#   (b) ERA5(PyAPS) — 기상모델 기반. MintPy tropo_pyaps3. CDS API 키 필요.
#   (c) GACOS — http://www.gacos.net 에서 날짜별 zip 수동 주문 → MintPy tropo_gacos.
# 사용:  ./45_atmo.sh <recipe_dir> <work_dir> [era5|gacos|none]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RECIPE_DIR="${1:?recipe_dir}"; WORK="${2:?work_dir}"; MODE="${3:-era5}"
source "$HERE/_manifest.sh" "$RECIPE_DIR"
SARVEY_DIR="$WORK/sarvey"

case "$MODE" in
  none)
    echo ">> 대기보정 생략 — SARvey 내장 APS 필터링(filtering.apply_aps_filtering)에 의존" ;;

  era5)
    echo ">> ERA5(PyAPS) 트로포 보정"
    # 전제: conda activate mintpy (PyAPS3 포함), ~/.cdsapirc 에 CDS API 키
    : "${CDSAPIRC:=$HOME/.cdsapirc}"
    [ -f "$CDSAPIRC" ] || echo "  [필요] ~/.cdsapirc (CDS API 키) — https://cds.climate.copernicus.eu 가입 후 발급"
    # MintPy 시계열(timeseries.h5)에 적용. SARvey→MintPy 포맷 변환이 필요할 수 있음(TODO(verify)).
    # tropo_pyaps3.py -f timeseries.h5 -g geometryRadar.h5 -m ERA5 \
    #   --weather-dir "$WORK/weather"   # 기상모델 캐시 위치
    echo "  TODO(verify): SARvey 산출 → MintPy timeseries 포맷 후 tropo_pyaps3 적용"
    echo "  AOI=$AOI_NAME lat=$LAT lon=$LON  취득일은 manifest scene_dates 사용" ;;

  gacos)
    echo ">> GACOS 트로포 보정"
    GACOS_DIR="$WORK/GACOS"; mkdir -p "$GACOS_DIR"
    echo "  [수동] http://www.gacos.net 에서 다음 조건으로 주문해 $GACOS_DIR 에 배치:"
    echo "    영역: lat $SNWE_S~$SNWE_N, lon $SNWE_W~$SNWE_E"
    echo "    날짜: manifest scene_dates (취득 시각 UTC)"
    echo "    산출: 날짜별 *.ztd (+ *.rsc)"
    # tropo_gacos.py -f timeseries.h5 -g geometryRadar.h5 --GACOS-dir "$GACOS_DIR"   # TODO(verify)
    ls -1 "$GACOS_DIR" 2>/dev/null | head || echo "  (GACOS 파일 없음 — 주문 후 재실행)" ;;

  *) echo "MODE 는 era5|gacos|none"; exit 1 ;;
esac

echo "완료(45 대기보정: $MODE). 다음: 50_export_to_inframon.py 로 Track H5 변환."
