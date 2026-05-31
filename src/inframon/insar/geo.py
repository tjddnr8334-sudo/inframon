"""좌표 정합 — CV 지오레퍼런스(geo_transform/crs)로 Track 점을 CV 픽셀에 정합한다.

InSAR real 엔진이 Track 결과 H5 의 점 좌표(world: lon/lat 또는 투영 x/y)를 CV 픽셀
프레임으로 옮길 때 쓴다. 순수 픽셀↔world 아핀은 모듈 중립적이라 `..geotransform` 에
두고(여기서 재노출), 이 모듈은 InSAR 특화 로직(CRS 재투영·고정단 거리)을 담는다.
CRS 가 같으면 역아핀만으로 충분하고, 다를 때만 pyproj 를 지연 import 한다.
"""

from __future__ import annotations

import numpy as np

# 픽셀↔world 아핀 원시함수는 CV·InSAR 공용 → 중립 모듈에서 가져와 재노출.
from ..geotransform import GeoTransform, pixel_to_world, world_to_pixel

__all__ = ["GeoTransform", "pixel_to_world", "world_to_pixel", "axial_from_fixed", "reproject"]


def axial_from_fixed(
    xy: np.ndarray,
    member: np.ndarray | None = None,
    fixed_index: int | None = None,
) -> np.ndarray:
    """[N,2] 점군의 종축(PC1) 위에서 **고정단까지의 거리**를 반환(입력과 같은 단위).

    교량 점은 종축을 따라 분포하므로 PC1 ≈ 종방향 축이다. 열팽창 변위는 고정단
    으로부터 선형 증가하므로 기준점(영점)을 고정단에 둔다:

      - `member` 와 `fixed_index`(예: abutment 라벨)가 주어지고 해당 점이 있으면
        그 점들의 종축 좌표 평균을 고정단으로 본다(교대=고정 지지의 관례).
      - 없으면 종축의 한쪽 끝(min 투영)을 기본 고정단으로 둔다.

    부호 모호한 PC1 방향과 무관하도록 `|s - s_fixed|` 로 거리만 돌려준다.
    """
    xy = np.asarray(xy, dtype=np.float64)
    centered = xy - xy.mean(axis=0)
    # 공분산 고유분해로 제1주성분(최대 고유값) 방향을 구한다.
    _, vecs = np.linalg.eigh(centered.T @ centered)
    s = centered @ vecs[:, -1]
    if member is not None and fixed_index is not None and np.any(np.asarray(member) == fixed_index):
        s_fixed = float(np.mean(s[np.asarray(member) == fixed_index]))
    else:
        s_fixed = float(s.min())
    return np.abs(s - s_fixed)


def reproject(xy: np.ndarray, src_crs: str | None, dst_crs: str | None) -> np.ndarray:
    """[N,2] world 좌표를 src_crs → dst_crs 로 재투영한다.

    둘 중 하나라도 None 이거나 같으면 그대로 반환(no-op). 다를 때만 pyproj 를
    지연 import 한다(미설치 시 친절한 에러).
    """
    if not src_crs or not dst_crs or src_crs == dst_crs:
        return np.asarray(xy, dtype=np.float64)
    try:
        from pyproj import Transformer
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise ImportError(
            f"CRS 재투영({src_crs}→{dst_crs})에 pyproj 가 필요합니다. "
            "`pip install pyproj` 후 다시 실행하거나, Track 좌표를 CV 의 CRS 로 맞추세요."
        ) from exc
    tf = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    x, y = tf.transform(xy[:, 0], xy[:, 1])
    return np.column_stack([np.asarray(x), np.asarray(y)])
