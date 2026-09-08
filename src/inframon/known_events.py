"""알려진 사고 교량 대조 — 결과가 '정상'인데 실제로는 사고가 있었으면 그것을 **먼저** 말한다.

정자교(성남 분당)는 2023-04-05 남측 보도부가 붕괴했다. 그런데 우리 파이프라인은 그 교량을
2017~2025 관측으로 돌려 CRI 0.546 '정상' 을 냈다. 파이프라인 오류가 아니다 — 열·추세를
빼면 붕괴 시점의 계단이 z=−0.6 으로 잡음과 구별되지 않는다. 낙하한 보도부(수 m)에는
살아남은 PS 가 없고(있었다면 201시점 내내 남아 있을 수 없다), 남은 부위는 붕괴 전후
변위 차이가 잡음 수준이다. 즉 **이 해상도·이 점들로는 그 붕괴를 볼 수 없다.**

그 사실을 산출물이 스스로 적지 않으면 '정상' 이 보고로 나간다. 여기서는 좌표로 알려진
사건을 찾아 감사 표기(notes)에 넣고, 관측 기간이 사건을 가로지르면 붕괴 시점 전후의
변위 계단을 계산해 함께 적는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

# 알려진 사건. 좌표는 교량 중심, 출처는 공개 보도. 더 알게 되면 여기 추가한다.
KNOWN_EVENTS: tuple[dict, ...] = (
    {"name": "정자교", "lat": 37.36854, "lon": 127.10900, "date": "2023-04-05",
     "event": "남측 보도부 붕괴(사망 1·부상 1)",
     "note": "보도부 국부 낙하 — Sentinel-1 화소(~11 m)보다 작은 부위"},
)
MATCH_M = 150.0


@dataclass
class EventCheck:
    event: dict
    dist_m: float
    covered: bool                 # 관측 기간이 사건을 가로지르는가
    step_mm: float | None = None  # 사건 전후 창 평균 차(열·추세 제거 후 잔차)
    z: float | None = None

    def describe(self) -> str:
        head = (f"⚠ 알려진 사건: {self.event['name']} {self.event['date']} "
                f"{self.event['event']} ({self.dist_m:.0f} m)")
        if not self.covered:
            return head + " — 관측 기간 밖"
        if self.z is None:
            return head + " — 관측 기간 안이나 시계열 검정 불가"
        seen = abs(self.z) >= 3.0
        return (head + f" — 관측 기간 안. 사건 전후 잔차 계단 {self.step_mm:+.1f} mm (z={self.z:+.1f}) → "
                + ("시계열에 보인다" if seen else
                   "**잡음과 구별되지 않는다**. 이 점들·이 해상도로는 사건을 볼 수 없다. "
                   "'정상' 은 관측 한계 안의 판정이다"))


def _step_z(los, days, event_day: float, window: int = 6):
    """중앙값 시계열에서 연주기+선형 제거 → 사건 전후 window 창 평균 차 / 잡음."""
    import numpy as np

    los = np.asarray(los, float)
    t = np.asarray(days, float) / 365.25
    X = np.column_stack([np.ones_like(t), t, np.sin(2 * np.pi * t), np.cos(2 * np.pi * t)])
    med = np.nanmedian(los, axis=0)
    b, *_ = np.linalg.lstsq(X, med, rcond=None)
    r = med - X @ b
    s = float(np.std(np.diff(r)) / math.sqrt(2)) or 1e-9
    k = int(np.searchsorted(days, event_day))
    if k < window or k + window > len(r):
        return None, None
    step = float(r[k:k + window].mean() - r[k - window:k].mean())
    return step, step / (s * math.sqrt(2.0 / window))


def check(lat: float, lon: float, *, epochs=None, los=None) -> list[EventCheck]:
    """좌표 근처의 알려진 사건. epochs(YYYYMMDD)·los[N,M] 를 주면 계단 검정까지."""
    out = []
    k = math.cos(math.radians(lat)) * 111_320.0
    for ev in KNOWN_EVENTS:
        d = math.hypot((ev["lon"] - lon) * k, (ev["lat"] - lat) * 111_320.0)
        if d > MATCH_M:
            continue
        covered = False
        step = z = None
        if epochs is not None and len(epochs):
            ds = [datetime.strptime((e.decode() if isinstance(e, bytes) else str(int(e) if not isinstance(e, str) else e)),
                                    "%Y%m%d") for e in epochs]
            ev_d = datetime.strptime(ev["date"], "%Y-%m-%d")
            covered = min(ds) <= ev_d <= max(ds)
            if covered and los is not None:
                import numpy as np
                order = np.argsort([x.timestamp() for x in ds])
                days = np.array([(ds[i] - ds[order[0]]).days for i in order], float)
                step, z = _step_z(np.asarray(los)[:, order], days, (ev_d - ds[order[0]]).days)
        out.append(EventCheck(ev, d, covered, step, z))
    return out
