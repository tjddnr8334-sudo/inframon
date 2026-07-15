"""PSI 방법론 적용 파이프라인 — 간섭도/진폭 → PS·SBAS(TS)·QPS 데크 트랙 H5.

psi_methods(순수 numpy 방법론)를 실 간섭도·진폭 tif 에 적용해 **세 방법론 비교** H5 를
만든다. 대시보드 '방법론 비교' 탭이 이 H5 를 읽는다.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import numpy as np

from . import psi_methods as psi
from .snap_backend import WAVELENGTH_M, _polyline_dist_km, adi_at_points

_SCALE = -WAVELENGTH_M / (4.0 * np.pi) * 1000.0    # phase[rad] → LOS[mm]


def _deck_pixels(tif, geom, buffer_m):
    import rasterio
    with rasterio.open(tif) as ds:
        ph = ds.read(1); H, W = ph.shape
        rr, cc = np.mgrid[0:H, 0:W]
        xs, ys = rasterio.transform.xy(ds.transform, rr.ravel(), cc.ravel())
        glon = np.array(xs).reshape(H, W); glat = np.array(ys).reshape(H, W)
    mlon = min(g[0] for g in geom); Mlon = max(g[0] for g in geom)
    mlat = min(g[1] for g in geom); Mlat = max(g[1] for g in geom); mrg = 0.001
    near = ((glon >= mlon - mrg) & (glon <= Mlon + mrg) &
            (glat >= mlat - mrg) & (glat <= Mlat + mrg))
    d = np.full((H, W), np.inf)
    d.ravel()[near.ravel()] = _polyline_dist_km(glon.ravel()[near.ravel()],
                                                glat.ravel()[near.ravel()], geom)
    sel = (d <= buffer_m / 1000.0) & np.isfinite(ph) & (ph != 0)
    idx = np.where(sel.ravel())[0]
    return idx, glon.ravel()[idx], glat.ravel()[idx]


def build_psi_comparison_h5(sbas_tifs: list, amp_tifs: list, geometry_latlon: list,
                            out_h5, *, buffer_m: float = 30.0, ps_adi_max: float = 0.25,
                            ds_coh_min: float = 0.7, ref_epoch: int = 0,
                            heading: float | None = None) -> dict:
    """소baseline 간섭도(sbas_tifs: tc_REF_SEC.tif)+진폭(amp_tifs)으로 PS·SBAS(TS)·QPS
    데크 적용 → 비교 H5 저장. 반환: 방법론별 요약.

    · SBAS→TS: 네트워크 최소제곱 역산(누적 변위 시계열)·위상 결맞음.
    · PS: 진폭 ADI<ps_adi_max.  · QPS: PS ∪ DS(SBAS γ≥ds_coh_min).
    """
    import h5py
    import rasterio

    geom = [(float(lon), float(lat)) for lat, lon in geometry_latlon]
    sbas_tifs = [str(t) for t in sbas_tifs if Path(t).exists()]
    if not sbas_tifs:
        raise ValueError("SBAS 간섭도 tif 가 없습니다.")
    dates = sorted({d for t in sbas_tifs
                    for d in re.search(r"tc_(\d{8})_(\d{8})", t).groups()})
    didx = {d: i for i, d in enumerate(dates)}
    idx, plon, plat = _deck_pixels(sbas_tifs[0], geom, buffer_m)
    if idx.size == 0:
        raise ValueError(f"데크 {buffer_m:.0f}m 이내 픽셀이 없습니다.")

    pairs, pdisp = [], []
    for t in sbas_tifs:
        a, b = re.search(r"tc_(\d{8})_(\d{8})", t).groups()
        with rasterio.open(t) as ds:
            ph = ds.read(1).ravel()[idx]
        pairs.append((didx[a], didx[b])); pdisp.append(ph * _SCALE)
    pdisp = np.asarray(pdisp).T                           # [N, n_pairs]

    red = psi.network_redundancy(pairs, len(dates))
    ts = psi.sbas_invert(pairs, pdisp, len(dates), ref_epoch=ref_epoch)   # [N, M]
    G = psi.sbas_design_matrix(pairs, len(dates))
    resid_phase = (pdisp - (G @ ts.T).T) / _SCALE
    sbas_coh = psi.temporal_coherence(resid_phase)

    adi = adi_at_points([str(a) for a in amp_tifs], plon, plat) if amp_tifs \
        else np.full(idx.size, np.nan)
    adi = np.where(np.isfinite(adi), adi, 9.99)
    ps_mask = psi.ps_selection(adi, adi_max=ps_adi_max)
    qps = psi.qps_classification(adi, sbas_coh, adi_max=ps_adi_max, ds_coh_min=ds_coh_min)

    days = np.array([(datetime.strptime(d, "%Y%m%d") -
                      datetime.strptime(dates[0], "%Y%m%d")).days for d in dates], float)
    A = np.vstack([days / 365.25, np.ones_like(days)]).T
    vel = np.linalg.lstsq(A, ts.T, rcond=None)[0][0]

    out_h5 = Path(out_h5); out_h5.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(out_h5, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack([plon, plat]))
        f.create_dataset("epochs", data=np.array([int(d) for d in dates], np.int32))
        f.create_dataset("ts_sbas_mm", data=ts.astype(np.float32))
        f.create_dataset("velocity_mm_yr", data=vel.astype(np.float32))
        f.create_dataset("adi", data=adi.astype(np.float32))
        f.create_dataset("sbas_coherence", data=sbas_coh.astype(np.float32))
        f.create_dataset("ps_mask", data=ps_mask.astype(np.int8))
        f.create_dataset("qps_class", data=qps.astype(np.int8))   # 2=PS,1=DS,0=제외
        f.attrs["n_points"] = int(idx.size)
        f.attrs["n_epochs"] = len(dates)
        f.attrs["n_ps"] = int(ps_mask.sum())
        f.attrs["n_ds"] = int((qps == 1).sum())
        f.attrs["n_qps"] = int((qps > 0).sum())
        f.attrs["network_rank"] = red["rank"]
        f.attrs["network_connected"] = red["connected"]
        f.attrs["min_pairs_per_epoch"] = red["min_pairs_per_epoch"]
        f.attrs["ps_adi_max"] = ps_adi_max
        f.attrs["ds_coh_min"] = ds_coh_min
        if heading is not None:
            f.attrs["HEADING"] = float(heading)
        f.attrs["source"] = "PSI 방법론 비교 (PS·SBAS/DS·QPS)"
    return {"n_points": int(idx.size), "n_epochs": len(dates), "n_pairs": len(pairs),
            "n_ps": int(ps_mask.sum()), "n_ds": int((qps == 1).sum()),
            "n_qps": int((qps > 0).sum()), "network": red,
            "velocity_median_mm_yr": float(np.median(vel)),
            "sbas_coh_mean": float(sbas_coh.mean()), "out": str(out_h5)}
