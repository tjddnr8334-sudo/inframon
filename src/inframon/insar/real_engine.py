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


def _centroid_lonlat(lonlat, world, crs):
    """점군의 대표 (lat, lon)[WGS84] — ERA5 온도 조회용. 실패 시 (None, None)."""
    ll = np.asarray(lonlat, dtype=float)
    if ll.size and float(np.abs(ll[:, 0]).max()) <= 180 and float(np.abs(ll[:, 1]).max()) <= 90:
        return float(np.median(ll[:, 1])), float(np.median(ll[:, 0]))   # (lat, lon)
    if world is not None and crs:
        try:
            w = np.asarray(world, dtype=float)
            cx, cy = float(np.median(w[:, 0])), float(np.median(w[:, 1]))
            out = geo.reproject(np.array([[cx, cy]]), crs, "EPSG:4326")  # (x=lon, y=lat)
            return float(out[0, 1]), float(out[0, 0])
        except Exception:  # noqa: BLE001 — 재투영 실패 → 온도 조회 skip
            return None, None
    return None, None


def run_insar_real(store: ProjectStore, cv: CVOutput, cfg: PipelineConfig) -> InSAROutput:
    if not cfg.insar_source_h5:
        raise ValueError(
            "insar=real 에는 처리된 결과 H5 경로가 필요합니다. "
            "`--insar-source <track.h5>` 또는 cfg.insar_source_h5 를 지정하세요."
        )

    td = read_track_h5(cfg.insar_source_h5)

    # ── asc+desc 융합(가능하면) → 연직+종축 분리. 불가 시 단일 궤도 폴백. ──
    fused_axial = None      # [N_src, M] 융합 종축(longitudinal). None 이면 단일 처리.
    fused_vertical = None   # [N_src, M] 융합 연직(U). 계약 외 추가 데이터셋으로 저장.
    fusion_meta: dict | None = None
    desc_h5 = getattr(cfg, "insar_source_desc_h5", None)
    if desc_h5:
        from .fusion import FusionError, fuse_asc_desc
        try:
            res = fuse_asc_desc(td, read_track_h5(desc_h5))
            td = res.track                       # asc 기준 융합 점·공통시점
            fused_axial = res.longitudinal
            fused_vertical = res.vertical
            fusion_meta = res.meta
        except FusionError as exc:               # 융합 불가 → 단일 궤도로 진행
            fusion_meta = {"ok": False, "attempted": True, "reason": str(exc),
                           "desc_source": str(desc_h5)}

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

    # ── 레이더 음영/겹침 필터: CV 가 제공하면 신뢰불가 점 제거(없으면 무시) ──
    # 음영(shadow)=신호 없음(이진), 겹침(layover)=혼신(이진 또는 분율>0.5).
    def _drop_where(ds_path: str, thresh: float) -> int:
        arr = store.read_array(ds_path)
        idx = np.where(keep)[0]                 # 현재 유지 점(프레임 안 → row/col 유효)
        bad = arr[row[idx], col[idx]] > thresh
        keep[idx[bad]] = False
        return int(bad.sum())

    n_dropped_shadow = n_dropped_layover = 0
    if cv.shadow_ds and store.has_array(cv.shadow_ds):
        n_dropped_shadow = _drop_where(cv.shadow_ds, 0.0)
    if cv.layover_ds and store.has_array(cv.layover_ds):
        n_dropped_layover = _drop_where(cv.layover_ds, 0.5)

    if int(keep.sum()) < 2:
        raise ValueError(
            f"유효 InSAR 점이 {int(keep.sum())}개뿐입니다(소스 {n_src}개, "
            f"음영 {n_dropped_shadow}·겹침 {n_dropped_layover} 제거). "
            "좌표 프레임/ROI 정합·음영겹침 마스크를 확인하세요(1차 증분은 H5 좌표를 CV 픽셀로 간주)."
        )

    col_k, row_k = col[keep], row[keep]
    los = td.los[keep].astype(np.float32)
    coherence = td.coherence[keep].astype(np.float32)
    n_points, _ = los.shape

    # z(고도): ① Track H5 점별 고도 → ② DEM GeoTIFF 샘플(world 좌표 필요) → ③ 0.
    # 고도는 트러스 상·하현/아치 리브·데크/케이블 주탑·데크를 분리하는 형식별 해석에 쓰이고,
    # 아래 고도상관(성층 대기) 보정에도 쓰이므로 종방향 분해 전에 먼저 구한다.
    dem_meta = None
    if td.height is not None:
        z = td.height[keep].astype(float)
        z_source = "track_height"
    elif getattr(cfg, "insar_dem_geotiff", None) and world is not None:
        from .dem import DemError, sample_dem
        try:
            ds = sample_dem(world[keep][:, :2], cv.geometry.crs, cfg.insar_dem_geotiff)
            z = ds.z.astype(float)
            z_source = "dem_raster"
            dem_meta = ds.meta
        except DemError as exc:                  # DEM 실패 → z=0 폴백(파이프라인 계속)
            z = np.zeros(n_points)
            z_source = "zero"
            dem_meta = {"ok": False, "reason": str(exc), "path": str(cfg.insar_dem_geotiff)}
    else:
        z = np.zeros(n_points)
        z_source = "zero"

    # ── InSAR 정확도 보정(opt-in): 기준점 정합 + 고도상관 성층대기 보정 ──
    # LOS 도메인에서 먼저 보정하면 아래 종방향 분해가 보정된 los 에서 유도돼 일관된다.
    # 융합 경로의 종축/연직은 이미 축 도메인이라 기준점 정합만 별도로 적용한다.
    corr_meta = None
    fused_axial_k = fused_axial[keep] if fused_axial is not None else None
    fused_vertical_k = fused_vertical[keep] if fused_vertical is not None else None
    if getattr(cfg, "insar_apply_corrections", False):
        from .atmo import correct_los_field
        min_coh = float(getattr(cfg, "insar_ref_min_coherence", 0.9))
        # 열팽창 보정용 온도(opt-in): CSV 우선, 없으면 ERA5 fetch(점군 중심 lon/lat).
        temp = temp_meta = None
        if getattr(cfg, "insar_thermal_correction", False):
            from .atmo import resolve_temperature
            clat, clon = _centroid_lonlat(td.lonlat[keep], world[keep] if world is not None else None,
                                          cv.geometry.crs)
            tr = resolve_temperature(
                td.date_labels, lat=clat, lon=clon,
                csv_path=getattr(cfg, "insar_temperature_csv", None),
                fetch=bool(getattr(cfg, "insar_fetch_temperature", False)))
            temp, temp_meta = tr["temperature"], {"source": tr["source"], **tr["meta"]}
        res = correct_los_field(los, coherence=coherence, height=z, min_ref_coh=min_coh,
                                days=td.dates, temperature=temp)
        los = res["corrected"]
        corr_meta = res["meta"]
        if temp_meta is not None:
            corr_meta["temperature"] = temp_meta
        if fused_axial_k is not None:            # 융합 종축/연직 — 기준점 정합만(고도상관 생략)
            fa = correct_los_field(fused_axial_k, coherence=coherence, height=None,
                                   height_corr=False, min_ref_coh=min_coh)
            fused_axial_k = fa["corrected"]
            fv = correct_los_field(fused_vertical_k, coherence=coherence, height=None,
                                   height_corr=False, min_ref_coh=min_coh)
            fused_vertical_k = fv["corrected"]
            corr_meta["fusion_axial"] = fa["meta"]

    # ── 부재 할당: CV member 마스크에서 점 위치별 라벨 ──
    member = np.zeros(n_points, dtype=np.int8)
    for mi, name in enumerate(MEMBER_TYPES):
        ds = cv.member_label_ds.get(name)
        if ds is None:
            continue
        mmask = store.read_array(ds)
        member[mmask[row_k, col_k] == 1] = mi

    # ── 기하: LOS→종방향 분해 + 고정단까지 거리 + xyz ──
    # 우선순위: ① asc+desc 융합(종축+연직) → ② 입사각 deprojection → ③ 투영 폴백.
    az = float(cv.geometry.azimuth_angle)
    proj = (los * np.cos(np.deg2rad(az))).astype(np.float32)  # 폴백(투영, sinθ 누락)
    vertical = None
    if fused_axial_k is not None:
        # 융합 경로: 종축은 2×2 역산 결과, 연직은 계약 외 /insar/vertical 로 저장.
        longitudinal = fused_axial_k.astype(np.float32)
        vertical = fused_vertical_k.astype(np.float32)
        longitudinal_method = "asc_desc_fusion"
        n_low_sens = 0
        incidence_mean = float(np.mean(td.incidence[keep])) if td.incidence is not None else None
    elif td.incidence is not None:
        inc_k = td.incidence[keep]
        factor = geo.los_axial_factor(inc_k, az)             # sinθ·cosΔ [n_points]
        axial, valid = geo.los_to_axial(los, factor)
        longitudinal = np.where(valid[:, None], axial, proj).astype(np.float32)
        longitudinal_method = "deprojection_incidence"
        n_low_sens = int((~valid).sum())
        incidence_mean = float(np.mean(inc_k))
    else:
        longitudinal = proj
        longitudinal_method = "projection_approx"
        n_low_sens = 0
        incidence_mean = None
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
        vertical=vertical,   # 융합 연직(있으면) → 계약 필드 vertical_ds
    )
    # 점별 선형 속도[mm/yr] — 보정된 종방향 시계열에서 최소제곱. 대시보드/리포트가 바로 소비.
    from .atmo import temporal_decompose
    velocity = temporal_decompose(longitudinal, td.dates)["velocity_mm_yr"].astype(np.float32)
    store.write_array("/insar/velocity_mm_yr", velocity)
    store.write_json_attr(
        "insar",
        "insar_source",
        {
            "velocity_ds": "/insar/velocity_mm_yr",
            "corrections": corr_meta,
            "path": str(cfg.insar_source_h5),
            "mode": "real",
            "frame": frame,
            "registration": "geo_affine" if frame == "cv_geo" else "identity",
            "cv_crs": cv.geometry.crs,
            "xyz_frame": xyz_frame,
            "l_unit": l_unit,
            "l_ref": l_ref,
            "z_source": z_source,
            "dem": dem_meta,
            "unit": "mm",
            "n_source_points": int(n_src),
            "n_kept": int(n_points),
            "n_dropped_outside_roi": int(n_src - n_points),
            "azimuth_angle_deg": az,
            "longitudinal_method": longitudinal_method,
            "n_low_axial_sensitivity": n_low_sens,
            "incidence_mean_deg": incidence_mean,
            "heading_deg": td.heading,
            "fusion": fusion_meta,
            "date_labels_ds": "/insar/date_labels",
            "attrs": td.attrs,
        },
    )
    return out
