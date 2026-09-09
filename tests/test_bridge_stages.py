"""단계별 실행(run_bridge_stage) — 대시보드 ①~④ 탭의 '▶ 이 단계 실행'.

⓪ 전체 실행(_run_heavy)과 같은 _stage_* 함수를 하나씩 부른다. 앞 단계 산출이 없으면
실행하지 않고 어느 단계를 먼저 하라고 적는다. FRAM 만 다시 내기(run_custom_fram)는
/pinn 에 남긴 fram_cfg 를 써서 ⓪ 과 같은 경보차등을 낸다.
"""

from __future__ import annotations

import pytest

import inframon.pipeline_bridge as pb
from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.orchestrator.pipeline import run_pipeline


@pytest.fixture
def out(tmp_path):
    """stub 파이프라인으로 /insar·/pinn·/fram 이 있는 project.h5 를 <out>/pipeline 자리에."""
    run_pipeline(str(tmp_path / "project.h5"), PipelineConfig(n_points=40, n_dates=12))
    return tmp_path


def test_잘못된_stage_는_ValueError(tmp_path):
    with pytest.raises(ValueError, match="stage"):
        pb.run_bridge_stage("nope", 37.0, 127.0, out_dir=tmp_path)


def test_앞_단계가_없으면_실행하지_않고_먼저_할_단계를_적는다(tmp_path):
    rep = pb.run_bridge_stage("pinn", 37.0, 127.0, out_dir=tmp_path)
    assert [s.status for s in rep.stages] == ["skip"]
    assert "① InSAR" in rep.stages[0].detail
    rep = pb.run_bridge_stage("fram", 37.0, 127.0, out_dir=tmp_path)
    assert rep.stages[0].status == "skip" and "② PINN" in rep.stages[0].detail
    rep = pb.run_bridge_stage("twin", 37.0, 127.0, out_dir=tmp_path)
    assert all(s.status == "skip" for s in rep.stages)
    # 기록은 성공·실패 무관하게 남는다
    assert (tmp_path / "pipeline_report_twin.json").exists()
    assert (tmp_path / "pipeline_context.json").exists()


def test_FRAM_단계는_pinn_위에_CRI만_다시_낸다(out):
    rep = pb.run_bridge_stage("fram", 37.0, 127.0, out_dir=out)
    s = {x.step: x for x in rep.stages}["⑫FRAM(CRI)"]
    assert s.status == "done", s.detail
    assert "CRI" in s.detail and "경보" in s.detail
    assert rep.context["pinn"]["cri_max"] is not None
    with ProjectStore(str(out / "project.h5"), mode="r") as store:
        assert store.has_meta("fram")


def test_잔존수명_단계는_insar_위에_life_를_쓴다(out):
    rep = pb.run_bridge_stage("life", 37.0, 127.0, out_dir=out)
    s = {x.step: x for x in rep.stages}["잔존수명"]
    assert s.status == "done", s.detail
    with ProjectStore(str(out / "project.h5"), mode="r") as store:
        assert store.has_meta("life")


def test_run_custom_fram_은_pinn_없으면_ValueError(tmp_path):
    from inframon.custom_pinn import run_custom_fram
    p = tmp_path / "p.h5"
    with ProjectStore(str(p), mode="w"):
        pass
    with pytest.raises(ValueError, match="pinn"):
        run_custom_fram(p)


def test_run_custom_fram_은_저장된_fram_cfg_를_쓴다(out):
    """/pinn 의 fram_cfg(점검등급 등)가 있으면 그것으로 — ⓪ 전체 실행과 같은 경보차등."""
    from inframon.custom_pinn import run_custom_fram
    proj = str(out / "project.h5")
    with ProjectStore(proj, mode="a") as store:
        store.write_json_attr("pinn", "fram_cfg", {"bridge_inspect_grade": "E", "fram_mode": "real",
                                                   "reference_range": True})
    seen = {}
    import inframon.fram.real_engine as re_

    orig = re_.run_fram_real

    def spy(store, insar, pinn, cfg):
        seen["grade"] = getattr(cfg, "bridge_inspect_grade", None)
        return orig(store, insar, pinn, cfg)

    import unittest.mock as um
    with um.patch.object(re_, "run_fram_real", spy):
        res = run_custom_fram(proj)
    assert seen["grade"] == "E"
    assert res["fram_mode"] == "real" and res["cri_global_max"] >= 0


def test_전체_실행은_단계_함수를_순서대로_부른다(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(pb, "_stage_insar", lambda *a, **k: (calls.append("insar"), "deck.h5")[1])
    monkeypatch.setattr(pb, "_stage_import", lambda *a, **k: (calls.append("import"), "p.h5")[1])
    monkeypatch.setattr(pb, "_stage_pinn", lambda *a, **k: calls.append("pinn"))
    monkeypatch.setattr(pb, "_twin_and_register", lambda *a, **k: calls.append("twin"))
    rep = pb.PipelineReport(lat=1.0, lon=2.0)
    pb._run_heavy(rep, rep.context, 1.0, 2.0, tmp_path, None, 8)
    assert calls == ["insar", "import", "pinn", "twin"]


def test_전체_실행은_InSAR_실패면_뒤_단계를_부르지_않는다(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(pb, "_stage_insar", lambda *a, **k: None)
    monkeypatch.setattr(pb, "_stage_import", lambda *a, **k: calls.append("import"))
    monkeypatch.setattr(pb, "_twin_and_register", lambda *a, **k: calls.append("twin"))
    rep = pb.PipelineReport(lat=1.0, lon=2.0)
    pb._run_heavy(rep, rep.context, 1.0, 2.0, tmp_path, None, 8)
    assert calls == []
