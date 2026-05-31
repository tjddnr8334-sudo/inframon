"""모듈 1: InSAR — 실데이터 엔진 (Phase 3, 1차 증분).

오프라인 SAR 처리(SLC→간섭도→언래핑→SBAS 시계열; ISCE2/MintPy, WSL2/HyP3)가
만든 **Track 결과 H5** 를 읽어, CV 교량 기하(ROI/부재/축선)에 정합시켜 /insar 계약을
채운다. 무거운 SAR 처리 자체는 inframon 밖에서 수행하고 여기서는 결과만 소비한다.

좌표 정합(2단계):
  - 소스 포맷 : Track export H5 (pixel_lonlat/epochs/los_mm/coh)  → track_reader.read_track_h5
  - CV 가 geo_transform(+crs)을 제공하면 : Track world 좌표(필요시 pyproj 재투영)를
    역아핀으로 CV 픽셀(col,row)에 정합한다(geo.world_to_pixel). frame="cv_geo".
  - geo_transform 이 없으면(stub CV 등) : H5 좌표를 CV 픽셀로 간주(identity 폴백).
    frame="cv_pixel".

핫스왑: orchestrator.engines 가 ("insar","real") 로 등록 → `--engine insar=real` 로 켠다.
계약(InSAROutput) 시그니처는 stub 과 동일하므로 PINN/FRAM 은 영향받지 않는다.
"""

from __future__ import annotations

import numpy as np

from ..config import PipelineConfig
from ..contracts.io import ProjectStore
from ..contracts.schema import MEMBER_TYPES, CVOutput, InSAROutput
from . import geo
from .track_reader import read_track_h5, write_insar_contract


def run_insar_real(store: ProjectStore, cv: CVOutput, cfg: PipelineConfig) -> InSAROutput:
    if not cfg.insar_source_h5:
        raise ValueError(
            "insar=real 에는 처리된 결과 H5 경로가 필요합니다. "
            "`--insar-source <track.h5>` 또는 cfg.insar_source_h5 를 지정하세요."
        )

    td = read_track_h5(cfg.insar_source_h5)
    H, W = cv.image_shape
    n_src = td.los.shape[0]

    # ── 좌표 → CV 픽셀 프레임 ──
    # CV 가 geo_transform 을 주면 world→pixel 역아핀으로 정합(필요시 pyproj 재투영),
    # 없으면 H5 좌표를 픽셀로 간주(identity 폴백). col0=x(열), col1=y(행).
    gt = cv.geometry.geo_transform
    world = None  # geo 경로에서만 채워짐(world 좌표, cv.crs 기준)
    if gt is not None:
        src_crs = td.attrs.get("crs") or cv.geometry.crs
        world = geo.reproject(td.lonlat, src_crs, cv.geometry.crs)
        colrow = geo.world_to_pixel(gt, world[:, 0], world[:, 1])
        col = np.rint(colrow[:, 0]).astype(int)
        row = np.rint(colrow[:, 1]).astype(int)
        frame = "cv_geo"
    else:
        col = np.rint(td.lonlat[:, 0]).astype(int)
        row = np.rint(td.lonlat[:, 1]).astype(int)
        frame = "cv_pixel"
    in_frame = (col >= 0) & (col < W) & (row >= 0) & (row < H)

    # ── ROI 필터: 프레임 안 + ROI 마스크 내부인 점만 유지 ──
    roi = store.read_array(cv.roi_mask_ds)
    keep = in_frame.copy()
    keep[in_frame] &= roi[row[in_frame], col[in_frame]] > 0
    if int(keep.sum()) < 2:
        raise ValueError(
            f"ROI 안에 들어온 InSAR 점이 {int(keep.sum())}개뿐입니다(소스 {n_src}개). "
            "좌표 프레임/ROI 정합을 확인하세요(1차 증분은 H5 좌표를 CV 픽셀로 간주)."
        )

    col_k, row_k = col[keep], row[keep]
    los = td.los[keep].astype(np.float32)
    coherence = td.coherence[keep].astype(np.float32)
    n_points, _ = los.shape

    # ── 부재 할당: CV member 마스크에서 점 위치별 라벨 ──
    member = np.zeros(n_points, dtype=np.int8)
    for mi, name in enumerate(MEMBER_TYPES):
        ds = cv.member_label_ds.get(name)
        if ds is None:
            continue
        mmask = store.read_array(ds)
        member[mmask[row_k, col_k] == 1] = mi

    # ── 기하: LOS→종방향 분해 + 고정단까지 거리 + xyz ──
    az = float(cv.geometry.azimuth_angle)
    longitudinal = (los * np.cos(np.deg2rad(az))).astype(np.float32)
    # z(고도): Track H5 가 점별 고도를 주면 사용, 없으면 0(DEM 미연계). 프레임 무관.
    z = td.height[keep].astype(float) if td.height is not None else np.zeros(n_points)
    z_source = "track_height" if td.height is not None else "zero"
    if world is not None:
        # geo 경로: xyz 는 world 좌표(cv.crs, 예: EPSG:5179), 단위 미터.
        xy2d = world[keep][:, :2]
        xyz = np.column_stack([xy2d[:, 0], xy2d[:, 1], z])
        xyz_frame, l_unit = f"world:{cv.geometry.crs or 'unknown'}", "m"
    else:
        # identity 폴백: x·y 는 픽셀(geo 정보 없음), z 는 고도가 있으면 미터.
        xy2d = np.column_stack([col_k.astype(float), row_k.astype(float)])
        xyz = np.column_stack([xy2d[:, 0], xy2d[:, 1], z])
        xyz_frame, l_unit = "pixel", "pixel"
    # 고정단까지 거리: abutment(교대) 라벨이 있으면 그 위치를 영점, 없으면 종축 한쪽 끝.
    fixed_index = MEMBER_TYPES.index("abutment")
    l_from_fixed = geo.axial_from_fixed(xy2d, member, fixed_index).astype(np.float32)
    l_ref = "abutment" if int(np.sum(member == fixed_index)) > 0 else "axis_end"

    out = write_insar_contract(
        store, xyz=xyz, member=member, coherence=coherence, l_from_fixed=l_from_fixed,
        los=los, longitudinal=longitudinal, dates=td.dates, date_labels=td.date_labels,
    )
    store.write_json_attr(
        "insar",
        "insar_source",
        {
            "path": str(cfg.insar_source_h5),
            "mode": "real",
            "frame": frame,
            "registration": "geo_affine" if frame == "cv_geo" else "identity",
            "cv_crs": cv.geometry.crs,
            "xyz_frame": xyz_frame,
            "l_unit": l_unit,
            "l_ref": l_ref,
            "z_source": z_source,
            "unit": "mm",
            "n_source_points": int(n_src),
            "n_kept": int(n_points),
            "n_dropped_outside_roi": int(n_src - n_points),
            "azimuth_angle_deg": az,
            "date_labels_ds": "/insar/date_labels",
            "attrs": td.attrs,
        },
    )
    return out
