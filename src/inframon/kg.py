"""project.h5 → 지식그래프(Knowledge Graph) 내보내기 — KG/VLM 확장점.

KAIA 흐름의 마지막 소비단(**텍스트→지식그래프→보수보강 추천**)이 그대로 삼킬 수 있도록,
InSAR·PINN(가상센싱 전체변위)·FRAM 산출을 **타입 있는 속성 그래프**(nodes/edges)로
구조화한다. 시방서 RAG·그래프 추론·보수보강은 타 팀(VLM) 파트 — 우리는 자기기술적
온톨로지와 함께 그래프를 산출하고, 위험지표(CRI/경보)는 *참고용 내부 물리지표*로 면책한다.

`build_graph(summary)` 는 `vlm_package.build_summary()` 다이제스트를 재사용하는 순수함수라
프로젝트 파일 없이도 테스트·재구성 가능하다. 산출:
  graph  = {schema, ontology, provenance, disclaimer, nodes[], edges[]}
  triples= [(subject, predicate, object), ...]  (RDF 스타일 KG 적재용)
  jsonld = @context + @graph                     (JSON-LD 소비자용)

확장(다른 KG 스키마/저장소)은 이 그래프를 어댑터로 변환해 붙이면 된다 — 온톨로지가
노드/엣지 타입·단위를 자기기술하므로 소비단이 추측하지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .api.transform import WGS84
from .contracts.io import ProjectStore
from .vlm_package import RISK_NOTE, UNITS, build_summary

SCHEMA = "inframon.kg/1.0"

# 온톨로지 — 노드/엣지 타입 카탈로그(자기기술). VLM/KG 팀이 스키마를 추측하지 않게.
ONTOLOGY: dict[str, Any] = {
    "node_types": {
        "Bridge": "모니터링 대상 교량(루트).",
        "Member": "부재 유형 롤업(deck/pier/abutment/bearing).",
        "MeasurementPoint": "InSAR 측점(강반사 산란체). 핫스팟만 노드화.",
        "StructuralParameter": "PINN 역산 구조지표(EI·고유진동수·저강성).",
        "VirtualSensingField": "PINN 가상센싱 전체 변위장(상부거더 1D + 상판 2D).",
        "RiskSignal": "FRAM 내부 물리 위험지표(CRI/경보) — 시방서 판정 아님(참고용).",
    },
    "edge_types": {
        "HAS_MEMBER": "Bridge→Member.",
        "HAS_HOTSPOT": "Bridge→MeasurementPoint(변위/위험 상위).",
        "LOCATED_ON": "MeasurementPoint→Member(소속 부재).",
        "HAS_STRUCTURAL_PARAM": "Bridge→StructuralParameter.",
        "LOW_STIFFNESS_AT": "StructuralParameter→MeasurementPoint(손상 의심 저강성).",
        "HAS_VIRTUAL_SENSING": "Bridge→VirtualSensingField.",
        "HAS_RISK_SIGNAL": "Bridge→RiskSignal(참고용).",
    },
    "units": UNITS,
}

# JSON-LD @context — 소비자가 속성/타입을 IRI 로 매핑할 수 있게(로컬 vocab).
JSONLD_CONTEXT = {
    "@vocab": "https://inframon.local/kg#",
    "id": "@id",
    "type": "@type",
}


def _bid(bridge_id: str) -> str:
    return bridge_id or "unknown"


def build_graph(summary: dict[str, Any], *, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    """VLM 다이제스트(summary) → 타입 있는 속성 그래프(nodes/edges).

    순수함수 — project.h5 없이 summary dict 만으로 그래프를 구성한다.
    """
    bid = _bid(summary.get("bridge_id", ""))
    bridge_uid = f"bridge:{bid}"
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def add_node(uid: str, ntype: str, props: dict[str, Any]) -> str:
        nodes.append({"id": uid, "type": ntype,
                      "props": {k: v for k, v in props.items() if v is not None}})
        return uid

    def add_edge(src: str, etype: str, dst: str, props: dict[str, Any] | None = None) -> None:
        e: dict[str, Any] = {"source": src, "type": etype, "target": dst}
        if props:
            e["props"] = props
        edges.append(e)

    # Bridge (루트)
    obs = summary.get("observation", {})
    add_node(bridge_uid, "Bridge", {
        "bridge_id": bid,
        "date_first": obs.get("date_first"), "date_last": obs.get("date_last"),
        "n_points": obs.get("n_points"), "n_dates": obs.get("n_dates"),
        "channels_present": summary.get("channels_present"),
    })

    # Member 롤업
    for m in summary.get("members", []):
        uid = f"member:{bid}:{m['member']}"
        add_node(uid, "Member", {
            "member": m["member"], "n_points": m.get("n_points"),
            "max_abs_disp_mm": m.get("max_abs_disp_mm"), "mean_cri": m.get("mean_cri"),
        })
        add_edge(bridge_uid, "HAS_MEMBER", uid)

    # 핫스팟 측점(salient points)
    for h in summary.get("settlement_hotspots", []):
        uid = f"point:{bid}:{h['point_id']}"
        add_node(uid, "MeasurementPoint", {
            "point_id": h["point_id"], "member": h.get("member"),
            "lat": h.get("lat"), "lon": h.get("lon"),
            "cumulative_disp_mm": h.get("cumulative_disp_mm"),
            "rate_mm_per_yr": h.get("rate_mm_per_yr"),
            "cri_latest": h.get("cri_latest"), "channel": h.get("channel"),
        })
        add_edge(bridge_uid, "HAS_HOTSPOT", uid)
        if h.get("member"):
            add_edge(uid, "LOCATED_ON", f"member:{bid}:{h['member']}")

    # PINN 구조지표
    pinn = summary.get("pinn")
    if pinn:
        uid = f"param:{bid}:structural"
        add_node(uid, "StructuralParameter", {
            "EI_Nm2": pinn.get("EI_Nm2"),
            "natural_frequencies_hz": pinn.get("natural_frequencies_hz"),
            "low_stiffness_points": pinn.get("low_stiffness_points"),
        })
        add_edge(bridge_uid, "HAS_STRUCTURAL_PARAM", uid)
        for pt in pinn.get("low_stiffness_points", []):
            add_edge(uid, "LOW_STIFFNESS_AT", f"point:{bid}:{pt}")

    # PINN 가상센싱 전체 변위장
    vs = summary.get("virtual_sensing")
    if vs:
        uid = f"vsensing:{bid}"
        add_node(uid, "VirtualSensingField", {
            "girder": vs.get("girder"), "deck": vs.get("deck"),
            "note": "관측점(희소) 없는 위치까지 PINN 이 추정한 교량 상부 전체 변위장.",
        })
        add_edge(bridge_uid, "HAS_VIRTUAL_SENSING", uid)

    # 위험 참고지표(면책)
    rr = summary.get("risk_reference")
    if rr:
        uid = f"risk:{bid}"
        add_node(uid, "RiskSignal", {
            "cri_global_max": rr.get("cri_global_max"),
            "warning_level": rr.get("warning_level"),
            "critical_members": rr.get("critical_members"),
            "function_states": rr.get("function_states"),
            "lead_time_forecast_days": rr.get("lead_time_forecast_days"),
            "is_code_judgment": False, "note": rr.get("note", RISK_NOTE),
        })
        add_edge(bridge_uid, "HAS_RISK_SIGNAL", uid)

    return {
        "schema": SCHEMA,
        "ontology": ONTOLOGY,
        "provenance": provenance or {"producer": "inframon (InSAR + PINN + FRAM)",
                                     "root": bridge_uid},
        "disclaimer": RISK_NOTE,
        "nodes": nodes,
        "edges": edges,
    }


def graph_to_triples(graph: dict[str, Any]) -> list[list[Any]]:
    """그래프 → (subject, predicate, object) 트리플 목록(RDF 스타일 적재).

    각 노드: (id, rdf:type, type) + 스칼라 속성 (id, prop, value). 중첩 dict/list 속성은
    JSON 문자열로 직렬화(리터럴). 각 엣지: (source, edge_type, target).
    """
    triples: list[list[Any]] = []
    for n in graph.get("nodes", []):
        triples.append([n["id"], "rdf:type", n["type"]])
        for k, v in n.get("props", {}).items():
            obj = v if isinstance(v, (str, int, float, bool)) else json.dumps(v, ensure_ascii=False)
            triples.append([n["id"], k, obj])
    for e in graph.get("edges", []):
        triples.append([e["source"], e["type"], e["target"]])
    return triples


def graph_to_jsonld(graph: dict[str, Any]) -> dict[str, Any]:
    """그래프 → JSON-LD(@context + @graph). 노드는 개체, 엣지는 참조 속성으로 편입."""
    by_id = {n["id"]: {"id": n["id"], "type": n["type"], **n.get("props", {})}
             for n in graph.get("nodes", [])}
    for e in graph.get("edges", []):
        node = by_id.get(e["source"])
        if node is None:
            continue
        node.setdefault(e["type"], []).append({"id": e["target"]})
    return {"@context": JSONLD_CONTEXT, "@graph": list(by_id.values())}


def build_graph_from_store(store: ProjectStore, *, bridge_id: str = "",
                           to_crs: str = WGS84) -> dict[str, Any]:
    """project.h5 → 그래프(build_summary 다이제스트 경유)."""
    summary = build_summary(store, bridge_id=bridge_id, to_crs=to_crs)
    return build_graph(summary)


def export_kg(h5_path: str | Path, out_path: str | Path, *, bridge_id: str = "",
              to_crs: str = WGS84, with_triples: bool = True,
              with_jsonld: bool = True) -> dict[str, Any]:
    """project.h5 → 지식그래프 JSON(+선택 triples/JSON-LD 사이드카). 요약 dict 반환."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with ProjectStore(Path(h5_path), mode="r") as store:
        graph = build_graph_from_store(store, bridge_id=bridge_id, to_crs=to_crs)

    out_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    written = [str(out_path)]
    if with_triples:
        tp = out_path.with_suffix(".triples.json")
        tp.write_text(json.dumps(graph_to_triples(graph), ensure_ascii=False, indent=2),
                      encoding="utf-8")
        written.append(str(tp))
    if with_jsonld:
        jp = out_path.with_suffix(".jsonld")
        jp.write_text(json.dumps(graph_to_jsonld(graph), ensure_ascii=False, indent=2),
                      encoding="utf-8")
        written.append(str(jp))
    return {"graph": str(out_path), "files": written,
            "n_nodes": len(graph["nodes"]), "n_edges": len(graph["edges"])}
