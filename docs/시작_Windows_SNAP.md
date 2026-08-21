# 시작 — Windows 네이티브 SNAP 경로 (레인 A, WSL 불필요)

> 이 문서는 **WSL·리눅스 없이 Windows 만으로** 실 Sentinel-1 → Track H5 → CRI 를 뽑는
> 기본 경로다. 최고품질 full PSI 가 필요하면 레인 B(`F_SARvey_WSL2.md`), 로컬 연산조차
> 없애려면 레인 C(`시작_클라우드_HyP3.md`)를 본다. 세 레인의 산출물(Track H5 계약)은
> 동일해서 하류는 그대로다.

## 0. 준비물 (1회)

1. **ESA SNAP 설치** — <https://step.esa.int/main/download/snap-download/>
   기본 경로(`C:\Program Files\esa-snap\bin\gpt.exe`)면 자동탐지된다. 다른 곳이면
   `--snap-gpt PATH` 로 지정.
2. **Earthdata 계정**(무료) — SLC 다운로드용. <https://urs.earthdata.nasa.gov>
   자격은 `--earthdata-token`(권장) / `--earthdata-user`+`--earthdata-pass` / `~/.netrc` 순.
3. `pip install -e ".[search,insar]"` — asf_search(조회)·rasterio(지오코딩 tif 읽기).

## 1. 한 방 실행 — 좌표만 주면 끝

```bash
python -m inframon --snap-auto 37.3219,127.1083 --out data/jeongja/track_snap.h5 \
  --snap-count 8 --snap-start 2024-01-01 --snap-end 2025-07-01 \
  --earthdata-token YOUR_TOKEN
```

내부 순서: ① ASF 조회로 교량을 덮는 **최적 프레임 자동선정**(footprint 중심성) →
② SLC 다운로드 → ③ 교량 burst 자동판별 → ④ SNAP `gpt` 스타 네트워크
(기준 vs 각 보조, 코레지+간섭도+지오코딩) → ⑤ Track H5.

- 이미 SLC 가 있으면: `--snap-insar SLC_DIR --snap-target LAT,LON`
- ERA5 기상 기반 master 선정·악천후 소거: `--snap-era5-master`
- 여러 교량 배치(같은 burst 코레지 재사용): `--snap-bridges bridges.json`
- 상승+하강 연직분해: 두 번 돌린 뒤 `--snap-fuse ASC_H5,DESC_H5`

## 2. 검증 → 인제스트 → 해석 (모든 레인 공통)

```bash
python -m inframon --check-track data/jeongja/track_snap.h5        # 사전검증 (exit 0 = 가능)
python -m inframon --import-track-h5 data/jeongja/track_snap.h5 --out data/project.h5
python -m inframon --demo --insar-source data/jeongja/track_snap.h5 --out data/project.h5 \
  --engine cv=real --engine insar=real --engine pinn=real --engine fram=real
```

대시보드: `streamlit run src/inframon/dashboard/app.py` → 사이드바에서 `data/project.h5`.

## 3. 품질·한계 (자리매김)

| | 레인 A (SNAP) | 레인 B (SARvey/WSL) | 레인 C (HyP3/클라우드) |
|---|---|---|---|
| 처리 위치 | 로컬 Windows | 로컬 WSL2 | ASF 클라우드 |
| 방법론 | 스타 네트워크, 교량 burst 1개 | full PSI(시공간 unwrap·치밀화) | 스타 네트워크, burst InSAR |
| 속도 | 장당 수 분 | 수 시간~ | 로컬 0 (잡 대기만) |
| 용도 | **실용 모니터링 기본값** | 연구·정밀 검증 | 저사양·무설치 보급 |

SNAP 경로 구현: `src/inframon/insar/snap_backend.py` (그래프 XML 은 `scripts/snap/`).
