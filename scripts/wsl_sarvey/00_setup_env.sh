#!/usr/bin/env bash
# 00단계 — WSL2 에 SARvey 툴체인 환경 구축 (sudo 불필요: conda 를 홈에 설치).
# ⚠️ 네트워크 수백 MB~수 GB 다운로드, 수십 분 소요. 디스크 여유 확인.
# 사용:  bash 00_setup_env.sh
set -euo pipefail

MF="$HOME/miniforge3"

# (1) Miniforge (conda) — root 없이 홈에 설치
if [ ! -x "$MF/bin/conda" ]; then
  echo ">> Miniforge 설치"
  URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh"
  if command -v wget >/dev/null; then wget -q "$URL" -O ~/miniforge.sh
  else curl -fsSL "$URL" -o ~/miniforge.sh; fi   # wget/curl 둘 중 하나
  bash ~/miniforge.sh -b -p "$MF"
  "$MF/bin/conda" init bash
fi
source "$MF/etc/profile.d/conda.sh"

# (2) ISCE2 — **필수**(스택·코레지스트레이션의 근간). 실패하면 여기서 중단한다:
#     조용히 넘어가면 20단계에서 알 수 없는 오류로 다시 만난다.
echo ">> env: isce2 (검색·다운로드·코레지스트레이션) — 필수"
if ! conda create -y -n isce2 -c conda-forge "python=3.10" isce2 asf_search sentineleof sardem; then
  echo "⛔ ISCE2 설치 실패 — F코어(20단계)는 ISCE2 없이 진행할 수 없습니다."
  echo "   네트워크/디스크 확인 후 재실행: bash 00_setup_env.sh"
  echo "   (검색·다운로드만 필요하면: conda create -n s1 -c conda-forge python=3.10 asf_search sentineleof sardem)"
  exit 1
fi

# (3) MiaplPy + MintPy
echo ">> env: miaplpy"
conda create -y -n miaplpy -c conda-forge "python=3.10" mintpy || true
# miaplpy 가 conda-forge 에 없으면: conda activate miaplpy && pip install git+https://github.com/insarlab/MiaplPy.git

# (4) SARvey (+ inframon 코어: 50 변환/인제스트용)
# 리포 경로는 이 스크립트 위치에서 역산한다 — 드라이브 문자를 하드코딩하면 리포를
# 옮기는 순간(D:→E: 이사처럼) 조용히 깨진다.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
echo ">> env: sarvey  (inframon 코어: $REPO)"
conda create -y -n sarvey -c conda-forge "python=3.10" numpy h5py
conda activate sarvey && pip install sarvey && pip install -e "$REPO" || true

echo
echo "완료. 새 셸을 열거나 'source ~/.bashrc' 후:"
echo "  conda activate isce2   && ./10_download.sh   <recipe> <work>"
echo "  conda activate isce2   && ./20_stack_isce.sh <recipe> <work>"
echo "  conda activate miaplpy && ./30_miaplpy.sh    <recipe> <work>"
echo "  conda activate sarvey  && ./40_sarvey.sh     <recipe> <work>"
echo "⚠️ ISCE_STACK 환경변수(topsStack 경로) 지정 필요 — 20단계 참고."
