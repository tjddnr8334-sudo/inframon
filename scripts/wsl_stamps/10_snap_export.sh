#!/usr/bin/env bash
# 10단계 — SNAP 으로 코레지스트레이션·간섭도를 만들고 **StaMPS 형식으로 내보낸다**.
#
# SNAP 의 StampsExport 연산자가 INSAR_<마스터> 폴더에 rslc/ifg/dem/geo 를 깔아 준다.
# 이 폴더가 20단계 mt_prep_snap 의 입력이다.
#
# 사용:  ./10_snap_export.sh <SLC폴더> <작업폴더> <마스터YYYYMMDD> [서브스와스] [폴라]
set -euo pipefail
SLC="${1:?SLC 폴더}"; WORK="${2:?작업 폴더}"; MASTER="${3:?마스터 YYYYMMDD}"
SUBSWATH="${4:-IW2}"; POL="${5:-VV}"
GPT="${GPT:-$HOME/esa-snap/bin/gpt}"
command -v "$GPT" >/dev/null || { echo "!! gpt 가 없다: $GPT (GPT=... 로 지정)"; exit 1; }

OUT="$WORK/INSAR_$MASTER"
mkdir -p "$WORK/stack" "$OUT"
M_ZIP=$(ls "$SLC"/*"$MASTER"*.zip | head -1)
[ -n "$M_ZIP" ] || { echo "!! 마스터 SLC 를 못 찾았다: $SLC/*$MASTER*.zip"; exit 1; }

# 슬레이브마다 따로 도는 편이 메모리에 안전하다 — 스택 한 번에 올리면 IW 전체가
# 수십 GB 가 된다.
for S_ZIP in "$SLC"/*.zip; do
  [ "$S_ZIP" = "$M_ZIP" ] && continue
  SLAVE=$(basename "$S_ZIP" | grep -oE '[0-9]{8}T' | head -1 | tr -d 'T')
  echo ">> $MASTER x $SLAVE"
  "$GPT" "$(dirname "$0")/graphs/snap_stamps_pair.xml" \
    -Pmaster="$M_ZIP" -Pslave="$S_ZIP" \
    -Psubswath="$SUBSWATH" -Ppol="$POL" -Ptarget="$OUT" \
    -c 8G -q "$(nproc)"
done

echo
echo ">> 내보낸 폴더: $OUT"
ls "$OUT"
echo "   다음: ./20_mt_prep.sh $OUT $MASTER"
