"""픽셀↔world 아핀 원시함수 (CV·InSAR 공용, 모듈 간 중립).

geo_transform 은 GDAL 6-tuple (c,a,b,f,d,e) 로 픽셀(col,row)→world(x,y) 를 정의한다::

    x = c + a*col + b*row
    y = f + d*col + e*row

CV(상류)가 영상 지오레퍼런스에서 산출하고, InSAR(하류)가 정합에 쓴다. 두 모듈이
같은 규약을 공유하도록 원시 변환만 여기에 둔다(상위 모듈 간 의존 없음).
"""

from __future__ import annotations

import numpy as np

GeoTransform = tuple[float, float, float, float, float, float]


def pixel_to_world(gt: GeoTransform, col: np.ndarray, row: np.ndarray) -> np.ndarray:
    """픽셀(col,row) → world(x,y). 반환 [N,2]."""
    c, a, b, f, d, e = gt
    x = c + a * col + b * row
    y = f + d * col + e * row
    return np.column_stack([x, y])


def world_to_pixel(gt: GeoTransform, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """world(x,y) → 픽셀(col,row) (역아핀). 반환 [N,2] float (반올림은 호출 측)."""
    c, a, b, f, d, e = gt
    det = a * e - b * d
    if abs(det) < 1e-12:
        raise ValueError(f"geo_transform 의 선형부가 특이행렬입니다(det={det:g}): {gt}")
    dx = np.asarray(x, dtype=np.float64) - c
    dy = np.asarray(y, dtype=np.float64) - f
    col = (e * dx - b * dy) / det
    row = (-d * dx + a * dy) / det
    return np.column_stack([col, row])
