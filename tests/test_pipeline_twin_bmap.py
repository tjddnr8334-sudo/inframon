"""파이프라인 ⑬ 디지털트윈 · ⑭ BMAP 등록 — 목표 체인의 마지막 두 고리.

핵심 계약: **IFC 가 없어도 체인이 끊기지 않는다**(임의 교량은 IFC 미확보가 정상).
IFC/부재테이블이 있으면 GlobalId 결합까지, 없으면 점군 트윈만 만들고 진행한다.
⑭ 산출 레지스트리는 BMAP 측 `BridgeRegistry.from_file` 로 그대로 읽혀야 한다.
"""

from __future__ import annotations

import json

import h5py
import numpy as np
import pytest

pytest.importorskip("pyproj")   # 3D Tiles transform 은 정밀 georef 필요

from inframon.api.registry import BridgeRegistry  # noqa: E402
from inframon.pipeline_bridge import (  # noqa: E402
    PipelineReport,
    _twin_and_register,
    run_bridge_pipeline,
)


def _project(tmp_path, n=12, m=6):
    """최소 project.h5(/insar) — glTF 내보내기가 읽는 형식."""
    p = tmp_path / "project.h5"
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


def _run(tmp_path, *, ifc=None, elements=None, ctx_extra=None, project=True):
    rep = PipelineReport(lat=36.4547, lon=126.8013)
    if project:
        rep.context["pinn"] = {"project": str(_project(tmp_path))}
    rep.context["bridge"] = {"name": "테스트교"}
    rep.context.update(ctx_extra or {})
    _twin_and_register(rep, rep.context, 36.4547, 126.8013, tmp_path,
                       ifc=ifc, bim_elements=elements, registry=None,
                       bridge_id=None, twin_value="velocity")
    return rep


def _stage(rep, name):
    return next(s for s in rep.stages if s.step == name)


# ── ⑬ 트윈: IFC 없이도 만들어져야 한다 ──
def test_twin_without_ifc_still_produces_gltf_and_tiles(tmp_path):
    rep = _run(tmp_path)
    t = _stage(rep, "⑬IFC디지털트윈")
    assert t.status == "done" and "점군 트윈" in t.detail
    assert (tmp_path / "twin.glb").exists()
    assert (tmp_path / "twin.glb.meta.json").exists()
    assert (tmp_path / "tileset.json").exists()          # 3D Tiles 까지


def test_twin_meta_keeps_globalid_slot_even_unbound(tmp_path):
    """결합 전이어도 트윈 계약(element_globalid)은 유지 — 나중에 IFC 가 와도 형식이 같다."""
    _run(tmp_path)
    meta = json.loads((tmp_path / "twin.glb.meta.json").read_text(encoding="utf-8"))
    assert meta["binding"]["key"] == "element_globalid"
    assert all("element_globalid" in f for f in meta["features"])


def test_twin_binding_failure_degrades_not_crashes(tmp_path):
    """부재 테이블이 깨져 결합에 실패해도 트윈은 나와야 한다(체인 유지)."""
    bad = tmp_path / "elements.json"
    bad.write_text('{"elements": [{"guid": "X"}]}', encoding="utf-8")   # bbox 없음 → 로드 실패
    rep = _run(tmp_path, elements=bad)
    t = _stage(rep, "⑬IFC디지털트윈")
    assert t.status == "done" and "점군 트윈" in t.detail   # 실패해도 done + 사유
    assert (tmp_path / "twin.glb").exists()


# ── ⑭ BMAP 등록 ──
def test_registry_is_bmap_loadable(tmp_path):
    rep = _run(tmp_path)
    reg = tmp_path / "bridge_registry.json"
    assert _stage(rep, "⑭BMAP등록").status == "done" and reg.exists()
    entries = BridgeRegistry.from_file(reg).list()        # BMAP 측 로더로 그대로 읽힘
    assert len(entries) == 1
    e = entries[0]
    assert e.name == "테스트교" and e.exists()             # project.h5 가 실제로 연결됨
    assert e.wgs84_center == (36.4547, 126.8013)


def test_registry_records_twin_paths(tmp_path):
    _run(tmp_path)
    raw = json.loads((tmp_path / "bridge_registry.json").read_text(encoding="utf-8"))
    b = raw["bridges"][0]
    assert b["twin_gltf"].endswith("twin.glb") and b["twin_tileset"].endswith("tileset.json")


def test_registry_rerun_updates_not_duplicates(tmp_path):
    """같은 교량을 다시 돌려도 항목이 중복되면 BMAP 목록이 오염된다."""
    _run(tmp_path)
    _run(tmp_path)
    raw = json.loads((tmp_path / "bridge_registry.json").read_text(encoding="utf-8"))
    assert len(raw["bridges"]) == 1


def test_registry_preserves_other_bridges(tmp_path):
    """다중 교량 레지스트리 — 남의 항목을 지우면 안 된다."""
    reg = tmp_path / "bridge_registry.json"
    reg.write_text(json.dumps({"bridges": [
        {"bridge_id": "OTHER-1", "name": "다른교", "project_h5": "x/p.h5"}]}),
        encoding="utf-8")
    _run(tmp_path)
    ids = {b["bridge_id"] for b in json.loads(reg.read_text(encoding="utf-8"))["bridges"]}
    assert "OTHER-1" in ids and len(ids) == 2


def test_corrupt_registry_is_rebuilt_not_fatal(tmp_path):
    (tmp_path / "bridge_registry.json").write_text("{ 깨진 json", encoding="utf-8")
    rep = _run(tmp_path)
    assert _stage(rep, "⑭BMAP등록").status == "done"


# ── 선행 실패 시 조용히 사라지지 않고 사유와 함께 skip ──
def test_skips_are_reported_when_no_project(tmp_path):
    rep = _run(tmp_path, project=False)
    for name in ("⑬IFC디지털트윈", "⑭BMAP등록"):
        s = _stage(rep, name)
        assert s.status == "skip" and "project.h5" in s.detail


# ── plan 모드가 새 단계를 노출하는지(네트워크 없이) ──
def test_plan_lists_twin_and_bmap_stages(tmp_path, monkeypatch):
    import inframon.pipeline_bridge as pb

    def _boom(*a, **k):                     # 경량 단계의 네트워크를 전부 차단
        raise RuntimeError("network off")
    monkeypatch.setattr(pb, "_run_heavy", lambda *a, **k: None)
    for mod, fn in (("inframon.insar.osm_bridge", "confirm_bridge"),
                    ("inframon.insar.roi_selection", "select_roi"),
                    ("inframon.insar.snap_acquire", "search_frames")):
        monkeypatch.setattr(f"{mod}.{fn}", _boom, raising=False)
    rep = run_bridge_pipeline(36.4547, 126.8013, out_dir=tmp_path, mode="plan")
    steps = [s.step for s in rep.stages]
    assert "⑬IFC디지털트윈" in steps and "⑭BMAP등록" in steps
    assert _stage(rep, "⑬IFC디지털트윈").status == "planned"


def test_plan_notes_ifc_absence_explicitly(tmp_path, monkeypatch):
    import inframon.pipeline_bridge as pb
    monkeypatch.setattr(pb, "_run_heavy", lambda *a, **k: None)
    monkeypatch.setattr("inframon.insar.osm_bridge.confirm_bridge",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr("inframon.insar.roi_selection.select_roi",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("off")),
                        raising=False)
    monkeypatch.setattr("inframon.insar.snap_acquire.search_frames",
                        lambda *a, **k: [], raising=False)
    rep = run_bridge_pipeline(36.4547, 126.8013, out_dir=tmp_path, mode="plan")
    assert "IFC 미지정" in _stage(rep, "⑬IFC디지털트윈").detail
