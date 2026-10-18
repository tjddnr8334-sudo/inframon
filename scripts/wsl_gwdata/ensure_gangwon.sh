#!/usr/bin/env bash
# 강원 작업 디스크를 WSL Ubuntu 에서 쓸 수 있게 붙인다. 멱등 — 여러 번 불러도 된다.
#
# 구조 (2026-09-26, docs/강원_전수감시_계획.md §7):
#   E:\wsl\gangwon\ext4.vhdx   ← 데이터 전용 WSL 배포판 "gwdata"(Alpine) 의 디스크. 1 TB 동적.
#   gwdata 의 /etc/wsl.conf [boot] 가 /data 를 /mnt/wsl/gangwon 에 bind-mount 한다.
#   /mnt/wsl 은 같은 WSL2 VM 의 모든 배포판이 공유하므로 Ubuntu 에서 ext4 속도로 보인다
#   (실측 쓰기 1.5 GB/s, /mnt/e 9p 경유는 83 MB/s).
#   gwdata 가 Stopped 여도 마운트는 VM 이 살아 있는 한 남는다. `wsl --shutdown`·재부팅 뒤에만
#   gwdata 를 한 번 깨우면 된다 — 이 스크립트가 그 일을 한다. 로그인 시 자동: Startup\wsl_gangwon_mount.cmd
set -u
GW=/mnt/wsl/gangwon
if [ ! -d "$GW/slc" ]; then
  /mnt/c/Windows/System32/wsl.exe -d gwdata -- true 2>/dev/null
  sleep 2
fi
if [ -d "$GW/slc" ]; then
  free=$(df -h "$GW" | awk 'NR==2 {print $4}')
  echo "gangwon disk: $GW (${free} free)"
  [ -L "$HOME/gangwon" ] || ln -sfn "$GW" "$HOME/gangwon"
  exit 0
fi
echo "gangwon disk NOT mounted — run on Windows: wsl.exe -d gwdata -- true" >&2
exit 1
