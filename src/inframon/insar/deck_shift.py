"""교량마다 다른 기하를 자동으로 푼다 — 쉬프트·버퍼·관측가능성.

InSAR 지오코딩은 DEM 을 쓴다. 교량 데크는 그 DEM(지면·수면)보다 높으므로 점이
`δh/tanθ` 만큼 **지상 LOS 방향으로** 밀린다. 그런데 이게 문제가 되는 정도는 교량마다,
궤도마다 다르다:

  · **데크가 LOS 와 직교**하면 쉬프트가 통째로 **횡방향**으로 나타난다 → 점이 다리에서
    벗어난다. 보정이 꼭 필요하고, 보정 효과도 눈에 보인다.
  · **데크가 LOS 와 나란**하면 쉬프트가 **종방향**으로 미끄러진다 → 다리 위에서 앞뒤로
    움직일 뿐 벗어나지 않는다. 보정해도 "다리 위인가"는 달라지지 않고, 검증도 불가능하다.

청양교가 후자였다(LOS 의 데크 횡축 성분 0.11). 그래서 12.4m 쉬프트 중 횡방향은 1.4m
뿐이고, 추출 버퍼가 만든 산포(σ 17m)에 묻혀 분리되지 않았다.

그래서 **고정값(버퍼 30m·쉬프트 무시)으로는 안 된다.** 교량 방위·LOS·폭·형하고·입사각을
받아 그 교량에 맞는 값을 낸다:

    권장 버퍼 = 반폭 + |횡방향 쉬프트| + 여유
    관측가능성 = |LOS·데크법선|  — 이게 낮으면 "쉬프트 검증 불가"라고 말한다
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .geolocation import los_ground_unit

PIXEL_M = 14.0                 # 지오코딩 픽셀(SNAP TC 기본) — 여유의 하한 근거
MIN_BUFFER_M = 8.0
MAX_BUFFER_M = 60.0
OBSERVABLE_MIN = 0.4           # 이 미만이면 쉬프트가 종방향으로 미끄러져 검증 불가


@dataclass
class DeckShift:
    """이 교량·이 궤도에서의 쉬프트 기하."""

    deck_azimuth_deg: float
    los_ground: tuple[float, float]
    incidence_deg: float
    dh_m: float
    shift_m: float                 # δh/tanθ 전체 크기
    cross_m: float                 # 데크 **횡**방향 성분(부호 있음) — 다리를 벗어나게 하는 성분
    along_m: float                 # 데크 **종**방향 성분 — 다리 위에서 미끄러짐
    observability: float           # |LOS·데크법선| ∈ [0,1]
    buffer_m: float                # 권장 추출 버퍼(반폭 + |cross| + 여유)
    half_width_m: float

    @property
    def shift_observable(self) -> bool:
        """쉬프트가 횡방향으로 드러나 검증·보정 효과를 볼 수 있는가."""
        return self.observability >= OBSERVABLE_MIN

    def describe(self) -> str:
        obs = ("횡방향으로 드러남 — 보정 필요·검증 가능"
               if self.shift_observable else
               "종방향으로 미끄러짐 — 다리를 벗어나지 않고 검증도 불가")
        return (f"데크 {self.deck_azimuth_deg:.0f}° · 입사각 {self.incidence_deg:.1f}° · "
                f"δh {self.dh_m:.1f}m → 쉬프트 {self.shift_m:.1f}m "
                f"(횡 {self.cross_m:+.1f} · 종 {self.along_m:+.1f}) · "
                f"관측가능성 {self.observability:.2f} [{obs}] · 버퍼 {self.buffer_m:.0f}m")

    def as_dict(self) -> dict:
        return {"deck_azimuth_deg": round(self.deck_azimuth_deg, 1),
                "incidence_deg": round(self.incidence_deg, 2),
                "dh_m": round(self.dh_m, 2), "shift_m": round(self.shift_m, 2),
                "cross_m": round(self.cross_m, 2), "along_m": round(self.along_m, 2),
                "observability": round(self.observability, 3),
                "shift_observable": self.shift_observable,
                "buffer_m": round(self.buffer_m, 1),
                "half_width_m": round(self.half_width_m, 1)}


def deck_azimuth_deg(geometry_latlon) -> float:
    """데크 폴리라인 → 종축 방위[°] (북=0, 동=90). 양 끝점 기준."""
    pts = [(float(a), float(b)) for a, b in geometry_latlon]
    if len(pts) < 2:
        raise ValueError("데크선에는 점이 둘 이상 필요합니다.")
    (la1, lo1), (la2, lo2) = pts[0], pts[-1]
    k = math.cos(math.radians((la1 + la2) / 2))
    return math.degrees(math.atan2((lo2 - lo1) * k, la2 - la1)) % 360.0


def shift_geometry(*, deck_azimuth: float, heading_deg: float, incidence_deg: float,
                   dh_m: float, width_m: float | None = None,
                   look_side: str = "right", margin_m: float | None = None) -> DeckShift:
    """이 교량의 쉬프트 성분·권장 버퍼를 계산한다.

    deck_azimuth: 데크 종축 방위[°] · heading_deg: 위성 진행방위 · incidence_deg: 입사각
    dh_m: DEM 대비 데크 고도차(표준데이터 교량높이) · width_m: 교량 폭(없으면 20m 가정)
    """
    inc = float(incidence_deg)
    if not (10.0 <= inc <= 75.0):
        raise ValueError(f"입사각이 물리 범위를 벗어납니다: {inc}° (Sentinel-1 IW 10~75°)")
    ue, un = los_ground_unit(heading_deg, look_side)
    shift = float(dh_m) / math.tan(math.radians(inc))

    a = math.radians(deck_azimuth)
    axis = np.array([math.sin(a), math.cos(a)])            # 데크 종축 (E,N)
    nrm = np.array([-axis[1], axis[0]])                    # 데크 법선(횡축)
    los = np.array([ue, un])
    cross = float(shift * (los @ nrm))
    along = float(shift * (los @ axis))
    obs = float(abs(los @ nrm))

    half_w = (float(width_m) / 2.0) if width_m else 10.0
    margin = float(margin_m) if margin_m is not None else PIXEL_M / 2.0
    buf = min(MAX_BUFFER_M, max(MIN_BUFFER_M, half_w + abs(cross) + margin))
    return DeckShift(deck_azimuth_deg=float(deck_azimuth) % 360.0, los_ground=(ue, un),
                     incidence_deg=inc, dh_m=float(dh_m), shift_m=abs(shift),
                     cross_m=cross, along_m=along, observability=obs,
                     buffer_m=buf, half_width_m=half_w)


def for_bridge(geometry_latlon, *, heading_deg: float, incidence_deg: float,
               dh_m: float, width_m: float | None = None,
               look_side: str = "right") -> DeckShift:
    """데크선 + 궤도 기하 + 제원 → 그 교량의 쉬프트 기하(한 번에)."""
    return shift_geometry(deck_azimuth=deck_azimuth_deg(geometry_latlon),
                          heading_deg=heading_deg, incidence_deg=incidence_deg,
                          dh_m=dh_m, width_m=width_m, look_side=look_side)
