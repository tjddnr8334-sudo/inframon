"""Track 결과 HDF5 읽기 + /insar 계약 쓰기 (실데이터 경로 공용).

오프라인 SAR 처리(ISCE2/MintPy 등, WSL2/HyP3)가 만든 Track export HDF5:
  - pixel_lonlat 또는 ps_lonlat: [N,2]  (1차 증분에서는 CV 픽셀 좌표로 간주)
  - epochs: [M] YYYYMMDD 정수
  - los_mm: [N,M]
  - coh 또는 temp_coh: [N]
  - height/hgt/dem/elevation: [N] (선택) — 상류 지오코딩이 산출한 점별 고도(m).
    있으면 xyz 의 z 로 쓰고, 없으면 z=0 (DEM 미연계).
를 표준 형식으로 읽고(`read_track_h5`), /insar 계약 데이터셋으로 적재한다
(`write_insar_contract`). `import_track_h5`(CLI)와 `run_insar_real`(엔진)이 공유한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import h5py
import numpy as np

from ..contracts.io import ProjectStore
from ..contracts.schema import InSAROutput


@dataclass
class TrackData:
    """Track export HDF5 에서 읽은 표준화 데이터."""

    lonlat: np.ndarray       # [N,2] float64
    los: np.ndarray          # [N,M] float32 (mm)
    dates: np.ndarray        # [M] float64 (첫 취득일로부터 일수)
    date_labels: np.ndarray  # [M] S8 (YYYYMMDD)
    coherence: np.ndarray    # [N] float32
    height: np.ndarray | None = None  # [N] float32 (m) — 점별 고도, 없으면 None
    attrs: dict = field(default_factory=dict)


def _read_first_dataset(f: h5py.File, names: tuple[str, ...]) -> np.ndarray:
    for name in names:
        if name in f:
            return f[name][()]
    raise KeyError(f"None of the datasets exist: {', '.join(names)}")


def _read_optional_dataset(f: h5py.File, names: tuple[str, ...]) -> np.ndarray | None:
    """이름 후보 중 처음 존재하는 데이터셋(없으면 None)."""
    for name in names:
        if name in f:
            return f[name][()]
    return None


def _decode_epochs(raw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    labels = np.asarray(raw).astype(str)
    parsed = [datetime.strptime(label, "%Y%m%d") for label in labels]
    t0 = parsed[0]
    days = np.array([(d - t0).days for d in parsed], dtype=np.float64)
    return labels.astype("S8"), days


def read_track_h5(track_h5: str | Path) -> TrackData:
    """Track export HDF5 를 읽어 형상 검증 후 TrackData 로 반환한다."""
    track_h5 = Path(track_h5)
    if not track_h5.exists():
        raise FileNotFoundError(f"track result HDF5 not found: {track_h5}")

    with h5py.File(track_h5, "r") as f:
        lonlat = _read_first_dataset(f, ("pixel_lonlat", "ps_lonlat")).astype(np.float64)
        epochs_raw = f["epochs"][()]
        los = f["los_mm"][()].astype(np.float32)
        coherence = _read_first_dataset(f, ("coh", "temp_coh")).astype(np.float32)
        height_raw = _read_optional_dataset(f, ("height", "hgt", "dem", "elevation"))
        height = None if height_raw is None else np.asarray(height_raw).astype(np.float32)
        attrs = {str(k): str(v) for k, v in f.attrs.items()}

    if lonlat.ndim != 2 or lonlat.shape[1] != 2:
        raise ValueError(f"lon/lat dataset must have shape [N,2], got {lonlat.shape}")
    if los.ndim != 2:
        raise ValueError(f"los_mm must have shape [N,M], got {los.shape}")
    n_points, n_dates = los.shape
    if lonlat.shape[0] != n_points:
        raise ValueError(f"lon/lat point count {lonlat.shape[0]} != los point count {n_points}")
    if coherence.shape[0] != n_points:
        raise ValueError(f"coherence count {coherence.shape[0]} != point count {n_points}")
    if height is not None:
        height = np.ravel(height)
        if height.shape[0] != n_points:
            raise ValueError(f"height count {height.shape[0]} != point count {n_points}")

    date_labels, dates = _decode_epochs(epochs_raw)
    if len(dates) != n_dates:
        raise ValueError(f"epoch count {len(dates)} != los date count {n_dates}")

    return TrackData(lonlat=lonlat, los=los, dates=dates, date_labels=date_labels,
                     coherence=coherence, height=height, attrs=attrs)


def write_insar_contract(
    store: ProjectStore,
    *,
    xyz: np.ndarray,
    member: np.ndarray,
    coherence: np.ndarray,
    l_from_fixed: np.ndarray,
    los: np.ndarray,
    longitudinal: np.ndarray,
    dates: np.ndarray,
    date_labels: np.ndarray | None = None,
) -> InSAROutput:
    """표준 /insar 데이터셋 + InSAROutput 메타를 적재한다(출처 attr 은 호출 측에서)."""
    n_points, n_dates = los.shape
    g = "/insar"
    store.write_array(f"{g}/point_id", np.arange(n_points, dtype=np.int64))
    store.write_array(f"{g}/xyz", xyz)
    store.write_array(f"{g}/member", member)
    store.write_array(f"{g}/coherence", coherence)
    store.write_array(f"{g}/l_from_fixed", l_from_fixed)
    store.write_array(f"{g}/los", los)
    store.write_array(f"{g}/longitudinal", longitudinal)
    store.write_array(f"{g}/dates", dates)
    if date_labels is not None:
        store.write_array(f"{g}/date_labels", date_labels)
    store.write_array(f"{g}/temporal_coherence", coherence)

    out = InSAROutput(
        n_points=n_points,
        n_dates=n_dates,
        point_id_ds=f"{g}/point_id",
        xyz_ds=f"{g}/xyz",
        member_ds=f"{g}/member",
        coherence_ds=f"{g}/coherence",
        l_from_fixed_ds=f"{g}/l_from_fixed",
        los_ds=f"{g}/los",
        longitudinal_ds=f"{g}/longitudinal",
        dates_ds=f"{g}/dates",
        temporal_coherence_ds=f"{g}/temporal_coherence",
    )
    store.write_meta("insar", out)
    return out


def import_track_h5(
    store: ProjectStore,
    track_h5: str | Path,
    *,
    azimuth_angle_deg: float = 0.0,
    member_default: int = 0,
) -> InSAROutput:
    """Track A/B/C/D export HDF5를 /insar 데이터셋으로 적재한다(CLI 단독 변환용).

    CV 결합 없이 좌표/부재를 단순 가정한다. CV 기하 정합이 필요하면 run_insar_real 사용.
    """
    td = read_track_h5(track_h5)
    n_points, _ = td.los.shape

    z = td.height.astype(np.float64) if td.height is not None else np.zeros(n_points)
    xyz = np.column_stack([td.lonlat[:, 0], td.lonlat[:, 1], z])
    longitudinal = td.los * np.cos(np.deg2rad(azimuth_angle_deg))
    x_center = float(np.nanmedian(td.lonlat[:, 0]))
    l_from_fixed = np.abs(td.lonlat[:, 0] - x_center).astype(np.float32)
    member = np.full(n_points, member_default, dtype=np.int8)

    out = write_insar_contract(
        store, xyz=xyz, member=member, coherence=td.coherence, l_from_fixed=l_from_fixed,
        los=td.los, longitudinal=longitudinal, dates=td.dates, date_labels=td.date_labels,
    )
    store.write_json_attr(
        "insar",
        "track_source",
        {
            "path": str(Path(track_h5)),
            "attrs": td.attrs,
            "unit": "mm",
            "mode": "import",
            "date_labels_ds": "/insar/date_labels",
        },
    )
    return out
