#!/usr/bin/env bash
# inframon 리눅스 설치 — 홈 디렉터리에 설치하고 앱 메뉴에 등록한다(root 불필요).
#
#   ./install_linux.sh              # 설치 (~/.local/share/inframon)
#   ./install_linux.sh --uninstall  # 제거
#
# 푼 tar.gz 폴더 안에서 실행하세요.
set -euo pipefail

APP="inframon"
PREFIX="${PREFIX:-$HOME/.local}"
APPDIR="$PREFIX/share/$APP"
BINDIR="$PREFIX/bin"
DESKTOP="$PREFIX/share/applications/$APP.desktop"
ICON="$PREFIX/share/icons/hicolor/256x256/apps/$APP.png"

if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$APPDIR" "$BINDIR/$APP" "$DESKTOP" "$ICON"
  command -v update-desktop-database >/dev/null && \
    update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true
  echo "제거 완료. (데이터 폴더는 그대로 둡니다)"
  exit 0
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
[ -x "$SRC/$APP" ] || { echo "이 폴더에 실행파일 '$APP' 이 없습니다: $SRC" >&2; exit 1; }

echo "== inframon 설치 → $APPDIR =="
rm -rf "$APPDIR"
mkdir -p "$APPDIR" "$BINDIR" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
cp -a "$SRC/." "$APPDIR/"

# PATH 용 런처 — 어디서 실행하든 앱 폴더를 기준으로 동작하게 한다.
cat > "$BINDIR/$APP" <<EOF
#!/usr/bin/env bash
exec "$APPDIR/$APP" "\$@"
EOF
chmod 755 "$BINDIR/$APP"

[ -f "$SRC/$APP.png" ] && install -m 644 "$SRC/$APP.png" "$ICON"
if [ -f "$SRC/$APP.desktop" ]; then
  sed "s|^Exec=.*|Exec=$BINDIR/$APP|" "$SRC/$APP.desktop" > "$DESKTOP"
  chmod 644 "$DESKTOP"
fi
command -v update-desktop-database >/dev/null && \
  update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true

echo "완료 — 앱 메뉴의 'inframon' 또는 터미널에서 \`$APP\` 로 실행하세요."
case ":$PATH:" in
  *":$BINDIR:"*) ;;
  *) echo "참고: $BINDIR 가 PATH 에 없습니다. ~/.profile 에 다음을 추가하세요:"
     echo "      export PATH=\"$BINDIR:\$PATH\"" ;;
esac
