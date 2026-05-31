"""Track 결과 H5 투입 전 사전검증(preflight) — 실데이터 인제스트 게이트.

`run_insar_real`/`import_track_h5` 가 소비하는 Track export H5 를 인제스트 **전에**
점검한다. `read_track_h5` 는 문제가 있으면 읽다가 예외를 던지지만, 여기서는 절대
예외를 내지 않고 **구조적 리포트**(errors=차단 / warnings=비차단 / 요약 통계)를 돌려준다.
사용자가 실데이터를 넣기 전 "투입 가능한가"를 한눈에 판단하게 한다(CLI `--check-track`).

점검: 필수 데이터셋 존재·형상 일관(N/M)·취득일 파싱·coherence 범위·LOS 유한·고도/CRS·
좌표계(경위도 vs 투영). CV geo_transform 으로 정합하므로 CRS 누락은 경고(차단 아님).
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


def preflight_track_h5(path: str | Path) -> TrackPreflight:
    """Track export H5 를 점검해 TrackPreflight 리포트를 돌려준다(예외 없음)."""
    path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return TrackPreflight(path, errors=[f"파일이 없습니다: {path}"])

    info: dict[str, Any] = {}
    try:
        with h5py.File(path, "r") as f:
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
        errors=errors,
        warnings=warnings,
    )


def _pyproj_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("pyproj") is not None
