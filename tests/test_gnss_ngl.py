"""NGL GNSS 검증 — holdings·tenv3 파싱·최근접·LOS 투영·InSAR 대조 (네트워크 mock)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from inframon import gnss_ngl as gn

# 실 포맷 기준 합성 데이터 (DataHoldings.txt: Sta Lat Long(0-360) Hgt X Y Z ...)
_HOLDINGS = """\
Sta  Lat(deg)   Long(deg) Hgt(m)  X(m)           Y(m)         Z(m)          Dtbeg      Dtend
SUWN  37.2755   127.0542  82.259  -3062023.0  4055449.0  3841989.0  1997-11-30 2026-01-01
DAEJ  36.3994   127.3745  116.86  -3120000.0  4080000.0  3760000.0  2000-01-01 2026-01-01
FARW -12.4666   238.2064  104.85  -4073662.0  4712064.0 -1367874.0  2008-03-27 2018-09-25
"""

# tenv3: site YYMMMDD decyr MJD week day reflon _e0 east _n0 north u0 up ...
def _tenv3(rows):
    hdr = "SUWN 00XXX00 x x x x 127.1 " + "x " * 14
    lines = []
    for yr, e, n, u in rows:  # e/n/u = 절대 위치[m] (정수부는 0 로 둠)
        lines.append(f"SUWN 00XXX00 {yr} 50000 900 0 127.1 0 {e} 0 {n} 0 {u} "
                     "1.5 0.001 0.001 0.005 0 0 0")
    return "\n".join(lines)


def test_parse_holdings_normalizes_lon():
    st = gn.parse_holdings(_HOLDINGS)
    assert len(st) == 3
    suwn = next(s for s in st if s.sta == "SUWN")
    assert suwn.lat == pytest.approx(37.2755) and suwn.lon == pytest.approx(127.0542)
    farw = next(s for s in st if s.sta == "FARW")
    assert farw.lon == pytest.approx(238.2064 - 360)   # 0-360 → -180..180


def test_nearest_stations_within_radius():
    st = gn.parse_holdings(_HOLDINGS)
    near = gn.nearest_stations(37.3634, 127.1090, st, max_km=50, k=5)
    assert near and near[0].sta == "SUWN"              # 수원 ~10km 최근접
    assert near[0].dist_km < 15
    assert all(s.dist_km <= 50 for s in near)          # DAEJ(대전~110km)·FARW 제외


def test_parse_tenv3_displacement_mm():
    # 1mm/yr 상승: up 이 매년 0.001m 증가
    txt = _tenv3([(2020.0, 0.0, 0.0, 100.000),
                  (2021.0, 0.0, 0.0, 100.001),
                  (2022.0, 0.0, 0.0, 100.002)])
    ser = gn.parse_tenv3(txt, "SUWN")
    assert ser.n_epochs == 3 and ser.span_yr == pytest.approx(2.0)
    assert ser.du_mm[0] == 0.0 and ser.du_mm[-1] == pytest.approx(2.0)   # 첫에폭 기준 mm
    assert gn.linear_rate_mm_yr(ser.decyr, ser.du_mm) == pytest.approx(1.0, abs=1e-6)


def test_enu_to_los_vertical_matches_cos():
    # 순수 연직 → LOS = U·cosθ (기존 validation 과 일관)
    los = gn.enu_to_los(0.0, 0.0, 10.0, 39.0, -13.0)
    assert los == pytest.approx(10.0 * math.cos(math.radians(39.0)))


def test_robust_rate_rejects_step():
    # 중간 스텝(장비교체) 포함 시계열: 최소자승은 왜곡, Theil-Sen 은 견고
    decyr = [2018.0 + i * 0.1 for i in range(20)]
    up = [0.0] * 10 + [6700.0] * 10                 # 10번째에서 6.7m 점프(SWON형)
    ls = gn.linear_rate_mm_yr(decyr, up)
    ts, scatter = gn.robust_rate_mm_yr(decyr, up)
    assert abs(ls) > 3000                            # 최소자승은 거대 속도(왜곡)
    assert abs(ts) < abs(ls)                         # Theil-Sen 이 덜 왜곡
    assert scatter > 100                             # 큰 잔차산포 → 게이트 신호


def test_gnss_los_velocities_gates_bad_station():
    holdings_txt = _HOLDINGS + "SWON  37.2800   127.0500  80.0  0 0 0  2017 2020\n"

    def fake(url):
        if url == gn.HOLDINGS_URL:
            return holdings_txt                      # SUWN + SWON 둘 다 반경 내
        if "SUWN" in url:                            # 깨끗: 2mm/yr 상승
            return _tenv3([(2020.0 + i * 0.2, 0, 0, 100.0 + i * 0.0004) for i in range(15)])
        if "SWON" in url:                            # 불량: 6.7m 스텝
            return _tenv3([(2020.0 + i * 0.2, 0, 0, 100.0 if i < 7 else 106.7)
                           for i in range(15)])
        raise OSError("no data")
    out, dropped = gn.gnss_los_velocities(37.2755, 127.0542, incidence_deg=39.0,
                                          heading_deg=-13.0, max_km=50, k=8,
                                          fetch_fn=fake)
    stas = {s.sta for s in out}
    assert "SUWN" in stas                            # 깨끗한 관측소 유지
    assert "SWON" not in stas                        # 스텝 관측소 게이트
    assert any("SWON" in d for d in dropped)
    suwn = next(s for s in out if s.sta == "SUWN")
    assert suwn.up_vel_mm_yr == pytest.approx(2.0, abs=0.2)
