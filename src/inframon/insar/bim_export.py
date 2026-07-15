"""InSAR → BIM/IFC 오버레이 내보내기 — 지오레퍼런스 점군(값+색)을 GeoJSON·CSV 로.

BIM/IFC 위에 교량 변위를 색으로 올리기 위한 **1단계 산출물**(방식 A: 외부 지오레퍼런스
오버레이). IFC 지오레퍼런싱(IfcMapConversion)이 준비되면 여기 X_proj 를 IFC 로컬좌표로
역변환하거나, IfcOpenShell 로 부재별 Pset 주입(방식 B)으로 확장한다.

각 점에 **여러 값(LOS 속도·연직 속도·누적 변위·CRI)과 값별 색(hex)** 을 모두 담아,
뷰어/UI 가 어느 값으로 색칠할지 토글할 수 있게 한다. 좌표는 WGS84(웹지도용)+IFC CRS
투영(기본 EPSG:5186 한국 중부원점)을 함께 저장.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

# 값 종류별 컬러맵·범례 성격
_VALUE_SPECS = {
    "los_velocity_mm_yr": ("RdBu", "diverging", "LOS 속도(mm/yr)"),
    "vertical_velocity_mm_yr": ("RdBu", "diverging", "연직 속도(mm/yr)"),
    "cumulative_mm": ("RdBu", "diverging", "누적 변위(mm)"),
    "cri": ("RdYlGn_r", "unit", "FRAM CRI(위험도)"),
}


def _hex_colors(values: np.ndarray, cmap_name: str, kind: str) -> tuple[list, float, float]:
    """값 배열 → hex 색 리스트 + (vmin,vmax). diverging 은 0 중심 대칭, unit 은 [0,1]."""
    import matplotlib as mpl
    import matplotlib.colors as mcolors

    v = np.asarray(values, dtype=float)
    finite = v[np.isfinite(v)]
    if kind == "unit":
        vmin, vmax = 0.0, 1.0
    elif finite.size:
        m = float(np.nanpercentile(np.abs(finite), 95)) or 1.0    # 로버스트 대칭 범위
        vmin, vmax = -m, m
    else:
        vmin, vmax = -1.0, 1.0
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    cmap = mpl.colormaps[cmap_name]
    cols = [mcolors.to_hex(cmap(norm(x))) if np.isfinite(x) else "#888888" for x in v]
    return cols, vmin, vmax


def _load_points(h5) -> dict:
    """PSI 비교 H5 또는 Track/insar H5 에서 점군·값을 유연하게 읽는다."""
    import h5py

    with h5py.File(str(h5), "r") as f:
        g = f["insar"] if "insar" in f else f
        d = {k: g[k][()] for k in g}
        attrs = {k: g.attrs[k] for k in g.attrs}
    lonlat = d.get("pixel_lonlat")
    if lonlat is None and "xyz" in d:
        lonlat = d["xyz"][:, :2]
    ts = d.get("ts_sbas_mm", d.get("los_mm", d.get("los")))
    vel = d.get("velocity_mm_yr", d.get("los_velocity_mm_yr"))
    coh = d.get("sbas_coherence", d.get("coh", d.get("coherence")))
    cls = d.get("qps_class", d.get("scatterer_class"))
    inc = d.get("incidenceAngle")
    epochs = d.get("epochs", d.get("date_labels"))
    return {"lonlat": np.asarray(lonlat, float), "ts": ts, "vel": vel, "coh": coh,
            "cls": cls, "inc": inc, "epochs": epochs, "attrs": attrs}


def export_insar_for_bim(h5, out_prefix, *, ifc_crs: str = "EPSG:5186",
                         cri=None, incidence_deg: float | None = None) -> dict:
    """InSAR H5 → BIM 오버레이 GeoJSON+CSV. 반환: 요약(경로·값범위·범례).

    · WGS84 lon/lat + IFC CRS(기본 EPSG:5186) 투영좌표 동시 저장.
    · 값: LOS 속도·연직 속도(입사각 있으면)·누적 변위·CRI(주면). 값마다 색(hex) 사전계산.
    · cri: 점별 CRI 배열(FRAM 매핑) 또는 None. incidence_deg: 입사각 없을 때 대표값.
    """
    p = _load_points(h5)
    lon = p["lonlat"][:, 0]; lat = p["lonlat"][:, 1]
    n = len(lon)

    # 값 계산
    vel = np.asarray(p["vel"], float) if p["vel"] is not None else np.full(n, np.nan)
    ts = np.asarray(p["ts"], float) if p["ts"] is not None else None
    cumulative = (ts[:, -1] - ts[:, 0]) if ts is not None and ts.ndim == 2 else np.full(n, np.nan)
    inc = p["inc"]
    inc_arr = (np.asarray(inc, float) if inc is not None
               else (np.full(n, incidence_deg) if incidence_deg else None))
    vertical = (vel / np.cos(np.radians(inc_arr)) if inc_arr is not None else np.full(n, np.nan))
    cri_arr = (np.asarray(cri, float) if cri is not None else np.full(n, np.nan))

    values = {"los_velocity_mm_yr": vel, "vertical_velocity_mm_yr": vertical,
              "cumulative_mm": cumulative, "cri": cri_arr}

    # 투영(WGS84 → IFC CRS)
    try:
        from pyproj import Transformer
        tr = Transformer.from_crs("EPSG:4326", ifc_crs, always_xy=True)
        xp, yp = tr.transform(lon, lat)
    except Exception:  # noqa: BLE001 — pyproj 없거나 CRS 오류면 투영 생략
        xp = yp = np.full(n, np.nan); ifc_crs = None

    # 값별 색·범례
    colors = {}; legend = {}
    for key, (cmap, kind, label) in _VALUE_SPECS.items():
        if np.isfinite(values[key]).any():
            cols, vmin, vmax = _hex_colors(values[key], cmap, kind)
            colors[key] = cols
            legend[key] = {"label": label, "cmap": cmap, "vmin": round(vmin, 3),
                           "vmax": round(vmax, 3)}

    cls = p["cls"]
    cls_name = None
    if cls is not None:
        cmap_cls = {2: "PS", 1: "DS", 0: "제외"}
        cls_name = [cmap_cls.get(int(c), str(int(c))) for c in np.asarray(cls)]
    coh = np.asarray(p["coh"], float) if p["coh"] is not None else np.full(n, np.nan)

    # GeoJSON (WGS84 좌표, 속성에 투영좌표·값·색)
    feats = []
    for i in range(n):
        pr = {"id": i, "coherence": round(float(coh[i]), 3) if np.isfinite(coh[i]) else None}
        if ifc_crs:
            pr[f"x_{ifc_crs.split(':')[-1]}"] = round(float(xp[i]), 3)
            pr[f"y_{ifc_crs.split(':')[-1]}"] = round(float(yp[i]), 3)
        if cls_name:
            pr["class"] = cls_name[i]
        for key in values:
            if np.isfinite(values[key][i]):
                pr[key] = round(float(values[key][i]), 3)
            if key in colors:
                pr[f"color_{key}"] = colors[key][i]
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [float(lon[i]), float(lat[i])]},
                      "properties": pr})
    gj = {"type": "FeatureCollection",
          "metadata": {"n_points": n, "ifc_crs": ifc_crs, "wgs84": "EPSG:4326",
                       "legend": legend, "note": "값 색은 사전계산(color_*), BIM/UI 는 원하는 값 토글",
                       "los_note": "변위는 LOS(위성 시선) 기준 — 연직은 입사각 투영값"},
          "features": feats}

    out_prefix = Path(out_prefix); out_prefix.parent.mkdir(parents=True, exist_ok=True)
    gj_path = out_prefix.with_suffix(".geojson")
    gj_path.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")

    csv_path = out_prefix.with_suffix(".csv")
    cols = (["id", "lon", "lat"] + ([f"x_{ifc_crs.split(':')[-1]}", f"y_{ifc_crs.split(':')[-1]}"] if ifc_crs else [])
            + ["class", "coherence"] + list(values)
            + [f"color_{k}" for k in colors])
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow(cols)
        for i in range(n):
            row = [i, round(float(lon[i]), 7), round(float(lat[i]), 7)]
            if ifc_crs:
                row += [round(float(xp[i]), 3), round(float(yp[i]), 3)]
            row += [cls_name[i] if cls_name else "",
                    round(float(coh[i]), 3) if np.isfinite(coh[i]) else ""]
            row += [round(float(values[k][i]), 3) if np.isfinite(values[k][i]) else "" for k in values]
            row += [colors[k][i] for k in colors]
            w.writerow(row)

    return {"n_points": n, "ifc_crs": ifc_crs, "legend": legend,
            "values": [k for k in values if np.isfinite(values[k]).any()],
            "geojson": str(gj_path), "csv": str(csv_path)}
