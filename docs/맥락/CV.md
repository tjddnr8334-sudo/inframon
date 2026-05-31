# CV 엔진 (상류) — ROI/부재 분할 자동화 · 상태: STUB + REAL(Phase 2)

> **real 구현됨**(`cv/real_engine.py`, `run_cv_real`, 핫스왑 `--engine cv=real`): stub의 하드코딩 도형 대신 **실제 영상처리** — Otsu 임계화 → 연결요소(scipy.ndimage, 최대=교량 ROI) → ROI 픽셀 **PCA**로 주축(축선·방위각·길이·폭) → 축선 투영으로 부재 할당(deck=ROI / abutment=양끝 / pier=등간격 / bearing=교각 좁은띠) → 거리변환 격자밀도 + **shadow/layover 산출**(stub의 always-None 해소). 영상은 `cfg.cv_image_path` 있으면 로드(imageio), 없으면 합성 교량영상 생성→처리(어디서나 동작). **분할 백엔드 2종(`cfg.cv_backend`)**: `classical`(기본, Otsu+CC, 경량) / **`transformer`**(HuggingFace `transformers` — **SegFormer/Mask2Former/DETR** 계열, ADE20k 'bridge' 클래스. **YOLO 미사용**). transformer 는 지연 import + 가중치 없으면 classical 자동 폴백. 계약(CVOutput) 보존. ⚠️ 부재 sub분류는 기하 휴리스틱.

---


> 파이프라인 최상류. 영상에서 측정 영역(ROI)·부재 라벨·격자 가이드를 만들어 InSAR에 넘긴다.
> 공통 계약: [00_공통_계약과_파이프라인.md](00_공통_계약과_파이프라인.md) · 전체: [`../개발_맥락_맵.md`](../개발_맥락_맵.md) §5.2

## 역할
위성 영상에서 교량을 탐지하고 부재를 분할하여 **측정 영역(ROI)을 자동 산정**. 하류 InSAR가 "어디를" 측정할지 결정하는 게이트.

## 입출력 계약
- **공개 API**: `run_cv(store, cfg) -> CVOutput`
- **입력**: `cfg`(image_h/image_w, seed)
- **출력(CVOutput)**: `roi_mask`, `member_label`, `grid_density`, `geometry(CVGeometry)`, `azimuth`, `bridge_width`
- **하류가 쓰는 것**: `grid_density`(InSAR 샘플링 가중), `member_label`, `azimuth`

## 현재 구현 (STUB)
- `np.random.default_rng(cfg.seed)` + `image_h/image_w`로 하드코딩 도형 생성:
  - ROI = 세로 중앙 ±12px 수평 띠
  - pier = 등간격 세로 기둥, abutment = 좌우 끝 6px, bearing = 단일 픽셀 열
  - `azimuth` = ±10° 난수, `bridge_width` = 상수 24.0
- **`shadow_ds`/`layover_ds`는 항상 None** → 다운스트림에서 None 처리 필요

## 교체 시 보존할 계약
- `CVOutput` 필드/`*_ds` 경로 유지. `grid_density`, `member_label`, `azimuth`는 InSAR가 의존하므로 의미 보존.
- `member_label` 값은 표준 `MEMBER_TYPES=('deck','pier','abutment','bearing')` 인덱싱을 따라야 함.
- ⚠️ `CVGeometry.centerline`은 좌표를 meta JSON에 직접 보유 → 실구현에서 축선이 길어지면 **HDF5 64KB attribute 오버플로** 위험. 길면 `*_ds`로 빼라.

## 로드맵 (Phase 2, 게이트 G2: mAP≥0.7 / mIoU≥0.6 / 축선오차<3°)
YOLO11-OBB(교량 탐지) → SAM2(부재 분할) → PCA/Hough(축선 추정) → sarsen(격자) 로 합성 도형 코드 교체.
`shadow_ds`/`layover_ds` 실제 SAR 기하 산출. **영상 입력 인터페이스 추가**(현재 시그니처에 없음 → 확장 필요).

## 이 엔진 고유 리스크
- `bearing`의 `[band, ::W//4]` 슬라이스, pier 루프가 `W//4`/`W//8` **정수나눗셈** 의존 → 작은 `image_w`에서 의도와 다른 결과.
- CV↔InSAR 좌표계 정합(EPSG:5179·affine·MEMBER_TYPES 인덱싱) 실패 위험.
