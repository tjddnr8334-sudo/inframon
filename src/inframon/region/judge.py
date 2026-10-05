"""inframon 한계값(``life.limits``) 기준 허용변위 비율 — 광역 감시의 **단일 기준**.

엔진(bridge-insar-monitor) 설정(criteria.yaml)도 같은 값(침하 25 mm, 각변위 1/500)으로 통일돼 있다.
이 함수는 엔진이 낸 측정값(누적 수직변위·각변위)으로 비율을 다시 계산해, 엔진 설정과 inframon 한계값이
어긋나지 않았는지 확인하는 데에도 쓴다. 출처 라벨을 함께 돌려준다.
"""

from __future__ import annotations

from ..life.limits import DEFAULTS, SOURCES


def ratios(d_now_mm, beta_now, *, d_proj_mm=None, beta_proj=None) -> dict:
    """측정(또는 예측) 누적 수직변위[mm]·각변위[rad] → inframon 기준 비율과 출처."""
    s_lim, b_lim = DEFAULTS["settlement_mm"], DEFAULTS["angular_distortion"]

    def r(d, b):
        if d is None and b is None:
            return None
        return max((d or 0.0) / s_lim, (b or 0.0) / b_lim)
    return dict(ratio_now=r(d_now_mm, beta_now), ratio_proj=r(d_proj_mm, beta_proj),
                settlement_mm=s_lim, angular_distortion=b_lim,
                source=f"settlement: {SOURCES.get('settlement_mm', '')} / angular: {SOURCES.get('angular_distortion', '')}")
