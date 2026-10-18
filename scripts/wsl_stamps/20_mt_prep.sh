#!/usr/bin/env bash
# 20단계 — mt_prep_snap: 패치로 나누고 **진폭분산(ADI)** 으로 PS 후보를 고른다.
#
# da_thresh 가 ADI 문턱이다(Ferretti 2001). 0.4 면 후보가 많고 느리며, 0.25 면
# 적고 빠르다. 교량처럼 강한 산란체를 볼 때는 0.4 로 넉넉히 뽑아 두고 뒤에서
# 거르는 편이 낫다 — 한번 버린 점은 되돌아오지 않는다.
#
# 사용:  ./20_mt_prep.sh <INSAR_마스터폴더> <마스터YYYYMMDD> [da_thresh] [패치r] [패치a]
set -euo pipefail
DIR="${1:?INSAR_<마스터> 폴더}"; MASTER="${2:?마스터 YYYYMMDD}"
DA="${3:-0.4}"; PR="${4:-1}"; PA="${5:-1}"; OR="${6:-50}"; OA="${7:-200}"
command -v mt_prep_snap >/dev/null || { echo "!! mt_prep_snap 이 없다 — 00단계부터"; exit 1; }

cd "$DIR"
echo ">> mt_prep_snap $MASTER $DIR $DA  패치 ${PR}x${PA} (겹침 ${OR}/${OA})"
mt_prep_snap "$MASTER" "$DIR" "$DA" "$PR" "$PA" "$OR" "$OA"

echo
echo ">> 후보 수"
wc -l < pscands.1.ij 2>/dev/null || echo "   (pscands.1.ij 가 없다 — 패치 폴더를 보라)"
echo "   다음: ./30_stamps_matlab.sh $DIR"
