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
        # 국토교통부 전국교량표준데이터. API=15028196 / 파일(CSV,무료)=15081953. 35,593행.
        # 컬럼명은 실 데이터셋(2025) 확인 기준 + generic 후보 병행.
        "id": "15081953",
        "portal": "https://www.data.go.kr/data/15081953/fileData.do",
        "desc": "전국교량표준데이터(국토교통부, CSV 무료) — 연장·폭·차로수·상부구조형식·준공연도·위경도·종별등급",
        "fields": {
            "name": ["교량명", "BRDG_NM", "bridgeName", "fcltyNm"],
            "length_m": ["교량연장", "교량길이", "연장", "BRDG_LT", "brdgLt", "lt"],
            "width_m": ["교량폭", "폭", "BRDG_WDT", "brdgWdth", "wdt"],
            "lanes": ["차로수", "차선수", "LANE_CNT", "laneCnt"],
            "structure": ["상부구조형식", "상부형식", "형식", "UPPER_STRCT_KND", "upperStructKnd"],
            "completion": ["교량준공연도", "준공연도", "준공일자", "CNSTR_YMD", "준공"],
            # 실 데이터셋(15081953) 컬럼은 '교량시작점위도/경도'. generic 후보도 병행.
            "lat": ["교량시작점위도", "위도", "위도시점", "시점위도", "시작지점위도", "LAT", "yCrdnt"],
            "lon": ["교량시작점경도", "경도", "경도시점", "시점경도", "시작지점경도", "LOT", "xCrdnt"],
            "grade": ["시설물종별등급구분", "시설물종별", "종별등급", "FCLTY_GRD", "안전등급"],
            "design_load": ["설계활하중", "설계하중", "DSGN_LOAD"],       # DB-24 등 한국 설계활하중
            "inspect_grade": ["최종안전점검결과", "안전점검결과", "안전등급", "INSP_GRD"],  # A~E
            "height_m": ["교량높이", "높이", "HGHT"],
            "facility_kind": ["시설물종류", "FCLTY_KND", "fcltyKnd"],
        },
    },
    "korex_traffic": {
        # 한국도로공사 EX OpenAPI(LINK형). 인증키는 data.ex.co.kr 에서 별도 발급(data.go.kr 아님).
        # apiId=0617 일자별 전국 교통량 — sumDate 당 전국 집계(차종·TCS/hipass 분해). 시간변조용.
        "id": "15062049",
        "apiId": "0617",
        "portal": "https://www.data.go.kr/data/15062049/openapi.do",
        "ex_portal": "https://data.ex.co.kr/openapi/basicinfo/openApiInfoM?apiId=0617",
        "endpoint": "https://data.ex.co.kr/openapi/trafficapi/nationalTrafficVolumn",
        "desc": "한국도로공사 일자별 전국 교통량(EX API) — sumDate 당 전국 집계, 일자별 시간변조",
        "request": {"key": "발급키", "type": "json", "sumDate": "YYYYMMDD"},
        "fields": {"date": ["sumDate"], "count": ["trafficVolumn"]},
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


# 시설물종별등급구분 숫자코드(실 데이터셋) → 종별. 99=기타(제3종 미만 소규모).
_GRADE_CODE = {"01": "1종", "02": "2종", "03": "3종", "99": "기타", "00": "기타"}


def normalize_grade(v) -> str | None:
    """시설물종별등급구분 → '1종'|'2종'|'3종'|'기타'|None.

    실 데이터셋은 '01'/'02'/'03'/'99' 숫자코드, generic 은 '제1종시설물' 문자열 — 둘 다 처리.
    """
    s = str(v or "").strip()
    if s in _GRADE_CODE:
        return _GRADE_CODE[s]
    for g in ("1종", "2종", "3종"):
        if g in s:
            return g
    return "기타" if "기타" in s else None


# 한국 설계활하중(DB/DL/KL 등급) → 자중 대비 활하중 배율(DB-24 등가등분포 ≈ 차로당 12.7kN/m 기준=1.0)
_DESIGN_LOAD_FACTOR = {
    "db-24": 1.0, "db-18": 0.75, "db-13.5": 0.5625, "db-9": 0.375,
    "dl-24": 1.0, "kl-510": 1.10,          # KL-510(2015~) ≈ DB-24 초과
}


def design_load_factor(v) -> float | None:
    """설계활하중 문자열('DB-24','KL-510'…) → 차로당 활하중 배율. 미상/기타/None → None."""
    s = str(v or "").strip().lower().replace(" ", "")
    if not s or s in ("미상", "기타"):
        return None
    for k, f in _DESIGN_LOAD_FACTOR.items():
        if s.startswith(k):
            return f
    return None


def default_bridge_csv(data_dir: str = "data") -> str | None:
    """data/ 에 받아둔 전국교량표준데이터 CSV 자동탐색(최신). 없으면 None."""
    import glob
    import os
    hits = sorted(glob.glob(os.path.join(data_dir, "national_bridge_standard*.csv")))
    return hits[-1] if hits else None


def load_bridges_csv(path, *, encoding: str | None = None) -> list[dict]:
    """전국교량표준데이터 CSV(15081953) → 레코드 리스트. 한글 인코딩(utf-8/cp949) 자동감지."""
    import csv
    encs = [encoding] if encoding else ["utf-8-sig", "cp949", "euc-kr", "utf-8"]
    for enc in encs:
        try:
            with open(path, newline="", encoding=enc) as f:
                rows = list(csv.DictReader(f))
            if rows:
                return rows
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(f"CSV 를 읽지 못했습니다(인코딩 확인): {path}")


def nearest_bridge_profile(csv_path, lat: float, lon: float, *,
                           max_km: float = 1.0) -> BridgeProfile | None:
    """전국교량표준데이터 CSV 에서 (lat,lon) 최근접 교량 → BridgeProfile. 없으면 None."""
    import math
    f = DATASETS["national_bridge_standard"]["fields"]
    best, bestd = None, float("inf")
    for r in load_bridges_csv(csv_path):
        rlat = _num(_pick(r, f["lat"])); rlon = _num(_pick(r, f["lon"]))
        if rlat is None or rlon is None:
            continue
        d = math.hypot((rlat - lat), (rlon - lon) * math.cos(math.radians(lat))) * 111000.0
        if d < bestd:
            bestd, best = d, r
    if best is None or bestd > max_km * 1000.0:
        return None
    prof = bridge_profile_from_record(best)
    prof.extra["match_dist_m"] = round(bestd, 1)
    return prof


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
    # ⚠️ 교량높이(표준데이터)는 형하공간·교각높이 성격 → 거더 단면높이와 다름. 참고값으로만 저장하고
    #    단면높이는 형식별 스팬비(infer_structural_defaults)로 추정한다.
    height_m = _num(_pick(record, f.get("height_m", [])))
    design_load = _pick(record, f.get("design_load", []))
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
               "grade": normalize_grade(_pick(record, f["grade"])),   # 공식 종별등급
               "design_load": design_load,                            # 설계활하중 원문(DB-24)
               "design_load_factor": design_load_factor(design_load), # 활하중 배율
               "inspect_grade": _pick(record, f.get("inspect_grade", [])),  # 최종안전점검 A~E
               "height_m": height_m,
               "dataset": DATASETS["national_bridge_standard"]["id"]},
    )


def describe_datasets() -> str:
    """프리셋 데이터셋 안내(키 발급용 포털 URL 포함)."""
    lines = ["공공데이터포털(data.go.kr) 실데이터 프리셋 — serviceKey 발급 후 사용:"]
    for name, d in DATASETS.items():
        lines.append(f"  · {name} [{d['id']}] {d['desc']}")
        lines.append(f"      신청: {d['portal']}")
    return "\n".join(lines)
