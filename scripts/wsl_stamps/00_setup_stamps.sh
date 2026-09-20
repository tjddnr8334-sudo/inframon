#!/usr/bin/env bash
# 00단계 — StaMPS 내려받고 전처리기(mt_prep_*) 빌드 · $STAMPS 설정.
#
# StaMPS 의 시계열 부분은 MATLAB 코드다. 여기서 빌드하는 것은 C/C++ 전처리기뿐이고,
# MATLAB 은 따로 있어야 한다(라이선스 필요). conda 로 못 깔기 때문에 F코어 감지에서
# StaMPS 는 **선택 도구**다 — 없어도 준비 완료로 뜬다.
#
# 사용:  ./00_setup_stamps.sh [설치폴더]        기본값 $HOME/StaMPS
set -euo pipefail
DEST="${1:-$HOME/StaMPS}"
VERSION="${STAMPS_VERSION:-v4.1-beta}"

if [ ! -d "$DEST" ]; then
  echo ">> 내려받는다: dbekaert/StaMPS $VERSION → $DEST"
  git clone --depth 1 --branch "$VERSION" https://github.com/dbekaert/StaMPS.git "$DEST"
else
  echo ">> 이미 있다: $DEST (건너뛴다)"
fi

echo ">> 전처리기 빌드 (src/)"
make -C "$DEST/src" || {
  echo "!! 빌드 실패 — build-essential 과 libgfortran 이 필요하다:"
  echo "   sudo apt-get install -y build-essential gfortran"
  exit 1
}
make -C "$DEST/src" install

# 쉘에 $STAMPS 를 박아 둔다 — 감지 프로브(toolchain.py)가 이것을 본다.
LINE_A="export STAMPS=\"$DEST\""
LINE_B='export PATH="$STAMPS/bin:$PATH"'
for f in "$HOME/.bashrc" "$HOME/.profile"; do
  [ -f "$f" ] || continue
  grep -qF "$LINE_A" "$f" || { printf '%s\n%s\n' "$LINE_A" "$LINE_B" >> "$f"; }
done
export STAMPS="$DEST"; export PATH="$STAMPS/bin:$PATH"

echo
echo ">> 확인"
ls "$STAMPS/matlab/ps_load_initial_gamma.m" >/dev/null && echo "   MATLAB 코드 OK"
command -v mt_prep_snap >/dev/null && echo "   mt_prep_snap OK" \
  || echo "   !! mt_prep_snap 이 PATH 에 없다 — 새 셸을 열거나 source ~/.bashrc"
command -v matlab >/dev/null && echo "   matlab OK" \
  || echo "   !! matlab 이 없다 — 30단계(stamps(1,8))를 못 돌린다. 라이선스가 필요하다"
echo
echo "   다음: ./10_snap_export.sh <SLC폴더> <작업폴더> <마스터YYYYMMDD>"
