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
    incidence: np.ndarray | None = None  # [N] float32 (deg) — LOS 입사각, 종축 분해용. 없으면 None
    heading: float | None = None         # 위성 heading(deg) — 기록용. 없으면 None
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


def _scalar_from_attrs(attrs, names: tuple[str, ...]) -> float | None:
    """attr 후보 중 처음으로 float 변환되는 스칼라(없으면 None). 대소문자 무시."""
    lower = {str(k).lower(): v for k, v in attrs.items()}
    for name in names:
        if name.lower() in lower:
            try:
                return float(lower[name.lower()])
            except (TypeError, ValueError):
                continue
    return None


# LOS 기하 데이터셋/attr 이름 후보(MintPy/ISCE/HyP3 관례).
_INC_DATASETS = ("incidenceAngle", "los_inc_angle", "inc_angle", "incidence", "incidenceMap")
_INC_ATTRS = ("incidence_angle", "incidenceAngle", "INCIDENCE_ANGLE", "CENTER_INCIDENCE_ANGLE",
              "inc_angle", "centerIncidenceAngle")
_HEAD_DATASETS = ("headingAngle", "heading", "sat_heading", "los_az_angle", "azimuthAngle")
_HEAD_ATTRS = ("heading", "HEADING", "headingAngle", "sat_heading", "ORBIT_HEADING")


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
        inc_raw = _read_optional_dataset(f, _INC_DATASETS)
        inc_attr = _scalar_from_attrs(f.attrs, _INC_ATTRS)
        head_raw = _read_optional_dataset(f, _HEAD_DATASETS)
        head_attr = _scalar_from_attrs(f.attrs, _HEAD_ATTRS)
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

    # 시간순 정렬 + 최이른 취득일 재기준. 스타네트워크 기준일이 중간 날짜이면 epochs 가
    # [기준, 슬레이브…] 순이라 비단조(예: [0,-636,…,+168])가 되는데, 하류 secular·관측기간
    # 계산은 dates 가 단조(오름차순, 최이른=0)라고 가정하므로 여기서 강제 정렬한다.
    order = np.argsort(dates, kind="stable")
    if not np.array_equal(order, np.arange(n_dates)):
        dates = dates[order]
        date_labels = date_labels[order]
        los = los[:, order]
    dates = dates - dates[0]          # 최이른 취득일 기준(0..)

    # 입사각: 데이터셋(점별 [N] 또는 스칼라) 우선, 없으면 attr 스칼라 → [N] 브로드캐스트.
    incidence: np.ndarray | None = None
    if inc_raw is not None:
        inc = np.ravel(np.asarray(inc_raw).astype(np.float32))
        if inc.size == 1:
            incidence = np.full(n_points, float(inc[0]), dtype=np.float32)
        elif inc.size == n_points:
            incidence = inc
        else:
            raise ValueError(f"incidence count {inc.size} != point count {n_points}")
    elif inc_attr is not None:
        incidence = np.full(n_points, float(inc_attr), dtype=np.float32)

    # heading: 데이터셋이면 중앙값 스칼라, 없으면 attr.
    heading: float | None = None
    if head_raw is not None:
        heading = float(np.nanmedian(np.asarray(head_raw, dtype=np.float64)))
    elif head_attr is not None:
        heading = float(head_attr)

    return TrackData(lonlat=lonlat, los=los, dates=dates, date_labels=date_labels,
                     coherence=coherence, height=height, incidence=incidence,
                     heading=heading, attrs=attrs)


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
    vertical: np.ndarray | None = None,
    deck_station: np.ndarray | None = None,
) -> InSAROutput:
    """표준 /insar 데이터셋 + InSAROutput 메타를 적재한다(출처 attr 은 호출 측에서).

    `vertical`([N,M], asc+desc 융합 연직 성분)을 주면 /insar/vertical 에 쓰고
    계약 필드 vertical_ds 를 채운다(단일 궤도면 None → 미적재).
    `deck_station`([N], 데크 호길이)을 주면 /insar/deck_station 에 쓴다 — 곡선 교량에서
    공간전파·구조축 정렬을 데크를 따라(직선 X정렬 대신) 하기 위함.
    """
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
    if deck_station is not None:
        store.write_array(f"{g}/deck_station", np.asarray(deck_station, np.float32))
    vertical_ds = None
    if vertical is not None:
        vertical_ds = store.write_array(f"{g}/vertical", vertical)

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
        vertical_ds=vertical_ds,
    )
    store.write_meta("insar", out)
    return out


def import_track_h5(
    store: ProjectStore,
    track_h5: str | Path,
    *,
    azimuth_angle_deg: float = 0.0,
    member_default: int = 0,
    geometry_latlon=None,
    apply_corrections: bool = False,
    ref_min_coherence: float = 0.9,
    dem_geotiff: str | Path | None = None,
    thermal_correction: bool = False,
    temperature_csv: str | None = None,
    fetch_temperature: bool = False,
) -> InSAROutput:
    """Track A/B/C/D export HDF5를 /insar 데이터셋으로 적재한다(CLI 단독 변환용).

    CV 결합 없이 좌표/부재를 단순 가정한다. CV 기하 정합이 필요하면 run_insar_real 사용.
    `geometry_latlon`([[lat,lon],...] 데크 중심선)을 주면 각 점을 폴리라인에 투영해
    **호길이 station**(곡선 교량 대응)으로 l_from_fixed·deck_station 을 채운다. 없으면
    점군 주곡선으로 station 을 추정한다(직선이면 X거리와 동등).

    z(고도): ① Track 점별 height → ② `dem_geotiff`(WGS84 lon/lat 로 샘플) → ③ 0.
    `apply_corrections=True` 면 LOS 시계열에 기준점 정합 + 고도상관 성층대기 보정을 적용하고
    (`atmo.correct_los_field`), 보정된 los/longitudinal + /insar/velocity_mm_yr 를 저장한다.
    """
    from .deck_geometry import deck_station as _deck_station
    td = read_track_h5(track_h5)
    n_points, _ = td.los.shape

    # z(고도): Track height 우선, 없으면 DEM GeoTIFF 샘플(lon/lat=WGS84), 그도 없으면 0.
    dem_meta = None
    z_source = "zero"
    if td.height is not None:
        z = td.height.astype(np.float64)
        z_source = "track_height"
    elif dem_geotiff is not None:
        from .dem import DemError, sample_dem
        try:
            ds = sample_dem(td.lonlat[:, :2], "EPSG:4326", str(dem_geotiff))
            z = ds.z.astype(np.float64)
            z_source, dem_meta = "dem_raster", ds.meta
        except DemError as exc:                  # DEM 실패 → z=0 폴백(변환 계속)
            z = np.zeros(n_points)
            dem_meta = {"ok": False, "reason": str(exc), "path": str(dem_geotiff)}
    else:
        z = np.zeros(n_points)
    los = td.los
    corr_meta = None
    if apply_corrections:
        from .atmo import correct_los_field, resolve_temperature
        temp = temp_meta = None
        if thermal_correction:
            ll = td.lonlat
            clat = clon = None
            if float(np.abs(ll[:, 0]).max()) <= 180 and float(np.abs(ll[:, 1]).max()) <= 90:
                clat, clon = float(np.median(ll[:, 1])), float(np.median(ll[:, 0]))
            tr = resolve_temperature(td.date_labels, lat=clat, lon=clon,
                                     csv_path=temperature_csv, fetch=bool(fetch_temperature))
            temp, temp_meta = tr["temperature"], {"source": tr["source"], **tr["meta"]}
        res = correct_los_field(los, coherence=td.coherence, height=z,
                                min_ref_coh=float(ref_min_coherence),
                                days=td.dates, temperature=temp)
        los = res["corrected"]
        corr_meta = res["meta"]
        if temp_meta is not None:
            corr_meta["temperature"] = temp_meta
    xyz = np.column_stack([td.lonlat[:, 0], td.lonlat[:, 1], z])
    longitudinal = los * np.cos(np.deg2rad(azimuth_angle_deg))
    # 곡선 교량: 호길이 station(데크를 따라 잰 거리). 폴리라인 있으면 투영, 없으면 주곡선.
    station = _deck_station(td.lonlat, geometry_latlon).astype(np.float32)
    l_from_fixed = station                                            # 고정단(=station 0)에서 호길이
    member = np.full(n_points, member_default, dtype=np.int8)

    out = write_insar_contract(
        store, xyz=xyz, member=member, coherence=td.coherence, l_from_fixed=l_from_fixed,
        los=los, longitudinal=longitudinal, dates=td.dates, date_labels=td.date_labels,
        deck_station=station,
    )
    from .atmo import temporal_decompose
    velocity = temporal_decompose(longitudinal, td.dates)["velocity_mm_yr"].astype(np.float32)
    store.write_array("/insar/velocity_mm_yr", velocity)
    store.write_json_attr(
        "insar",
        "track_source",
        {
            "path": str(Path(track_h5)),
            "attrs": td.attrs,
            "unit": "mm",
            "z_source": z_source,
            "dem": dem_meta,
            "mode": "import",
            "date_labels_ds": "/insar/date_labels",
            "velocity_ds": "/insar/velocity_mm_yr",
            "corrections": corr_meta,
        },
    )
    return out
