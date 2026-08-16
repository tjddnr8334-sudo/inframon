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
    summary = {"associated": int(sum(1 for g in guids if g)),
               "n_points": int(guids.size), "association": r.get("association"),
               "warnings": r.get("warnings", [])}
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


def write_web_viewer(glb_path: str | Path, out_html: str | Path | None = None) -> dict:
    """`.glb`(+사이드카) → 자립형 웹 뷰어 HTML (Bmaps 통합 스텁).

    glb 를 base64 data URI 로 **인라인**해 로컬에서 파일만 열면 렌더된다(서버·CORS 불필요).
    three.js 는 CDN(ES 모듈). 정점색 점군 + 범례 + georef + GlobalId 결합수 오버레이.
    Bmaps(Cesium)엔 tileset.json 을 addTileset 하면 되고, 이 HTML 은 단독 확인용.
    """
    import base64
    glb_path = Path(glb_path)
    meta = json.loads(glb_path.with_suffix(".glb.meta.json").read_text(encoding="utf-8"))
    b64 = base64.b64encode(glb_path.read_bytes()).decode("ascii")
    lg = meta.get("legend", {})
    g = meta.get("georef", {})
    bound = sum(1 for f in meta.get("features", []) if f.get("element_globalid"))
    title = f"inframon 웹 트윈 — {meta.get('value_channel','')} ({lg.get('units','')})"
    html = _VIEWER_HTML.replace("__TITLE__", title).replace("__B64__", b64).replace(
        "__CHANNEL__", str(meta.get("value_channel", ""))).replace(
        "__UNITS__", str(lg.get("units", ""))).replace(
        "__VMIN__", f"{lg.get('vmin', 0):.2f}").replace("__VMAX__", f"{lg.get('vmax', 0):.2f}").replace(
        "__KIND__", str(lg.get("kind", ""))).replace(
        "__NPTS__", str(meta.get("n_points", 0))).replace("__BOUND__", str(bound)).replace(
        "__ORIGIN__", f"{g.get('origin_lat', 0):.4f}, {g.get('origin_lon', 0):.4f}").replace(
        "__PROJ__", str(g.get("projection", "")))
    out_html = Path(out_html) if out_html else glb_path.with_suffix(".viewer.html")
    out_html.write_text(html, encoding="utf-8")
    return {"viewer": str(out_html), "inlined_kb": round(len(b64) / 1024, 1),
            "n_points": meta.get("n_points", 0), "bound": bound}


_VIEWER_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>body{margin:0;background:#0b0f14;color:#e6edf3;font:13px/1.5 system-ui,sans-serif;overflow:hidden}
#hud{position:fixed;top:12px;left:12px;background:rgba(20,26,34,.82);border:1px solid #2b3947;
border-radius:8px;padding:12px 14px;max-width:320px}#hud h1{font-size:14px;margin:0 0 6px}
#hud .row{color:#9fb0c0;margin:2px 0}#bar{height:12px;border-radius:3px;margin:6px 0 2px}
.lbl{display:flex;justify-content:space-between;color:#9fb0c0;font-size:11px}
#tip{position:fixed;bottom:12px;left:12px;color:#6b7d8f;font-size:11px}</style></head>
<body><div id="hud"><h1>__TITLE__</h1>
<div class="row">점 <b>__NPTS__</b> · GlobalId 결합 <b>__BOUND__</b>/__NPTS__</div>
<div class="row">georef __ORIGIN__ · __PROJ__</div>
<div class="row">채널 __CHANNEL__ (__UNITS__)</div>
<div id="bar"></div><div class="lbl"><span>__VMIN__</span><span>0</span><span>__VMAX__</span></div></div>
<div id="tip">드래그 회전 · 휠 확대 · 정점색 = 값 · glb 인라인(자립형) · Bmaps 는 tileset.json 을 Cesium 에 addTileset</div>
<script type="importmap">{"imports":{"three":"https://unpkg.com/three@0.160.0/build/three.module.js",
"three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}</script>
<script type="module">
import*as THREE from'three';import{OrbitControls}from'three/addons/controls/OrbitControls.js';
import{GLTFLoader}from'three/addons/loaders/GLTFLoader.js';
const kind="__KIND__";
document.getElementById('bar').style.background=kind==='cri'
?'linear-gradient(90deg,#2a9d8f,#e9c46a,#f4a261,#c1121f)'
:'linear-gradient(90deg,#214e89,#f5f5f5,#c1121f)';
const rn=new THREE.WebGLRenderer({antialias:true});rn.setSize(innerWidth,innerHeight);
rn.setPixelRatio(devicePixelRatio);document.body.appendChild(rn.domElement);
const sc=new THREE.Scene();sc.background=new THREE.Color(0x0b0f14);
const cam=new THREE.PerspectiveCamera(55,innerWidth/innerHeight,.1,1e6);
const ct=new OrbitControls(cam,rn.domElement);ct.enableDamping=true;
sc.add(new THREE.GridHelper(4000,20,0x223,0x162));
function b64toBuf(b){const s=atob(b),a=new Uint8Array(s.length);for(let i=0;i<s.length;i++)a[i]=s.charCodeAt(i);return a.buffer}
new GLTFLoader().parse(b64toBuf("__B64__"),"",(gltf)=>{
 let pts;gltf.scene.traverse(o=>{if(o.isPoints||o.isMesh)pts=o});
 const geo=pts.geometry;const m=new THREE.PointsMaterial({size:6,sizeAttenuation:false,vertexColors:true});
 const P=new THREE.Points(geo,m);sc.add(P);
 geo.computeBoundingSphere();const bs=geo.boundingSphere;
 ct.target.copy(bs.center);cam.position.set(bs.center.x,bs.center.y+bs.radius*.6,bs.center.z+bs.radius*1.6);
 cam.updateProjectionMatrix();
},(e)=>{document.getElementById('hud').innerHTML+='<div style="color:#f4a261">로드 실패: '+e+'</div>'});
addEventListener('resize',()=>{cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();rn.setSize(innerWidth,innerHeight)});
(function loop(){requestAnimationFrame(loop);ct.update();rn.render(sc,cam)})();
</script></body></html>"""


def export_insar_gltf(h5: str | Path, out_path: str | Path, *, value: str = "velocity",
                      fram_project: str | Path | None = None, ifc_crs: str = "EPSG:5186",
                      z_exaggerate: float = 0.0, element_map: dict | None = None,
                      element_guids=None) -> dict:
    """InSAR/PSI H5 → 웹 트윈용 .glb + .meta.json.

    value: 'velocity'(LOS 속도)·'cri'(FRAM CRI 최근접, fram_project 필요)·'cumulative'(누적 LOS).
    z_exaggerate>0 이면 값(또는 누적변위)을 Z로 과장해 3D 기복 표현.
    결합(택1): element_guids([N] 점별 GlobalId, 인덱스 정렬 — guid_map_from_alignment 산출) 우선,
    없으면 element_map(point_id→GlobalId). 둘 다 없으면 슬롯 null 로 계약만 노출.
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
