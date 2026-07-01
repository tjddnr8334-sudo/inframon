# NOTICE — 제3자 도구 및 라이선스 고지

inframon 은 **GPLv3** 로 배포됩니다(`LICENSE`). inframon 자체는 아래 InSAR 도구들을
**별도 프로그램으로(CLI 로) 호출**하며, 이들의 소스를 포함(embed)하거나 직접 링크하지
않습니다. 각 도구는 각자의 라이선스를 따릅니다.

## 기반 InSAR 처리 도구 (별도 설치 — WSL2/Linux)
| 도구 | 역할 | 라이선스 |
|---|---|---|
| [SARvey](https://github.com/luhipi/sarvey) | PS/DS 다중시기 InSAR 시계열 (핵심 엔진) | GPLv3 |
| [MiaplPy](https://github.com/insarlab/MiaplPy) | 위상연결·SLC 스택 로딩 | GPLv3 |
| [MintPy](https://github.com/insarlab/MintPy) | InSAR 시계열 처리 | GPLv3 |
| [ISCE2](https://github.com/isce-framework/isce2) | Sentinel-1 topsStack 코레지 | Apache-2.0 (일부 조건) |

## 주요 파이썬 의존성 (pip)
numpy·h5py·pydantic(코어), torch·transformers(PINN/CV), rasterio·pyproj·gdal(지리),
asf_search(ASF SLC 검색), streamlit·folium(대시보드), matplotlib(리포트),
sentineleof(궤도)·sardem(DEM). 각 패키지의 라이선스를 따릅니다.

## 데이터 출처
- Copernicus **Sentinel-1** SLC (ESA/Copernicus, ASF 배포) — Copernicus 데이터 이용약관.
- **Copernicus GLO-30 DEM**, **ERA5**(Open-Meteo 재배포), **OpenStreetMap**(ODbL) 교량 정보.
- 사용자는 각 데이터 제공처의 이용약관·인용 요건을 준수해야 합니다.

## 인용
`CITATION.cff` 참조. inframon 및 기반 도구(특히 SARvey)를 함께 인용해 주세요.
