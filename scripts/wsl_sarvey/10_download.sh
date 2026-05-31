#!/usr/bin/env bash
# 10단계 — 선별된 트랙의 SLC + 궤도 + DEM 다운로드.
# 전제: conda activate isce2 (asf_search, sentineleof, isce2/sardem 설치)
#       NASA Earthdata 로그인(~/.netrc 또는 asf_search 인증) 준비.
# 사용:  ./10_download.sh <recipe_dir> <work_dir>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RECIPE_DIR="${1:?recipe_dir}"; WORK="${2:?work_dir}"
source "$HERE/_manifest.sh" "$RECIPE_DIR"

SLC_DIR="$WORK/SLC"; ORBIT_DIR_PATH="$WORK/orbits"; DEM_DIR="$WORK/DEM"
mkdir -p "$SLC_DIR" "$ORBIT_DIR_PATH" "$DEM_DIR"

# (1) SLC 다운로드 — manifest 의 scene_names(정확한 granule)로 직접 (geo 재검색 X)
#     ※ bbox 재검색은 교량 bbox 가 선으로 collapse 되어 부정확. 선별된 granule 이름으로 받는다.
echo ">> SLC 다운로드 (granule by name, $ORBIT_DIR path$REL_ORBIT frame$FRAME $POL)"
python3 - "$RECIPE_DIR/processing_manifest.json" "$SLC_DIR" <<'PY'
import json, sys, netrc
import asf_search as asf  # env: isce2 또는 asf_search
manifest, out = sys.argv[1], sys.argv[2]
# Earthdata 인증 — ~/.netrc 의 urs.earthdata 자격을 명시적으로 사용
try:
    _a = netrc.netrc().authenticators("urs.earthdata.nasa.gov")
    session = asf.ASFSession().auth_with_creds(_a[0], _a[2]) if _a else asf.ASFSession()
except Exception:
    session = asf.ASFSession()   # netrc 없으면 기본(다운로드 시 인증 실패할 수 있음)
names = json.load(open(manifest))["stack"]["scene_names"]
results = asf.granule_search(names)                      # 정확히 그 granule 들
# granule_search 는 SLC 외에 METADATA_SLC 도 함께 반환 → SLC·VV 만, sceneName 중복 제거
seen, sel = set(), []
for r in results:
    p = r.properties
    if p.get("processingLevel") != "SLC":               continue
    if "VV" not in (p.get("polarization") or ""):       continue
    if p["sceneName"] in seen:                          continue
    seen.add(p["sceneName"]); sel.append(r)
gb = sum((r.properties.get("bytes") or 0) for r in sel) / 1e9
miss = [n for n in names if n not in seen]
print(f"  요청 {len(names)} / SLC·VV·중복제거 {len(sel)}장 ~ {gb:.0f} GB / 누락 {miss or '없음'}")
asf.download_urls([r.properties["url"] for r in sel], path=out, session=session)
PY

# (2) 정밀 궤도(EOF) — sentineleof (POEORB, 없으면 RESORB)
echo ">> 궤도(EOF) 다운로드"
eof --search-path "$SLC_DIR" --save-dir "$ORBIT_DIR_PATH"   # TODO(verify): sentineleof CLI

# (3) DEM (Copernicus GLO-30 권장) — AOI SNWE 로
echo ">> DEM 다운로드 (SNWE=$SNWE_S $SNWE_N $SNWE_W $SNWE_E)"
sardem --bbox "$SNWE_W" "$SNWE_S" "$SNWE_E" "$SNWE_N" --data COP --output "$DEM_DIR/dem.wgs84"  # TODO(verify)

# (4) AUX_CAL (S1 보정 보조파일) — ISCE stackSentinel 의 -a 에 필요. 자주 안 바뀜(한 번 받아 재사용).
echo ">> AUX_CAL 준비"
AUX_DIR="$WORK/aux"; mkdir -p "$AUX_DIR"
if [ -z "$(ls -A "$AUX_DIR" 2>/dev/null)" ]; then
  # sentineleof 가 aux_cal 도 받을 수 있으면 사용 (버전에 따라 다름)
  eof --search-path "$SLC_DIR" --save-dir "$AUX_DIR" --asf 2>/dev/null || true   # TODO(verify): aux_cal 옵션
  # 위가 안 되면 ESA MPC(https://sar-mpc.eu) 또는 ASF 에서 S1[AB]_AUX_CAL_*.SAFE 를 받아 $AUX_DIR 에 풀어둔다.
  [ -z "$(ls -A "$AUX_DIR" 2>/dev/null)" ] && \
    echo "  [수동필요] AUX_CAL 미수신 — sar-mpc.eu 에서 최신 AUX_CAL.SAFE 를 $AUX_DIR 에 배치"
fi

echo "완료: SLC=$SLC_DIR  orbits=$ORBIT_DIR_PATH  DEM=$DEM_DIR  aux=$AUX_DIR"
