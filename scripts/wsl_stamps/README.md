# StaMPS 레인 — SARvey 대신 갈아 끼울 수 있는 MT-InSAR 경로

SARvey 는 잘 도는 도구지만 **논문으로 인용하기가 마땅치 않다.** StaMPS 는
Hooper et al. 2004(GRL) · 2007(JGR) 로 널리 인용되고 구현도 공개돼 있어, 같은
자리에 끼울 수 있는 두 번째 길이 필요했다. 이 폴더가 그 길이다.

산출 형식은 **SNAP·SARvey 레인과 똑같다** — `track_*.h5`(`pixel_lonlat` · `epochs` ·
`los_mm` · `coh` · `incidenceAngle` · `bperp_m`). 그래서 뒤 단계(`bridge_run` ·
`run_mtinsar` · PINN · IFC 트윈)는 하나도 안 고치고 그대로 돈다.

    SLC ─┬─ SNAP ──────────── snaphu ─────────→ track_*.h5   (지금 쓰는 길)
         ├─ ISCE2 → MiaplPy → SARvey ─────────→ track_*.h5
         └─ SNAP(StaMPS Export) → mt_prep_snap → MATLAB stamps ─→ track_*.h5  ← 여기

## 먼저 알아 둘 것 — 이건 conda 로 못 깐다

StaMPS 의 시계열 부분은 **MATLAB 코드**다. `mt_prep_*` 전처리기만 C/C++ 이고,
`stamps(1,8)` 은 MATLAB 에서 돈다. 그래서

- **MATLAB 라이선스가 필요하다.** Octave 로 도는 포크가 있지만 공식은 아니다.
- conda 환경으로 못 만든다. `python -m inframon --doctor` 가 StaMPS 를 **선택 도구**
  로만 보는 이유다 — 없어도 F코어는 준비 완료로 뜬다.
- Windows 에서는 WSL 안에 두는 편이 낫다(`mt_prep_snap` 이 POSIX 셸 스크립트다).

## 단계

| | 하는 일 | 파일 |
|---|---|---|
| 00 | StaMPS 내려받고 전처리기 빌드 · `$STAMPS` 설정 | `00_setup_stamps.sh` |
| 10 | SNAP 으로 코레지스트레이션·간섭도 → StaMPS 내보내기 | `10_snap_export.sh` |
| 20 | `mt_prep_snap` — 패치 나누기 · 진폭분산(ADI)으로 후보 고르기 | `20_mt_prep.sh` |
| 30 | MATLAB `stamps(1,8)` — PS 선별 · 언래핑 | `30_stamps_matlab.sh` |
| 40 | 처리폴더 → inframon Track H5 | `40_to_inframon.py` |

40단계는 **`ps_plot` 으로 따로 내보낼 필요가 없다.** 처리폴더의 `ps2.mat` ·
`phuw2.mat` · `bp2.mat` · `la2.mat` · `pm2.mat` · `hgt2.mat` · `parms.mat` 를
`inframon.insar.stamps_io.read_stamps()` 가 직접 읽는다.

## 왜 직접 읽는가

`ps_plot` 으로 내보내면 **수직기선 B⊥ 와 입사각이 같이 안 나온다.** 그 둘이 있어야
점별 잔차고도 Δh 와 열팽창을 같이 풀 수 있고(`run_mtinsar.py`), 그게 이 파이프라인이
SNAP 뒤에 더 붙인 부분이다. 처리폴더를 직접 읽으면 셋 다 그대로 나온다.

    # StaMPS 처리폴더를 그대로 MT-InSAR 에 물린다 (SNAP 산출물이 없어도 된다)
    python scripts/run_mtinsar.py --only 가양대교 --stamps ~/stamps/가양대교
    python scripts/run_mtinsar.py --stamps-root ~/stamps      # 전 교량

## 부호 규약

`stamps_io.phase_to_los_mm` 은 `d[mm] = −λ/(4π)·φ·1000` 을 쓴다. **양수 = 위성 접근**
으로, SNAP·HyP3 백엔드와 같다. StaMPS 자체 그림(`ps_plot('v')`)은 부호가 반대로
보이는 경우가 있으니 그림끼리 비교할 때 주의한다.

## 걸리는 데

- **`phuw2.mat` 의 시점 수가 `ps2.mat` 의 `day` 와 다르다** — 단일마스터(PS)냐
  소기선(SB)이냐에 따라 마스터 열이 있기도 없기도 하다. `read_stamps` 는 이때
  **조용히 자르지 않고 멈춘다.** 자르면 나중에 아무도 원인을 못 찾는다.
- **v7.3 .mat 은 HDF5 이고 열우선** — h5py 로 읽으면 전치돼 나온다. 처리해 뒀다.
- **`day` 는 MATLAB datenum** — 719529 를 빼야 유닉스 날짜가 된다. 처리해 뒀다.
- **`mean_range` 가 없는 경우** — 슬랜트 거리는 잔차고도 환산 K = 1000·B⊥/(R·sinθ)
  에 들어간다. 없으면 `run_mtinsar.py --slant-range` 기본값(880 km)을 쓰고 그
  사실을 `mtinsar.json` 의 `origin` 에 적는다. Δh 가 그만큼 치우친다.
- **패치를 나눴다면** `PATCH_1` … 아래에 결과가 흩어진다. `read_stamps` 는 상위
  폴더를 주면 첫 패치를 잡고 그 사실을 `sources["dir"]` 에 적는다. 여러 패치를
  합치려면 StaMPS 쪽에서 `ps_merge_patches` 를 먼저 돌린다.

## 인용

> Hooper, A., Zebker, H., Segall, P., Kampes, B. (2004). A new method for measuring
> deformation on volcanoes and other natural terrains using InSAR persistent
> scatterers. *Geophysical Research Letters*, 31, L23611.
>
> Hooper, A., Segall, P., Zebker, H. (2007). Persistent scatterer InSAR for crustal
> deformation analysis, with application to Volcán Alcedo, Galápagos.
> *Journal of Geophysical Research*, 112, B07407.
