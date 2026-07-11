"""표준 교량 파이프라인 오케스트레이터 — 순서·상태 보고(경량단계 모킹)."""

from __future__ import annotations

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


def test_pipeline_summary_renders(monkeypatch):
    _patch_light(monkeypatch)
    rep = pb.run_bridge_pipeline(37.3219, 127.1083, mode="plan")
    txt = rep.summary()
    assert "표준 교량" in txt and "①교량선정" in txt and "③ROI도심지가중" in txt
