"""DEM 래스터에서 점별 고도(z)를 샘플링 — Track 결과에 고도가 없을 때의 폴백.

상류 지오코딩이 점별 고도(`height`)를 주면 그대로 쓰지만(가장 정확), 많은 Track
export 는 고도를 빼고 평면 좌표만 준다. 그때 z=0 으로 두면 교량 상부구조의 높이
정보가 사라져 형식별 해석(트러스 상·하현, 아치 리브/데크, 케이블 주탑/데크를 고도로
분리; `bridge_profile._class_tuning` 참고)이 불가능하다.

이 모듈은 WSL2 처리 1단계에서 ISCE2 용으로 받은 **DEM GeoTIFF**(SRTM/Copernicus 등)를
점의 world 좌표에서 샘플링해 z 를 채운다. 무거운 의존(rasterio/pyproj)은 지연 import 라
DEM 을 안 쓰면 비용이 없다. DEM CRS 와 world CRS 가 다르면 좌표를 DEM CRS 로 재투영해
샘플링한다(반대로 래스터를 워핑하지 않음 — 점만 변환하면 충분·정확).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["DemError", "DemSample", "sample_dem"]


class DemError(RuntimeError):
    """DEM 샘플링 실패(파일 없음/CRS 미지정/rasterio 미설치 등). 호출 측이 z=0 폴백."""


@dataclass
class DemSample:
    """DEM 샘플 결과 + 자기기술 메타."""

    z: np.ndarray            # [N] float32 (m). nodata/범위밖은 fill 로 채움.
    meta: dict


def _reproject(xy: np.ndarray, src_crs, dst_crs) -> np.ndarray:
    """[N,2] world 좌표를 src_crs→dst_crs 로 재투영. 같거나 비면 그대로."""
    if src_crs is None or dst_crs is None:
        return xy
    try:
        from pyproj import CRS, Transformer
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise DemError(
            f"DEM CRS 재투영에 pyproj 가 필요합니다({src_crs}→{dst_crs}). "
            "`pip install pyproj` 후 다시 실행하세요."
        ) from exc
    # 객체/문자열/EPSG 어느 쪽이든 CRS 로 정규화해 동일성 비교(같으면 no-op).
    s, d = CRS.from_user_input(src_crs), CRS.from_user_input(dst_crs)
    if s == d:
        return xy
    tf = Transformer.from_crs(s, d, always_xy=True)
    x, y = tf.transform(xy[:, 0], xy[:, 1])
    return np.column_stack([np.asarray(x, float), np.asarray(y, float)])


def sample_dem(
    xy_world: np.ndarray,
    world_crs: str | None,
    dem_path: str,
    *,
    band: int = 1,
    fill: str = "median",
) -> DemSample:
    """world 좌표 [N,2](world_crs)에서 DEM GeoTIFF 고도를 샘플링한다.

    절차: 점을 DEM CRS 로 재투영 → rasterio 로 점별 샘플 → nodata/래스터 범위밖은
    NaN 으로 표시 → `fill`('median'=유효값 중앙값, 'zero'=0)로 메움. world_crs 가
    None 이면 DEM CRS 와 동일하다고 보고 그대로 샘플링한다.

    반환 `DemSample.z` 는 [N] float32(m). 실패 시 `DemError`.
    """
    xy = np.asarray(xy_world, dtype=np.float64)
    if xy.ndim != 2 or xy.shape[1] < 2:
        raise DemError(f"xy_world 형상이 [N,2] 가 아닙니다: {xy.shape}")
    try:
        import rasterio
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise DemError(
            "DEM 샘플링에 rasterio 가 필요합니다. `pip install rasterio`(또는 conda) 후 "
            "다시 실행하거나, --insar-dem 없이 z=0 으로 진행하세요."
        ) from exc

    try:
        with rasterio.open(dem_path) as src:
            dem_crs = src.crs.to_string() if src.crs else None
            xy_dem = _reproject(xy[:, :2], world_crs, dem_crs)
            nodata = src.nodata
            left, bottom, right, top = (float(b) for b in src.bounds)
            # rasterio.sample: (x,y) 쌍 generator → 각 점의 밴드값. 범위밖 점은 nodata
            # 가 설정돼야 nodata 를 돌려주고, 미설정이면 0 을 돌려주므로(오인 위험)
            # 래스터 extent 로 명시적으로 범위밖을 따로 가려낸다.
            sampled = np.array(
                [v[band - 1] for v in src.sample(zip(xy_dem[:, 0], xy_dem[:, 1]), indexes=[band])],
                dtype=np.float64,
            )
            bounds = (round(left, 6), round(bottom, 6), round(right, 6), round(top, 6))
            outside = (
                (xy_dem[:, 0] < left) | (xy_dem[:, 0] > right)
                | (xy_dem[:, 1] < bottom) | (xy_dem[:, 1] > top)
            )
    except DemError:
        raise
    except Exception as exc:  # rasterio 오류(파일 손상/포맷 등)
        raise DemError(f"DEM 열기/샘플 실패({dem_path}): {exc}") from exc

    z = sampled.copy()
    invalid = ~np.isfinite(z) | outside
    if nodata is not None:
        invalid |= np.isclose(z, float(nodata))
    n_invalid = int(invalid.sum())
    z[invalid] = np.nan
    valid_vals = z[~np.isnan(z)]
    if valid_vals.size == 0:
        raise DemError(
            f"DEM 에서 유효 고도를 한 점도 얻지 못했습니다({dem_path}). "
            "교량 좌표·CRS·DEM 범위(bounds)를 확인하세요."
        )
    fill_value = float(np.median(valid_vals)) if fill == "median" else 0.0
    z = np.where(np.isnan(z), fill_value, z).astype(np.float32)

    meta = {
        "ok": True,
        "path": str(dem_path),
        "dem_crs": dem_crs,
        "world_crs": world_crs,
        "band": band,
        "n_points": int(xy.shape[0]),
        "n_nodata_or_outside": n_invalid,
        "fill": fill,
        "fill_value": round(fill_value, 3),
        "z_min": round(float(valid_vals.min()), 3),
        "z_max": round(float(valid_vals.max()), 3),
        "z_mean": round(float(valid_vals.mean()), 3),
        "dem_bounds": bounds,
    }
    return DemSample(z=z, meta=meta)
