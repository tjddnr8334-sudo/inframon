#!/usr/bin/env bash
# 40단계 — SARvey MTI 시계열 추정 + 내보내기.
# 전제: conda activate sarvey (SARvey 설치)
# 사용:  ./40_sarvey.sh <recipe_dir> <work_dir>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RECIPE_DIR="${1:?recipe_dir}"; WORK="${2:?work_dir}"

MIAPLPY_DIR="$WORK/miaplpy"
SARVEY_DIR="$WORK/sarvey"; mkdir -p "$SARVEY_DIR/inputs" "$SARVEY_DIR/outputs"

# MiaplPy 입력을 SARvey inputs/ 로 연결
ln -sf "$MIAPLPY_DIR/inputs/slcStack.h5"      "$SARVEY_DIR/inputs/slcStack.h5"
ln -sf "$MIAPLPY_DIR/inputs/geometryRadar.h5" "$SARVEY_DIR/inputs/geometryRadar.h5"

# inframon 이 생성한 config 사용 (input/output 경로만 현재 작업폴더로 맞춤)
CONFIG="$SARVEY_DIR/config.json"
python3 - "$RECIPE_DIR/sarvey_config.json" "$CONFIG" "$SARVEY_DIR" <<'PY'
import json, sys
src, dst, work = sys.argv[1], sys.argv[2], sys.argv[3]
c = json.load(open(src))
c.pop("_README", None)                      # SARvey 는 모르는 키
c.setdefault("general", {})["input_path"]  = f"{work}/inputs/"
c["general"]["output_path"] = f"{work}/outputs/"
json.dump(c, open(dst, "w"), indent=2)
print("config:", dst)
PY

echo ">> SARvey 실행 (steps 0..4: preparation→consistency→unwrapping→filtering→densification)"
cd "$SARVEY_DIR"
sarvey -f "$CONFIG" 0 4         # TODO(verify): 스텝 번호/범위, --workdir 옵션은 버전별 확인

echo ">> 결과 내보내기"
# SARvey 산출(outputs/p2_*_ts.h5)을 inframon Track H5 로 변환 (다음 단계 50)
echo "   outputs/ 내용:"; ls -1 "$SARVEY_DIR/outputs" || true
echo "완료. 이제: python3 $HERE/50_export_to_inframon.py --sarvey-h5 <outputs/...ts.h5> --out track_${AOI_NAME:-bridge}.h5"
