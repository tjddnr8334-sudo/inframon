#!/usr/bin/env bash
# (WSL) SARvey 에 MiaplPy 위상연결(DS) 결과를 켠 것과 끈 것을 같은 스택에서 나란히 돌린다.
#
#   bash scripts/wsl_gwdata/sarvey_pl_compare.sh <miaplpy_dir> <out_root> [recipe_sarvey_config.json]
#   예) bash sarvey_pl_compare.sh /home/insar/insar_processing_naegok_full/miaplpy ~/gangwon/out/naegok_sarvey
#
# 전제: <miaplpy_dir>/inputs/{slcStack.h5,geometryRadar.h5}, <miaplpy_dir>/inverted/phase_series.h5, <miaplpy_dir>/maskPS.h5
# 산출: <out_root>/pl_off/outputs, <out_root>/pl_on/outputs, 로그 <out_root>/*.log
# 두 실행은 병렬(각 4 코어). 끝나면 sarvey_pl_report.py 로 점 수를 비교한다.
# set -u 금지: conda activate 훅이 미정의 변수를 참조한다
MIA="${1:?miaplpy_dir}"; OUT="${2:?out_root}"
RECIPE="${3:-/mnt/e/프로그램/data/recipe_naegok_asc/sarvey_config.json}"
for f in "$MIA/inputs/slcStack.h5" "$MIA/inputs/geometryRadar.h5" "$MIA/inverted/phase_series.h5" "$MIA/maskPS.h5"; do
  [ -f "$f" ] || { echo "없음: $f"; exit 2; }
done
mkdir -p "$OUT"
source /home/insar/miniforge3/etc/profile.d/conda.sh; conda activate sarvey

for MODE in pl_off pl_on; do
  D="$OUT/$MODE"; mkdir -p "$D/inputs" "$D/outputs"
  ln -sf "$MIA/inputs/slcStack.h5" "$D/inputs/slcStack.h5"
  ln -sf "$MIA/inputs/geometryRadar.h5" "$D/inputs/geometryRadar.h5"
  python - "$RECIPE" "$D" "$MIA" "$MODE" <<'PY'
import json, sys, h5py
recipe, d, mia, mode = sys.argv[1:5]
c = json.load(open(recipe, encoding="utf-8"))
for k in ("_README", "temporal_model", "bridge_profile"):
    c.pop(k, None)
with h5py.File(f"{mia}/inputs/slcStack.h5") as f:
    dates = [x.decode() if isinstance(x, bytes) else str(x) for x in f["date"][:]]
iso = lambda s: f"{s[:4]}-{s[4:6]}-{s[6:8]}"
c["general"]["input_path"] = f"{d}/inputs/"
c["general"]["output_path"] = f"{d}/outputs/"
c["general"]["num_cores"] = 4
c["preparation"]["start_date"] = iso(min(dates))
c["preparation"]["end_date"] = iso(max(dates))
if mode == "pl_on":
    c["phase_linking"] = {
        "use_phase_linking_results": True,
        "inverted_path": f"{mia}/inverted/",
        "num_siblings": 20,
        "mask_phase_linking_file": "",
        "use_ps": True,
        "mask_ps_file": f"{mia}/maskPS.h5",
    }
else:
    c["phase_linking"] = {"use_phase_linking_results": False}
json.dump(c, open(f"{d}/config.json", "w"), indent=2)
print(f"{mode}: {len(dates)} dates {iso(min(dates))}..{iso(max(dates))} → {d}/config.json")
PY
  ( cd "$D" && setsid nohup sarvey -f "$D/config.json" 0 4 > "$OUT/$MODE.log" 2>&1 < /dev/null & )
  echo "launched $MODE (log $OUT/$MODE.log)"
done
sleep 5; pgrep -af "sarvey -f" | grep -v pgrep | cut -c1-120
