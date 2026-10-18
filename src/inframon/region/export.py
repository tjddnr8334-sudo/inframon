"""광역 감시 교량 하나 → inframon 단일교량 ``project.h5`` (PINN·FRAM·잔존수명 입력).

엔진의 품질필터 통과 StaMPS 점(npz: lon, lat, dates, disp_mm[N,M] LOS mm, coh)을 교량 축 주변으로 자르고,
표준 Track H5(``scripts/wsl_sarvey/56_stamps_to_inframon.py`` 와 같은 레이아웃)로 쓴 뒤
``insar.track_reader.import_track_h5`` 로 /insar 계약에 적재한다 — 새 계약을 만들지 않는다.

주의: npz 의 변위는 엔진이 이미 **품질필터(기선·기준점 결맞음)와 기준점 재참조**를 적용한 LOS 다.
수직 환산은 계약 규칙대로 소비 측(``life.geometry.los_to_vertical``)에 맡긴다(단일궤도 LOS/cosθ 를
``vertical_ds`` 에 넣지 않는다).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _axis_distance_m(lon, lat, geometry) -> np.ndarray:
    """점들 ↔ 교량 축(시점–종점 선분) 수평거리[m]."""
    (la0, lo0), (la1, lo1) = geometry[0], geometry[-1]
    ke = 111320.0 * np.cos(np.radians((la0 + la1) / 2))
    p = np.c_[(np.asarray(lon) - lo0) * ke, (np.asarray(lat) - la0) * 111320.0]
    d = np.array([(lo1 - lo0) * ke, (la1 - la0) * 111320.0])
    L2 = float(d @ d)
    t = np.clip((p @ d) / L2, 0, 1) if L2 > 0 else np.zeros(len(p))
    return np.hypot(*(p - t[:, None] * d[None, :]).T)


def npz_to_track_h5(npz_path, out_h5, geometry, *, buffer_m: float = 60.0, coh_min: float = 0.0,
                    incidence_deg: float | None = None) -> int:
    """엔진 npz → Track H5. 교량 축에서 ``buffer_m`` 이내 점만. 반환: 점 개수(0이면 파일을 쓰지 않음)."""
    import h5py
    e = np.load(npz_path, allow_pickle=True)
    lon, lat = np.asarray(e["lon"], float), np.asarray(e["lat"], float)
    los = np.asarray(e["disp_mm"], float); coh = np.asarray(e["coh"], float)
    m = (_axis_distance_m(lon, lat, geometry) <= buffer_m) & np.isfinite(los).all(1) & (coh >= coh_min)
    if not m.any():
        return 0
    epochs = np.array([int(str(d)) for d in e["dates"]], dtype=np.int64)
    o = np.argsort(epochs)
    Path(out_h5).parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(out_h5, "w") as f:
        f["pixel_lonlat"] = np.c_[lon[m], lat[m]]
        f["epochs"] = epochs[o]
        f["los_mm"] = los[m][:, o].astype(np.float32)
        f["coh"] = coh[m].astype(np.float32)
        if incidence_deg is not None:
            f["incidenceAngle"] = np.full(int(m.sum()), float(incidence_deg), np.float32)
        f.attrs["unit"] = "mm"
        f.attrs["FILE_TYPE"] = "bim_stamps_qc"
        f.attrs["SOURCE"] = str(npz_path)
    return int(m.sum())


def export_bridge(row: dict, npz_path, out_dir, *, buffer_m: float = 60.0) -> dict:
    """``state.json`` 의 교량 행(geo·r.inc) + 엔진 npz → ``<out_dir>/project.h5``. 결과 요약을 돌려준다."""
    geom = row.get("geo") or []
    if geom and isinstance(geom[0][0], (list, tuple)):      # 엔진 형식: [[[lon, lat], [lon, lat]]]
        geometry = [(p[1], p[0]) for p in geom[0]]
    else:
        geometry = [(row["lat"], row["lon"]), (row["lat"], row["lon"])]
    out_dir = Path(out_dir); track = out_dir / "track_bim.h5"
    inc = (row.get("r") or {}).get("inc")
    n = npz_to_track_h5(npz_path, track, geometry, buffer_m=buffer_m, incidence_deg=inc)
    if n == 0:
        return dict(ok=False, reason=f"교량 축 {buffer_m:.0f} m 이내 측정점 없음", project=None)
    from ..contracts.io import ProjectStore
    from ..insar.track_reader import import_track_h5
    proj = out_dir / "project.h5"
    with ProjectStore(str(proj), mode="a") as store:
        ins = import_track_h5(store, track, geometry_latlon=[list(g) for g in geometry])
    return dict(ok=True, project=str(proj), track_h5=str(track), n_points=ins.n_points, n_dates=ins.n_dates)
