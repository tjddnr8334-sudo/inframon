"""모듈 3: CV 실구현 (Phase 2) — 영상 → ROI/부재/축선/격자.

교량 분할 백엔드 2종(stub 의 하드코딩 도형 대신 실제 영상 기반):
  • "classical" (기본, 의존성 경량): Otsu 임계화 → 연결요소(최대=교량 ROI). 모델/가중치 불필요.
  • "transformer" (선택): **Transformer 기반 시맨틱 분할**(HuggingFace `transformers` —
     SegFormer / Mask2Former / DETR 계열). ADE20k 의 'bridge' 클래스 픽셀을 ROI 로.
     (YOLO 미사용. transformers/가중치 없으면 classical 로 자동 폴백.)

두 백엔드 모두 같은 하류로: ROI 픽셀 PCA → 주축(축선·방위각·길이·폭) → **부재 시맨틱 분할**
(deck/abutment/pier/bearing) → 거리변환 격자밀도 + shadow/layover.

부재 분할 백엔드(`cfg.cv_member_backend`): "shape"(기본) 은 직교 protrusion 형상 증거로
교각을 검출(축 등간격 추측 대신), "sam" 은 SAM(Segment Anything) 인스턴스 분할로 정교화
하되 가중치 없으면 shape 로 폴백. (게이트 G2: 부재 mIoU≥0.6 — 합성 검증.)

영상은 cfg.cv_image_path 있으면 로드, 없으면 합성 교량영상 처리(어디서나 동작).
백엔드: cfg.cv_backend ∈ {"classical","transformer"} (기본 classical). 계약(CVOutput) 보존.
"""

from __future__ import annotations

import warnings

import numpy as np

from ..config import PipelineConfig
from ..contracts.io import ProjectStore
from ..contracts.schema import MEMBER_TYPES, CVGeometry, CVOutput

DEFAULT_SEG_MODEL = "nvidia/segformer-b2-finetuned-ade-512-512"   # Transformer 시맨틱(ADE20k)


def _synth_bridge_image(H: int, W: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = rng.normal(0.20, 0.05, (H, W))
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    cx, cy = W / 2, H / 2
    th = np.deg2rad(rng.uniform(-20, 20))
    u = (xx - cx) * np.cos(th) + (yy - cy) * np.sin(th)
    v = -(xx - cx) * np.sin(th) + (yy - cy) * np.cos(th)
    half_L, half_Wd = 0.72 * W / 2, 0.045 * H
    img[(np.abs(u) < half_L) & (np.abs(v) < half_Wd)] = 0.85
    for uu in np.linspace(-half_L * 0.8, half_L * 0.8, 5):
        img[(np.abs(u - uu) < half_Wd * 0.5) & (np.abs(v) < half_Wd * 2.2)] = 0.97
    img += rng.normal(0, 0.03, (H, W))
    return np.clip(img, 0, 1)


def _load_image(path: str) -> np.ndarray:
    import imageio.v3 as iio
    a = np.asarray(iio.imread(path), dtype=float)
    if a.ndim == 3:
        a = a.mean(axis=2)
    return (a - a.min()) / (np.ptp(a) + 1e-9)


def _read_geo(path: str) -> tuple[tuple[float, ...] | None, str | None]:
    """GeoTIFF 등 지오레퍼런스 래스터에서 (geo_transform, crs)를 읽는다.

    `transform.to_gdal()` 은 우리 규약과 같은 GDAL 6-tuple (c,a,b,f,d,e). rasterio 가
    없거나(경량 환경) 파일이 지오레퍼런스되지 않았으면 (None, None) — InSAR 가 identity
    폴백한다. 무거운 GIS 의존은 지오 영상이 들어올 때만 지연 import 한다.
    """
    try:
        import rasterio
    except ImportError:
        return None, None
    try:
        with rasterio.open(path) as ds:
            if ds.crs is not None and ds.transform is not None and not ds.transform.is_identity:
                return tuple(float(v) for v in ds.transform.to_gdal()), str(ds.crs)
    except Exception:  # noqa: BLE001 — 비래스터/손상 파일이면 지오 없음으로 안전 처리
        return None, None
    return None, None


def _resolve_geo(cfg: PipelineConfig, path: str | None) -> tuple[tuple[float, ...] | None, str | None]:
    """지오레퍼런스 결정: cfg 명시 override 우선, 없으면 입력 영상(GeoTIFF)에서 읽기."""
    gt = getattr(cfg, "cv_geo_transform", None)
    crs = getattr(cfg, "cv_crs", None)
    if gt is not None:
        gt = tuple(float(v) for v in gt)
        if len(gt) != 6:
            raise ValueError(f"cv_geo_transform 은 GDAL 6-tuple 이어야 합니다, got {len(gt)}개")
        return gt, (str(crs) if crs is not None else None)
    if path:
        return _read_geo(path)
    return None, None


def _otsu(img: np.ndarray) -> float:
    try:
        from skimage.filters import threshold_otsu
        return float(threshold_otsu(img))
    except Exception:  # noqa: BLE001
        return float(img.mean() + 0.5 * img.std())


def _classical_bridge_mask(img: np.ndarray) -> np.ndarray:
    """Otsu 임계화 + 최대 연결요소 = 교량 ROI (의존성 경량)."""
    from scipy import ndimage as ndi
    lab, n = ndi.label(img > _otsu(img))
    if n == 0:
        raise ValueError("CV: 임계 후 구조물을 못 찾음.")
    sizes = ndi.sum(np.ones_like(lab), lab, index=range(1, n + 1))
    return lab == (int(np.argmax(sizes)) + 1)


def _transformer_bridge_mask(img: np.ndarray, model_name: str = DEFAULT_SEG_MODEL) -> np.ndarray:
    """Transformer 시맨틱 분할(SegFormer 등 HF transformers)로 'bridge' 픽셀 마스크.

    transformers/가중치가 없으면 ImportError/예외 → 호출측에서 classical 폴백.
    """
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, SegformerForSemanticSegmentation

    proc = AutoImageProcessor.from_pretrained(model_name)
    model = SegformerForSemanticSegmentation.from_pretrained(model_name).eval()
    rgb = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).convert("RGB")
    inputs = proc(images=rgb, return_tensors="pt")
    with torch.no_grad():
        logits = model(**inputs).logits                       # [1,C,h,w]
    up = torch.nn.functional.interpolate(logits, size=img.shape, mode="bilinear",
                                         align_corners=False)
    seg = up.argmax(1)[0].cpu().numpy()
    bridge_ids = [i for i, lab in model.config.id2label.items() if "bridge" in lab.lower()]
    if not bridge_ids:
        raise ValueError("모델 라벨에 'bridge' 클래스가 없습니다.")
    return np.isin(seg, np.array(bridge_ids))


def _detect_pier_columns(pp: np.ndarray, sn: np.ndarray, n_bins: int = 80) -> np.ndarray:
    """직교 protrusion(데크 폭 밖으로 튀어나온 부분)으로 교각 축 위치를 검출.

    교각은 데크에 수직으로 돌출하므로 |pp| 가 데크 반폭을 넘는 픽셀들이 교각 후보다.
    이들이 축선(sn)상 어디에 모이는지 빈으로 집계해, 교각이 있는 축 컬럼을 가린다.
    축방향 등간격 '추측'(영상 무관) 대신 **영상 형상 증거**로 교각을 찾는다(SAM 폴백).
    반환: ROI 픽셀별 교각 컬럼 여부 bool + 데크 반폭 추정값.
    """
    deck_half = 2.0 * float(np.median(np.abs(pp))) + 1e-9     # 데크 반폭(중앙값 기반, 강건)
    protr = np.abs(pp) > deck_half * 1.1                       # 데크 밖 돌출 = 교각 후보
    in_pier_col = np.zeros(len(sn), dtype=bool)
    if int(protr.sum()) >= 3:
        bins = np.clip((sn * n_bins).astype(int), 0, n_bins - 1)
        cnt = np.bincount(bins[protr], minlength=n_bins)
        pier_bin = cnt >= max(2, int(0.25 * cnt.max()))       # 돌출이 충분한 빈=교각 위치
        in_pier_col = pier_bin[bins]
    return in_pier_col, deck_half


def _segment_members(roi, ys, xs, sn, pp, H: int, W: int) -> dict:
    """부재 시맨틱 분할 — deck(전체)/abutment(축 양끝)/pier(돌출 검출)/bearing(교각∩데크).

    교각·받침은 축 등간격 추측이 아니라 직교 protrusion 형상 증거로 검출한다(SAM 폴백).
    """
    member = {m: np.zeros((H, W), dtype=np.uint8) for m in MEMBER_TYPES}
    member["deck"][ys, xs] = 1
    abut = (sn < 0.06) | (sn > 0.94)
    member["abutment"][ys[abut], xs[abut]] = 1

    in_pier_col, deck_half = _detect_pier_columns(pp, sn)
    bear_m = in_pier_col & (np.abs(pp) < deck_half)            # 교각이 데크와 만나는 핵심
    member["pier"][ys[in_pier_col], xs[in_pier_col]] = 1
    member["bearing"][ys[bear_m], xs[bear_m]] = 1
    return member


def _sam_member_masks(img, roi, ys, xs, sn, pp, H: int, W: int) -> dict:
    """SAM(Segment Anything) 인스턴스 분할로 교각을 정교화한다(가중치 있을 때만).

    형상 증거로 검출한 교각 컬럼의 중심을 SAM 프롬프트 점으로 주어 교각 인스턴스
    마스크를 받고, ROI 와 교차해 pier/bearing 으로 매핑한다. deck/abutment 는 기하.
    transformers/SAM·가중치가 없으면 예외 → 호출측에서 shape 폴백.
    """
    import torch
    from transformers import SamModel, SamProcessor

    in_pier_col, deck_half = _detect_pier_columns(pp, sn)
    if int(in_pier_col.sum()) < 3:
        raise ValueError("SAM: 교각 시드를 찾지 못함")
    # 교각 컬럼들을 축 위치로 묶어 각 컬럼 중심을 프롬프트 점으로
    n_bins = 80
    seed_bins = sorted(set(np.clip((sn[in_pier_col] * n_bins).astype(int), 0, n_bins - 1)))
    seeds = []
    for b in seed_bins:
        sel = in_pier_col & (np.clip((sn * n_bins).astype(int), 0, n_bins - 1) == b)
        if sel.any():
            seeds.append([float(xs[sel].mean()), float(ys[sel].mean())])  # (x,y)

    model = SamModel.from_pretrained("facebook/sam-vit-base").eval()
    proc = SamProcessor.from_pretrained("facebook/sam-vit-base")
    rgb = np.stack([(np.clip(img, 0, 1) * 255).astype(np.uint8)] * 3, axis=-1)
    inputs = proc(rgb, input_points=[[s] for s in seeds], return_tensors="pt")
    with torch.no_grad():
        out = model(**inputs)
    masks = proc.image_processor.post_process_masks(
        out.pred_masks.cpu(), inputs["original_sizes"].cpu(), inputs["reshaped_input_sizes"].cpu()
    )
    pier = np.zeros((H, W), dtype=bool)
    for mset in masks:
        m = np.asarray(mset)[0]                       # 점당 최고점수 마스크
        pier |= (m.reshape(H, W) if m.ndim == 1 else m[0]).astype(bool)
    pier &= roi

    member = {m: np.zeros((H, W), dtype=np.uint8) for m in MEMBER_TYPES}
    member["deck"][ys, xs] = 1
    abut = (sn < 0.06) | (sn > 0.94)
    member["abutment"][ys[abut], xs[abut]] = 1
    member["pier"][pier] = 1
    pier_pix = pier[ys, xs]
    bear = pier_pix & (np.abs(pp) < deck_half)
    member["bearing"][ys[bear], xs[bear]] = 1
    return member


def _member_masks(img, roi, ys, xs, sn, pp, H: int, W: int, backend: str) -> dict:
    """부재 분할 백엔드 선택: "sam"(폴백 가능) 또는 "shape"(형상 증거, 기본)."""
    if backend == "sam":
        try:
            return _sam_member_masks(img, roi, ys, xs, sn, pp, H, W)
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"SAM 부재 분할 실패 → shape 폴백: {exc}", stacklevel=2)
    return _segment_members(roi, ys, xs, sn, pp, H, W)


def _bridge_mask(img: np.ndarray, backend: str, model_name: str) -> np.ndarray:
    """백엔드 선택: transformer(폴백 가능) 또는 classical."""
    if backend == "transformer":
        try:
            m = _transformer_bridge_mask(img, model_name)
            if int(m.sum()) >= 10:
                return m
            raise ValueError("transformer: bridge 픽셀 미검출")
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"transformer 분할 실패 → classical 폴백: {exc}", stacklevel=2)
    return _classical_bridge_mask(img)


def run_cv_real(store: ProjectStore, cfg: PipelineConfig) -> CVOutput:
    path = getattr(cfg, "cv_image_path", None)
    backend = getattr(cfg, "cv_backend", "classical")
    model_name = getattr(cfg, "cv_seg_model", DEFAULT_SEG_MODEL)
    img = _load_image(path) if path else _synth_bridge_image(cfg.image_h, cfg.image_w, cfg.seed)
    H, W = img.shape
    geo_transform, crs = _resolve_geo(cfg, path)   # 지오 영상/override 면 채워짐, 아니면 None

    roi = _bridge_mask(img, backend, model_name)             # [H,W] bool

    ys, xs = np.where(roi)
    pts = np.stack([xs.astype(float), ys.astype(float)], axis=1)
    center = pts.mean(axis=0)
    evals, evecs = np.linalg.eigh(np.cov((pts - center).T))
    axis = evecs[:, int(np.argmax(evals))]
    perp = evecs[:, int(np.argmin(evals))]
    s = (pts - center) @ axis
    pp = (pts - center) @ perp
    L = float(s.max() - s.min())
    Wd = float(pp.max() - pp.min())
    azimuth = float(np.degrees(np.arctan2(axis[1], axis[0])))
    e0, e1 = center + axis * s.min(), center + axis * s.max()
    centerline = [(float(e0[0]), float(e0[1])), (float(center[0]), float(center[1])),
                  (float(e1[0]), float(e1[1]))]

    # geo_transform 이 있으면 길이/폭을 픽셀 → world(미터) 로 환산.
    # 픽셀 끝점을 world 로 옮겨 유클리드 거리를 재므로 비등방·회전 아핀에도 정확하다.
    length_unit = "pixel"
    if geo_transform is not None:
        from ..geotransform import pixel_to_world
        w_axis = pixel_to_world(geo_transform, np.array([e0[0], e1[0]]), np.array([e0[1], e1[1]]))
        wperp = pixel_to_world(geo_transform,
                               np.array([center[0] - perp[0] * Wd / 2, center[0] + perp[0] * Wd / 2]),
                               np.array([center[1] - perp[1] * Wd / 2, center[1] + perp[1] * Wd / 2]))
        L = float(np.hypot(*(w_axis[1] - w_axis[0])))
        Wd = float(np.hypot(*(wperp[1] - wperp[0])))
        length_unit = "m"

    # sn 은 geo 환산 전 픽셀 축 길이로 정규화(pp 도 픽셀이라 부재 분할은 픽셀 일관).
    sn = (s - s.min()) / (float(s.max() - s.min()) + 1e-9)
    member_backend = getattr(cfg, "cv_member_backend", "shape")
    member = _member_masks(img, roi, ys, xs, sn, pp, H, W, member_backend)

    from scipy import ndimage as ndi
    dist = ndi.distance_transform_edt(roi)
    grid_density = (dist / (dist.max() + 1e-9) + 0.2) * roi
    near = ndi.binary_dilation(roi, iterations=max(2, int(0.02 * H))) & ~roi
    shadow = (near & (img < _otsu(img) * 0.5)).astype(np.uint8)
    layover = (near & (img > 0.9)).astype(np.uint8)

    g = "/cv"
    out = CVOutput(
        roi_mask_ds=store.write_array(f"{g}/roi_mask", roi.astype(np.uint8)),
        member_label_ds={m: store.write_array(f"{g}/member_{m}", member[m]) for m in MEMBER_TYPES},
        geometry=CVGeometry(centerline=centerline, azimuth_angle=azimuth,
                            bridge_length=L, bridge_width=Wd, length_unit=length_unit,
                            crs=crs, geo_transform=geo_transform),
        shadow_ds=store.write_array(f"{g}/shadow", shadow),
        layover_ds=store.write_array(f"{g}/layover", layover),
        grid_density_ds=store.write_array(f"{g}/grid_density", grid_density.astype(np.float32)),
        image_shape=(H, W),
    )
    store.write_meta("cv", out)
    return out
