#!/usr/bin/env bash
# inframon 리눅스 빌드 — 뷰어 onedir 을 만들어 배포용 tar.gz 로 묶는다.
#
#   bash packaging/build_linux.sh            # 뷰어(경량)
#   bash packaging/build_linux.sh full       # 풀 빌드(torch/rasterio 포함, 수 GB)
#
# 산출: dist/inframon-<ver>-linux-x86_64.tar.gz
#       (푼 뒤 ./inframon 실행, 또는 install_linux.sh 로 메뉴 등록)
#
# PyInstaller 는 크로스컴파일이 안 된다 — 리눅스 바이너리는 반드시 리눅스에서 빌드해야 한다.
# Windows 사용자는 WSL2 나 컨테이너 안에서 이 스크립트를 돌리면 된다.
set -euo pipefail

cd "$(dirname "$0")/.."
VARIANT="${1:-viewer}"

case "$VARIANT" in
  viewer) SPEC="inframon.spec";      NAME="inframon" ;;
  full)   SPEC="inframon_full.spec"; NAME="inframon_full" ;;
  *) echo "사용법: $0 [viewer|full]" >&2; exit 2 ;;
esac

command -v python3 >/dev/null || { echo "python3 가 필요합니다." >&2; exit 1; }
python3 -c "import PyInstaller" 2>/dev/null || {
  echo "PyInstaller 가 없습니다 → pip install pyinstaller" >&2; exit 1; }

VER="$(python3 -c "import tomllib,pathlib;print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])")"
ARCH="$(uname -m)"

echo "== inframon $VER ($VARIANT) 리눅스 빌드 =="
python3 -m PyInstaller "$SPEC" --noconfirm

OUT="dist/${NAME}"
[ -d "$OUT" ] || { echo "빌드 산출물이 없습니다: $OUT" >&2; exit 1; }

# .desktop + 아이콘을 함께 넣어, 풀고 나서 install_linux.sh 만 돌리면 메뉴에 등록되게 한다.
install -m 644 assets/inframon.png "$OUT/inframon.png"
cat > "$OUT/inframon.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=inframon
Comment=InSAR·PINN·FRAM 교량 인프라 모니터링
Exec=inframon
Icon=inframon
Terminal=false
Categories=Science;Engineering;
EOF
install -m 755 packaging/install_linux.sh "$OUT/install_linux.sh"

TARBALL="dist/inframon-${VER}-linux-${ARCH}.tar.gz"
tar -czf "$TARBALL" -C dist "$NAME"
echo "완료: $TARBALL ($(du -h "$TARBALL" | cut -f1))"
