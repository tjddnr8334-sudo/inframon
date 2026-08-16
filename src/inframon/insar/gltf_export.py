"""InSAR/CRI → glTF 2.0(.glb) 웹 트윈 내보내기 — Bmaps/Cesium 런타임 뷰.

설계(docs/digital_twin_integration.md)의 **시각화 층**. 의미·기록은 IFC(bim_export),
시간축 데이터는 VLM 패키지, 여기서는 **웹 렌더용 파생 뷰**만 만든다:

* `.glb`  — 점군 지오메트리(POSITION) + 정점색(COLOR_0=CRI/속도 컬러맵). POINTS 프리미티브.
* `.gltf.meta.json` — **GlobalId 결합 계약**(부재 외래키 슬롯)·georef 원점(ENU→글로브 배치)·
  값 범례·점별 원시값. 트윈 플랫폼이 라이브 데이터를 이 사이드카로 읽는다.

의존성 없음(struct/json 으로 glb 직접 기록). 좌표 투영은 pyproj 있으면 EPSG(기본 5186),
없으면 등거리 근사(로컬 미터)로 폴백. glTF 은 우수(Y-up, 미터) 로컬 프레임 — 글로브 배치는
사이드카 origin 으로 뷰어(Cesium eastNorthUpToFixedFrame)가 수행한다.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

from .bim_export import _load_points, map_cri_to_points

# glTF 상수
_FLOAT, _UBYTE = 5126, 5121
_ARRAY_BUFFER = 34962
_POINTS = 0


def _ramp(t: np.ndarray, stops: list[tuple[float, tuple[int, int, int]]]) -> np.ndarray:
    """piecewise-linear 컬러맵. t∈[0,1] → uint8 RGB (N,3). 의존성 없음."""
    t = np.clip(t, 0.0, 1.0)
    xs = np.array([s[0] for s in stops])
    cs = np.array([s[1] for s in stops], float)
    out = np.empty((t.size, 3))
    for k in range(3):
        out[:, k] = np.interp(t, xs, cs[:, k])
    return out.round().astype(np.uint8)


# CRI: 정상(teal)→주의(amber)→경고(orange)→위험(red) — 그림들과 일관
_CRI_STOPS = [(0.0, (42, 157, 143)), (0.5, (233, 196, 106)),
              (0.75, (244, 162, 97)), (1.0, (193, 18, 31))]
# 속도(diverging): 침하(파랑)–0(흰)–융기(빨강)
_DIV_STOPS = [(0.0, (33, 78, 137)), (0.5, (245, 245, 245)), (1.0, (193, 18, 31))]


def _colors_and_legend(values: np.ndarray, kind: str) -> tuple[np.ndarray, dict]:
    v = np.asarray(values, float)
    fin = v[np.isfinite(v)]
    if kind == "cri":                                   # [0,1] 절대
        vmin, vmax = 0.0, 1.0
        t = (v - vmin) / (vmax - vmin)
        rgb = _ramp(t, _CRI_STOPS)
    else:                                               # diverging 0 중심 대칭
        m = float(np.nanpercentile(np.abs(fin), 95)) if fin.size else 1.0
        m = m or 1.0
        vmin, vmax = -m, m
        t = (v - vmin) / (vmax - vmin)
        rgb = _ramp(t, _DIV_STOPS)
    rgb = rgb.copy()
    rgb[~np.isfinite(v)] = (136, 136, 136)              # NaN=회색
    legend = {"kind": kind, "vmin": vmin, "vmax": vmax,
              "units": "CRI(무차원)" if kind == "cri" else "mm/yr"}
    return rgb, legend


def _to_local_meters(lonlat: np.ndarray, ifc_crs: str) -> tuple[np.ndarray, dict]:
    """lon/lat → 로컬 ENU 미터(centroid 원점). pyproj 있으면 EPSG 투영, 없으면 등거리 근사."""
    lon0, lat0 = float(np.median(lonlat[:, 0])), float(np.median(lonlat[:, 1]))
    try:
        from pyproj import Transformer
        tr = Transformer.from_crs("EPSG:4326", ifc_crs, always_xy=True)
        X, Y = tr.transform(lonlat[:, 0], lonlat[:, 1])
        x0, y0 = tr.transform(lon0, lat0)
        east = np.asarray(X) - x0
        north = np.asarray(Y) - y0
        proj = ifc_crs
    except Exception:                                   # pyproj 없음 → 등거리 근사
        east = (lonlat[:, 0] - lon0) * np.cos(np.radians(lat0)) * 111320.0
        north = (lonlat[:, 1] - lat0) * 111320.0
        proj = "equirectangular_approx"
    georef = {"origin_lon": lon0, "origin_lat": lat0, "origin_height_m": 0.0,
              "projection": proj, "frame": "ENU_meters",
              "note": "뷰어가 origin 을 eastNorthUpToFixedFrame 로 글로브에 배치"}
    return np.column_stack([east, north]), georef


def _write_glb(positions: np.ndarray, colors_rgb: np.ndarray, out_path: Path) -> None:
    """POSITION(vec3 float) + COLOR_0(vec4 ubyte normalized) POINTS glb 직접 기록."""
    n = positions.shape[0]
    pos = positions.astype("<f4")
    rgba = np.empty((n, 4), np.uint8)
    rgba[:, :3] = colors_rgb
    rgba[:, 3] = 255
    pos_bytes = pos.tobytes()
    col_bytes = rgba.tobytes()
    bin_blob = pos_bytes + col_bytes                    # 둘 다 4-정렬(12n, 4n)
    gltf = {
        "asset": {"version": "2.0", "generator": "inframon.gltf_export"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "insar_points"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 1},
                                    "mode": _POINTS}]}],
        "buffers": [{"byteLength": len(bin_blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes), "target": _ARRAY_BUFFER},
            {"buffer": 0, "byteOffset": len(pos_bytes), "byteLength": len(col_bytes),
             "target": _ARRAY_BUFFER},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": _FLOAT, "count": n, "type": "VEC3",
             "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
            {"bufferView": 1, "componentType": _UBYTE, "normalized": True, "count": n,
             "type": "VEC4"},
        ],
    }
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    bin_blob += b"\x00" * ((4 - len(bin_blob) % 4) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_blob)
    with open(out_path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))          # 'glTF', ver2, len
        f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))    # JSON chunk
        f.write(json_bytes)
        f.write(struct.pack("<II", len(bin_blob), 0x004E4942))      # BIN chunk
        f.write(bin_blob)


def export_insar_gltf(h5: str | Path, out_path: str | Path, *, value: str = "velocity",
                      fram_project: str | Path | None = None, ifc_crs: str = "EPSG:5186",
                      z_exaggerate: float = 0.0, element_map: dict | None = None) -> dict:
    """InSAR/PSI H5 → 웹 트윈용 .glb + .meta.json.

    value: 'velocity'(LOS 속도)·'cri'(FRAM CRI 최근접, fram_project 필요)·'cumulative'(누적 LOS).
    z_exaggerate>0 이면 값(또는 누적변위)을 Z로 과장해 3D 기복 표현. element_map: point_id→GlobalId
    (IFC 부재 결합; 없으면 슬롯 null 로 계약만 노출).
    """
    out_path = Path(out_path)
    if out_path.suffix.lower() != ".glb":
        out_path = out_path.with_suffix(".glb")
    P = _load_points(h5)
    lonlat = P["lonlat"]
    n = len(lonlat)
    # 값 선택
    if value == "cri":
        if fram_project is None:
            raise ValueError("value='cri' 는 fram_project(project.h5, /fram/CRI) 가 필요합니다.")
        vals = map_cri_to_points(lonlat, fram_project, reduce="max")
        kind = "cri"
    elif value == "cumulative":
        ts = P["ts"]
        vals = (np.asarray(ts)[:, -1] - np.asarray(ts)[:, 0]) if ts is not None else np.full(n, np.nan)
        kind = "div"
    else:                                               # velocity
        vals = P["vel"]
        if vals is None:                                # 없으면 ts 로버스트 기울기 근사(단순)
            ts = np.asarray(P["ts"], float)
            vals = (ts[:, -1] - ts[:, 0]) if ts is not None else np.full(n, np.nan)
        vals = np.asarray(vals, float)
        kind = "div"
    colors, legend = _colors_and_legend(vals, kind)
    east_north, georef = _to_local_meters(lonlat, ifc_crs)
    # glTF: Y-up. ENU(east,north,up) → glTF(x=east, y=up, z=-north)
    z = np.zeros(n)
    if z_exaggerate:
        base = np.nan_to_num(vals, nan=0.0)
        z = base * z_exaggerate / 1000.0                # mm→m×배율
    positions = np.column_stack([east_north[:, 0], z, -east_north[:, 1]]).astype(float)
    _write_glb(positions, colors, out_path)
    # 사이드카: GlobalId 결합 계약 + georef + 점별 원시값
    pid = P["attrs"].get("point_id")
    ids = (np.asarray(P.get("point_id", None)) if P.get("point_id") is not None
           else np.arange(n))
    emap = element_map or {}
    features = []
    for i in range(n):
        vi = float(vals[i]) if np.isfinite(vals[i]) else None
        features.append({"index": i, "point_id": int(ids[i]) if i < len(ids) else i,
                         "element_globalid": emap.get(int(ids[i]) if i < len(ids) else i),
                         "value": vi, "lon": float(lonlat[i, 0]), "lat": float(lonlat[i, 1])})
    meta = {"schema": "inframon.gltf.meta/1.0", "glb": out_path.name, "n_points": n,
            "value_channel": value, "legend": legend, "georef": georef,
            "binding": {"key": "element_globalid",
                        "note": "IFC 4.3 부재 GlobalId 외래키. --bim-align 이 채우면 시계열↔부재 영구결합"},
            "features": features}
    meta_path = out_path.with_suffix(".glb.meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"glb": str(out_path), "meta": str(meta_path), "n_points": n,
            "value_channel": value, "legend": legend, "georef": georef,
            "bound": sum(1 for f in features if f["element_globalid"] is not None)}
