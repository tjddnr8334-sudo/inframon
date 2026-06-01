"""교량 위치 기온 자동 수집 (Open-Meteo ERA5, 키 불필요) → PINN 온도 입력.

InSAR 취득일(date_labels, YYYYMMDD)에 맞춰 **일평균 기온[°C] 시계열[M]**을 만든다.
`cfg.pinn_temperature` 로 넣으면 PINN 열팽창 성분이 실측 온도로 구동된다(α·L·ΔT).
era5_master 와 같은 Open-Meteo archive 를 쓴다(키·로그인 불필요).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from statistics import mean

import numpy as np

OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"


def _daily_mean_temps(data: dict) -> dict[str, float]:
    """Open-Meteo hourly 응답 → {YYYYMMDD: 일평균 기온[°C]}."""
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    buckets: dict[str, list[float]] = {}
    for t, v in zip(times, temps):
        if v is None:
            continue
        buckets.setdefault(t[:10].replace("-", ""), []).append(float(v))
    return {d: mean(vs) for d, vs in buckets.items() if vs}


def fetch_temperature_series(
    lat: float, lon: float, date_labels, *, timeout: float = 30.0
) -> np.ndarray:
    """취득일별 일평균 기온[°C] 배열[M] (date_labels 순서). 누락일은 전체 평균으로.

    date_labels: YYYYMMDD 문자열/정수 리스트(InSAR `/insar/date_labels`).
    네트워크/파싱 실패 시 RuntimeError.
    """
    days = [str(d) for d in np.asarray(date_labels).ravel().tolist()]
    if not days:
        raise ValueError("date_labels 가 비었습니다")
    span = sorted(days)
    start = f"{span[0][:4]}-{span[0][4:6]}-{span[0][6:8]}"
    end = f"{span[-1][:4]}-{span[-1][4:6]}-{span[-1][6:8]}"
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
        "hourly": "temperature_2m", "timezone": "UTC",
    })
    req = urllib.request.Request(f"{OPEN_METEO_ARCHIVE}?{q}",
                                 headers={"User-Agent": "inframon/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 고정 공공 API
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Open-Meteo 기온 조회 실패: {exc}") from exc

    daily = _daily_mean_temps(data)
    if not daily:
        raise RuntimeError("기온 데이터가 비었습니다(좌표/기간 확인)")
    glob = mean(daily.values())
    return np.array([daily.get(d, glob) for d in days], dtype=float)
