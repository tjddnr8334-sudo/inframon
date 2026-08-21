# 시작 — HyP3 클라우드 경로 (레인 C, 로컬 SAR 연산 없음)

> 간섭계 처리를 **ASF HyP3 클라우드**가 대신한다. SLC 원본(장당 4GB+) 다운로드도, SNAP·
> ISCE2 설치도 없다 — 무료 Earthdata 계정과 노트북이면 어느 OS 에서든 돈다. 산출물은
> 레인 A/B 와 동일한 Track H5 계약이라 하류(인제스트→4대 엔진→대시보드)는 그대로다.
> 구현: `src/inframon/insar/hyp3_backend.py`.

## 0. 준비물 (1회)

1. **Earthdata 계정**(무료) — <https://urs.earthdata.nasa.gov>
   ⚠️ HyP3 는 **월간 크레딧 쿼터**가 있다. 스타 N쌍 = 잡 N개 — 교량 1곳 8쌍이면 소량이지만,
   배치로 여러 교량을 돌릴 땐 쿼터를 확인한다: <https://hyp3-docs.asf.alaska.edu/using/credits/>
2. `pip install -e ".[hyp3]"` — hyp3_sdk(잡 제출)·asf_search(burst 조회)·rasterio(변환).

## 1. 한 방 실행 — 좌표만 주면 클라우드가 처리

```bash
python -m inframon --hyp3-insar 37.3219,127.1083 --out data/jeongja/track_hyp3.h5 \
  --hyp3-count 8 --hyp3-start 2024-01-01 --hyp3-end 2025-07-01 \
  --earthdata-token YOUR_TOKEN
```

내부 순서: ① asf_search 로 교량을 덮는 **S1 burst 시계열** 조회(최다 시계열 fullBurstID
자동선택) → ② `INSAR_ISCE_BURST` 잡을 **스타 네트워크**(기준=중간 날짜 vs 각 보조)로
제출 → ③ 폴링·완료 산출물(GeoTIFF) 다운로드(`--hyp3-dir`, 기본 `<out>/hyp3_products`) →
④ unw_phase→LOS(mm)·corr→coherence·lv_theta→입사각·**동봉 DEM→점별 고도** 변환 → Track H5.

잡 폴링은 수십 분 걸릴 수 있다(클라우드 큐 상황에 따라). 일부 쌍이 실패해도 성공한
쌍만으로 Track 을 만들고 실패 목록을 출력한다.

## 2. 이미 받아둔 산출물이 있으면 (오프라인 변환)

HyP3 웹 UI(<https://search.asf.alaska.edu>)에서 직접 주문·다운로드한 산출물 폴더도
네트워크 없이 변환된다:

```bash
python -m inframon --hyp3-import D:/hyp3_downloads --hyp3-target 37.3219,127.1083 \
  --out data/jeongja/track_hyp3.h5
```

`*_unw_phase.tif` 를 가진 폴더들을 재귀 탐색하고, 폴더 이름의 날짜 쌍에서 스타 기준일을
자동 추론한다(모든 쌍에 공통인 날짜).

## 3. 검증 → 인제스트 → 해석 (모든 레인 공통)

```bash
python -m inframon --check-track data/jeongja/track_hyp3.h5      # 사전검증 (exit 0 = 가능)
python -m inframon --import-track-h5 data/jeongja/track_hyp3.h5 --out data/project.h5
```

HyP3 산출물엔 DEM 이 동봉되므로 점별 고도(z)가 처음부터 채워진다 — 레인 A/B 에서 흔한
"고도 없음 z=0" 경고가 없다.

## 4. 품질·한계 (자리매김)

- 해상도: `INSAR_ISCE_BURST` 산출 격자는 SARvey full PSI 점밀도보다 거칠다 —
  **실용 모니터링·보급용**이고, 게이트 검증(G3)급 정밀도가 필요하면 레인 B 를 쓴다.
- 좌표: HyP3 산출은 UTM — 변환기가 WGS84 경위도로 역투영해 계약에 맞춘다(pyproj).
- 부호: los_mm 양수 = 위성 접근(레인 A 와 동일 관례, `RADAR_WAVELENGTH` attr 기록).
