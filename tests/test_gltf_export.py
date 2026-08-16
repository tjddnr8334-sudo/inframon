"""웹 트윈 glTF(.glb) 내보내기 — 유효 glTF 2.0 구조·정점색·GlobalId 결합 계약·georef."""

from __future__ import annotations

import json
import struct

import h5py
import numpy as np

from inframon.insar.gltf_export import export_insar_gltf


def _track(tmp_path, n=12, m=6):
    """최소 Track H5(pixel_lonlat/epochs/los_mm/coh)."""
    p = tmp_path / "t.h5"
    rng = np.random.default_rng(0)
    with h5py.File(p, "w") as f:
        g = f.create_group("insar")
        g.create_dataset("pixel_lonlat", data=np.column_stack([
            126.80 + rng.random(n) * 0.01, 36.45 + rng.random(n) * 0.01]))
        g.create_dataset("epochs", data=np.array([20200101 + i for i in range(m)], np.int64))
        g.create_dataset("los_mm", data=rng.normal(0, 3, (n, m)).astype("float32"))
        g.create_dataset("los_velocity_mm_yr", data=rng.normal(0, 5, n).astype("float32"))
        g.create_dataset("coh", data=np.full(n, 0.8, "float32"))
    return p


def _parse_glb(path):
    b = path.read_bytes()
    magic, ver, total = struct.unpack("<III", b[:12])
    assert magic == 0x46546C67 and ver == 2 and total == len(b)
    off = 12
    jlen, jtyp = struct.unpack("<II", b[off:off + 8]); off += 8
    assert jtyp == 0x4E4F534A
    gltf = json.loads(b[off:off + jlen]); off += jlen
    blen, btyp = struct.unpack("<II", b[off:off + 8])
    assert btyp == 0x004E4942
    return gltf, blen


def test_glb_is_valid_gltf2_points(tmp_path):
    r = export_insar_gltf(_track(tmp_path), tmp_path / "twin.glb", value="velocity")
    glb = tmp_path / "twin.glb"
    assert glb.exists()
    gltf, blen = _parse_glb(glb)
    assert gltf["asset"]["version"] == "2.0"
    prim = gltf["meshes"][0]["primitives"][0]
    assert prim["mode"] == 0                                   # POINTS
    assert set(prim["attributes"]) == {"POSITION", "COLOR_0"}
    acc = gltf["accessors"]
    assert acc[0]["count"] == 12 and acc[0]["type"] == "VEC3"  # POSITION
    assert acc[1]["type"] == "VEC4" and acc[1]["normalized"]   # COLOR_0 ubyte
    assert gltf["buffers"][0]["byteLength"] == blen            # 버퍼 선언=BIN 길이
    assert acc[0]["min"] and acc[0]["max"]                     # POSITION min/max 필수


def test_meta_sidecar_binding_and_georef(tmp_path):
    r = export_insar_gltf(_track(tmp_path), tmp_path / "twin.glb", value="velocity")
    meta = json.loads((tmp_path / "twin.glb.meta.json").read_text(encoding="utf-8"))
    assert meta["schema"] == "inframon.gltf.meta/1.0"
    # GlobalId 결합 계약 슬롯(설계 노트) — 채워지기 전엔 null
    assert meta["binding"]["key"] == "element_globalid"
    assert all("element_globalid" in f for f in meta["features"])
    assert all(f["element_globalid"] is None for f in meta["features"])  # IFC 미결합
    # georef 원점(뷰어 글로브 배치용)
    assert meta["georef"]["frame"] == "ENU_meters"
    assert 126.8 < meta["georef"]["origin_lon"] < 126.82
    assert meta["n_points"] == 12


def test_element_map_binds_globalid(tmp_path):
    """element_map(point_id→GlobalId) 주면 사이드카에 결합이 채워진다(IFC 4.3 부재 외래키)."""
    r = export_insar_gltf(_track(tmp_path), tmp_path / "twin.glb", value="velocity",
                          element_map={0: "1a2B3c4D5e6F", 1: "9z8Y7x6W5v4U"})
    assert r["bound"] == 2
    meta = json.loads((tmp_path / "twin.glb.meta.json").read_text(encoding="utf-8"))
    bound = {f["point_id"]: f["element_globalid"] for f in meta["features"] if f["element_globalid"]}
    assert bound[0] == "1a2B3c4D5e6F" and bound[1] == "9z8Y7x6W5v4U"


def test_cri_channel_needs_fram(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        export_insar_gltf(_track(tmp_path), tmp_path / "twin.glb", value="cri")


def test_3dtiles_tileset_places_on_globe(tmp_path):
    """glb → 3D Tiles 1.1 tileset.json: ECEF 루트변환(글로브 배치)·box·content 참조."""
    import math

    import pytest
    pytest.importorskip("pyproj")                     # 정밀 georef 필요
    from inframon.insar.gltf_export import write_3dtiles_tileset

    export_insar_gltf(_track(tmp_path), tmp_path / "twin.glb", value="velocity")
    t = write_3dtiles_tileset(tmp_path / "twin.glb")
    ts = json.loads((tmp_path / "tileset.json").read_text(encoding="utf-8"))
    assert ts["asset"]["version"] == "1.1"
    assert ts["root"]["content"]["uri"] == "twin.glb"
    assert ts["root"]["refine"] == "ADD"
    tr = ts["root"]["transform"]
    assert len(tr) == 16 and tr[15] == 1.0
    X, Y, Z = tr[12], tr[13], tr[14]                  # ECEF 원점 = 지구 반지름 근처
    assert 6.3e6 < math.sqrt(X * X + Y * Y + Z * Z) < 6.4e6
    assert len(ts["root"]["boundingVolume"]["box"]) == 12


def test_web_viewer_is_self_contained(tmp_path):
    """glb → 자립형 뷰어 HTML: glb 인라인(base64)·three.js·HUD(범례·georef·결합수)."""
    from inframon.insar.gltf_export import write_web_viewer
    export_insar_gltf(_track(tmp_path), tmp_path / "twin.glb", value="velocity",
                      element_map={0: "GUID-A"})
    v = write_web_viewer(tmp_path / "twin.glb")
    html = (tmp_path / "twin.viewer.html").read_text(encoding="utf-8")
    assert "atob(" in html and "GLTFLoader" in html          # glb 인라인 디코드
    assert "three.module.js" in html                         # three.js ES 모듈
    assert "__B64__" not in html and "__NPTS__" not in html  # 플레이스홀더 치환 완료
    assert "GlobalId 결합" in html and "georef" in html      # HUD
    assert v["bound"] == 1 and v["inlined_kb"] > 0


def test_globalid_binding_via_alignment(tmp_path):
    """IFC 4.3 부재 정합 → glb 사이드카 element_globalid 자동 결합(설계 마지막 고리)."""
    import math

    import numpy as np

    from inframon.bim import Element, align_project_to_bim  # noqa: F401
    from inframon.bim.georef import MapConversion
    from inframon.config import PipelineConfig
    from inframon.contracts.io import ProjectStore
    from inframon.contracts.schema import InSAROutput
    from inframon.insar.gltf_export import guid_map_from_alignment
    from inframon.orchestrator.pipeline import run_pipeline

    project = str(tmp_path / "project.h5")
    run_pipeline(project, PipelineConfig(n_points=60, n_dates=24))
    els = [Element("DECK1", "상판", "IfcSlab", bbox_min=(0, -5, 8), bbox_max=(100, 5, 9))]
    t = math.radians(20.0)
    mc = MapConversion(eastings=200000.0, northings=550000.0, orthogonal_height=0.0,
                       x_axis_abscissa=math.cos(t), x_axis_ordinate=math.sin(t),
                       target_crs="EPSG:5186", source="ifc")
    # 데모 점을 상판 위로 옮겨 지도 CRS 저장(정합 대상)
    with ProjectStore(project) as s:
        ins = s.read_meta("insar", InSAROutput)
        n = s.read_array(ins.xyz_ds).shape[0]
        local = np.column_stack([np.linspace(2.0, 98.0, n), np.zeros(n), np.full(n, 8.5)])
        s.write_array(ins.xyz_ds, mc.to_map(local))

    guids, summ = guid_map_from_alignment(project, els, map_conversion=mc.to_dict(), source_crs="EPSG:5186")
    assert summ["associated"] == n and guids.size == n
    assert all(g == "DECK1" for g in guids)                       # 전부 상판에 결합

    r = export_insar_gltf(project, tmp_path / "twin.glb", value="velocity",
                          element_guids=guids)
    assert r["bound"] == n                                        # 사이드카 결합 수
    meta = json.loads((tmp_path / "twin.glb.meta.json").read_text(encoding="utf-8"))
    assert all(f["element_globalid"] == "DECK1" for f in meta["features"])
