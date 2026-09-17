#!/usr/bin/env bash
# 30단계 — MATLAB 에서 stamps(1,8) 을 돌린다. PS 선별부터 언래핑까지.
#
# 1 부하기 · 2 진폭분산 추정 · 3 PS 선별 · 4 가중치 · 5 잡음 제거 · 6 언래핑
# 7 공간상관 오차 · 8 SB 병합. 시간이 오래 걸리니 nohup 으로 띄우는 편이 낫다.
#
# 사용:  ./30_stamps_matlab.sh <INSAR_마스터폴더> [시작] [끝]
set -euo pipefail
DIR="${1:?INSAR_<마스터> 폴더}"; A="${2:-1}"; B="${3:-8}"
command -v matlab >/dev/null || {
  echo "!! matlab 이 없다. StaMPS 시계열은 MATLAB 코드라 라이선스가 필요하다."
  echo "   SARvey 레인(scripts/wsl_sarvey/)으로 가거나 SNAP+snaphu 레인을 쓴다."; exit 1; }

cd "$DIR"
echo ">> matlab -batch \"addpath(genpath('\$STAMPS/matlab')); stamps($A,$B)\""
matlab -nodisplay -nosplash -batch \
  "addpath(genpath('$STAMPS/matlab')); stamps($A,$B); exit"

echo
echo ">> 나온 파일"
ls -1 ps2.mat phuw2.mat pm2.mat la2.mat hgt2.mat bp2.mat parms.mat 2>/dev/null || true
echo "   다음: python3 40_to_inframon.py --stamps $DIR --out track_stamps.h5"
