"""한국 측량 원점계 → WGS84 — 좌표로 교량 위치 지정."""
from __future__ import annotations

import pytest

from inframon.korea_crs import (
    KOREA_ORIGINS,
    epsg_of,
    locate,
    resolve_origin,
    suggest_origin,
    to_wgs84,
)

# 검증 기준점 — WGS84 좌표를 각 원점계로 역산해 넣으면 그대로 복원되어야 한다.
pyproj = pytest.importorskip("pyproj")
from pyproj import Transformer  # noqa: E402


def _to_tm(lon, lat, epsg):
    return Transformer.from_crs("EPSG:4326", epsg, always_xy=True).transform(lon, lat)


def test_jeongja_central_origin_roundtrip():
    """정자교(WGS84 37.3685,127.109) 중부원점 좌표 → 다시 WGS84 복원."""
    x, y = _to_tm(127.109, 37.3685, "EPSG:5186")
    r = to_wgs84(x, y, "중부원점")
    assert r.lat == pytest.approx(37.3685, abs=1e-4)
    assert r.lon == pytest.approx(127.109, abs=1e-4)
    assert r.in_korea and not r.note


@pytest.mark.parametrize("disp,epsg,lon,lat", [
    ("중부원점", "EPSG:5186", 127.0, 37.5),
    ("서부원점", "EPSG:5185", 125.3, 37.6),
    ("동부원점", "EPSG:5187", 128.9, 37.75),
    ("동해원점", "EPSG:5188", 130.9, 37.5),
])
def test_all_origins_roundtrip(disp, epsg, lon, lat):
    x, y = _to_tm(lon, lat, epsg)
    r = to_wgs84(x, y, disp)
    assert r.lat == pytest.approx(lat, abs=1e-4) and r.lon == pytest.approx(lon, abs=1e-4)
    assert r.epsg == epsg and r.in_korea


def test_aliases_and_epsg_resolve():
    for k in ("중부", "중부원점", "5186", "EPSG:5186", "central", "TM"):
        assert resolve_origin(k) == "중부원점" and epsg_of(k) == "EPSG:5186"
    assert resolve_origin("동부") == "동부원점"
    assert resolve_origin("EPSG:5188") == "동해원점"


def test_unknown_origin_raises():
    with pytest.raises(ValueError, match="알 수 없는 원점계"):
        resolve_origin("일본원점")


def test_wrong_origin_out_of_jurisdiction_is_flagged():
    """중부 좌표를 동부원점으로 해석하면 경도가 관할을 벗어나 경고해야 한다.

    ⚠️ 좌표만으로 100% 구분은 불가능하다(TM 은 경도원점만 다른 같은 수식이라, 한 좌표가
    여러 원점계에서 유효할 수 있다 — 특히 인접 원점계는 정확히 겹칠 수 있다). 여기서는
    경도가 관할을 **크게** 벗어나는 명백한 오인만 검증한다.
    """
    x, y = _to_tm(130.4, 37.6, "EPSG:5187")     # 동부 관할 동쪽(129°±1° 안)
    r = to_wgs84(x, y, "중부원점")               # 중부로 해석 → 경도 128.4°, 중부 관할(±1°) 밖
    assert r.note != "" and "관할" in r.note
    assert "동부원점" in r.note                  # 맞는 원점계를 제안


def test_out_of_korea_is_flagged():
    """변환 결과가 한국 밖이면 경고한다(비정상 좌표·잘못된 원점계)."""
    r = to_wgs84(900000.0, 100000.0, "중부원점")
    assert not r.in_korea and r.note != ""


def test_suggest_origin_returns_a_candidate():
    """suggest_origin 은 한국 안으로 떨어지는 원점계 후보를 준다.

    ⚠️ TM 은 경도원점만 다른 같은 수식이라 같은 X,Y 는 모든 원점계에서 |경도−원점|이
    동일하다 — 좌표만으로 원점계를 확정할 수 없다. 확정은 사용자가 지도로 한다.
    """
    x, y = _to_tm(128.9, 37.75, "EPSG:5187")
    assert suggest_origin(x, y) is not None


def test_origin_ambiguity_is_fundamental():
    """같은 좌표가 원점계에 따라 경도 2° 차이나는 다른 위치가 된다(원리상 모호)."""
    x, y = _to_tm(129.0, 37.75, "EPSG:5187")
    central = to_wgs84(x, y, "중부원점")
    east = to_wgs84(x, y, "동부원점")
    assert abs(central.lon - east.lon) == pytest.approx(2.0, abs=0.01)


def test_locate_returns_map_url_and_provenance():
    x, y = _to_tm(127.109, 37.3685, "EPSG:5186")
    info = locate(x, y, "중부원점")
    assert info["in_korea"] is True
    assert info["input"]["epsg"] == "EPSG:5186"
    assert info["map_url"].startswith("https://www.openstreetmap.org/")
    assert str(info["wgs84"]["lat"]) in info["map_url"]


def test_origins_table_covers_four_standard():
    assert set(KOREA_ORIGINS) == {"중부원점", "서부원점", "동부원점", "동해원점"}
    for _disp, (epsg, lon0, desc) in KOREA_ORIGINS.items():
        assert epsg.startswith("EPSG:518") and 124 <= lon0 <= 132 and desc
