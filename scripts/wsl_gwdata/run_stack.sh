#!/usr/bin/env bash
# (WSL) 버스트 SAFE 스택 → ISCE2 topsStack 코레지스트레이션 (MiaplPy/SARvey 입력용 coreg SLC).
#
#   bash scripts/wsl_gwdata/run_stack.sh <set> "<S N W E>" [ref_date YYYYMMDD] [--force]
#   예) bash run_stack.sh asc54_gangneung "37.41 38.05 128.04 129.18"
#
# 입력: ~/gangwon/slc/<set>/S1*_IW_SLC*.SAFE  (sync_safes.sh 로 E: 에서 복사)
#       ~/gangwon/dem/dem.wgs84(.xml/.vrt)  ~/gangwon/orbits/*.EOF  ~/gangwon/aux/
# 출력: ~/gangwon/stacks/<set>/  (run_files/, configs/, coreg_secondarys/, merged/SLC/ ...)
# 재개: run_files/run_NN 마다 .done 마커. 다시 실행하면 안 끝난 단계부터.
# 다른 stackSentinel 이 돌고 있으면 기본적으로 시작하지 않는다 (--force 로 강행).
set -u
SET="${1:?set name}"; BBOX="${2:?bbox 'S N W E'}"; REF="${3:-}"; FORCE="${4:-}"
[ "$REF" = "--force" ] && { FORCE="--force"; REF=""; }
GW=/home/insar/gangwon
SLC="$GW/slc/$SET"; WORK="$GW/stacks/$SET"; DEM="$GW/dem/dem.wgs84"
LOGDIR="$GW/logs"; mkdir -p "$LOGDIR" "$WORK"
LOG="$LOGDIR/stack_${SET}.log"
say() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

# conda 의 isce2-activate.sh 가 $ISCE_HOME 을 참조하므로 set -u 아래서는 먼저 export 해야 한다
export ISCE_HOME=/home/insar/miniforge3/envs/isce2_mintpy/lib/python3.11/site-packages/isce
set +u; source /home/insar/miniforge3/etc/profile.d/conda.sh; conda activate isce2_mintpy; set -u
export ISCE_STACK=/home/insar/miniforge3/envs/isce2_mintpy/share/isce2
export PATH="$ISCE_STACK/topsStack:$ISCE_HOME/bin:$ISCE_HOME/applications:$PATH"
export PYTHONPATH="$ISCE_HOME/components:$ISCE_STACK:${PYTHONPATH:-}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"

n_safe=$(ls -d "$SLC"/S1*_IW_SLC*.SAFE 2>/dev/null | wc -l)
[ "$n_safe" -ge 2 ] || { say "SAFE 가 $n_safe 개 — $SLC 에 2개 이상 필요 (sync_safes.sh 먼저)"; exit 2; }
for f in "$DEM" "$DEM.xml" "$DEM.vrt"; do [ -f "$f" ] || { say "DEM 누락: $f"; exit 3; }; done
[ "$(ls "$GW/orbits"/*.EOF 2>/dev/null | wc -l)" -ge 1 ] || { say "orbits 비어 있음"; exit 3; }
[ "$(ls "$GW/aux" 2>/dev/null | wc -l)" -ge 1 ] || { say "aux 비어 있음"; exit 3; }

if [ "$FORCE" != "--force" ] && pgrep -f "SentinelWrapper|stackSentinel" > /dev/null; then
  say "다른 stackSentinel/SentinelWrapper 가 실행 중이다. 디스크 I/O 경쟁을 피하려면 끝난 뒤 실행 (--force 로 강행)."
  pgrep -af "SentinelWrapper|stackSentinel" | cut -c1-120 | tee -a "$LOG"; exit 4
fi

if [ -z "$REF" ]; then  # 기준 영상: 날짜 목록의 중앙값
  REF=$(ls -d "$SLC"/S1*_IW_SLC*.SAFE | sed -E 's/.*_1S[SD]V_([0-9]{8})T.*/\1/' | sort -u | awk '{a[NR]=$1} END{print a[int((NR+1)/2)]}')
fi
say "set=$SET  SAFE=$n_safe  ref=$REF  bbox=$BBOX  work=$WORK"

cd "$WORK"
if [ ! -d run_files ] || [ -z "$(ls -A run_files 2>/dev/null)" ]; then
  say "stackSentinel.py 설정 생성"
  stackSentinel.py -s "$SLC" -d "$DEM" -a "$GW/aux" -o "$GW/orbits" -w "$WORK" \
      -b "$BBOX" -p vv -m "$REF" -W slc -C geometry -c 3 -z 1 -r 1 --num_proc 4 >> "$LOG" 2>&1
  rc=$?; [ $rc -eq 0 ] && [ -d run_files ] || { say "stackSentinel 실패 rc=$rc — $LOG 확인"; exit 5; }
  say "run_files: $(ls run_files | wc -l)개"
fi

for f in $(ls run_files/run_* | grep -v '\.done$' | sort); do
  name=$(basename "$f")
  [ -f "$f.done" ] && { say "skip $name (done)"; continue; }
  say "start $name"
  # topsStack run 파일은 마지막 묶음의 '&' 뒤에 wait 가 없다 — 그대로 bash 로 돌리면 마지막 날짜들이
  # 끝나기 전에 다음 단계로 넘어가고 그 프로세스가 죽는다(2026-10-16 강릉: resample 3날짜 누락). 끝에 wait 를 붙인다.
  { cat "$f"; echo; echo wait; } | bash >> "$LOGDIR/stack_${SET}_${name}.log" 2>&1
  rc=$?
  if [ $rc -ne 0 ]; then say "FAIL $name rc=$rc — $LOGDIR/stack_${SET}_${name}.log"; exit 6; fi
  touch "$f.done"; say "done  $name"
done
say "ALL DONE → $WORK/merged/SLC ($(ls "$WORK/merged/SLC" 2>/dev/null | wc -l) dates)"
