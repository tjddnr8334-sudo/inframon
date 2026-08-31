"""Track 결과 H5 투입 전 사전검증(preflight) — 실데이터 인제스트 게이트.

`run_insar_real`/`import_track_h5` 가 소비하는 Track export H5 를 인제스트 **전에**
점검한다. `read_track_h5` 는 문제가 있으면 읽다가 예외를 던지지만, 여기서는 절대
예외를 내지 않고 **구조적 리포트**(errors=차단 / warnings=비차단 / 요약 통계)를 돌려준다.
사용자가 실데이터를 넣기 전 "투입 가능한가"를 한눈에 판단하게 한다(CLI `--check-track`).

점검: 필수 데이터셋 존재·형상 일관(N/M)·취득일 파싱·coherence 범위·LOS 유한·고도/CRS·
좌표계(경위도 vs 투영). CV geo_transform 으로 정합하므로 CRS 누락은 경고(차단 아님).

여기에 더해 **산출물이 측정하려던 대상을 실제로 담고 있는가**를 본다. 이 게이트가 없어서
"6×6km 광역 PS 필드 20000점 중 교량 30m 안은 6점(0.03%)" 인 트랙과, 위상 언래핑을 하지
않아 LOS 가 ±λ/4 에 갇힌 트랙이 ✅ 를 받고 하류(PINN·CRI·트윈·BMAP)까지 그대로 흘러갔다.
  · `target=(lat, lon)` 을 주면 교량 반경 내 유효점 수·이격 분포를 세고, 0 점이면 차단한다.
  · 언래핑 검사는 좌표 없이도 돈다 — |LOS| 가 λ/4 를 한 번도 넘지 않고 그 안에서 균일하게
    퍼져 있으면 래핑 위상이다(변위가 작은 실제 신호는 0 근처에 몰린다).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import h5py
import numpy as np

_LONLAT = ("pixel_lonlat", "ps_lonlat")
_COH = ("coh", "temp_coh")
_HEIGHT = ("height", "hgt", "dem", "elevation")
_CRS_ATTRS = ("crs", "CRS", "epsg", "EPSG")
MIN_POINTS = 2
MIN_DATES = 2

# Sentinel-1 C-band λ. 언래핑 안 된 간섭위상은 한 파장(2π)이 λ/2 변위에 대응하므로
# LOS 는 ±λ/4 안에 갇힌다 — 이 밖의 점이 하나도 없으면 언래핑을 의심한다.
# snap_backend.WAVELENGTH_M 과 같은 값을 쓴다(파일에 RADAR_WAVELENGTH 가 있으면 그쪽 우선).
WAVELENGTH_MM = 55.46576
LOS_WRAP_LIMIT_MM = WAVELENGTH_MM / 4.0          # 13.8664 mm
# 래핑 산출물의 |LOS|max 는 λ/4 에 **정확히** 닿는다. 부동소수·λ 미세차로 그 값을 아주
# 조금 넘는 일이 있어 상대여유를 둔다(0.1% = λ/4 기준 0.014mm).
WRAP_LIMIT_TOL = 1.0e-3
# 래핑이면 [-λ/4, λ/4] 에 **균일**하게 퍼져 바깥 절반이 약 50% 를 차지한다.
# 실제 변위 신호는 0 근처에 몰려 이 비율이 낮다. 여유를 두고 0.40 을 임계로 쓴다.
WRAP_OUTER_HALF_FRAC = 0.40
DECK_RADIUS_M = 30.0                             # ⑨ 데크 PS/DS 와 같은 반경
DECK_NEAR_RADIUS_M = 100.0                       # 교량 인근(참고 집계)


@dataclass(frozen=True)
class TrackPreflight:
    path: Path
    n_points: int | None = None
    n_dates: int | None = None
    has_height: bool = False
    crs: str | None = None
    looks_geographic: bool = False
    coherence_min: float | None = None
    coherence_max: float | None = None
    los_finite_frac: float | None = None
    date_first: str | None = None
    date_last: str | None = None
    los_abs_max: float | None = None
    looks_wrapped: bool = False
    target: tuple[float, float] | None = None
    n_within_deck: int | None = None              # target 반경 30m 내 점수
    n_within_near: int | None = None              # target 반경 100m 내 점수
    dist_median_m: float | None = None            # 대상까지 이격 중앙값
    dist_min_m: float | None = None
    extent_km: tuple[float, float] | None = None  # 점군 공간범위(가로, 세로)[km]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        """차단 오류가 없으면 인제스트 가능."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["path"] = str(self.path)
        d["is_ready"] = self.is_ready
        return d


def _first(f: h5py.File, names: tuple[str, ...]) -> str | None:
    return next((n for n in names if n in f), None)


def preflight_track_h5(path: str | Path, *,
                       target: tuple[float, float] | None = None,
                       deck_radius_m: float = DECK_RADIUS_M) -> TrackPreflight:
    """Track export H5 를 점검해 TrackPreflight 리포트를 돌려준다(예외 없음).

    `target=(lat, lon)` 을 주면 **그 교량을 실제로 담고 있는지**까지 본다 — 반경
    `deck_radius_m` 내 점이 0 이면 차단한다. 좌표를 주지 않으면 기존 점검만 수행한다.
    """
    path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return TrackPreflight(path, errors=[f"파일이 없습니다: {path}"])

    info: dict[str, Any] = {}
    try:
        with h5py.File(path, "r") as f:
            # 파일이 자기 파장을 기록해 두면 그것으로 래핑 한계를 잡는다(엔진별 λ 차이).
            _wl = f.attrs.get("RADAR_WAVELENGTH")
            if _wl is not None:
                try:                              # m 로 기록됨 → mm
                    info["wavelength_mm"] = float(np.asarray(_wl).item()) * 1000.0
                except (TypeError, ValueError):
                    pass
            lon_name = _first(f, _LONLAT)
            coh_name = _first(f, _COH)
            if lon_name is None:
                errors.append("점 좌표 데이터셋(pixel_lonlat 또는 ps_lonlat)이 없습니다")
            if "epochs" not in f:
                errors.append("epochs(취득일) 데이터셋이 없습니다")
            if "los_mm" not in f:
                errors.append("los_mm(LOS 변위) 데이터셋이 없습니다")
            if coh_name is None:
                errors.append("coherence(coh 또는 temp_coh) 데이터셋이 없습니다")

            lonlat = np.asarray(f[lon_name][()]) if lon_name else None
            los = np.asarray(f["los_mm"][()]) if "los_mm" in f else None
            coh = np.asarray(f[coh_name][()]) if coh_name else None
            epochs = f["epochs"][()] if "epochs" in f else None

            # ── 형상 일관성 ──
            n_points = n_dates = None
            if los is not None and los.ndim == 2:
                n_points, n_dates = int(los.shape[0]), int(los.shape[1])
            elif los is not None:
                errors.append(f"los_mm 는 [N,M] 2차원이어야 합니다 (실제 {los.shape})")

            if lonlat is not None and (lonlat.ndim != 2 or lonlat.shape[1] != 2):
                errors.append(f"좌표는 [N,2] 여야 합니다 (실제 {lonlat.shape})")
            elif lonlat is not None and n_points is not None and lonlat.shape[0] != n_points:
                errors.append(f"좌표 점수 {lonlat.shape[0]} ≠ los 점수 {n_points}")
            if coh is not None and n_points is not None and coh.shape[0] != n_points:
                errors.append(f"coherence 점수 {coh.shape[0]} ≠ los 점수 {n_points}")

            if n_points is not None and n_points < MIN_POINTS:
                errors.append(f"측정점이 {n_points}개뿐입니다 (최소 {MIN_POINTS})")
            if n_dates is not None and n_dates < MIN_DATES:
                errors.append(f"취득 시점이 {n_dates}개뿐입니다 (최소 {MIN_DATES})")
            info["n_points"], info["n_dates"] = n_points, n_dates

            # ── 취득일 파싱 ──
            if epochs is not None:
                try:
                    labels = np.asarray(epochs).astype(str)
                    parsed = sorted(datetime.strptime(s, "%Y%m%d") for s in labels)
                    info["date_first"] = parsed[0].strftime("%Y%m%d")
                    info["date_last"] = parsed[-1].strftime("%Y%m%d")
                    if n_dates is not None and len(labels) != n_dates:
                        errors.append(f"epochs 수 {len(labels)} ≠ los 시점 {n_dates}")
                except (ValueError, TypeError):
                    errors.append("epochs 를 YYYYMMDD 로 해석하지 못했습니다")

            # ── coherence 범위 ──
            if coh is not None and coh.size:
                cmin, cmax = float(np.nanmin(coh)), float(np.nanmax(coh))
                info["coherence_min"], info["coherence_max"] = cmin, cmax
                if cmin < 0.0 or cmax > 1.0:
                    warnings.append(f"coherence 가 [0,1] 밖입니다 (min={cmin:.3g}, max={cmax:.3g})")
                if np.isnan(coh).any():
                    warnings.append("coherence 에 NaN 이 있습니다")

            # ── LOS 유한성 ──
            if los is not None and los.size:
                finite = float(np.isfinite(los).mean())
                info["los_finite_frac"] = finite
                if finite == 0.0:
                    errors.append("los_mm 가 전부 비유한(NaN/Inf)입니다")
                elif finite < 1.0:
                    warnings.append(f"los_mm 의 {(1 - finite) * 100:.1f}% 가 NaN/Inf 입니다")
                _w = _wrap_check(los, wavelength_mm=info.get("wavelength_mm"))
                info.update(_w)
                if _w.get("looks_wrapped"):
                    errors.append(
                        f"LOS 가 ±λ/4({_w['wrap_limit_mm']:.2f}mm) 안에 갇혀 균일하게 퍼져 "
                        f"있습니다 — **위상 언래핑이 안 된 산출물**로 보입니다"
                        f"(|LOS|max={_w['los_abs_max']:.2f}mm). 이 값으로 낸 변위·CRI 는 "
                        f"물리적 의미가 없습니다. 언래핑(snaphu 등) 후 재산출하세요.")

            # ── 고도(z) ──
            info["has_height"] = _first(f, _HEIGHT) is not None
            if not info["has_height"]:
                warnings.append("고도(height/hgt) 데이터셋이 없습니다 — xyz 의 z=0 (DEM 미연계)")

            # ── CRS / 좌표계 ──
            crs = None
            for k in _CRS_ATTRS:
                if k in f.attrs:
                    v = f.attrs[k]
                    crs = v.decode() if isinstance(v, bytes) else str(v)
                    break
            info["crs"] = crs
            if lonlat is not None and lonlat.ndim == 2 and lonlat.shape[1] == 2:
                x, y = lonlat[:, 0], lonlat[:, 1]
                geographic = bool(
                    np.all(np.abs(x) <= 180.0) and np.all(np.abs(y) <= 90.0)
                    and (np.ptp(x) < 10.0 and np.ptp(y) < 10.0)
                )
                info["looks_geographic"] = geographic
                if geographic and not _pyproj_available() and (crs is None or "4326" in str(crs)):
                    warnings.append(
                        "좌표가 경위도(degrees)로 보입니다 — CV 의 투영 geo_transform 으로 "
                        "정합하려면 pyproj 가 필요할 수 있습니다(`pip install pyproj`)."
                    )
            if crs is None:
                warnings.append("CRS 메타가 없습니다 — CV geo_transform 으로 정합(없으면 픽셀 identity 가정)")

            # ── 대상 교량을 실제로 담고 있는가(target 을 준 경우만) ──
            if lonlat is not None and lonlat.ndim == 2 and lonlat.shape[1] == 2:
                info.update(_extent(lonlat, geographic=bool(info.get("looks_geographic"))))
            if target is not None:
                info["target"] = (float(target[0]), float(target[1]))
                if lonlat is None or lonlat.ndim != 2 or lonlat.shape[1] != 2:
                    warnings.append("좌표 데이터셋이 없어 대상 교량 포함 여부를 볼 수 없습니다")
                elif not info.get("looks_geographic"):
                    warnings.append("점 좌표가 경위도가 아니어서(투영좌표) 대상 이격을 계산하지 "
                                    "못했습니다 — 지오코딩 후 다시 검사하세요")
                else:
                    sp = _spatial_report(lonlat, target, deck_radius_m)
                    info.update(sp)
                    if sp["n_within_deck"] == 0:
                        errors.append(
                            f"대상 좌표 반경 {deck_radius_m:.0f}m 안에 점이 **0개**입니다"
                            f"(가장 가까운 점 {sp['dist_min_m']:.0f}m, 이격 중앙값 "
                            f"{sp['dist_median_m']:.0f}m) — 이 교량의 트랙이 아닙니다.")
                    elif n_points and sp["n_within_deck"] / n_points < 0.01:
                        warnings.append(
                            f"교량 {deck_radius_m:.0f}m 내 {sp['n_within_deck']}/{n_points}점"
                            f"({sp['n_within_deck'] / n_points * 100:.2f}%) — 광역 필드입니다. "
                            f"데크 bbox 로 잘라 쓰지 않으면 하류가 교량 아닌 지반을 봅니다.")
    except OSError as exc:
        return TrackPreflight(path, errors=[f"HDF5 를 열 수 없습니다: {exc}"])

    return TrackPreflight(
        path=path,
        n_points=info.get("n_points"),
        n_dates=info.get("n_dates"),
        has_height=bool(info.get("has_height", False)),
        crs=info.get("crs"),
        looks_geographic=bool(info.get("looks_geographic", False)),
        coherence_min=info.get("coherence_min"),
        coherence_max=info.get("coherence_max"),
        los_finite_frac=info.get("los_finite_frac"),
        date_first=info.get("date_first"),
        date_last=info.get("date_last"),
        los_abs_max=info.get("los_abs_max"),
        looks_wrapped=bool(info.get("looks_wrapped", False)),
        target=info.get("target"),
        n_within_deck=info.get("n_within_deck"),
        n_within_near=info.get("n_within_near"),
        dist_median_m=info.get("dist_median_m"),
        dist_min_m=info.get("dist_min_m"),
        extent_km=info.get("extent_km"),
        errors=errors,
        warnings=warnings,
    )


def _pyproj_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("pyproj") is not None


def _wrap_check(los: np.ndarray, *, wavelength_mm: float | None = None) -> dict[str, Any]:
    """LOS 가 래핑 위상인가 — |LOS| 가 λ/4 를 한 번도 못 넘고 그 안에서 균일한가.

    두 조건을 **함께** 요구한다. 실제로 변위가 작은 교량도 |LOS|max 는 작을 수 있지만,
    그 경우 값이 0 근처에 몰려 바깥 절반(λ/8~λ/4) 비율이 낮다. 래핑이면 위상이 한 바퀴를
    균일하게 돌아 바깥 절반이 절반가량을 차지한다.
    """
    v = los[np.isfinite(los)]
    if v.size == 0:
        return {}
    limit = (float(wavelength_mm) / 4.0) if wavelength_mm else LOS_WRAP_LIMIT_MM
    a = np.abs(v)
    out: dict[str, Any] = {"los_abs_max": float(a.max()), "wrap_limit_mm": round(limit, 4)}
    if a.max() > limit * (1.0 + WRAP_LIMIT_TOL):
        out["looks_wrapped"] = False              # 한 점이라도 넘으면 래핑이 아니다
        return out
    outer = float((a > limit / 2.0).mean())
    out["looks_wrapped"] = bool(outer >= WRAP_OUTER_HALF_FRAC)
    return out


def _dist_m(lonlat: np.ndarray, lat: float, lon: float) -> np.ndarray:
    """점군 → (lat, lon) 평면근사 거리[m]. 교량 규모(수 km)에서 충분하다."""
    import math
    k = math.cos(math.radians(lat))
    dy = (lonlat[:, 1] - lat) * 111_320.0
    dx = (lonlat[:, 0] - lon) * 111_320.0 * k
    return np.hypot(dx, dy)


def _extent(lonlat: np.ndarray, *, geographic: bool) -> dict[str, Any]:
    """점군 공간범위[km] — '교량 트랙인가 광역 필드인가' 를 한 눈에 보이게 한다."""
    if not geographic or lonlat.size == 0:
        return {}
    import math
    lat0 = float(np.median(lonlat[:, 1]))
    w = float(np.ptp(lonlat[:, 0])) * 111.320 * math.cos(math.radians(lat0))
    h = float(np.ptp(lonlat[:, 1])) * 111.320
    return {"extent_km": (round(w, 2), round(h, 2))}


def _spatial_report(lonlat: np.ndarray, target: tuple[float, float],
                    deck_radius_m: float) -> dict[str, Any]:
    """대상 교량 반경 내 점수와 이격 분포."""
    d = _dist_m(lonlat, float(target[0]), float(target[1]))
    return {
        "n_within_deck": int((d <= deck_radius_m).sum()),
        "n_within_near": int((d <= DECK_NEAR_RADIUS_M).sum()),
        "dist_median_m": round(float(np.median(d)), 1),
        "dist_min_m": round(float(d.min()), 1),
    }
