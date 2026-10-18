#!/usr/bin/env bash
# (WSL) 강릉 6개 버스트(상승 54 IW2 114060-062, 하강 61 IW3 129727, 134 IW1 286524-525)를 덮는 Copernicus GLO-30 DEM
# bbox W S E N = 128.0 37.35 129.45 38.15 (버스트 풋프린트 합집합 + 여유). ISCE 형식(dem.wgs84 + .vrt + .xml)
set -u
OUT=/home/insar/gangwon/dem
mkdir -p "$OUT"; cd "$OUT" || exit 1
if [ -s dem.wgs84 ] && [ -s dem.wgs84.xml ]; then echo "DEM already: $(ls -la dem.wgs84 | awk '{print $5}') bytes"; exit 0; fi
SARDEM=/home/insar/miniforge3/envs/isce2_mintpy/bin/sardem
[ -x "$SARDEM" ] || SARDEM=/home/insar/miniforge3/envs/b2s/bin/sardem
ENV=$(dirname "$(dirname "$SARDEM")")
export PROJ_DATA="$ENV/share/proj" PROJ_LIB="$ENV/share/proj" GDAL_DATA="$ENV/share/gdal"
rm -f dem.wgs84* 
echo "$(date '+%F %T') sardem start ($SARDEM)"
nohup "$SARDEM" --bbox 128.0 37.35 129.45 38.15 --data COP --output dem.wgs84 > sardem.log 2>&1 < /dev/null &
echo "pid $!  log $OUT/sardem.log"
