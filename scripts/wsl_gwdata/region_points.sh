#!/usr/bin/env bash
# (WSL) 정합된 스택을 교량 묶음 구역(region_points.py plan)마다 MiaplPy → SARvey 로 돌린다.
#
#   bash scripts/wsl_gwdata/region_points.sh <stack_dir> <tiles.json> <out_root> [동시 구역 수=3] [구역 id ...]
#   예) bash region_points.sh ~/gangwon/stacks/asc54_gangneung /mnt/e/프로그램/data/gangneung/tiles.json ~/gangwon/out/gangneung_points 3
#
# 구역마다 <out_root>/<id>/{miaplpy,sarvey}, 로그 <out_root>/<id>/run.log, 끝나면 DONE(실패면 FAILED).
# DONE 이 있는 구역은 건너뛴다 — 중간에 끊겨도 같은 명령으로 이어 돈다.
# SARvey 설정은 내곡교 기준선과 같은 recipe(위상연결 켬)를 쓴다 — 결과를 그 기준선과 비교하기 위해서다.
# set -u 금지: conda activate 훅이 미정의 변수를 참조한다
STACK="${1:?stack_dir}"; TILES="${2:?tiles.json}"; OUT="${3:?out_root}"; PAR="${4:-3}"; shift 4 2>/dev/null || shift $#
RECIPE="${RECIPE:-/mnt/e/프로그램/data/recipe_naegok_asc/sarvey_config.json}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT"

if [ "${1:-}" = "--one" ]; then
  # 구역 × 프로세스를 동시에 돌리므로 계산 라이브러리 스레드를 1개로 묶는다. 안 묶으면 프로세스마다
  # 코어 수만큼 스레드를 띄워 부하가 140 을 넘고 위상연결이 수십 배 느려진다(강릉 2026-10-16 실측).
  export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1
  TID="$2"; D="$OUT/$TID"; mkdir -p "$D"; rm -f "$D/FAILED"
  exec >> "$D/run.log" 2>&1
  echo "=== $TID 시작 $(date '+%F %T')"
  SNWE=$(python3 -c "import json;t=[t for t in json.load(open('$TILES'))['tiles'] if t['id']=='$TID'][0];print(*t['snwe'])")
  read -r S N W E <<< "$SNWE"
  REF=$(ls "$STACK/baselines" | head -1 | cut -d_ -f1)
  source /home/insar/miniforge3/etc/profile.d/conda.sh

  M="$D/miaplpy"; T="$D/miaplpy.txt"; mkdir -p "$M"
  cat > "$T" <<EOF
miaplpy.load.processor      = isce
miaplpy.load.slcFile        = $STACK/merged/SLC/*/*.slc.full
miaplpy.load.metaFile       = $STACK/reference/IW*.xml
miaplpy.load.baselineDir    = $STACK/baselines
miaplpy.load.demFile        = $STACK/merged/geom_reference/hgt.rdr.full
miaplpy.load.lookupYFile    = $STACK/merged/geom_reference/lat.rdr.full
miaplpy.load.lookupXFile    = $STACK/merged/geom_reference/lon.rdr.full
miaplpy.load.incAngleFile   = $STACK/merged/geom_reference/los.rdr.full
miaplpy.load.azAngleFile    = $STACK/merged/geom_reference/los.rdr.full
miaplpy.load.compression    = no
miaplpy.load.startDate      = None
miaplpy.load.endDate        = None
miaplpy.subset.lalo         = $S:$N,$W:$E
miaplpy.subset.yx           = auto
miaplpy.multiprocessing.numProcessor = 4
miaplpy.inversion.patchSize          = 200
miaplpy.inversion.ministackSize      = 15
miaplpy.inversion.rangeWindow        = 15
miaplpy.inversion.azimuthWindow      = 5
miaplpy.inversion.shpTest            = auto
miaplpy.inversion.phaseLinkingMethod = auto
miaplpy.inversion.PsNumShp           = auto
miaplpy.interferograms.networkType   = single_reference
miaplpy.interferograms.referenceDate = $REF
EOF
  conda activate miaplpy
  if [ ! -s "$M/inputs/slcStack.h5" ]; then
    echo ">> load $(date '+%T')"
    load_slc_geometry.py --template "$T" --project_dir "$D" --work_dir "$M" || true   # 성공해도 비0 종료(30_miaplpy.sh 참고)
  fi
  [ -s "$M/inputs/slcStack.h5" ] || { echo "실패: slcStack.h5"; touch "$D/FAILED"; exit 1; }
  if [ ! -s "$M/inverted/phase_series.h5" ] || [ ! -s "$M/maskPS.h5" ]; then
    echo ">> phase_linking $(date '+%T')"
    miaplpyApp "$T" --dir "$M" --dostep phase_linking
    miaplpyApp "$T" --dir "$M" --dostep concatenate_patches
  fi
  [ -s "$M/inverted/phase_series.h5" ] || { echo "실패: phase_series.h5"; touch "$D/FAILED"; exit 1; }

  conda activate sarvey
  V="$D/sarvey"; mkdir -p "$V/inputs" "$V/outputs"
  ln -sf "$M/inputs/slcStack.h5" "$V/inputs/slcStack.h5"
  ln -sf "$M/inputs/geometryRadar.h5" "$V/inputs/geometryRadar.h5"
  python - "$RECIPE" "$V" "$M" <<'PY'
import json, sys, h5py
recipe, d, mia = sys.argv[1:4]
c = json.load(open(recipe, encoding="utf-8"))
for k in ("_README", "temporal_model", "bridge_profile"):
    c.pop(k, None)
with h5py.File(f"{mia}/inputs/slcStack.h5") as f:
    dates = [x.decode() if isinstance(x, bytes) else str(x) for x in f["date"][:]]
iso = lambda s: f"{s[:4]}-{s[4:6]}-{s[6:8]}"
c["general"].update(input_path=f"{d}/inputs/", output_path=f"{d}/outputs/", num_cores=4)
c["preparation"].update(start_date=iso(min(dates)), end_date=iso(max(dates)))
c["phase_linking"] = {"use_phase_linking_results": True, "inverted_path": f"{mia}/inverted/",
                      "num_siblings": 20, "mask_phase_linking_file": "", "use_ps": True,
                      "mask_ps_file": f"{mia}/maskPS.h5"}
json.dump(c, open(f"{d}/config.json", "w"), indent=2)
print(f"sarvey: {len(dates)} dates {iso(min(dates))}..{iso(max(dates))}")
PY
  echo ">> sarvey $(date '+%T')"
  ( cd "$V" && sarvey -f "$V/config.json" 0 4 )
  if ls "$V"/outputs/p2_*_ts.h5 >/dev/null 2>&1; then
    echo "=== $TID 끝 $(date '+%F %T')"; touch "$D/DONE"
  else
    echo "실패: SARvey p2 산출 없음"; touch "$D/FAILED"; exit 1
  fi
  exit 0
fi

IDS="$*"
[ -n "$IDS" ] || IDS=$(python3 -c "import json;print(' '.join(t['id'] for t in json.load(open('$TILES'))['tiles']))")
echo "구역: $IDS (동시 $PAR) → $OUT  시작 $(date '+%F %T')"
for id in $IDS; do [ -f "$OUT/$id/DONE" ] || echo "$id"; done \
  | xargs -P "$PAR" -I{} bash "$HERE/region_points.sh" "$STACK" "$TILES" "$OUT" "$PAR" --one {}
echo "전체 끝 $(date '+%F %T') — DONE $(ls "$OUT"/*/DONE 2>/dev/null | wc -l) · FAILED $(ls "$OUT"/*/FAILED 2>/dev/null | wc -l)"
