"""지식그래프(KG) 내보내기 — 타입 그래프·트리플·JSON-LD·가상센싱·VLM 패키지 번들."""

from __future__ import annotations

import json

from inframon.config import PipelineConfig
from inframon.kg import (
    ONTOLOGY,
    SCHEMA,
    build_graph,
    export_kg,
    graph_to_jsonld,
    graph_to_triples,
)
from inframon.orchestrator.pipeline import run_pipeline
from inframon.vlm_package import RISK_NOTE


def _summary(with_vs=True):
    s = {
        "schema": "inframon.vlm_package/1.0", "bridge_id": "B1",
        "observation": {"date_first": "2024-01-01", "date_last": "2024-06-01",
                        "n_dates": 6, "n_points": 10},
        "displacement": {"los_mm": {"min": -5, "max": 5, "mean": 0}},
        "settlement_hotspots": [
            {"point_id": 3, "member": "pier", "lat": 37.1, "lon": 127.2,
             "cumulative_disp_mm": -12.0, "rate_mm_per_yr": -8.0,
             "cri_latest": 0.7, "channel": "vertical"},
            {"point_id": 5, "member": "deck", "lat": 37.2, "lon": 127.3,
             "cumulative_disp_mm": -3.0, "rate_mm_per_yr": -1.0,
             "cri_latest": 0.2, "channel": "vertical"},
        ],
        "members": [
            {"member": "deck", "n_points": 6, "max_abs_disp_mm": 5.0, "mean_cri": 0.2},
            {"member": "pier", "n_points": 4, "max_abs_disp_mm": 12.0, "mean_cri": 0.5},
        ],
        "pinn": {"EI_Nm2": {"min": 1e8, "median": 5e8, "max": 1e9},
                 "low_stiffness_points": [3], "natural_frequencies_hz": [3.2, 8.1]},
        "virtual_sensing": None,
        "risk_reference": {"note": RISK_NOTE, "cri_global_max": 0.8, "warning_level": "주의",
                           "critical_members": ["pier"], "function_states": {},
                           "lead_time_forecast_days": None},
        "channels_present": {"vertical_fused": True, "pinn": True, "fram_cri": True,
                             "virtual_sensing": with_vs},
    }
    if with_vs:
        s["virtual_sensing"] = {
            "girder": {"n_virtual": 200, "span_m": 80.0, "peak_total_mm": 15.0,
                       "peak_l_from_fixed_m": 40.0, "vertical_separated": True},
            "deck": {"n_deck": 540, "n_long": 60, "n_trans": 9, "footprint_m": [80.0, 10.0],
                     "peak_total_mm": 16.0, "peak_xy": [37.15, 127.25], "peak_date_index": 5},
        }
    return s


def _types(graph):
    return {n["type"] for n in graph["nodes"]}


def test_build_graph_structure():
    g = build_graph(_summary())
    assert g["schema"] == SCHEMA
    assert g["disclaimer"] == RISK_NOTE
    t = _types(g)
    assert {"Bridge", "Member", "MeasurementPoint", "StructuralParameter",
            "VirtualSensingField", "RiskSignal"} <= t
    # 루트 Bridge 유일
    assert sum(n["type"] == "Bridge" for n in g["nodes"]) == 1
    etypes = {e["type"] for e in g["edges"]}
    assert {"HAS_MEMBER", "HAS_HOTSPOT", "LOCATED_ON", "HAS_STRUCTURAL_PARAM",
            "LOW_STIFFNESS_AT", "HAS_VIRTUAL_SENSING", "HAS_RISK_SIGNAL"} <= etypes


def test_edges_reference_existing_nodes():
    g = build_graph(_summary())
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["source"] in ids
        assert e["target"] in ids, e   # LOCATED_ON/LOW_STIFFNESS_AT 대상도 노드로 존재


def test_low_stiffness_links_hotspot_point():
    g = build_graph(_summary())
    # StructuralParameter --LOW_STIFFNESS_AT--> point:B1:3 (핫스팟이자 저강성)
    low = [e for e in g["edges"] if e["type"] == "LOW_STIFFNESS_AT"]
    assert any(e["target"] == "point:B1:3" for e in low)


def test_virtual_sensing_node_carries_girder_and_deck():
    g = build_graph(_summary(with_vs=True))
    vsn = [n for n in g["nodes"] if n["type"] == "VirtualSensingField"]
    assert len(vsn) == 1
    props = vsn[0]["props"]
    assert props["girder"]["peak_total_mm"] == 15.0
    assert props["deck"]["n_deck"] == 540


def test_no_virtual_sensing_when_absent():
    g = build_graph(_summary(with_vs=False))
    assert "VirtualSensingField" not in _types(g)
    assert not any(e["type"] == "HAS_VIRTUAL_SENSING" for e in g["edges"])


def test_triples_include_types_and_edges():
    g = build_graph(_summary())
    tr = graph_to_triples(g)
    assert ["bridge:B1", "rdf:type", "Bridge"] in tr
    # 엣지도 트리플로
    assert any(s == "bridge:B1" and p == "HAS_MEMBER" for s, p, o in tr)
    # 중첩 속성은 JSON 리터럴로 직렬화(문자열)
    assert all(isinstance(o, (str, int, float, bool)) for _, _, o in tr)


def test_jsonld_has_context_and_graph():
    g = build_graph(_summary())
    ld = graph_to_jsonld(g)
    assert "@context" in ld and "@graph" in ld
    bridge = [x for x in ld["@graph"] if x["type"] == "Bridge"][0]
    assert bridge["id"] == "bridge:B1"
    assert "HAS_MEMBER" in bridge          # 엣지가 참조 속성으로 편입


def test_ontology_declares_all_used_types():
    g = build_graph(_summary())
    for n in g["nodes"]:
        assert n["type"] in ONTOLOGY["node_types"]
    for e in g["edges"]:
        assert e["type"] in ONTOLOGY["edge_types"]


def test_export_kg_writes_graph_and_sidecars(tmp_path):
    cfg = PipelineConfig(n_points=12, n_dates=6,
                         engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    out = tmp_path / "p.h5"
    run_pipeline(out, cfg)
    r = export_kg(out, tmp_path / "kg.json", bridge_id="B1")
    assert (tmp_path / "kg.json").exists()
    assert (tmp_path / "kg.triples.json").exists()
    assert (tmp_path / "kg.jsonld").exists()
    assert r["n_nodes"] > 0 and r["n_edges"] > 0
    graph = json.loads((tmp_path / "kg.json").read_text(encoding="utf-8"))
    assert "Bridge" in {n["type"] for n in graph["nodes"]}
