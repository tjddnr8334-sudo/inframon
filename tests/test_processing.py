"""InSAR 처리 파이프라인(F) 통합 — demo 모드 end-to-end + real plan."""

from __future__ import annotations

import numpy as np

from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import FRAMOutput, InSAROutput
from inframon.insar import processing
from inframon.insar.recipe import (
    BridgeTarget,
    TrackSelection,
    save_bridge_target,
    save_track_selection,
)


def _seed_recipe(d):
    save_bridge_target(d / "bridge_target.json", BridgeTarget(
        name="정자교", selected_lat=37.3667, selected_lon=127.1075,
        osm_type="way", osm_id=1, bbox=(127.108, 37.368, 127.110, 37.369)))
    save_track_selection(d / "track_selection.json", TrackSelection(
        flight_direction="ASCENDING", path=127, frame=115, n_scenes=5,
        first_date="20230112", last_date="20230305",
        scene_dates=["20230112", "20230124", "20230205", "20230217", "20230305"]))


def test_synthesize_track_h5_in_bbox(tmp_path):
    _seed_recipe(tmp_path)
    from inframon.insar.track_reader import read_track_h5

    out = processing.synthesize_track_h5(tmp_path, tmp_path / "t.h5", n_points=120, seed=1)
    td = read_track_h5(out)
    assert td.lonlat.shape == (120, 2)
    assert td.los.shape == (120, 5)
    # 점이 레시피 bbox 안
    assert (td.lonlat[:, 0] >= 127.108).all() and (td.lonlat[:, 0] <= 127.110).all()
    assert (td.lonlat[:, 1] >= 37.368).all() and (td.lonlat[:, 1] <= 37.369).all()
    assert td.date_labels.astype(str).tolist()[0] == "20230112"


def test_run_demo_full_pipeline(tmp_path):
    _seed_recipe(tmp_path)
    proj = tmp_path / "project.h5"
    fram = processing.run_demo(tmp_path, proj, n_points=80, seed=2)

    assert isinstance(fram, FRAMOutput)
    assert fram.n_points == 80 and fram.n_dates == 5
    assert 0.0 <= fram.cri_global_max <= 1.0
    # /insar·/pinn·/fram 계약 모두 채워짐
    with ProjectStore(proj, mode="r") as store:
        insar = store.read_meta("insar", InSAROutput)
        cri = store.read_array(fram.CRI_ds)
    assert insar.n_points == 80
    assert cri.shape == (80, 5)
    assert np.isfinite(cri).all()


def test_run_demo_insar_only(tmp_path):
    _seed_recipe(tmp_path)
    out = processing.run_demo(tmp_path, tmp_path / "p.h5", n_points=40, full_pipeline=False)
    assert isinstance(out, InSAROutput) and out.n_points == 40


def test_plan_real_has_stages(tmp_path):
    plan = processing.plan_real("rec", "wrk", isce_stack="/x/topsStack")
    joined = "\n".join(plan)
    for step in ("10_download", "20_stack_isce", "30_miaplpy", "40_sarvey",
                 "50_export_to_inframon", "import-track-h5"):
        assert step in joined
    assert "/x/topsStack" in joined


def test_run_dispatch(tmp_path):
    _seed_recipe(tmp_path)
    # demo 는 실행, real 은 plan(list)
    assert isinstance(processing.run(tmp_path, mode="real"), list)
    fram = processing.run(tmp_path, mode="demo", project_h5=tmp_path / "p.h5", n_points=30)
    assert isinstance(fram, FRAMOutput)
