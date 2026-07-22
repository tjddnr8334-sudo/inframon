"""LOS ↔ 연직 기하 변환 — 사용성 한계는 연직인데 관측은 LOS 다.

침하 허용치(25mm)도, 부등침하 각변위(1/500)도 **연직량**에 대한 규정이다. 그런데
단일 궤도 InSAR 가 재는 것은 위성 시선(LOS) 방향 성분뿐이다.

    LOS ≈ d_vertical·cos θ − d_horizontal·sin θ·(방위 성분)

θ 는 입사각. Sentinel-1 IW 는 29°~46°(대표 39°)이므로 cos θ ≈ 0.69~0.87 이다.
LOS 를 연직인 척 쓰면 변위가 **cos θ 배로 과소평가**되고, 잔존수명은 그 역수만큼
길게 나온다 — 39° 면 **1/cos39° = 1.29, 즉 29% 낙관적**이다.

여기서는 **단일 궤도 연직 가정**(관측 구간의 변위가 주로 연직)으로 되돌린다.

    d_vertical = d_LOS / cos θ

이 가정이 틀리는 경우(수평 이동이 지배적인 사면·측방유동)에는 연직량이 과대평가된다.
그래서 가정을 결과에 반드시 기록하고, 진짜 해법은 asc+desc 융합(`/insar/vertical`)임을
같이 알린다. 융합 연직이 있으면 이 모듈은 아예 쓰이지 않는다.
"""

from __future__ import annotations

import numpy as np

# Sentinel-1 IW 대표 입사각[deg] — 상류가 입사각을 안 줄 때의 폴백.
# 참값이 33° 인데 39° 를 쓰면 오차 8% 정도로, 투영을 아예 안 할 때(29%)보다 훨씬 낫다.
DEFAULT_INCIDENCE_DEG = 39.0
# 물리적으로 성립하는 입사각 범위. 벗어나면 상류 값이 라디안이거나 다른 각도 정의다.
_MIN_DEG, _MAX_DEG = 10.0, 75.0


def los_to_vertical(
    los: np.ndarray,
    incidence_deg: np.ndarray | float | None,
    *,
    default_deg: float = DEFAULT_INCIDENCE_DEG,
) -> tuple[np.ndarray, dict]:
    """LOS 시계열 [N,M] → 연직 가정 시계열 [N,M] 과 그 근거.

    Args:
        los: [N,M] LOS 변위[mm]
        incidence_deg: [N] 점별 입사각 또는 스칼라. None 이면 `default_deg` 가정.

    Returns:
        (vertical[N,M], meta) — meta 에 입사각 출처·범위·적용 배율을 남긴다.
    """
    L = np.atleast_2d(np.asarray(los, dtype=np.float64))
    n = L.shape[0]

    if incidence_deg is None:
        inc = np.full(n, float(default_deg))
        source = f"가정 {default_deg:.1f}° (Sentinel-1 IW 대표값 — 상류가 입사각을 주지 않음)"
        assumed = True
    else:
        arr = np.asarray(incidence_deg, dtype=np.float64).ravel()
        if arr.size == 1:
            inc = np.full(n, float(arr[0]))
        elif arr.size == n:
            inc = arr.copy()
        else:
            raise ValueError(f"입사각 개수 {arr.size} != 점 수 {n}")
        source = "관측 입사각(/insar/incidence_deg)"
        assumed = False

    bad = ~np.isfinite(inc) | (inc < _MIN_DEG) | (inc > _MAX_DEG)
    n_bad = int(bad.sum())
    if n_bad:
        inc[bad] = float(default_deg)
        source += f" · 범위 밖 {n_bad}점은 {default_deg:.1f}° 로 대체"

    cos_t = np.cos(np.deg2rad(inc))
    vertical = L / cos_t[:, None]

    return vertical, {
        "applied": True,
        "assumption": "단일 궤도 연직 가정 — 변위가 주로 연직이라고 보고 d_v = d_LOS / cos θ",
        "incidence_source": source,
        "incidence_assumed": assumed,
        "incidence_deg": {"median": round(float(np.median(inc)), 3),
                          "min": round(float(inc.min()), 3),
                          "max": round(float(inc.max()), 3)},
        "scale_1_over_cos": {"median": round(float(np.median(1.0 / cos_t)), 4),
                             "min": round(float((1.0 / cos_t).min()), 4),
                             "max": round(float((1.0 / cos_t).max()), 4)},
        "n_out_of_range": n_bad,
        "better": "asc+desc 융합(/insar/vertical)이 있으면 가정 없이 실제 연직 성분을 쓴다.",
    }
