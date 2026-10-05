"""inframon 한계값(``life.limits``) 기준 허용변위 비율 — 엔진 판정과 **병기**한다.

엔진(bridge-insar-monitor) 기본값: 허용 총침하 50 mm, 각변위 0.004(연속·강결)/0.008(단순) — AASHTO.
inframon ``life.limits.DEFAULTS``: 침하 25 mm, 각변위 1/500. 같은 측정값이라도 기준이 다르면 비율이
두 배까지 달라지므로, 어느 기준으로 본 값인지 출처 라벨과 함께 돌려준다(조용히 섞지 않는다).
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
