"""BIM 부재 연결·부재별 집계·정합 오케스트레이션."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from inframon.bim import Element, align_project_to_bim, associate, load_elements
from inframon.bim.elements import member_from_ifc_type
from inframon.bim.georef import AlignmentError, MapConversion
from inframon.bim.psets import aggregate_by_element, build_payload
from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import InSAROutput
from inframon.orchestrator.pipeline import run_pipeline


def _els() -> list[Element]:
    """교량 한 경간 — 상판 1 + 교각 2 + 교대 1 (IFC 로컬 좌표)."""
    return [
        Element("DECK1", "상판", "IfcSlab", bbox_min=(0, -5, 8), bbox_max=(100, 5, 9)),
        Element("PIER1", "교각1", "IfcColumn", bbox_min=(30, -2, 0), bbox_max=(34, 2, 8)),
        Element("PIER2", "교각2", "IfcColumn", bbox_min=(66, -2, 0), bbox_max=(70, 2, 8)),
        Element("ABUT1", "교대", "IfcFooting", bbox_min=(-4, -5, 0), bbox_max=(0, 5, 8)),
    ]


# ── 타입 추론 ──────────────────────────────────────────────────────────
def test_member_inference_from_ifc_type_and_korean_name():
    assert member_from_ifc_type("IfcSlab") == "deck"
    assert member_from_ifc_type("IfcColumn") == "pier"
    assert member_from_ifc_type("IfcFooting") == "abutment"
    assert member_from_ifc_type("IfcBearing") == "bearing"
    assert member_from_ifc_type("IfcBuildingElementProxy", "교각 P3") == "pier"
    assert member_from_ifc_type("IfcBuildingElementProxy", "무엇") is None


# ── 연결 ───────────────────────────────────────────────────────────────
def test_points_inside_bbox_attach_to_that_element():
    els = _els()
    pts = np.array([[50.0, 0.0], [32.0, 0.0], [68.0, 1.0], [-2.0, 0.0]])
    r = associate(pts, els, max_dist_m=5.0)
    # (50,0)/( -2,0) 은 한 부재에만, (32,0)/(68,1) 은 상판·교각 둘 다에 들어간다(2D 투영).
    assert list(r["guid"]) == ["DECK1", "PIER1", "PIER2", "ABUT1"]
    assert r["inside"].all()
    assert r["summary"]["n_assigned"] == 4


def test_overlap_ambiguity_is_broken_by_specificity_and_flagged():
    """2D 에선 상판 AABB 가 교각을 포함한다 — 배열 순서로 고르면 안 된다.

    라벨이 없으면 **더 작은(구체적인) 부재**를 고르고, 모호했다는 사실을 남긴다.
    """
    els = _els()
    r = associate(np.array([[32.0, 0.0], [50.0, 0.0]]), els, max_dist_m=5.0)
    assert r["guid"][0] == "PIER1"          # 상판(1000㎡)보다 교각(16㎡)이 특정적
    assert r["ambiguous"][0]                # 모호했음을 표시
    assert not r["ambiguous"][1]            # (50,0)은 상판에만 → 모호하지 않음
    assert r["summary"]["n_ambiguous"] == 1
    # 부재 배치 순서를 뒤집어도 결과가 같아야 한다(순서 의존성 없음)
    r2 = associate(np.array([[32.0, 0.0]]), list(reversed(els)), max_dist_m=5.0)
    assert r2["guid"][0] == "PIER1"


def test_insar_member_label_breaks_the_tie():
    """CV/InSAR 부재 라벨은 독립 증거다 — 모호할 때 그것을 우선한다."""
    from inframon.contracts.schema import MEMBER_TYPES
    els = _els()
    deck_i, pier_i = MEMBER_TYPES.index("deck"), MEMBER_TYPES.index("pier")
    pts = np.array([[32.0, 0.0], [32.0, 0.0]])
    r = associate(pts, els, member=np.array([deck_i, pier_i]), max_dist_m=5.0)
    assert list(r["guid"]) == ["DECK1", "PIER1"]
    assert r["summary"]["n_member_mismatch"] == 0      # 라벨을 따랐으니 불일치 없음


def test_far_points_are_left_unassigned():
    """억지로 붙이지 않는다 — 틀린 부재에 값을 넣는 것보다 미연결이 낫다."""
    r = associate(np.array([[500.0, 500.0], [50.0, 0.0]]), _els(), max_dist_m=5.0)
    assert r["guid"][0] == ""
    assert r["guid"][1] == "DECK1"
    assert r["summary"]["n_assigned"] == 1
    assert r["summary"]["assigned_fraction"] == 0.5


def test_nearest_element_within_max_dist():
    r = associate(np.array([[50.0, 7.0]]), _els(), max_dist_m=5.0, tol_m=0.0)
    assert r["guid"][0] == "DECK1"                    # bbox 표면에서 2m
    assert not r["inside"][0]
    assert r["distance_m"][0] == pytest.approx(2.0)


def test_member_mismatch_is_flagged_not_dropped():
    """InSAR 라벨과 BIM 타입이 어긋나도 값은 살리고 표시만 한다.

    (-2,0) 은 교대(ABUT1)에만 들어가므로 모호성이 없다 — 라벨이 deck 이면 순수한 불일치다.
    """
    from inframon.contracts.schema import MEMBER_TYPES
    deck_i = MEMBER_TYPES.index("deck")
    r = associate(np.array([[-2.0, 0.0]]), _els(), member=np.array([deck_i]), max_dist_m=5.0)
    assert r["guid"][0] == "ABUT1"                    # 연결은 유지
    assert r["member_mismatch"][0]
    assert r["summary"]["n_member_mismatch"] == 1


def test_elements_without_points_are_reported():
    r = associate(np.array([[50.0, 0.0]]), _els(), max_dist_m=5.0)
    assert set(r["summary"]["elements_without_points"]) == {"PIER1", "PIER2", "ABUT1"}


def test_3d_association_separates_deck_from_pier():
    """평면상 겹치는 상판/교각은 z 가 있어야 갈린다 — 수직기준면이 맞을 때만 유효."""
    els = _els()
    pts = np.array([[32.0, 0.0, 8.5], [32.0, 0.0, 3.0]])
    r2 = associate(pts, els, use_z=False, max_dist_m=5.0)
    r3 = associate(pts, els, use_z=True, max_dist_m=1.0, tol_m=0.1)
    assert r2["guid"][0] == r2["guid"][1]             # 2D 로는 구분 불가
    assert r3["guid"][0] == "DECK1" and r3["guid"][1] == "PIER1"


def test_empty_element_table_is_handled():
    r = associate(np.array([[1.0, 2.0]]), [])
    assert r["guid"][0] == "" and r["summary"]["n_assigned"] == 0


def test_load_elements_json_and_csv(tmp_path):
    j = tmp_path / "e.json"
    j.write_text(json.dumps({"elements": [
        {"guid": "A", "name": "상판", "ifc_type": "IfcSlab",
         "bbox_min": [0, 0, 0], "bbox_max": [10, 2, 1]}]}), encoding="utf-8")
    assert load_elements(j)[0].member == "deck"

    c = tmp_path / "e.csv"
    c.write_text("guid,name,ifc_type,xmin,ymin,zmin,xmax,ymax,zmax\n"
                 "B,교각,IfcColumn,0,0,0,2,2,8\n", encoding="utf-8")
    els = load_elements(c)
    assert els[0].guid == "B" and els[0].member == "pier"


def test_bbox_min_max_are_normalized():
    e = Element("X", bbox_min=(10, 10, 10), bbox_max=(0, 0, 0))
    assert e.bbox_min == (0.0, 0.0, 0.0) and e.bbox_max == (10.0, 10.0, 10.0)


# ── 집계·페이로드 ──────────────────────────────────────────────────────
def test_aggregate_uses_worst_case_for_risk_and_median_for_rate():
    guid = np.array(["A", "A", "A", "B"], dtype=object)
    r = aggregate_by_element(
        guid, velocity_mm_yr=np.array([-1.0, -1.2, -9.0, -0.1]),
        cri=np.array([0.2, 0.9, 0.3, 0.1]),
        rsl_lower_years=np.array([30.0, 4.0, np.inf, 80.0]))
    assert r["A"]["velocity_mm_yr"]["median"] == pytest.approx(-1.2)   # 이상치에 안 끌림
    assert r["A"]["velocity_mm_yr"]["max_abs"] == pytest.approx(9.0)   # 최악도 함께
    assert r["A"]["cri_max"] == pytest.approx(0.9)
    assert r["A"]["rsl_lower_years"] == pytest.approx(4.0)             # 가장 이른 값이 지배
    assert r["A"]["rsl_censored_fraction"] == pytest.approx(1 / 3, abs=1e-3)   # 소수 3자리 반올림
    assert r["B"]["sparse"] is True                                   # 점 1개


def test_aggregate_skips_unassigned_points():
    guid = np.array(["", "", "A"], dtype=object)
    r = aggregate_by_element(guid, velocity_mm_yr=np.array([1.0, 2.0, 3.0]))
    assert set(r) == {"A"} and r["A"]["n_points"] == 1


def test_payload_is_flat_scalars_and_keeps_source_link():
    per = aggregate_by_element(np.array(["A", "A"], dtype=object),
                               velocity_mm_yr=np.array([-2.0, -2.4]),
                               rsl_lower_years=np.array([12.0, 15.0]))
    pl = build_payload(per, project_h5="data/p.h5", alignment={}, association={})
    props = pl["elements"]["A"][pl["pset_name"]]
    assert all(not isinstance(v, (dict, list)) for v in props.values())   # IFC 는 스칼라만
    assert props["VelocityMedian_mm_per_yr"] == pytest.approx(-2.2)
    assert props["RemainingLifeLower_yr"] == pytest.approx(12.0)
    assert props["SourceProject"] == "data/p.h5"
    assert "시계열" in pl["provenance"]["note"]


# ── 오케스트레이션 (실 project.h5) ─────────────────────────────────────
@pytest.fixture()
def project(tmp_path):
    out = tmp_path / "project.h5"
    run_pipeline(str(out), PipelineConfig(n_points=60, n_dates=24))
    return str(out)


def _place_points_on_bridge(path: str, mc: MapConversion) -> None:
    """데모 점을 부재 위(로컬 좌표)로 옮기고 지도 CRS 로 되돌려 저장."""
    with ProjectStore(path) as s:
        ins = s.read_meta("insar", InSAROutput)
        xyz = s.read_array(ins.xyz_ds)
        n = xyz.shape[0]
        x = np.linspace(2.0, 98.0, n)
        local = np.column_stack([x, np.zeros(n), np.full(n, 8.5)])
        mapped = mc.to_map(local)
        s.write_array(ins.xyz_ds, np.column_stack([mapped[:, 0], mapped[:, 1], mapped[:, 2]]))


def test_align_requires_a_georeference_basis(project):
    with pytest.raises(AlignmentError, match="좌표 정합 근거가 없습니다"):
        align_project_to_bim(project, _els())


def test_align_end_to_end_with_map_conversion(project):
    t = math.radians(20.0)
    mc = MapConversion(eastings=200000.0, northings=550000.0, orthogonal_height=0.0,
                       x_axis_abscissa=math.cos(t), x_axis_ordinate=math.sin(t),
                       target_crs="EPSG:5186", source="ifc")
    _place_points_on_bridge(project, mc)
    r = align_project_to_bim(project, _els(), map_conversion=mc,
                             source_crs="EPSG:5186", max_dist_m=5.0)
    assert r["association"]["assigned_fraction"] == 1.0
    assert set(r["per_element"]) <= {"DECK1", "PIER1", "PIER2", "ABUT1"}
    assert "DECK1" in r["per_element"]
    props = r["payload"]["elements"]["DECK1"][r["payload"]["pset_name"]]
    assert props["PointCount"] > 0 and props["UpdatedAt"]


def test_align_via_control_points_when_no_map_conversion(project, tmp_path):
    """IfcMapConversion 이 없는 모델 — 측량 기준점으로 정합한다(국내 실무에서 흔함)."""
    t = math.radians(-11.0)
    truth = MapConversion(eastings=199000.0, northings=549000.0,
                          x_axis_abscissa=math.cos(t), x_axis_ordinate=math.sin(t),
                          target_crs="EPSG:5186")
    _place_points_on_bridge(project, truth)
    local = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 5.0]])
    cp = tmp_path / "cp.json"
    cp.write_text(json.dumps({"target_crs": "EPSG:5186", "points": [
        {"local": list(p), "map": list(truth.to_map(p[None, :])[0])} for p in local]}),
        encoding="utf-8")
    r = align_project_to_bim(project, _els(), control_points=str(cp), source_crs="EPSG:5186")
    assert r["alignment"]["source"] == "control_points"
    assert r["alignment"]["fit"]["rms_m"] < 1e-6
    assert r["association"]["assigned_fraction"] == 1.0


def test_align_warns_about_uncovered_elements_and_2d_only(project):
    t = math.radians(0.0)
    mc = MapConversion(eastings=0.0, northings=0.0, x_axis_abscissa=math.cos(t),
                       x_axis_ordinate=math.sin(t), target_crs="EPSG:5186", source="ifc")
    _place_points_on_bridge(project, mc)
    r = align_project_to_bim(project, _els(), map_conversion=mc, source_crs="EPSG:5186")
    joined = " ".join(r["warnings"])
    assert "관측점이 하나도 없는 부재" in joined      # 상판에만 점을 뒀으므로
    assert "'정상'이 아닙니다" in joined              # 관측 없음 ≠ 정상


def test_write_result_produces_two_jsons(project, tmp_path):
    from inframon.bim import write_result
    mc = MapConversion(target_crs="EPSG:5186", source="ifc")
    _place_points_on_bridge(project, mc)
    r = align_project_to_bim(project, _els(), map_conversion=mc, source_crs="EPSG:5186")
    paths = write_result(r, tmp_path / "bridge")
    state = json.loads(open(paths["elements_json"], encoding="utf-8").read())
    pay = json.loads(open(paths["pset_json"], encoding="utf-8").read())
    assert "elements" in state and "alignment" in state
    assert pay["n_elements"] == len(state["elements"])
