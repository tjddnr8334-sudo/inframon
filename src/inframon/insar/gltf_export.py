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


def _srtm_tile(lon: float, lat: float) -> str:
    ns = f"N{int(np.floor(lat)):02d}" if lat >= 0 else f"S{int(-np.floor(lat)):02d}"
    ew = f"E{int(np.floor(lon)):03d}" if lon >= 0 else f"W{int(-np.floor(lon)):03d}"
    return f"{ns}{ew}.SRTMGL1.hgt.zip"


def _sample_dem(lonlat: np.ndarray, dem: str | Path) -> np.ndarray:
    """각 점 lon/lat 의 표고(m). dem 은 단일 래스터(tif/hgt/vrt) 또는 SRTM 타일 디렉터리."""
    import rasterio
    n = len(lonlat)
    z = np.full(n, np.nan)
    dem = Path(dem)
    if dem.is_dir():                                    # SRTM 타일 디렉터리 — 타일별 묶어 샘플
        import zipfile
        tiles: dict[str, list[int]] = {}
        for i, (lo, la) in enumerate(lonlat):
            tiles.setdefault(_srtm_tile(lo, la), []).append(i)
        for tile, idx in tiles.items():
            zp = dem / tile
            if not zp.exists():
                continue
            inner = next(x for x in zipfile.ZipFile(zp).namelist() if x.lower().endswith(".hgt"))
            with rasterio.open(f"/vsizip/{zp}/{inner}") as ds:
                for i, v in zip(idx, ds.sample([(lonlat[i, 0], lonlat[i, 1]) for i in idx])):
                    z[i] = float(v[0])
    else:                                               # 단일 래스터
        with rasterio.open(str(dem)) as ds:
            for i, v in enumerate(ds.sample([(lo, la) for lo, la in lonlat])):
                z[i] = float(v[0])
    z[z <= -1000] = np.nan                              # SRTM void
    return z


def guid_map_from_alignment(project_h5: str | Path, elements, *, map_conversion=None,
                            control_points=None, ifc_crs: str = "EPSG:5186",
                            source_crs: str | None = None,
                            max_dist_m: float = 5.0) -> tuple[np.ndarray, dict]:
    """IFC 4.3 부재 정합 → 점별 GlobalId 배열(인덱스 정렬) + 요약.

    기존 bim.align 을 재사용한다: project.h5 를 IFC 로컬로 정합(map_conversion 또는 control_points)
    후 각 점을 최근접 부재(AABB)에 연결. elements 는 .ifc(ifcopenshell) 또는 부재 테이블(JSON/CSV).
    반환 guid[N] 의 미연결 점은 "" — export_insar_gltf(element_guids=...) 로 그대로 넘긴다.
    """
    from ..bim.align import align_project_to_bim
    r = align_project_to_bim(str(project_h5), elements, map_conversion=map_conversion,
                             control_points=control_points, target_crs=ifc_crs,
                             source_crs=source_crs, max_dist_m=max_dist_m)
    guids = np.asarray(r["point_guid"], dtype=object)
    # 점별 부재 상단 Z(데크 레벨) — IFC 로컬 orthogonal height. 결합점을 교량 위에 얹는 데 쓴다.
    from ..bim.elements import load_elements
    els = load_elements(elements) if isinstance(elements, (str, Path)) else list(elements)
    top_z = {e.guid: float(e.bbox_max[2]) for e in els}
    element_z = np.array([top_z.get(g, np.nan) for g in guids], float)
    summary = {"associated": int(sum(1 for g in guids if g)),
               "n_points": int(guids.size), "association": r.get("association"),
               "element_z": element_z, "warnings": r.get("warnings", [])}
    return guids, summary


def _enu_gltf_to_ecef(lon: float, lat: float, h: float) -> list[float]:
    """glTF 로컬(x=east,y=up,z=-north) → ECEF 4x4(column-major, 3D Tiles/Cesium 규약).

    Cesium eastNorthUpToFixedFrame 과 동일 원점에, glTF Y-up 축 배치를 반영한 열들:
    col0=east, col1=up, col2=-north, col3=원점 ECEF.
    """
    import math
    a, f = 6378137.0, 1.0 / 298.257223563
    e2 = f * (2 - f)
    lam, phi = math.radians(lon), math.radians(lat)
    sl, cl, sp, cp = math.sin(lam), math.cos(lam), math.sin(phi), math.cos(phi)
    N = a / math.sqrt(1 - e2 * sp * sp)
    X = (N + h) * cp * cl
    Y = (N + h) * cp * sl
    Z = (N * (1 - e2) + h) * sp
    east = [-sl, cl, 0.0]
    north = [-sp * cl, -sp * sl, cp]
    up = [cp * cl, cp * sl, sp]
    neg_north = [-north[0], -north[1], -north[2]]
    return [east[0], east[1], east[2], 0.0,      # col0 (glTF x=east)
            up[0], up[1], up[2], 0.0,            # col1 (glTF y=up)
            neg_north[0], neg_north[1], neg_north[2], 0.0,  # col2 (glTF z=-north)
            X, Y, Z, 1.0]                        # col3 (origin ECEF)


def write_3dtiles_tileset(glb_path: str | Path, out_path: str | Path | None = None) -> dict:
    """`.glb` + `.glb.meta.json` → 3D Tiles 1.1 tileset.json (Cesium/Bmaps 스트리밍).

    사이드카 georef 로 ECEF 루트 변환을 만들고, glb POSITION min/max 로 box 경계를 잡는다.
    단일 타일 프로토타입 — 대규모는 공간 타일링으로 확장.
    """
    glb_path = Path(glb_path)
    meta = json.loads(glb_path.with_suffix(".glb.meta.json").read_text(encoding="utf-8"))
    g = meta["georef"]
    if g["projection"] == "equirectangular_approx":
        raise ValueError("3D Tiles 는 정밀 georef 필요 — pyproj 설치 후 EPSG 투영으로 재생성하세요.")
    transform = _enu_gltf_to_ecef(g["origin_lon"], g["origin_lat"], g.get("origin_height_m", 0.0))
    # glb POSITION min/max → 로컬 box (center + 3 half-axes)
    b = glb_path.read_bytes()
    jlen = struct.unpack("<II", b[12:20])[0]
    gltf = json.loads(b[20:20 + jlen])
    lo = np.array(gltf["accessors"][0]["min"], float)
    hi = np.array(gltf["accessors"][0]["max"], float)
    c = (lo + hi) / 2
    hx, hy, hz = np.maximum((hi - lo) / 2, 1.0)
    box = [c[0], c[1], c[2], hx, 0, 0, 0, hy, 0, 0, 0, hz]
    geo_err = float(np.linalg.norm(hi - lo)) or 50.0
    tileset = {
        "asset": {"version": "1.1", "generator": "inframon.gltf_export"},
        "geometricError": geo_err,
        "root": {
            "transform": transform,
            "boundingVolume": {"box": box},
            "geometricError": 0.0,
            "refine": "ADD",
            "content": {"uri": glb_path.name},
        },
        "extras": {"channel": meta.get("value_channel"), "legend": meta.get("legend"),
                   "binding_key": meta.get("binding", {}).get("key")},
    }
    out_path = Path(out_path) if out_path else glb_path.with_name("tileset.json")
    out_path.write_text(json.dumps(tileset, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"tileset": str(out_path), "glb": glb_path.name, "geometricError": geo_err,
            "origin": (g["origin_lat"], g["origin_lon"])}


def _read_glb_points(glb_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """우리가 쓴 .glb(점군 1개, POSITION float32 VEC3 + COLOR_0 uint8 VEC4) 되읽기.

    뷰어에서 GLTFLoader 를 없애기 위해서다 — 그 로더는 상대 import 가 있어 오프라인
    인라인이 안 된다. 우리 glb 는 구조가 고정이라 직접 푼다.
    """
    raw = glb_path.read_bytes()
    magic, _ver, _len = struct.unpack_from("<4sII", raw, 0)
    if magic != b"glTF":
        raise ValueError("glb 가 아닙니다")
    off = 12
    js = bin_ = None
    while off < len(raw):
        clen, ctype = struct.unpack_from("<I4s", raw, off)
        body = raw[off + 8: off + 8 + clen]
        if ctype == b"JSON":
            js = json.loads(body.decode("utf-8"))
        elif ctype.startswith(b"BIN"):
            bin_ = body
        off += 8 + clen
    if js is None or bin_ is None:
        raise ValueError("glb 청크가 불완전합니다")
    prim = js["meshes"][0]["primitives"][0]["attributes"]

    def acc(idx, dtype, comps):
        a = js["accessors"][idx]
        bv = js["bufferViews"][a["bufferView"]]
        o = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        return np.frombuffer(bin_, dtype=dtype, count=a["count"] * comps,
                             offset=o).reshape(-1, comps)

    pos = acc(prim["POSITION"], "<f4", 3).astype(float)
    col = acc(prim["COLOR_0"], np.uint8, 4)[:, :3].astype(float) / 255.0
    return pos, col


def _elements_for_viewer(elements_json, map_conversion, georef: dict, ifc_crs: str):
    """IFC 로컬 부재 AABB → 뷰어 프레임(glTF x=east, y=고도, z=−north) 박스 목록.

    점군과 **같은 원점·같은 투영**을 써야 부재 위에 점이 앉는다. 원점은 glb 사이드카의
    origin_lon/lat, 투영은 내보내기와 같은 ifc_crs. 데크 방위 회전은 MapConversion 의
    X축 방향(a, o)에서 온다.
    """
    from ..bim.elements import load_elements

    els = load_elements(str(elements_json))
    if not els or map_conversion is None:
        return []
    try:
        from pyproj import Transformer
        tr = Transformer.from_crs("EPSG:4326", ifc_crs, always_xy=True)
        e0, n0 = tr.transform(georef["origin_lon"], georef["origin_lat"])
    except Exception:                                   # noqa: BLE001 — 근사 원점
        e0 = n0 = None
    a, o = map_conversion._axis()
    rot = float(np.arctan2(o, a))                       # glTF Y축 회전각 [rad]
    out = []
    for e in els:
        lo, hi = np.asarray(e.bbox_min, float), np.asarray(e.bbox_max, float)
        c_local = (lo + hi) / 2.0
        size = hi - lo
        c_map = map_conversion.to_map(c_local[None, :3])[0]           # (E, N, H)
        if e0 is None:
            east = c_map[0] - map_conversion.eastings
            north = c_map[1] - map_conversion.northings
        else:
            east, north = c_map[0] - e0, c_map[1] - n0
        out.append({"guid": e.guid, "name": e.name, "member": e.member or "",
                    "center": [float(east), float(c_map[2]), float(-north)],
                    "size": [float(size[0]), float(size[2]), float(size[1])],   # x, 높이, y
                    "rotY": rot})
    return out


def _three_module_inline() -> str | None:
    """동봉한 three.module.js 를 data: URL 로. 없으면 None(→ CDN 폴백)."""
    import base64
    vend = Path(__file__).parent / "_vendor" / "three.module.js"
    if not vend.exists():
        return None
    return "data:text/javascript;base64," + base64.b64encode(vend.read_bytes()).decode("ascii")


def write_web_viewer(glb_path: str | Path, out_html: str | Path | None = None, *,
                     elements_json: str | Path | None = None,
                     map_conversion=None, ifc_crs: str = "EPSG:5186") -> dict:
    """`.glb`(+사이드카) → **자립형** 웹 뷰어 HTML.

    · 점군은 glb 에서 직접 풀어 JSON 으로 싣는다(GLTFLoader 불필요).
    · three.js 는 동봉본을 data: URL 로 인라인 — **인터넷 없이 파일만 열면 뜬다.**
      동봉본이 없을 때만 CDN 으로 폴백한다(그 경우 오프라인에서는 빈 화면이다).
    · `elements_json` + `map_conversion` 을 주면 **IFC 부재를 반투명 박스로** 함께
      그린다 — 점만 띄우면 "다리 위"인지 아무도 알 수 없다.
    · 궤도 조작은 내장(드래그 회전·휠 확대·우클릭 이동). 애드온 의존 없음.
    """
    glb_path = Path(glb_path)
    meta = json.loads(glb_path.with_suffix(".glb.meta.json").read_text(encoding="utf-8"))
    pos, col = _read_glb_points(glb_path)
    lg = meta.get("legend", {})
    g = meta.get("georef", {})
    feats = meta.get("features", [])
    bound = sum(1 for f in feats if f.get("element_globalid"))
    boxes = (_elements_for_viewer(elements_json, map_conversion, g, ifc_crs)
             if elements_json else [])
    three_src = _three_module_inline()
    offline = three_src is not None
    if not offline:
        three_src = "https://unpkg.com/three@0.160.0/build/three.module.js"
    title = f"inframon 웹 트윈 — {meta.get('value_channel', '')} ({lg.get('units', '')})"
    vals = [f.get("value") for f in feats]
    guids = [f.get("element_globalid") or "" for f in feats]
    rep = {
        "__TITLE__": title, "__THREE_SRC__": three_src,
        "__POS__": json.dumps(np.round(pos, 3).tolist()),
        "__COL__": json.dumps(np.round(col, 3).tolist()),
        "__VALS__": json.dumps(vals), "__GUIDS__": json.dumps(guids),
        "__BOXES__": json.dumps(boxes, ensure_ascii=False),
        "__CHANNEL__": str(meta.get("value_channel", "")),
        "__UNITS__": str(lg.get("units", "")),
        "__VMIN__": f"{lg.get('vmin', 0):.2f}", "__VMAX__": f"{lg.get('vmax', 0):.2f}",
        "__KIND__": str(lg.get("kind", "")),
        "__NPTS__": str(meta.get("n_points", 0)), "__BOUND__": str(bound),
        "__NBOX__": str(len(boxes)),
        "__ORIGIN__": f"{g.get('origin_lat', 0):.4f}, {g.get('origin_lon', 0):.4f}",
        "__PROJ__": str(g.get("projection", "")),
        "__ZSRC__": str(g.get("z_source", "flat")),
        "__OFFLINE__": ("동봉 three.js — 오프라인 가능" if offline
                        else "CDN three.js — 인터넷 필요"),
    }
    html = _VIEWER_HTML
    for k, v in rep.items():
        html = html.replace(k, v)
    out_html = Path(out_html) if out_html else glb_path.with_suffix(".viewer.html")
    out_html.write_text(html, encoding="utf-8")
    return {"viewer": str(out_html), "n_points": int(len(pos)), "bound": bound,
            "n_boxes": len(boxes), "offline": offline,
            "size_kb": round(len(html.encode("utf-8")) / 1024, 1)}


_VIEWER_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>body{margin:0;background:#0b0f14;color:#e6edf3;font:13px/1.5 system-ui,sans-serif;overflow:hidden}
#hud{position:fixed;top:12px;left:12px;background:rgba(20,26,34,.86);border:1px solid #2b3947;
border-radius:8px;padding:12px 14px;max-width:340px}#hud h1{font-size:14px;margin:0 0 6px}
#hud .row{color:#9fb0c0;margin:2px 0}#bar{height:12px;border-radius:3px;margin:6px 0 2px}
.lbl{display:flex;justify-content:space-between;color:#9fb0c0;font-size:11px}
#tip{position:fixed;bottom:12px;left:12px;color:#6b7d8f;font-size:11px}
#pick{position:fixed;top:12px;right:12px;background:rgba(20,26,34,.86);border:1px solid #2b3947;
border-radius:8px;padding:10px 12px;font-size:12px;display:none;max-width:260px}
.leg{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}
</style></head>
<body><div id="hud"><h1>__TITLE__</h1>
<div class="row">점 <b>__NPTS__</b> · GlobalId 결합 <b>__BOUND__</b>/__NPTS__ · IFC 부재 <b>__NBOX__</b></div>
<div class="row">georef __ORIGIN__ · __PROJ__ · 고도 __ZSRC__</div>
<div class="row">채널 __CHANNEL__ (__UNITS__)</div>
<div id="bar"></div><div class="lbl"><span>__VMIN__</span><span>0</span><span>__VMAX__</span></div>
<div class="row" style="margin-top:6px"><span class="leg" style="background:#4a6b8a"></span>deck
<span class="leg" style="background:#8a8f96;margin-left:8px"></span>pier
<span class="leg" style="background:#7a8288;margin-left:8px"></span>abutment</div></div>
<div id="pick"></div>
<div id="tip">드래그 회전 · 휠 확대 · 우클릭 드래그 이동 · 점 클릭 = 값/부재 · __OFFLINE__ · Bmaps 는 tileset.json 을 Cesium 에 addTileset</div>
<script type="importmap">{"imports":{"three":"__THREE_SRC__"}}</script>
<script type="module">
import*as THREE from'three';
const POS=__POS__,COL=__COL__,VALS=__VALS__,GUIDS=__GUIDS__,BOXES=__BOXES__;
const kind="__KIND__";
document.getElementById('bar').style.background=kind==='cri'
?'linear-gradient(90deg,#2a9d8f,#e9c46a,#f4a261,#c1121f)'
:'linear-gradient(90deg,#214e89,#f5f5f5,#c1121f)';
const rn=new THREE.WebGLRenderer({antialias:true});rn.setSize(innerWidth,innerHeight);
rn.setPixelRatio(devicePixelRatio);document.body.appendChild(rn.domElement);
const sc=new THREE.Scene();sc.background=new THREE.Color(0x0b0f14);
const cam=new THREE.PerspectiveCamera(50,innerWidth/innerHeight,.1,1e6);
sc.add(new THREE.AmbientLight(0xffffff,.55));
const dl=new THREE.DirectionalLight(0xffffff,.9);dl.position.set(1,2,1.2);sc.add(dl);
const MC={deck:0x4a6b8a,pier:0x8a8f96,abutment:0x7a8288};
const bbox=new THREE.Box3();
for(const b of BOXES){
 const geo=new THREE.BoxGeometry(b.size[0],b.size[1],b.size[2]);
 const mat=new THREE.MeshLambertMaterial({color:MC[b.member]||0x6c7a89,transparent:true,opacity:b.member==='deck'?.55:.42});
 const m=new THREE.Mesh(geo,mat);m.position.set(...b.center);m.rotation.y=b.rotY;m.userData=b;sc.add(m);
 const ed=new THREE.LineSegments(new THREE.EdgesGeometry(geo),new THREE.LineBasicMaterial({color:0x1f2a36}));
 ed.position.copy(m.position);ed.rotation.copy(m.rotation);sc.add(ed);
 m.updateMatrixWorld();bbox.expandByObject(m);
 // 부재 이름 라벨(A1/P1/S1) — 캔버스 스프라이트, 애드온 없이
 const cv=document.createElement('canvas');cv.width=160;cv.height=48;const g2=cv.getContext('2d');
 g2.fillStyle='rgba(20,26,34,.85)';g2.fillRect(0,0,160,48);g2.fillStyle='#e6edf3';g2.font='bold 26px system-ui,sans-serif';
 g2.textAlign='center';g2.textBaseline='middle';g2.fillText(b.name,80,24);
 const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(cv),depthTest:false,transparent:true}));
 sp.position.set(b.center[0],b.center[1]+b.size[1]/2+(b.member==='deck'?2.5:1.2),b.center[2]);sp.scale.set(6,1.8,1);sp.renderOrder=20;sc.add(sp);
}
const geo=new THREE.BufferGeometry();
geo.setAttribute('position',new THREE.Float32BufferAttribute(POS.flat(),3));
geo.setAttribute('color',new THREE.Float32BufferAttribute(COL.flat(),3));
const P=new THREE.Points(geo,new THREE.PointsMaterial({size:11,sizeAttenuation:false,vertexColors:true}));
sc.add(P);geo.computeBoundingBox();bbox.union(geo.boundingBox);
if(BOXES.some(b=>b.member==='deck')){const top=Math.max(...BOXES.filter(b=>b.member==='deck').map(b=>b.center[1]+b.size[1]/2));
 const lp=[];for(const p of POS){lp.push(p[0],p[1],p[2],p[0],top,p[2])}
 const lg=new THREE.BufferGeometry();lg.setAttribute('position',new THREE.Float32BufferAttribute(lp,3));
 sc.add(new THREE.LineSegments(lg,new THREE.LineBasicMaterial({color:0xe6edf3,transparent:true,opacity:.35})))}
const c=new THREE.Vector3();bbox.getCenter(c);const sz=new THREE.Vector3();bbox.getSize(sz);
const grid=new THREE.GridHelper(Math.max(sz.x,sz.z)*3,30,0x223344,0x162030);grid.position.set(c.x,bbox.min.y-.05,c.z);sc.add(grid);
let target=c.clone(),r=Math.max(sz.length()*1.1,20),theta=-.9,phi=1.05;
function place(){cam.position.set(target.x+r*Math.sin(phi)*Math.cos(theta),target.y+r*Math.cos(phi),target.z+r*Math.sin(phi)*Math.sin(theta));cam.lookAt(target)}
let drag=null;const el=rn.domElement;
el.addEventListener('contextmenu',e=>e.preventDefault());
el.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,b:e.button,moved:false}});
addEventListener('pointerup',e=>{if(drag&&!drag.moved&&drag.b===0)pick(e);drag=null});
addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;
 if(Math.abs(dx)+Math.abs(dy)>2)drag.moved=true;
 if(drag.b===2){const right=new THREE.Vector3().crossVectors(cam.getWorldDirection(new THREE.Vector3()),cam.up).normalize();
  target.addScaledVector(right,-dx*r*.0015);target.y+=dy*r*.0015}
 else{theta-=dx*.006;phi=Math.min(Math.max(phi-dy*.006,.08),Math.PI-.08)}place()});
el.addEventListener('wheel',e=>{e.preventDefault();r*=Math.exp(e.deltaY*.0012);r=Math.max(r,2);place()},{passive:false});
const ray=new THREE.Raycaster();ray.params.Points.threshold=Math.max(sz.length()*.012,.6);
function pick(e){const m=new THREE.Vector2(e.clientX/innerWidth*2-1,-(e.clientY/innerHeight)*2+1);ray.setFromCamera(m,cam);
 const hit=ray.intersectObject(P)[0];const box=document.getElementById('pick');
 if(!hit){box.style.display='none';return}
 const i=hit.index,v=VALS[i],gd=GUIDS[i];const b=BOXES.find(x=>x.guid===gd);
 box.style.display='block';box.innerHTML='<b>점 #'+i+'</b><br>값 '+(v==null?'—':Number(v).toFixed(3))+' __UNITS__<br>부재 '+(b?b.name+' ('+b.member+')':'(미결합)')+'<br><span style="color:#6b7d8f">'+(gd||'')+'</span>'}
place();
addEventListener('resize',()=>{cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();rn.setSize(innerWidth,innerHeight)});
(function loop(){requestAnimationFrame(loop);rn.render(sc,cam)})();
</script></body></html>"""


def export_insar_gltf(h5: str | Path, out_path: str | Path, *, value: str = "velocity",
                      fram_project: str | Path | None = None, ifc_crs: str = "EPSG:5186",
                      z_exaggerate: float = 0.0, element_map: dict | None = None,
                      element_guids=None, z_source: str = "flat", dem=None,
                      element_z=None, psi_elev=None, track_height=None,
                      clearance_m: float | None = None,
                      element_z_datum: float | None = None) -> dict:
    """InSAR/PSI H5 → 웹 트윈용 .glb + .meta.json.

    value: 'velocity'(LOS 속도)·'cri'(FRAM CRI 최근접, fram_project 필요)·'cumulative'(누적 LOS).
    z_source(3D 고도): 'deck'(**권장** — IFC>PSI>DEM+형하고 순으로 데크 레벨에 얹는다)·
    'flat'(평면)·'value'(값×z_exaggerate 과장)·'dem'(DEM 표고, dem= 래스터/SRTM
    디렉터리)·'element'(IFC 부재 상단 Z=데크레벨, element_z= 점별 배열 — guid_map_from_alignment 산출).
    dem·element 는 실 미터 고도라 교량/지형 위에 얹힌다.
    결합(택1): element_guids([N] 점별 GlobalId) 우선, 없으면 element_map(point_id→GlobalId).
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
    if z_source == "dem":
        if dem is None:
            raise ValueError("z_source='dem' 은 dem=(래스터 또는 SRTM 디렉터리) 가 필요합니다.")
        z = np.nan_to_num(_sample_dem(lonlat, dem), nan=0.0)          # 지형 표고(m)
        georef["z_source"] = f"dem:{Path(dem).name}"
    elif z_source == "element":
        if element_z is None:
            raise ValueError("z_source='element' 은 element_z(점별 부재 Z) 가 필요합니다 — "
                             "guid_map_from_alignment 의 summary['element_z'] 를 넘기세요.")
        z = np.nan_to_num(np.asarray(element_z, float), nan=0.0)      # 데크 레벨(m)
        georef["z_source"] = "ifc_element_top"
    elif z_source == "psi":
        if psi_elev is None:
            raise ValueError("z_source='psi' 은 psi_elev(점별 절대고도=DEM+Δh) 가 필요합니다 — "
                             "psi_height.estimate_residual_height(ref_dem=)['abs_elev_m'] 를 넘기세요.")
        z = np.nan_to_num(np.asarray(psi_elev, float), nan=0.0)      # 산란체 실고도(DEM+Δh)
        georef["z_source"] = "psi_residual_height"
    elif z_source == "deck":
        # IFC 도 B⊥ 도 없는 임의 교량의 기본값 — 지면 표고 + 형하고로 데크 레벨 근사.
        # 이게 없으면 점이 z=0 에 깔려 **교량보다 수십~수백 m 아래**에 놓인다.
        from .deck_z import deck_elevation
        d = deck_elevation(lonlat, element_z=element_z, psi_elev=psi_elev,
                           track_height=track_height, clearance_m=clearance_m,
                           element_z_datum=element_z_datum)
        z = d.z
        georef["z_source"] = d.source
        georef["z_detail"] = d.describe()
        if d.ground_m is not None:
            georef["ground_m"] = round(d.ground_m, 1)
        if d.clearance_m is not None:
            georef["clearance_m"] = round(d.clearance_m, 2)
    elif z_source == "value" or z_exaggerate:
        z = np.nan_to_num(vals, nan=0.0) * (z_exaggerate or 1.0) / 1000.0
        georef["z_source"] = f"value×{z_exaggerate or 1.0}"
    else:
        z = np.zeros(n)
        georef["z_source"] = "flat"
    # 점의 Y 가 **절대고도(m)** 를 담는다(기존 dem·element 원천과 같은 규약). origin 은
    # 지오이드 면에 두고 뷰어가 eastNorthUpToFixedFrame 로 배치하므로 결과 고도는 같다.
    if z.size and np.isfinite(z).any() and georef.get("z_source") not in (None, "flat"):
        georef["deck_z_median_m"] = round(float(np.nanmedian(z)), 2)
    positions = np.column_stack([east_north[:, 0], z, -east_north[:, 1]]).astype(float)
    _write_glb(positions, colors, out_path)
    # 사이드카: GlobalId 결합 계약 + georef + 점별 원시값
    pid = P["attrs"].get("point_id")
    ids = (np.asarray(P.get("point_id", None)) if P.get("point_id") is not None
           else np.arange(n))
    emap = element_map or {}
    eg = np.asarray(element_guids, dtype=object) if element_guids is not None else None
    features = []
    for i in range(n):
        vi = float(vals[i]) if np.isfinite(vals[i]) else None
        pid = int(ids[i]) if i < len(ids) else i
        if eg is not None and i < eg.size:                 # 인덱스 정렬 결합 우선
            guid = str(eg[i]) if eg[i] else None
        else:
            guid = emap.get(pid)
        features.append({"index": i, "point_id": pid, "element_globalid": guid,
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
