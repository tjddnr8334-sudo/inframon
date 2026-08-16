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
