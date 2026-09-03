#!/usr/bin/env bash
# inframon — 이 파일 하나로 설치부터 결과까지. (옵션: ./start.sh --dashboard)
cd "$(dirname "$0")" || exit 1
python3 start.py "$@"
