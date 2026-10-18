#!/usr/bin/env bash
# (WSL) Windows 에서 조립한 SAFE 들을 강원 ext4 디스크로 복사 (ISCE 는 9p /mnt/e 에서 직접 읽으면 느리다).
#   bash scripts/wsl_gwdata/sync_safes.sh <set>
#   원본: /mnt/e/InSAR_DATA/gangwon/safe/<set>/<date>/S1*_IW_SLC*.SAFE
#   대상: ~/gangwon/slc/<set>/S1*_IW_SLC*.SAFE   (날짜 폴더 없이 평평하게 — stackSentinel -s 가 glob 으로 찾음)
# 멱등: rsync 로 새 날짜만 복사.
set -u
SET="${1:?set name}"
SRC="/mnt/e/InSAR_DATA/gangwon/safe/$SET"; DST="/home/insar/gangwon/slc/$SET"
mkdir -p "$DST"
n=0
for safe in "$SRC"/*/S1*_IW_SLC*.SAFE; do
  [ -d "$safe" ] || continue
  name=$(basename "$safe")
  if [ -f "$DST/$name/manifest.safe" ] && [ "$(du -sb "$safe" | cut -f1)" -eq "$(du -sb "$DST/$name" | cut -f1)" ]; then continue; fi
  rsync -a --delete "$safe/" "$DST/$name/" && n=$((n+1))
done
echo "synced $n new SAFE → $DST ($(ls -d "$DST"/*.SAFE 2>/dev/null | wc -l) total, $(du -sh "$DST" | cut -f1))"
