#!/usr/bin/env bash
# sardem 이 GTiff 로 쓴 dem.wgs84 를 ISCE 형식(raw + .xml + .vrt) 으로 바꾼다
# set -u 금지: conda 의 isce2-activate.sh 가 미정의 변수를 참조한다
cd /home/insar/gangwon/dem || exit 1
ENV=/home/insar/miniforge3/envs/isce2_mintpy
export PROJ_DATA="$ENV/share/proj" GDAL_DATA="$ENV/share/gdal"
if [ ! -f dem_cop30.tif ]; then
  if "$ENV/bin/gdalinfo" dem.wgs84 2>/dev/null | grep -q "Driver: GTiff"; then mv dem.wgs84 dem_cop30.tif; fi
fi
[ -f dem_cop30.tif ] || { echo "dem_cop30.tif 없음"; exit 2; }
[ -f dem.wgs84.xml ] || "$ENV/bin/gdal_translate" -of ISCE -ot Int16 dem_cop30.tif dem.wgs84 2>&1 | tail -2
ls -la
echo "--- fixImageXml (env 활성화)"
export ISCE_HOME=$ENV/lib/python3.11/site-packages/isce
source /home/insar/miniforge3/etc/profile.d/conda.sh; conda activate isce2_mintpy
export PATH="$ISCE_HOME/bin:$ISCE_HOME/applications:$PATH" PYTHONPATH="$ISCE_HOME/components:${PYTHONPATH:-}"
python "$ISCE_HOME/applications/fixImageXml.py" -i dem.wgs84 -f 2>&1 | tail -3
ls -la; head -c 600 dem.wgs84.xml; echo; head -c 400 dem.wgs84.vrt
