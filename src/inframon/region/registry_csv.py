"""전국교량표준데이터 CSV → 시도·시군구 교량 목록.

``public_data`` 의 컬럼 후보(``DATASETS['national_bridge_standard']['fields']``)와
``load_bridges_csv``·``normalize_grade`` 를 그대로 쓴다 — 컬럼 해석을 두 곳에 두지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..public_data import DATASETS, _num, _pick, load_bridges_csv, normalize_grade

_F = DATASETS["national_bridge_standard"]["fields"]


@dataclass
class RegionBridge:
    """대장 교량 한 개. ``geometry`` 는 [(lat, lon) 시점, (lat, lon) 종점]."""
    bridge_id: str
    name: str
    sido: str
    sigungu: str
    lat: float
    lon: float
    geometry: list = field(default_factory=list)
    length_m: float | None = None
    width_m: float | None = None
    structure: str | None = None
    facility_class: str | None = None       # '1종'|'2종'|'3종'|'기타'
    completion: str | None = None
    inspect_grade: str | None = None        # 최종안전점검결과 A~E
    inspect_date: str | None = None
    manager: str | None = None
    manager_tel: str | None = None


def _grade(v) -> str | None:
    """실 CSV(2025)는 종별을 '1','2','3','99' 로 준다 — ``normalize_grade`` 는 '01' 형식을 기대하므로 0 채움 후 위임."""
    s = str(v or "").strip()
    return normalize_grade(s.zfill(2) if s.isdigit() else s)


def _bid(rec: dict, i: int, name: str, lat: float, lon: float) -> str:
    """교량 ID. 대장에 관리번호 컬럼이 없어 이름+좌표 해시를 쓴다(행 순서가 바뀌어도 안정)."""
    import hashlib
    return "b" + hashlib.sha1(f"{name}|{lat:.5f}|{lon:.5f}".encode("utf-8")).hexdigest()[:10]


def bridges_in_sigungu(csv_path, *, sido: str, sigungu: str | list | None = None,
                       sigungu_code: str | None = None) -> list[RegionBridge]:
    """시도(부분일치) + 시군구(정확일치, 여러 개 가능) 또는 시군구코드로 교량을 고른다.

    시점 좌표가 없는 행은 위치를 알 수 없으므로 **제외**한다(조용히 0,0 으로 두지 않는다).
    """
    rows = load_bridges_csv(csv_path)
    want = None if sigungu is None else ({sigungu} if isinstance(sigungu, str) else set(sigungu))
    out: list[RegionBridge] = []
    for i, r in enumerate(rows):
        if sido not in str(_pick(r, _F["sido"]) or ""):
            continue
        sg = str(_pick(r, _F["sigungu"]) or "")
        if want is not None and sg not in want:
            continue
        if sigungu_code and str(_pick(r, _F["sigungu_code"]) or "") != str(sigungu_code):
            continue
        la0, lo0 = _num(_pick(r, _F["lat"])), _num(_pick(r, _F["lon"]))
        if la0 is None or lo0 is None:
            continue
        la1, lo1 = _num(_pick(r, _F["lat_end"])), _num(_pick(r, _F["lon_end"]))
        if la1 is None or lo1 is None:
            la1, lo1 = la0, lo0
        name = str(_pick(r, _F["name"]) or "")
        out.append(RegionBridge(
            bridge_id=_bid(r, i, name, la0, lo0), name=name, sido=str(_pick(r, _F["sido"]) or ""), sigungu=sg,
            lat=(la0 + la1) / 2, lon=(lo0 + lo1) / 2, geometry=[(la0, lo0), (la1, lo1)],
            length_m=_num(_pick(r, _F["length_m"])), width_m=_num(_pick(r, _F["width_m"])),
            structure=_pick(r, _F["structure"]), facility_class=_grade(_pick(r, _F["grade"])),
            completion=_pick(r, _F["completion"]), inspect_grade=_pick(r, _F["inspect_grade"]),
            inspect_date=_pick(r, _F["inspect_date"]), manager=_pick(r, _F["manager"]),
            manager_tel=_pick(r, _F["manager_tel"])))
    return out
