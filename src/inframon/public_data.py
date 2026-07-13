"""공공데이터포털(data.go.kr) 실데이터 프리셋 — 교량 제원·교통량을 "키만" 으로.

기존 bridge_info.fetch_from_data_go_kr / traffic.fetch_traffic_series 는 엔드포인트·필드맵을
매번 지정해야 하는 generic 이라, **실제 데이터셋 프리셋**을 여기 정의해 turnkey 로 만든다.

주요 데이터셋:
  · 전국교량표준데이터 (data.go.kr/15028196) — 교량명·연장·폭·차로수·**상부구조형식**·
    준공년도·위경도·시설물종류(종별). PINN 제원(형식→재료·단면, 연장→종별, 차로수→활하중).
  · 한국도로공사 실시간 전국 교통량 (data.go.kr/15076822) / ITS(its.go.kr/opendata).

⚠️ 오픈API 는 회원가입·활용신청 후 발급되는 serviceKey 가 필요하고, 데이터셋마다 응답
키가 달라 후보 키 다중 매칭으로 방어적 파싱한다. 실제 응답 키는 각 데이터셋 문서에서
확인해 field_candidates 를 보정할 수 있다.
"""

from __future__ import annotations

from .structure import BridgeProfile

# data.go.kr 데이터셋 프리셋(문서 URL·후보 응답키). serviceKey 는 사용자 발급.
DATASETS = {
    "national_bridge_standard": {
        "id": "15028196",
        "portal": "https://www.data.go.kr/data/15028196/standard.do",
        "desc": "전국교량표준데이터(국토교통부) — 연장·폭·차로수·상부구조형식·준공년도·위경도·종별",
        "fields": {
            "name": ["교량명", "BRDG_NM", "bridgeName", "fcltyNm"],
            "length_m": ["교량길이", "교량연장", "BRDG_LT", "brdgLt", "lt"],
            "width_m": ["교량폭", "BRDG_WDT", "brdgWdth", "wdt"],
            "lanes": ["차로수", "LANE_CNT", "laneCnt", "차선수"],
            "structure": ["상부구조형식", "상부형식", "UPPER_STRCT_KND", "upperStructKnd", "형식"],
            "completion": ["준공일자", "준공년도", "CNSTR_YMD", "cnstrYmd", "준공"],
            "lat": ["위도", "LAT", "latitude", "yCrdnt"],
            "lon": ["경도", "LOT", "longitude", "xCrdnt"],
            "facility_kind": ["시설물종류", "FCLTY_KND", "시설물종류코드", "fcltyKnd"],
        },
    },
    "korex_traffic": {
        "id": "15076822",
        "portal": "https://www.data.go.kr/data/15076822/openapi.do",
        "desc": "한국도로공사 실시간 전국 교통량 — 시간별 교통량(차종별)",
        "fields": {"date": ["stdDate", "일자", "collectDate", "trafficDate"],
                   "count": ["trafficAmout", "trafficAmount", "교통량", "sumTraffic"]},
    },
}

# 한글 상부구조형식 → inframon 형식(bridge_type) 매핑(부분일치)
_KO_STRUCTURE = [
    ("현수", "suspension"), ("사장", "cable_stayed"), ("아치", "arch"), ("트러스", "truss"),
    ("박스", "box_girder"), ("box", "box_girder"), ("라멘", "rahmen"), ("강결", "rahmen"),
    ("거더", "girder"), ("슬래브", "girder"), ("판형", "girder"), ("i형", "girder"),
]
# 형식명에 PSC/콘크리트/강 표기가 있으면 재료 직접 결정
_KO_MATERIAL = [
    ("psc", "prestressed_concrete"), ("피에스씨", "prestressed_concrete"),
    ("prestressed", "prestressed_concrete"),
    ("rc", "reinforced_concrete"), ("철근콘크리트", "reinforced_concrete"),
    ("콘크리트", "reinforced_concrete"),
    ("강교", "steel"), ("스틸", "steel"), ("steel", "steel"), ("강상", "steel"),
    ("강", "steel"),                    # 상부구조형식의 '강박스·강거더·강판' 등(콘크리트 키워드가 먼저 소진돼 안전)
]


def _num(v):
    try:
        return float(str(v).replace(",", "").split()[0])
    except (ValueError, IndexError, AttributeError):
        return None


def _pick(record: dict, candidates: list):
    for k in candidates:
        if k in record and record[k] not in (None, ""):
            return record[k]
    return None


def parse_structure_ko(text: str) -> tuple[str, str | None]:
    """한글 상부구조형식 문자열 → (bridge_type, material|None). 예 'PSC박스거더'→(box_girder, PSC)."""
    s = (text or "").lower().replace(" ", "")
    btype = next((t for kw, t in _KO_STRUCTURE if kw in s), "girder")
    mat = next((m for kw, m in _KO_MATERIAL if kw in s), None)
    return btype, mat


def bridge_profile_from_record(record: dict) -> BridgeProfile:
    """전국교량표준데이터 레코드 → BridgeProfile(형식·재료·연장·폭·차로수). 실제 제원 기반."""
    from .structure import infer_structural_defaults
    from .insar.bridge_meta import max_span_estimate

    f = DATASETS["national_bridge_standard"]["fields"]
    struct_raw = _pick(record, f["structure"]) or ""
    btype, mat_ko = parse_structure_ko(str(struct_raw))
    length_m = _num(_pick(record, f["length_m"]))
    width_m = _num(_pick(record, f["width_m"]))
    lanes = _num(_pick(record, f["lanes"]))
    if width_m is None and lanes:
        width_m = round(lanes * 3.5 + 1.0, 1)                # 차로수 → 폭 추정
    span = max_span_estimate(btype, length_m)
    inf = infer_structural_defaults(btype, has_material_tag=mat_ko is not None,
                                    length_m=length_m, max_span_m=span)
    return BridgeProfile(
        name=_pick(record, f["name"]),
        bridge_type=btype,
        material=mat_ko or inf.get("material", "steel"),
        length_m=length_m, width_m=width_m,
        section_depth_m=inf.get("section_depth_m", 1.0),
        load_per_len=inf.get("load_per_len", 1.0e4),
        boundary=inf.get("boundary", "simply_supported"),
        source="data_go_kr:전국교량표준데이터",
        extra={"osm_structure": str(struct_raw), "lanes": lanes, "max_span_m": span,
               "facility_kind": _pick(record, f["facility_kind"]),
               "completion": _pick(record, f["completion"]),
               "dataset": DATASETS["national_bridge_standard"]["id"]},
    )


def describe_datasets() -> str:
    """프리셋 데이터셋 안내(키 발급용 포털 URL 포함)."""
    lines = ["공공데이터포털(data.go.kr) 실데이터 프리셋 — serviceKey 발급 후 사용:"]
    for name, d in DATASETS.items():
        lines.append(f"  · {name} [{d['id']}] {d['desc']}")
        lines.append(f"      신청: {d['portal']}")
    return "\n".join(lines)
