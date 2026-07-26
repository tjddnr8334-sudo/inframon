"""한국 측량 원점계 ↔ WGS84 — 좌표로 교량 위치를 지정한다.

이름 없는 중소형 교량은 OSM 검색으로 못 찾는다. 대신 설계도서·대장의 **측량 좌표**로
위치를 지정할 수 있어야 한다. 한국 실무는 GRS80 타원체 TM(2010년 표준) 4개 원점계를 쓴다.

    중부원점 EPSG:5186  경도원점 127°E  — 서울·경기·충청·전북·전남·경남 대부분(**가장 흔함**)
    서부원점 EPSG:5185  경도원점 125°E  — 인천·서해 도서
    동부원점 EPSG:5187  경도원점 129°E  — 강원 동부·경북 동부
    동해원점 EPSG:5188  경도원점 131°E  — 울릉·독도

⚠️ **원점계를 잘못 고르면 오류 없이 경도가 2°(약 180km)씩 틀린다.** 그런데 TM 은 경도원점만
다른 같은 수식이라, **좌표 한 쌍만으로는 어느 원점계인지 수학적으로 구분할 수 없다**
(같은 X,Y 를 중부로 읽으면 127°, 동부로 읽으면 129°가 나오고 둘 다 한국 안이다). 그래서:

  · 변환 결과가 한국 범위를 **명백히 벗어나면**(비정상 좌표·축 스왑) 잡아서 알린다.
  · 경도가 그 원점계 관할을 크게 벗어나면(±1.3°) 오인 가능성을 경고한다.
  · 하지만 인접 원점계 사이의 모호성은 못 잡는다 — **결과 위치를 지도 링크로 보여줘
    사용자가 눈으로 확인**하게 한다(locate 의 map_url). 원점계는 설계도서·대장에 명시돼
    있으니 그대로 고르는 게 원칙이다.

옛 좌표계(Bessel 타원체·EPSG:2097 등)는 GRS80 대비 수백 m 어긋난다. 최신 성과는 GRS80.
"""

from __future__ import annotations

from dataclasses import dataclass

# 표시명 → (EPSG, 경도원점, 관할 설명). GRS80 TM.
KOREA_ORIGINS: dict[str, tuple[str, float, str]] = {
    "중부원점": ("EPSG:5186", 127.0, "서울·경기·충청·전북·전남·경남 (127°E) — 가장 흔함"),
    "서부원점": ("EPSG:5185", 125.0, "인천·서해 도서 (125°E)"),
    "동부원점": ("EPSG:5187", 129.0, "강원 동부·경북 동부 (129°E)"),
    "동해원점": ("EPSG:5188", 131.0, "울릉·독도 (131°E)"),
}
# 별칭 — 사용자가 EPSG 코드나 영문으로 줄 수도 있다.
_ALIASES: dict[str, str] = {
    "5186": "중부원점", "epsg:5186": "중부원점", "중부": "중부원점", "central": "중부원점",
    "5185": "서부원점", "epsg:5185": "서부원점", "서부": "서부원점", "west": "서부원점",
    "5187": "동부원점", "epsg:5187": "동부원점", "동부": "동부원점", "east": "동부원점",
    "5188": "동해원점", "epsg:5188": "동해원점", "동해": "동해원점",
    # 흔한 오표기: TM 중부 = 5186
    "tm중부": "중부원점", "tm": "중부원점", "grs80중부": "중부원점",
}
# 한국 육지+영해 대략 범위(WGS84) — 변환 결과 검증용.
_KR_LON = (124.0, 132.0)
_KR_LAT = (33.0, 39.5)
# 원점계 관할 경도 폭. 원점계는 2° 간격(125·127·129·131)이라 경계는 원점 사이 중간(±1°).
# 실무 좌표는 관할 안에 있으므로 약간 여유를 둔 ±1.3° 밖이면 인접 원점계 오인으로 본다.
# (±1° 부근은 인접 원점계와 겹쳐 좌표만으로 100% 구분 불가 — locate 의 지도 링크로 확인.)
_ORIGIN_LON_SPAN = 1.3


def resolve_origin(name: str) -> str:
    """표시명/별칭/EPSG → 표준 표시명. 못 알아보면 ValueError."""
    key = str(name).strip().lower().replace(" ", "").replace("_", "")
    if key in _ALIASES:
        return _ALIASES[key]
    for disp in KOREA_ORIGINS:
        if disp.lower() == key:
            return disp
    raise ValueError(f"알 수 없는 원점계: {name!r}. "
                     f"쓸 수 있는 값: {', '.join(KOREA_ORIGINS)} 또는 EPSG:5185~5188")


def epsg_of(name: str) -> str:
    """표시명/별칭 → EPSG 코드 문자열."""
    return KOREA_ORIGINS[resolve_origin(name)][0]


@dataclass
class LonLat:
    lon: float
    lat: float
    origin: str          # 입력에 쓰인 원점계 표시명
    epsg: str
    in_korea: bool       # 변환 결과가 한국 범위 안인가
    note: str = ""

    def as_tuple(self) -> tuple[float, float]:
        return (self.lon, self.lat)


def _in_korea(lon: float, lat: float) -> bool:
    return _KR_LON[0] <= lon <= _KR_LON[1] and _KR_LAT[0] <= lat <= _KR_LAT[1]


def to_wgs84(x: float, y: float, origin: str) -> LonLat:
    """한국 TM 좌표(X=동거리 Easting, Y=북거리 Northing) → WGS84 경위도.

    ⚠️ 한국 측량 성과표는 보통 **(X=Easting, Y=Northing)** 이지만, 자료에 따라 (N,E)로
    적힌 경우도 있다. 결과가 한국 범위를 벗어나면 축이 바뀌었을 수 있으니 그 사실을 알린다.
    """
    disp = resolve_origin(origin)
    epsg, lon0, _desc = KOREA_ORIGINS[disp]
    from pyproj import Transformer
    tr = Transformer.from_crs(epsg, "EPSG:4326", always_xy=True)   # always_xy: (E,N)→(lon,lat)
    lon, lat = tr.transform(float(x), float(y))
    ok = _in_korea(lon, lat)
    note = ""
    if not ok:
        # X/Y 를 바꿔서 다시 — 축 오표기 진단
        lon2, lat2 = tr.transform(float(y), float(x))
        if _in_korea(lon2, lat2):
            note = ("결과가 한국 범위 밖 — X/Y(동/북) 축이 바뀐 것 같습니다. "
                    "X=Easting(동거리), Y=Northing(북거리) 순서를 확인하세요.")
        else:
            sug = suggest_origin(x, y)
            note = (f"결과가 한국 범위 밖 — 원점계가 다를 수 있습니다. 제안: {sug}" if sug else
                    "결과가 한국 범위 밖 — 좌표계·값을 확인하세요.")
    elif abs(lon - lon0) > _ORIGIN_LON_SPAN:
        # 값은 한국 안이지만 이 원점계 관할을 크게 벗어남 → 원점계 오인 가능성.
        # (예: 중부원점 좌표를 동부원점으로 해석하면 경도가 129°로 나와 강원처럼 보인다.)
        sug = suggest_origin(x, y)
        note = (f"⚠️ 경도 {lon:.2f}°가 {disp}(경도원점 {lon0:.0f}°) 관할을 벗어납니다 — "
                f"원점계를 잘못 골랐을 수 있습니다."
                + (f" 이 좌표는 {sug} 관할로 보입니다." if sug and disp not in sug else ""))
    return LonLat(lon=round(lon, 7), lat=round(lat, 7), origin=disp, epsg=epsg,
                  in_korea=ok, note=note)


def suggest_origin(x: float, y: float) -> str | None:
    """이 (X,Y)를 각 원점계로 변환했을 때 **경도가 그 원점 관할에 가장 잘 맞는** 원점계.

    각 원점계로 변환하면 저마다 다른 경도가 나온다. 그중 |경도−경도원점|이 가장 작고
    한국 범위 안인 것이 맞는 원점계다(경계에 딱 걸려 아무것도 안 잡히는 문제 방지).
    """
    from pyproj import Transformer
    best, best_d = None, 1e9
    for disp, (epsg, lon0, _d) in KOREA_ORIGINS.items():
        tr = Transformer.from_crs(epsg, "EPSG:4326", always_xy=True)
        lon, lat = tr.transform(float(x), float(y))
        if _in_korea(lon, lat) and abs(lon - lon0) < best_d:
            best, best_d = f"{disp}({epsg})", abs(lon - lon0)
    return best


def locate(x: float, y: float, origin: str) -> dict:
    """좌표 → 위치 정보 한 벌. 대시보드·CLI 가 그대로 보여줄 수 있는 형태.

    가장 가까운 행정구역·교량 검색은 호출 측(OSM/역지오코딩)에서 하고, 여기서는
    좌표 변환과 검증까지만 한다(네트워크 불필요·결정론).
    """
    ll = to_wgs84(x, y, origin)
    return {
        "input": {"x": float(x), "y": float(y), "origin": ll.origin, "epsg": ll.epsg},
        "wgs84": {"lat": ll.lat, "lon": ll.lon},
        "in_korea": ll.in_korea,
        "note": ll.note,
        "map_url": f"https://www.openstreetmap.org/?mlat={ll.lat}&mlon={ll.lon}#map=17/{ll.lat}/{ll.lon}",
    }
