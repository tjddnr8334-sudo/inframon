"""ERA5(강수·습도)로 SARvey master 선정 — InSAR 데이터 선별 E 단계.

선별된 트랙의 각 취득일에 대해 교량 위치의 ERA5 재분석값(총 강수, 평균 상대습도)을
받아, 대기지연(APS)·강우 영향이 가장 작은 날(건조·저습)을 master 로 고른다.

ERA5 소스는 **Open-Meteo ERA5 archive API**(키 불필요·빠름). 네트워크는
`_fetch_era5_archive` 한 곳으로 격리(테스트에서 monkeypatch). CDS API 경로는 이후 확장.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime
from statistics import mean

from .recipe import MasterSelection, SceneWeather

OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/era5"


def _ymd_to_iso(ymd: str) -> str:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _fetch_era5_archive(lat: float, lon: float, start_date: str, end_date: str,
                        *, timeout: float = 30.0) -> dict:
    """Open-Meteo ERA5 archive 시간별 강수·상대습도 조회(네트워크 격리 지점).

    start_date/end_date 는 YYYY-MM-DD.
    """
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "start_date": start_date, "end_date": end_date,
        "hourly": "precipitation,relative_humidity_2m",
        "timezone": "UTC",
    })
    req = urllib.request.Request(f"{OPEN_METEO_ARCHIVE}?{q}",
                                 headers={"User-Agent": "inframon-insar/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _daily_aggregate(hourly: dict) -> dict[str, tuple[float, float]]:
    """시간별 → 날짜별 (총 강수[mm], 평균 상대습도[%]). 키는 YYYYMMDD."""
    times = hourly.get("time", [])
    precip = hourly.get("precipitation", [])
    rh = hourly.get("relative_humidity_2m", [])
    buckets: dict[str, tuple[float, list[float]]] = {}
    for t, p, h in zip(times, precip, rh):
        day = t[:10].replace("-", "")
        psum, hs = buckets.get(day, (0.0, []))
        if p is not None:
            psum += float(p)
        if h is not None:
            hs.append(float(h))
        buckets[day] = (psum, hs)
    return {d: (psum, mean(hs) if hs else float("nan")) for d, (psum, hs) in buckets.items()}


def _normalize(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi - lo <= 0:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _expected_coherence(
    dates: list[str], perp_baselines: dict[str, float] | None,
    temporal_crit_days: float, perp_crit_m: float,
) -> list[float]:
    """각 후보의 기대 coherence rho = 다른 장면들과의 시·공간 baseline coherence 평균.

    γ_temp = max(0, 1-|Δt|/Tc), γ_perp = max(0, 1-|ΔB|/Bc), rho_i = mean_{j≠i} γ_temp·γ_perp.
    수직 baseline 이 없으면 γ_perp=1 (시간 baseline 만).
    """
    t = [(datetime.strptime(d, "%Y%m%d") - datetime.strptime(dates[0], "%Y%m%d")).days
         for d in dates]
    use_perp = perp_baselines is not None
    rho = []
    for i in range(len(dates)):
        gam = []
        for j in range(len(dates)):
            if i == j:
                continue
            gt = max(0.0, 1.0 - abs(t[i] - t[j]) / temporal_crit_days)
            gp = 1.0
            if use_perp and dates[i] in perp_baselines and dates[j] in perp_baselines:
                gp = max(0.0, 1.0 - abs(perp_baselines[dates[i]] - perp_baselines[dates[j]]) / perp_crit_m)
            gam.append(gt * gp)
        rho.append(sum(gam) / len(gam) if gam else 0.0)
    return rho


def select_master(
    lat: float,
    lon: float,
    dates: list[str],
    scene_names: list[str] | None = None,
    *,
    perp_baselines: dict[str, float] | None = None,
    temporal_crit_days: float = 365.0,
    perp_crit_m: float = 300.0,
) -> MasterSelection:
    """master = baseline 기대 coherence × 건조도(강수·습도) 종합 최대인 취득일.

    combined = norm(rho) × dry_score,  dry_score = (1-norm(강수))·(1-norm(습도)).
    rho = 시·공간 baseline 기대 coherence(perp_baselines 있으면 수직 baseline 포함).
    """
    if not dates:
        raise ValueError("master 선정에는 취득일이 1개 이상 필요합니다.")

    hourly = _fetch_era5_archive(lat, lon, _ymd_to_iso(min(dates)), _ymd_to_iso(max(dates)))
    agg = _daily_aggregate(hourly.get("hourly", {}))
    items = [(d, agg[d]) for d in dates if d in agg]
    if not items:
        raise ValueError("요청한 취득일에 대한 ERA5 데이터를 찾지 못했습니다.")
    ds = [d for d, _ in items]

    # 건조도 (강수·습도)
    npre = _normalize([p for _, (p, _) in items])
    nhum = _normalize([h for _, (_, h) in items])
    dry = [(1.0 - npre[i]) * (1.0 - nhum[i]) for i in range(len(ds))]

    # baseline 기대 coherence (이미 [0,1] coherence 라 정규화하지 않고 그대로 곱한다 —
    # 정규화하면 baseline-최적 장면이 0이 돼 dry 와의 곱이 붕괴함)
    rho = _expected_coherence(ds, perp_baselines, temporal_crit_days, perp_crit_m)
    combined = [rho[i] * dry[i] for i in range(len(ds))]
    scenes = [
        SceneWeather(date=ds[i], precip_mm=round(items[i][1][0], 3),
                     humidity_pct=round(items[i][1][1], 2), rho=round(rho[i], 4),
                     dry_score=round(dry[i], 4), combined=round(combined[i], 4))
        for i in range(len(ds))
    ]
    best = max(range(len(ds)), key=lambda i: (combined[i], rho[i], -i))
    master = ds[best]
    name = scene_names[dates.index(master)] if (scene_names and master in dates) else None

    return MasterSelection(
        selected_master=master, master_scene=name, lat=lat, lon=lon,
        used_baseline=perp_baselines is not None,
        temporal_crit_days=temporal_crit_days, perp_crit_m=perp_crit_m, scenes=scenes,
    )
