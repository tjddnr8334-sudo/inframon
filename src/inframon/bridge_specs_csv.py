"""교량 제원 CSV — **파일에 있는 실측을 추정보다 먼저 쓴다**.

전국교량표준데이터(15081953)에는 **경간수·최대경간이 없다.** 그래서 지금까지 최대경간을
`max_span_estimate`(연장×형식별 비율)로 추정했고, 그 값이 광안대교에서 5,565m(실제 약
500m)로 나왔다. 그런데 KOTSA 매칭 제원 CSV 처럼 **경간수·최대경간을 실측으로 담은 파일**이
있으면 추정할 이유가 없다.

이 모듈은 그런 CSV 를 읽어 좌표·이름으로 찾아 준다. 형식을 하나로 강제하지 않는다:

  · 좌표와 제원이 한 파일에 있는 경우 — 그대로 쓴다
  · 좌표 파일 + 제원 파일이 **키(seq_no 등)로 나뉜 경우** — 조인해서 쓴다
    (예: bridges_load.csv[name·lat·lon] + bridges_specs.csv[n_spans·max_span_m])

컬럼 이름은 한글·영문 후보를 모두 본다. 없는 항목은 **채우지 않는다** — 지어낸 값보다
"없음"이 낫다. 어디서 온 값인지는 `source_file` 로 남긴다.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 필드 후보(앞이 우선). 실측 컬럼만 다룬다 — 추정은 여기서 하지 않는다.
FIELDS: dict[str, list[str]] = {
    "key": ["seq_no", "id", "bridge_id", "관리번호", "일련번호"],
    "name": ["name", "교량명", "bridge_name", "fcltyNm"],
    "lat": ["lat", "위도", "교량시작점위도", "y", "latitude"],
    "lon": ["lon", "경도", "교량시작점경도", "x", "longitude"],
    "lat_end": ["lat_end", "교량종료점위도", "종점위도", "종료지점위도"],
    "lon_end": ["lon_end", "교량종료점경도", "종점경도", "종료지점경도"],
    "n_spans": ["n_spans", "경간수", "span_count", "경간"],
    "length_m": ["length_m", "교량연장", "연장", "length"],
    "max_span_m": ["max_span_m", "최대경간", "최대경간장", "경간장", "max_span"],
    "lanes": ["lane_count", "차로수", "lanes"],
    "width_m": ["width_m", "교량폭", "폭"],
    "structure": ["kotsa_format", "structure", "상부구조형식", "형식"],
    "material": ["material", "재료"],
    "addr": ["addr_sigungu", "소재지지번주소", "주소", "addr"],
}
KEY_FIELDS = ("n_spans", "max_span_m", "length_m", "lanes", "width_m", "structure")


@dataclass
class BridgeSpec:
    """CSV 에서 **읽은 그대로**의 제원(없는 값은 None — 추정하지 않는다)."""

    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    lat_end: float | None = None
    lon_end: float | None = None
    n_spans: int | None = None
    length_m: float | None = None
    max_span_m: float | None = None
    lanes: int | None = None
    width_m: float | None = None
    structure_raw: str | None = None
    material: str | None = None
    addr: str | None = None
    dist_m: float | None = None
    source_file: str | None = None
    extra: dict = field(default_factory=dict)

    def measured(self) -> dict[str, Any]:
        """실제로 값이 있는 항목만 — 하류가 '있는 것만' 골라 쓰게."""
        out = {k: getattr(self, k) for k in
               ("n_spans", "length_m", "max_span_m", "lanes", "width_m")}
        return {k: v for k, v in out.items() if v is not None}

    def describe(self) -> str:
        bits = []
        if self.n_spans:
            bits.append(f"{self.n_spans}경간")
        if self.max_span_m:
            bits.append(f"최대경간 {self.max_span_m:g}m")
        if self.length_m:
            bits.append(f"연장 {self.length_m:g}m")
        if self.lanes:
            bits.append(f"{self.lanes}차로")
        if self.structure_raw:
            bits.append(str(self.structure_raw))
        return " · ".join(bits) or "제원 없음"


def _pick(row: dict, names: list[str]):
    for n in names:
        if n in row and str(row[n]).strip() not in ("", "-"):
            return str(row[n]).strip()
    return None


def _num(v):
    try:
        return float(str(v).replace(",", "").split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def load_specs(*paths: str | Path) -> list[BridgeSpec]:
    """CSV 여러 개를 읽어 제원 목록으로. 좌표 파일과 제원 파일은 키로 조인한다.

    같은 항목이 여러 파일에 있으면 **후보 이름 우선순위**로 고른다 — 예컨대 형식은
    `kotsa_format`(PC슬래브교 등 구체적)이 `structure`(girder 로 뭉뚱그림)보다 앞선다.
    """
    tables: list[tuple[Path, list[dict]]] = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        try:
            with open(p, encoding="utf-8-sig", errors="replace", newline="") as f:
                tables.append((p, list(csv.DictReader(f))))
        except (OSError, csv.Error):
            continue
    if not tables:
        return []

    merged: dict[str, dict] = {}          # key -> {field: (rank, value, file)}
    keyless: list[dict] = []
    for path, rows in tables:
        for r in rows:
            got: dict[str, tuple[int, str, str]] = {}
            for fname, cands in FIELDS.items():
                for rank, c in enumerate(cands):
                    if c in r and str(r[c]).strip() not in ("", "-"):
                        got[fname] = (rank, str(r[c]).strip(), path.name)
                        break
            k = got.get("key", (0, None, ""))[1]
            if k is None:
                if got.get("lat"):
                    keyless.append(got)
                continue
            slot = merged.setdefault(k, {})
            for fname, val in got.items():
                cur = slot.get(fname)
                if cur is None or val[0] < cur[0]:      # 더 앞선 후보 이름이 이긴다
                    slot[fname] = val

    def _build(slot: dict) -> BridgeSpec | None:
        g = {k: v[1] for k, v in slot.items()}
        lat, lon = _num(g.get("lat")), _num(g.get("lon"))
        if lat is None or lon is None:
            return None
        files = sorted({v[2] for v in slot.values()})
        return BridgeSpec(
            name=g.get("name"), lat=lat, lon=lon,
            lat_end=_num(g.get("lat_end")), lon_end=_num(g.get("lon_end")),
            n_spans=_int(g.get("n_spans")), length_m=_num(g.get("length_m")),
            max_span_m=_num(g.get("max_span_m")), lanes=_int(g.get("lanes")),
            width_m=_num(g.get("width_m")), structure_raw=g.get("structure"),
            material=g.get("material"), addr=g.get("addr"),
            source_file=", ".join(files), extra={"key": g.get("key")})

    out = [b for b in (_build(sl) for sl in merged.values()) if b is not None]
    out += [b for b in (_build(sl) for sl in keyless) if b is not None]
    return out


def find_spec_csvs(root: str | Path = "data") -> list[Path]:
    """제원 CSV 후보를 찾는다 — 이름 규칙이 아니라 **컬럼으로** 판단한다."""
    root = Path(root)
    if not root.exists():
        return []
    found = []
    for p in sorted(root.glob("*.csv")):
        try:
            with open(p, encoding="utf-8-sig", errors="replace", newline="") as f:
                header = next(csv.reader(f), [])
        except (OSError, csv.Error, StopIteration):
            continue
        cols = {c.strip() for c in header}
        has_key = any(c in cols for c in FIELDS["key"])
        has_spec = any(c in cols for k in KEY_FIELDS for c in FIELDS[k])
        has_pos = any(c in cols for c in FIELDS["lat"])
        if has_spec and (has_pos or has_key):
            found.append(p)
    return found


# 본교가 아닌 부속 구조물. 한 교량이 램프·접속교·연결로로 여러 줄 올라와 있고, 본교보다
# 가까운 줄이 흔하다 — 월드컵대교(주경간 855 m 사장교)에서 267 m 거리의
# '월드컵대교 북단연결로 RAMP-E교'(352 m)가 잡혀 데크선이 북단 접속부 357 m 로 갔다.
_AUX = ("램프", "RAMP", "U턴", "IC", "접속교", "연결로", "고가차도", "측도")


def _is_aux(spec_name: str, want: str) -> bool:
    """부속 구조물 이름인가 — 조회 이름 자체에 그 낱말이 있으면 부속이 아니다."""
    return any(k in spec_name and k not in want for k in _AUX)


def nearest_spec(specs: list[BridgeSpec], lat: float, lon: float, *,
                 name: str | None = None, max_km: float = 0.3,
                 name_max_km: float = 5.0) -> BridgeSpec | None:
    """좌표(+이름)로 제원을 찾는다. 이름이 맞으면 좀 더 멀어도 받아들인다.

    이름이 맞는 줄이 여럿이면 **가장 가까운 것이 아니라 본교**를 고른다 — 램프·접속교를
    빼고 남은 것 중 **가장 긴 줄**이 본교다. 제원(연장·폭)이 하류의 데크선 선택 기준이라,
    여기서 램프가 잡히면 교량 전체가 램프가 된다.

    연장이 사실상 같은 줄이 여럿이면(같은 교량이 여러 CSV 에 있다) **제원이 더 채워진
    줄**을 고른다. 한강대교가 그랬다 — 표준데이터 841.0 m 줄과 파트너 840.9 m 줄이
    같은 다리인데, '가장 긴 줄' 규칙이 0.1 m 차이로 최대경간장이 **빈** 쪽을 골라
    아치 상부구조를 세우지 못했다.

    이름이 안 맞아 좌표로만 찾을 때는 **램프·접속교를 건너뛴다**. 샛강문화다리가
    그랬다 — 136 m 옆 '여의중류램프교'(강박스거더 234 m)를 제원으로 삼아 보행교가
    박스거더교가 됐다. 본교가 없으면 제원 없음으로 두는 편이 낫다.
    """
    want = (name or "").strip()
    k = math.cos(math.radians(lat))
    hits: list[tuple[float, BridgeSpec]] = []
    near: list[tuple[float, BridgeSpec]] = []
    for s in specs:
        d = math.hypot((s.lat - lat), (s.lon - lon) * k) * 111_320.0
        if d <= max_km * 1000.0:
            near.append((d, s))
        if want and s.name and d <= name_max_km * 1000.0:
            n = s.name.strip()
            if n in want or want in n:
                hits.append((d, s))
    if hits:
        main = [(d, s) for d, s in hits if not _is_aux(s.name.strip(), want)]
        pool = main or hits
        # 본교 = 연장이 가장 긴 줄. 연장이 없는 줄뿐이면 가장 가까운 줄.
        if any(s.length_m for _, s in pool):
            longest = max((s.length_m or 0.0) for _, s in pool)
            tie = [(d, s) for d, s in pool
                   if (s.length_m or 0.0) >= longest * 0.99]      # 1 % 안이면 같은 다리
            d, s = max(tie, key=lambda ds: (_filled(ds[1]), ds[1].length_m or 0.0))
        else:
            d, s = min(pool, key=lambda ds: ds[0])
        s.dist_m = round(d, 1)
        return s
    solo = [(d, s) for d, s in near
            if not (s.name and _is_aux(s.name.strip(), want))]
    if solo:
        d, s = min(solo, key=lambda ds: ds[0])
        s.dist_m = round(d, 1)
        return s
    return None


def spec_candidates(specs: list[BridgeSpec], lat: float, lon: float, *,
                    name: str | None = None, name_max_km: float = 5.0,
                    tie: float = 0.02) -> list[BridgeSpec]:
    """이름이 맞는 **본교** 후보 중 연장이 사실상 같은 것들.

    나란히 놓인 상·하류 교량은 연장이 같고 형식이 다르다(행주대교 — 하류 사장교
    1460 m · 상류교 PSC박스거더 1460 m). 제원 단계에서는 어느 쪽인지 가릴 근거가
    없으니, 데크선이 정해진 뒤 **데크선에 가까운 쪽**을 고르라고 후보를 그대로 준다.
    """
    want = (name or "").strip()
    if not want:
        return []
    k = math.cos(math.radians(lat))
    hits: list[tuple[float, BridgeSpec]] = []
    for s in specs:
        if not s.name:
            continue
        d = math.hypot((s.lat - lat), (s.lon - lon) * k) * 111_320.0
        if d > name_max_km * 1000.0:
            continue
        n = s.name.strip()
        if (n in want or want in n) and not _is_aux(n, want):
            hits.append((d, s))
    if not hits:
        return []
    longest = max((s.length_m or 0.0) for _, s in hits)
    if longest <= 0:
        return []
    out = []
    for d, s in hits:
        if (s.length_m or 0.0) >= longest * (1.0 - tie):
            s.dist_m = round(d, 1)
            out.append(s)
    return out


def _filled(s: BridgeSpec) -> int:
    """이 줄에 실측이 몇 항목이나 들어 있나 — 같은 다리면 채워진 쪽을 쓴다."""
    fields = ("n_spans", "max_span_m", "length_m", "lanes", "width_m", "structure_raw")
    return sum(1 for f in fields if getattr(s, f, None) not in (None, ""))


def lookup(lat: float, lon: float, *, name: str | None = None,
           paths: list[str | Path] | None = None, root: str | Path = "data",
           max_km: float = 0.3) -> BridgeSpec | None:
    """좌표(+이름) → 제원. 경로를 안 주면 `root` 에서 제원 CSV 를 찾아 쓴다."""
    files = [Path(p) for p in paths] if paths else find_spec_csvs(root)
    if not files:
        return None
    return nearest_spec(load_specs(*files), lat, lon, name=name, max_km=max_km)
