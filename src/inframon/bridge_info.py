"""공공 교량 정보 → BridgeProfile (맞춤형 PINN 입력 자동 구성).

두 출처:
  1. **OSM**(키 불필요) — `bridge:structure`/`material`/`width`/길이 태그를 프로파일로.
  2. **공공데이터포털 data.go.kr 교량 제원 API**(서비스키 필요) — 형식·연장·폭·재료·
     준공 등을 받아 프로파일로. data.go.kr 은 교량 데이터셋이 여러 개라 엔드포인트·응답
     스키마가 데이터셋마다 다르므로, 키와 엔드포인트를 받아 **방어적으로** 파싱한다.

`fetch_bridge_profile(lat, lon, ...)`: 키 있으면 data.go.kr 우선, 없으면 OSM, 둘 다 실패
하면 강재 거더교 기본값. 결과는 `--engine pinn=real` 의 `cfg.bridge_profile` 로 넣으면
해당 교량 제원으로 PINN 이 돈다.
"""

from __future__ import annotations

from typing import Any

from .structure import BridgeProfile

# OSM bridge:structure → inframon 교량 형식
_OSM_STRUCTURE = {
    "beam": "girder", "girder": "girder",
    "box_girder": "box_girder", "box-girder": "box_girder", "box": "box_girder",
    "rahmen": "rahmen", "frame": "rahmen", "rigid_frame": "rahmen",
    "arch": "arch", "suspension": "suspension",
    "cable-stayed": "cable_stayed", "cablestayed": "cable_stayed",
    "truss": "truss", "cantilever": "girder",
}
# OSM/한글 재료 → inframon 재료
_MATERIAL = {
    "steel": "steel", "강": "steel", "강교": "steel",
    "concrete": "concrete", "콘크리트": "concrete",
    "reinforced_concrete": "reinforced_concrete", "rc": "reinforced_concrete",
    "prestressed_concrete": "prestressed_concrete", "psc": "prestressed_concrete",
    "철근콘크리트": "reinforced_concrete",
}


def _num(v: Any) -> float | None:
    try:
        return float(str(v).split()[0].replace(",", ""))
    except (TypeError, ValueError, IndexError):
        return None


def profile_from_osm(bridge: Any) -> BridgeProfile:
    """OSM Bridge(태그·길이) → BridgeProfile. 미상 항목은 기본값."""
    tags = getattr(bridge, "tags", {}) or {}
    struct = (tags.get("bridge:structure") or tags.get("bridge") or "").lower()
    material_raw = (tags.get("material") or "").lower()
    btype = _OSM_STRUCTURE.get(struct, "girder")
    length_m = (getattr(bridge, "length_m", 0.0) or None) or _num(tags.get("length"))
    # 형식·경간으로 재료(태그 없을 때)·단면높이·경계·자중 추론 — 강재 고정 가정 해소
    from .insar.bridge_meta import max_span_estimate
    from .structure import infer_structural_defaults
    span = max_span_estimate(btype, length_m)
    inferred = infer_structural_defaults(btype, has_material_tag=bool(material_raw),
                                         length_m=length_m, max_span_m=span)
    material = _MATERIAL.get(material_raw) if material_raw else inferred.get("material", "steel")
    return BridgeProfile(
        name=getattr(bridge, "name", None),
        bridge_type=btype, material=material or "steel",
        length_m=length_m, width_m=_num(tags.get("width")),
        section_depth_m=inferred.get("section_depth_m", 1.0),
        load_per_len=inferred.get("load_per_len", 1.0e4),
        boundary=inferred.get("boundary", "simply_supported"),
        source="osm",
        extra={"osm_structure": struct or "?", "max_span_m": span,
               "osm": getattr(bridge, "osm_url", None)},
    )


def fetch_from_data_go_kr(
    service_key: str,
    *,
    endpoint: str,
    params: dict[str, str] | None = None,
    field_map: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> BridgeProfile | None:
    """data.go.kr 교량 제원 API 를 호출해 BridgeProfile 로 파싱(방어적).

    data.go.kr 데이터셋마다 응답 키가 다르므로 `field_map`(응답키→프로파일필드)을 받아
    매핑한다. 예: {"brdgTypeNm":"bridge_type","brdgLt":"length_m","brdgWdth":"width_m"}.
    JSON 응답을 가정하며, 항목을 못 찾으면 None 항목으로 두고 가능한 만큼 채운다.
    네트워크/파싱 실패 시 None (호출측 폴백).
    """
    import json
    import urllib.parse
    import urllib.request

    q = {"serviceKey": service_key, "type": "json", "_type": "json", **(params or {})}
    url = endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — 사용자 지정 공공 API
            data = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — 진단/폴백, 네트워크·스키마 오류 흡수
        return None

    item = _first_item(data)
    if not isinstance(item, dict):
        return None
    fm = field_map or {}
    out: dict[str, Any] = {"source": "data_go_kr", "extra": {"raw": item}}
    for resp_key, prof_field in fm.items():
        if resp_key in item and item[resp_key] not in (None, ""):
            val = item[resp_key]
            if prof_field in ("length_m", "width_m", "youngs_modulus", "load_per_len"):
                val = _num(val)
            if prof_field == "bridge_type":
                val = _OSM_STRUCTURE.get(str(val).lower(), str(val))
            if prof_field == "material":
                val = _MATERIAL.get(str(val).lower(), "steel")
            out[prof_field] = val
    try:
        return BridgeProfile.model_validate(out)
    except Exception:  # noqa: BLE001
        return None


def _first_item(data: Any) -> Any:
    """data.go.kr 흔한 중첩(response.body.items.item[0])에서 첫 레코드를 끄집어낸다."""
    node = data
    for key in ("response", "body", "items", "item"):
        if isinstance(node, dict) and key in node:
            node = node[key]
    if isinstance(node, list):
        return node[0] if node else None
    return node


def fetch_bridge_profile(
    lat: float,
    lon: float,
    *,
    name: str | None = None,
    radius_m: float = 200.0,
    data_go_kr_key: str | None = None,
    data_go_kr_endpoint: str | None = None,
    data_go_kr_params: dict[str, str] | None = None,
    data_go_kr_field_map: dict[str, str] | None = None,
) -> BridgeProfile:
    """위치의 교량 제원을 모아 BridgeProfile 반환. data.go.kr(키) 우선 → OSM → 기본값."""
    if data_go_kr_key and data_go_kr_endpoint:
        prof = fetch_from_data_go_kr(
            data_go_kr_key, endpoint=data_go_kr_endpoint, params=data_go_kr_params,
            field_map=data_go_kr_field_map)
        if prof is not None:
            return prof
    try:
        from .insar.osm_bridge import find_bridges_near
        bridges = find_bridges_near(lat, lon, radius_m)
        if bridges:
            return profile_from_osm(bridges[0])
    except Exception:  # noqa: BLE001 — OSM 실패해도 기본값으로
        pass
    return BridgeProfile(name=name, source="default")
