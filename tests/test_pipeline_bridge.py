"""표준 교량 파이프라인 오케스트레이터 — 순서·상태 보고(경량단계 모킹)."""

from __future__ import annotations

import pytest

from inframon import pipeline_bridge as pb
from inframon.insar.roi_selection import RoiResult


class _FakeBridge:
    name = "테스트교"; osm_id = 1; osm_url = "http://osm/way/1"
    length_m = 500.0; tags = {"bridge": "yes"}; geometry = [(37.32, 127.10)]


class _FakeFrame:
    n_scenes = 40; centrality_km = 8.4
    def label(self):
        return "ASC path127 frame120"


def _patch_light(monkeypatch):
    monkeypatch.setattr("inframon.insar.osm_bridge.confirm_bridge",
                        lambda lat, lon: _FakeBridge())
    monkeypatch.setattr("inframon.insar.roi_selection.select_roi",
                        lambda lat, lon, **k: RoiResult((127.09, 37.31, 127.11, 37.33),
                                                        2.0, (37.32, 127.10), 1224, 306.0, True))
    monkeypatch.setattr("inframon.insar.snap_acquire.search_frames",
                        lambda lat, lon, **k: [_FakeFrame()])


def test_pipeline_plan_order_and_status(monkeypatch):
    _patch_light(monkeypatch)
    rep = pb.run_bridge_pipeline(37.3219, 127.1083, mode="plan")
    steps = [s.step for s in rep.stages]
    # 정규 순서: 교량 → ROI → 트랙 → ERA5 → 궤도 → asc/desc → 교량메타 → 중량3
    assert steps[0].startswith("①교량선정")
    assert any("③ROI" in s for s in steps)
    assert any("②④" in s for s in steps)
    # 경량 구현 단계는 done
    byname = {s.step: s for s in rep.stages}
    assert byname[[s for s in steps if s.startswith("①")][0]].status == "done"
    # 중량 단계는 plan 에서 planned
    assert all(s.status == "planned" for s in rep.stages if s.step.startswith(("⑧", "⑨", "⑫")))
    # context 채워짐
    assert rep.context["bridge"]["length_m"] == 500
    assert rep.context["roi"]["n_buildings"] == 1224


class _DummyStore:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_pipeline_full_runs_9_12(monkeypatch, tmp_path):
    _patch_light(monkeypatch)
    from inframon.insar import snap_acquire as sa, snap_backend as sb

    class _Acq:
        slc_dir = str(tmp_path / "SLC")
    monkeypatch.setattr(sa, "acquire", lambda *a, **k: _Acq())

    _res = sb.SnapRunResult("20240107", sb.BurstLoc("IW2", 1, 5.4, 37.34, 127.13, contained=True),
                            [sb.SnapPairResult("20240107", "20240119", "p.tif", True)])
    # 실제 SNAP 은 track_h5 를 만들고 끝난다 — 파일이 없으면 엔진 계약 위반으로 막힌다
    # (⑧이 산출물 없이 '성공'을 주장하면 하류가 조용히 깨지므로 processing_engine 이 검사).
    (tmp_path / "t.h5").write_bytes(b"x")
    _res.track_h5 = str(tmp_path / "t.h5"); _res.weather = None
    monkeypatch.setattr(sb, "run", lambda *a, **k: _res)
    monkeypatch.setattr(sb, "platform_heading", lambda *a, **k: -13.1)
    monkeypatch.setattr(sb, "scene_date", lambda s: "20240107")
    (tmp_path / "SLC").mkdir()
    (tmp_path / "SLC" / "S1A_IW_SLC__1SDV_20240107T093202_x.zip").write_text("x")
    monkeypatch.setattr(sb, "build_bridge_track_ps_ds",
                        lambda *a, **k: {"n_points": 229, "n_ps": 66, "n_ds": 163,
                                         "buffer_m": 30.0, "class_method": "coherence>=0.7(1차)",
                                         "deck_dist_max_m": 30.0, "coh_mean": 0.64, "out": "x"})
    monkeypatch.setattr("inframon.contracts.io.ProjectStore", _DummyStore)
    monkeypatch.setattr("inframon.insar.track_reader.import_track_h5", lambda store, h5, **k: None)
    monkeypatch.setattr("inframon.custom_pinn.run_custom_pinn",
                        lambda proj, lat, lon, **k: {"cri_global_max": 0.974, "warning_level": "위험"})

    rep = pb.run_bridge_pipeline(37.3219, 127.1083, out_dir=str(tmp_path), mode="full")
    byname = {s.step: s for s in rep.stages}
    assert byname["⑨PS/DS(교량30m)"].status == "done"
    assert "229" in byname["⑨PS/DS(교량30m)"].detail
    assert byname["⑫PINN→FRAM"].status == "done"
    assert "0.974" in byname["⑫PINN→FRAM"].detail
    assert rep.context["ps_ds"]["n_ps"] == 66
    assert rep.context["pinn"]["cri_max"] == 0.974


def test_pipeline_full_do_adi(monkeypatch, tmp_path):
    _patch_light(monkeypatch)
    from inframon.insar import snap_acquire as sa, snap_backend as sb

    class _Acq:
        slc_dir = str(tmp_path / "SLC")
    monkeypatch.setattr(sa, "acquire", lambda *a, **k: _Acq())
    _res = sb.SnapRunResult("20240107", sb.BurstLoc("IW2", 1, 5.4, 37.34, 127.13, contained=True),
                            [sb.SnapPairResult("20240107", "20240119", "p.tif", True)])
    # 실제 SNAP 은 track_h5 를 만들고 끝난다 — 파일이 없으면 엔진 계약 위반으로 막힌다
    # (⑧이 산출물 없이 '성공'을 주장하면 하류가 조용히 깨지므로 processing_engine 이 검사).
    (tmp_path / "t.h5").write_bytes(b"x")
    _res.track_h5 = str(tmp_path / "t.h5"); _res.weather = None
    monkeypatch.setattr(sb, "run", lambda *a, **k: _res)
    monkeypatch.setattr(sb, "platform_heading", lambda *a, **k: -13.1)
    monkeypatch.setattr(sb, "scene_date", lambda s: "20240107")
    (tmp_path / "SLC").mkdir()
    (tmp_path / "SLC" / "S1A_IW_SLC__1SDV_20240107T093202_x.zip").write_text("x")
    amp_called = {}
    monkeypatch.setattr(sb, "amplitude_pairs",
                        lambda *a, **k: amp_called.setdefault("v", ["amp1.tif", "amp2.tif"]))
    got = {}

    def rec_ps_ds(*a, **k):
        got["amp_pairs"] = k.get("amp_pairs")
        return {"n_points": 229, "n_ps": 35, "n_ds": 194, "buffer_m": 30.0,
                "class_method": "ADI<0.25", "deck_dist_max_m": 30.0, "coh_mean": 0.6, "out": "x"}
    monkeypatch.setattr(sb, "build_bridge_track_ps_ds", rec_ps_ds)
    monkeypatch.setattr("inframon.contracts.io.ProjectStore", _DummyStore)
    monkeypatch.setattr("inframon.insar.track_reader.import_track_h5", lambda s, h, **k: None)
    monkeypatch.setattr("inframon.custom_pinn.run_custom_pinn",
                        lambda p, la, lo, **k: {"cri_global_max": 0.9, "warning_level": "위험"})

    rep = pb.run_bridge_pipeline(37.32, 127.10, out_dir=str(tmp_path), mode="full", do_adi=True)
    assert amp_called.get("v") == ["amp1.tif", "amp2.tif"]     # 진폭쌍 실행됨
    assert got["amp_pairs"] == ["amp1.tif", "amp2.tif"]        # ADI 로 전달
    byname = {s.step: s for s in rep.stages}
    assert "ADI<0.25" in byname["⑨PS/DS(교량30m)"].detail


def test_pipeline_summary_renders(monkeypatch):
    _patch_light(monkeypatch)
    rep = pb.run_bridge_pipeline(37.3219, 127.1083, mode="plan")
    txt = rep.summary()
    assert "표준 교량" in txt and "①교량선정" in txt and "③ROI도심지가중" in txt


# ── 실행 기록(provenance) — 성공·실패 모두 남는가 ──
def test_pipeline_writes_report_json(tmp_path, monkeypatch):
    """산출물 옆에 '무엇을 어떤 인자로 돌렸나' 가 남아야 나중에 재현할 수 있다."""
    import json

    import inframon.pipeline_bridge as pb

    for mod, fn in (("inframon.insar.osm_bridge", "confirm_bridge"),
                    ("inframon.insar.roi_selection", "select_roi"),
                    ("inframon.insar.snap_acquire", "search_frames")):
        monkeypatch.setattr(f"{mod}.{fn}",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("off")),
                            raising=False)
    pb.run_bridge_pipeline(37.0, 127.0, out_dir=tmp_path, mode="plan", engine="hyp3")

    rec = json.loads((tmp_path / "pipeline_report.json").read_text(encoding="utf-8"))
    assert rec["target"] == {"lat": 37.0, "lon": 127.0}
    assert rec["args"]["engine"] == "hyp3" and rec["args"]["mode"] == "plan"
    assert any(s["step"].startswith("⑧") for s in rec["stages"])
    assert "git_commit" in rec                       # 어느 코드에서 나온 산출인가


def test_report_json_written_even_when_stage_raises(tmp_path, monkeypatch):
    """실패한 실행일수록 기록이 필요하다 — 예외가 나도 파일은 남는다."""
    import inframon.pipeline_bridge as pb

    for mod, fn in (("inframon.insar.osm_bridge", "confirm_bridge"),
                    ("inframon.insar.roi_selection", "select_roi"),
                    ("inframon.insar.snap_acquire", "search_frames")):
        monkeypatch.setattr(f"{mod}.{fn}",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("off")),
                            raising=False)
    monkeypatch.setattr(pb, "_run_heavy",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        pb.run_bridge_pipeline(37.0, 127.0, out_dir=tmp_path, mode="full")
    assert (tmp_path / "pipeline_report.json").exists()


def test_report_json_survives_unserializable_context(tmp_path, monkeypatch):
    """ndarray 같은 값이 섞여도 기록이 통째로 실패하지 않는다."""
    import json

    import numpy as np

    import inframon.pipeline_bridge as pb

    rep = pb.PipelineReport(lat=1.0, lon=2.0)
    rep.context["roi_bbox"] = np.arange(4, dtype=float)
    rep.context["big"] = np.zeros((100, 100))
    rep.write_json(tmp_path / "r.json")
    rec = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert rec["context"]["roi_bbox"] == [0.0, 1.0, 2.0, 3.0]
    assert "ndarray" in rec["context"]["big"]        # 큰 배열은 요약만
