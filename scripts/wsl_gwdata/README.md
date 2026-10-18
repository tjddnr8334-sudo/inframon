# 강원 작업 디스크 — E:\wsl\gangwon (관리자 권한 없이 WSL 에 ext4 디스크 추가)

2026-09-26. WSL 루트(D:\WSL, 3.6 TB)가 99%, F: 가 100% 라 강원 스택(약 245 GB + 12일마다 6 GB)을
E:(2.6 TB 여유)에 둔다. `wsl --mount --vhd` 는 관리자 권한이 필요해 못 쓰고, 대신 **데이터 전용
배포판** 을 E: 에 만들고 그 디스크를 `/mnt/wsl` 로 공유한다.

## 만든 것

| 항목 | 값 |
|---|---|
| 배포판 | `gwdata` (Alpine 3.20 minirootfs, 3.5 MB) |
| 디스크 | `E:\wsl\gangwon\ext4.vhdx`, 동적 1 TB (필요하면 `wsl --manage gwdata --resize 2TB`) |
| Ubuntu 에서 보이는 경로 | `/mnt/wsl/gangwon` (= `~/gangwon` 심볼릭 링크) |
| 하위 폴더 | `slc/ orbits/ dem/ stacks/ out/ logs/` |
| 쓰기 속도 (dd 2 GiB, fsync) | 1.5 GB/s. 비교: `/mnt/e` 9p 경유 83 MB/s |
| 자동 마운트 | 로그인 시 `Startup\wsl_gangwon_mount.cmd` → `wsl -d gwdata -- true`. Ubuntu `.bashrc` 도 `ensure_gangwon.sh` 호출 |

## 재현 절차 (다른 PC)

```powershell
New-Item -ItemType Directory -Force E:\wsl\gangwon
Invoke-WebRequest https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/x86_64/alpine-minirootfs-3.20.3-x86_64.tar.gz -OutFile E:\wsl\alpine-minirootfs.tar.gz
wsl --import gwdata E:\wsl\gangwon E:\wsl\alpine-minirootfs.tar.gz --version 2
wsl -d gwdata -u root -- sh -c 'mkdir -p /data/{slc,orbits,dem,stacks,out,logs}; chmod 1777 /data; printf "[boot]\ncommand=mkdir -p /mnt/wsl/gangwon; mountpoint -q /mnt/wsl/gangwon || mount -o bind /data /mnt/wsl/gangwon; chmod 1777 /mnt/wsl/gangwon\n" > /etc/wsl.conf'
wsl --terminate gwdata; wsl -d gwdata -- true
wsl -d Ubuntu-22.04 -- ls /mnt/wsl/gangwon      # slc orbits dem stacks out logs
```

Ubuntu 쪽: `cp scripts/wsl_gwdata/ensure_gangwon.sh ~/bin/` 후 `.bashrc` 에 호출 한 줄.

## 주의

- busybox `mount` 는 `--bind` 가 아니라 `-o bind` 다.
- gwdata 가 Stopped 로 보여도 정상이다. 마운트는 VM 에 남는다. `wsl --shutdown` 뒤엔 `wsl -d gwdata -- true` 한 번.
- `wsl --unregister gwdata` 는 **디스크째 삭제**다. 절대 하지 말 것.

## 데이터 흐름 (2026-09-26 확정)

| 단계 | 어디서 | 경로 | 이유 |
|---|---|---|---|
| ASF 버스트 다운로드 | **Windows** `python scripts/wsl_gwdata/download_bursts.py` | `E:\InSAR_DATA\gangwon\bursts\<burst>\` | WSL 은 루트 디스크 99% + 다른 작업의 I/O 로 파이썬이 멈춤. Windows 는 무관 |
| SAFE 조립 (burst2safe) | Windows conda env `b2s` (anaconda3, gdal+burst2safe) | `E:\InSAR_DATA\gangwon\safe\<path>\` | 같은 이유 |
| ISCE topsStack · MiaplPy | WSL Ubuntu, 입력은 `/mnt/e/InSAR_DATA/gangwon/safe` 에서 `~/gangwon/slc` 로 복사 후 | `~/gangwon/stacks/<burst_set>/` | ISCE 는 WSL 에만 있음 |

ASF 버스트 추출기는 첫 요청에 `202 {"message":"Accepted"}` 를 주고 비동기로 만든다 — 다운로더가 20 초마다 재요청한다.

## 강릉 상승(54/IW2 114060–062) 파일럿 실행 순서

| # | 어디서 | 명령 | 상태 2026-09-26 |
|---|---|---|---|
| 1 | Windows | `python scripts/wsl_gwdata/download_bursts.py --out E:/InSAR_DATA/gangwon/bursts --bursts 054_114060_IW2 054_114061_IW2 054_114062_IW2 --start 2025-01-01` (버스트별로 3 프로세스 병렬 가능) | 완료 2026-09-26 17:32 — 110 파일 16 GB, 실패 0 |
| 2 | Windows | `python scripts/wsl_gwdata/download_orbits.py --bursts 054_114060_IW2 --start 2025-01-01 --save-dir E:/InSAR_DATA/gangwon/orbits` → WSL `~/gangwon/orbits` 로 복사 | 36 EOF 완료 (S1A·S1C·S1D) |
| 3 | WSL | `bash scripts/wsl_gwdata/make_dem_gangneung.sh` 후 `bash scripts/wsl_gwdata/dem_to_isce.sh` | 완료 — dem.wgs84/.xml/.vrt, 5221×2881 SHORT |
| 4 | WSL | AUX_CAL: `~/insar_processing_naegok_full/aux` → `~/gangwon/aux` 복사 | 70 파일 완료 |
| 5 | Windows | `python scripts/wsl_gwdata/build_safes.py --bursts-dir E:/InSAR_DATA/gangwon/bursts --bursts 054_114060_IW2 054_114061_IW2 054_114062_IW2 --out E:/InSAR_DATA/gangwon/safe/asc54_gangneung` | 완료 — 37 SAFE (2025-11-16 은 114060 버스트가 ASF 에 없어 2 버스트뿐) |
| 6 | WSL | `bash scripts/wsl_gwdata/sync_safes.sh asc54_gangneung` | 완료 — `~/gangwon/slc/asc54_gangneung` 36 SAFE 16 GB. 2025-11-16 은 `slc/_excluded_asc54_gangneung/` 로 빼둠(버스트 부족, 스택 공통 버스트 집합을 깨뜨림) |
| 7 | WSL | `bash scripts/wsl_gwdata/run_stack.sh asc54_gangneung "37.41 38.05 128.04 129.18"` | 2026-10-16 시작. bbox 북단은 38.05 — 버스트 SAFE 북쪽 끝이 38.054° 라 38.08 이면 stackSentinel 이 "dates covering the bbox (0)" 로 멈춘다 |
| 8 | WSL | MiaplPy (기존 `scripts/wsl_sarvey/30_miaplpy.sh` 참고) → 교량별 PS/DS 추출 | 7 뒤 |

주의: ISCE `Sentinel1.py` 는 S1C·S1D 를 인식한다(확인함). conda 의 `isce2-activate.sh` 가 `$ISCE_HOME` 을 참조하므로
`set -u` 스크립트에서는 activate 전에 `export ISCE_HOME=...` 이 먼저 와야 한다.
sardem 은 Windows(b2s) 에서 GDAL Warp 오류로 실패, WSL isce2_mintpy 환경에서 `PROJ_DATA`·`GDAL_DATA` 를 지정해야 돈다.

## SARvey 성능 개선 1단계 — 위상연결(DS) 결과 켜기 (2026-09-27)

바뀐 것:
- `src/inframon/insar/sarvey_config.py`: `phase_linking.use_phase_linking_results=True`, `use_ps=True`, `inverted_path`·`mask_ps_file` 추가.
- `scripts/wsl_sarvey/30_miaplpy.sh`: load_data 뒤에 `miaplpyApp --dostep phase_linking` → `--dostep concatenate_patches` 를 돌려
  `miaplpy/inverted/phase_series.h5` 와 `miaplpy/maskPS.h5` 를 만든다 (거리창 15 × 방위창 5, 미니스택 15).
- `scripts/wsl_sarvey/40_sarvey.sh`: 위 두 파일이 없으면 위상연결을 자동으로 끄고 경고한다.

검증:
- `sarvey_pl_compare.sh`: 같은 스택에서 끔/켬을 병렬 실행. 내곡교 119장(`insar_processing_naegok_full`)으로 실행 → `~/gangwon/out/naegok_sarvey/{pl_off,pl_on}`.
- `sarvey_pl_report.py`: p1·p2 점을 OSM 데크 폴리곤 안에서 세고 20 m 격자 채움률을 낸다.

결과 (내곡교 119장, 2018-06-26~2025-12-22, OSM 데크 폴리곤 ≈180 m 구간, 20 m 격자 9칸):

| 실행 | p1 (AOI / 데크) | p2 coh≥0.7 (AOI / 데크) | 격자 채움 | 소요 |
|---|---|---|---|---|
| 위상연결 끔 | 3,457 / 3 | 31,892 / 15 | 6/9 (67%) | 28분 |
| 위상연결 켬 | 3,457 / 3 | 78,633 / **30** | **9/9 (100%)** | 41분 |

p1(1차 점)은 같고, 2차 밀집화에서 DS 후보가 들어와 데크 점이 2배·격자 빈칸이 0 이 됐다. 점 수만 늘린 것이 아닌지는
다음 단계에서 GNSS 대조·시계열 잔차로 확인한다. 산출: `~/gangwon/out/naegok_sarvey/{pl_off,pl_on,pl_compare.json}`.

30_miaplpy.sh 의 새 위상연결 경로 검증 (2026-09-27): 내곡교 19장 스택(`insar_processing_naegok`)에서 `miaplpyApp --dostep phase_linking`
(12 패치, 59분 — WSL 포화 상태) → `--dostep concatenate_patches` 로 `inverted/phase_series.h5`(62 MB)·`maskPS.h5` 생성 확인. 정상.
