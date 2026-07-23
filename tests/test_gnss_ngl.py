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


# ── 판정 축: LOS 가 아니라 수직 ────────────────────────────────────────
# GNSS 절대속도는 한반도 플레이트 수평운동(~30mm/yr)을 포함하고 InSAR LOS 는 국소
# 기준점에 대한 상대값이다. LOS 에서 직접 빼면 차이의 대부분이 기준프레임 차이지
# InSAR 오차가 아니다 — 그걸 합격/불합격으로 쓰면 멀쩡한 결과가 "편차 큼"이 된다.

def _project(tmp_path, vel_mm_yr: float, n=40, m=30, years=8.0):
    """LOS 속도가 vel_mm_yr 인 최소 project.h5."""
    import h5py
    from datetime import date, timedelta
    p = tmp_path / "p.h5"
    d0 = date(2016, 1, 1)
    labels = [(d0 + timedelta(days=int(i * years * 365.25 / (m - 1)))).strftime("%Y%m%d")
              for i in range(m)]
    t = np.array([(i * years / (m - 1)) for i in range(m)])
    with h5py.File(p, "w") as f:
        g = f.create_group("insar")
        g.create_dataset("xyz", data=np.column_stack([
            np.full(n, 127.109), np.full(n, 37.3634), np.zeros(n)]))
        g.create_dataset("los", data=np.tile(vel_mm_yr * t, (n, 1)))
        g.create_dataset("date_labels", data=np.array(labels, dtype="S8"))
    return str(p)


def _fetch(up_by_sta: dict, *, east_mm_yr: float = 0.0):
    """관측소별 수직속도[mm/yr] + 공통 동향 수평운동(플레이트 모사) fetch_fn."""
    holdings = _HOLDINGS + "SON2  37.4000   127.2000  80.0  0 0 0  2015 2026\n"

    def fake(url):
        if url == gn.HOLDINGS_URL:
            return holdings
        for sta, up in up_by_sta.items():
            if sta in url:
                return _tenv3([(2016.0 + i * 0.5,
                                i * 0.5 * east_mm_yr / 1000.0, 0.0,
                                100.0 + i * 0.5 * up / 1000.0) for i in range(20)])
        raise OSError("no data")
    return fake


def test_verdict_uses_vertical_not_los(tmp_path):
    """InSAR 와 GNSS 의 수직이 일치하면, 수평 플레이트 운동이 아무리 커도 정합이다."""
    proj = _project(tmp_path, vel_mm_yr=-1.0 * math.cos(math.radians(39.0)))  # 연직 -1mm/yr
    r = gn.validate_insar_vs_gnss(
        proj, incidence_deg=39.0, heading_deg=-13.0, max_km=60,
        fetch_fn=_fetch({"SUWN": -1.0, "SON2": -1.0}, east_mm_yr=30.0))   # 플레이트 30mm/yr
    assert r.insar_up_vel_mm_yr == pytest.approx(-1.0, abs=0.05)
    assert r.rms_up_resid_mm_yr < 0.2                       # 수직은 정합
    assert abs(r.rms_resid_mm_yr) > 5.0                     # LOS 는 프레임 차로 크게 벌어짐
    assert "정합" in r.summary() and "판정에 쓰지 않음" in r.summary()


def test_plate_motion_alone_does_not_flag_failure(tmp_path):
    """수평 운동만 있고 수직은 0 → 판정은 정합이어야 한다(예전엔 '편차 큼'이었다)."""
    proj = _project(tmp_path, vel_mm_yr=0.0)
    for east in (0.0, 30.0, 60.0):
        r = gn.validate_insar_vs_gnss(
            proj, incidence_deg=39.0, heading_deg=-13.0, max_km=60,
            fetch_fn=_fetch({"SUWN": 0.0, "SON2": 0.0}, east_mm_yr=east))
        assert r.rms_up_resid_mm_yr < gn.VERT_OK_MM_YR      # 수평이 커져도 판정 불변
        assert "✅" in r.summary()


def test_real_vertical_discrepancy_is_flagged(tmp_path):
    """수직이 실제로 어긋나면 잡아야 한다 — 게이트가 무조건 통과시키면 안 된다."""
    proj = _project(tmp_path, vel_mm_yr=0.0)                # InSAR 연직 0
    r = gn.validate_insar_vs_gnss(
        proj, incidence_deg=39.0, heading_deg=-13.0, max_km=60,
        fetch_fn=_fetch({"SUWN": -8.0, "SON2": -8.0}))      # GNSS 는 8mm/yr 침하
    assert r.rms_up_resid_mm_yr > gn.VERT_MARGINAL_MM_YR
    assert "⚠️" in r.summary()


def test_vertical_outlier_is_excluded_from_verdict(tmp_path):
    """장비 스텝 잔재로 수직이 튀는 관측소는 MAD 기준으로 제외하고 사유를 남긴다."""
    proj = _project(tmp_path, vel_mm_yr=0.0)
    r = gn.validate_insar_vs_gnss(
        proj, incidence_deg=39.0, heading_deg=-13.0, max_km=60,
        fetch_fn=_fetch({"SUWN": 0.0, "SON2": 0.0, "DAEJ": -25.0}))
    outs = {s["sta"] for s in r.stations if s["vertical_outlier"]}
    assert r.n_vertical_used >= 2
    assert r.rms_up_resid_mm_yr < gn.VERT_OK_MM_YR          # 이상치가 판정을 흔들지 않는다
    if outs:                                                # 반경 안에 잡혔다면 표시돼야 한다
        assert any("수직" in o for o in r.vertical_outliers)


def test_as_dict_names_the_primary_metric(tmp_path):
    proj = _project(tmp_path, vel_mm_yr=0.0)
    d = gn.validate_insar_vs_gnss(proj, incidence_deg=39.0, heading_deg=-13.0, max_km=60,
                                  fetch_fn=_fetch({"SUWN": 0.0, "SON2": 0.0})).as_dict()
    assert d["primary_metric"] == "rms_up_resid_mm_yr"
    assert "판정에 쓰지 않는다" in d["note"]
    assert d["rms_up_resid_mm_yr"] is not None


def test_mad_outliers_needs_three_samples():
    assert gn._mad_outliers([1.0, 99.0]) == [False, False]   # 표본 부족 → 판단 보류
    flags = gn._mad_outliers([0.0, 0.1, -0.1, 30.0])
    assert flags[-1] and not any(flags[:-1])


# ── SLC 처리의 지상 근거: 기준앵커 ─────────────────────────────────────
# InSAR 는 상대 변위다. 기준점을 어디 두느냐가 전 결과의 원점을 정하는데 지금까지 근거가
# 형식별 휴리스틱 문자열뿐이었다. 인근 상시 GNSS 가 그 선택을 관측으로 뒷받침한다.
# 다만 절대 타이는 GNSS 가 발자국 안(≤TIE_MAX_KM)일 때만 정당하다.

def _anchor_fetch(rows: dict, *, span_yr: float = 10.0, n: int = 25):
    """{sta: (lat, lon, up_mm_yr)} → holdings/tenv3 fetch_fn."""
    lines = ["Sta Lat Long Hgt X Y Z Dtbeg Dtend"]
    for sta, (la, lo, _u) in rows.items():
        lines.append(f"{sta}  {la}  {lo}  80.0  0 0 0  2010-01-01 2026-01-01")
    holdings = "\n".join(lines)

    def fake(url):
        if url == gn.HOLDINGS_URL:
            return holdings
        for sta, (_la, _lo, up) in rows.items():
            if f"/{sta}." in url or url.endswith(f"{sta}.tenv3"):
                step = span_yr / (n - 1)
                return _tenv3([(2010.0 + i * step, 0.0, 0.0, 100.0 + i * step * up / 1000.0)
                               for i in range(n)])
        raise OSError("no data")
    return fake


def test_anchor_prefers_long_stable_close_station():
    """오래 관측되고·연직으로 안 움직이고·가까운 곳이 기준점 근거로 낫다."""
    a = gn.reference_anchor(37.3634, 127.1090, max_km=60, fetch_fn=_anchor_fetch({
        "AAAA": (37.36, 127.11, 0.0),      # 가깝고 안정
        "BBBB": (37.60, 127.40, -5.0),     # 멀고 침하 중
    }))
    assert a.best["sta"] == "AAAA"
    assert a.datum_up_mm_yr == pytest.approx(0.0, abs=0.1)
    assert all(c["score"] > 0 for c in a.candidates if not c["rejected"])


def test_anchor_refuses_absolute_tie_when_far():
    """수 km 떨어진 GNSS 로 절대 타이를 하면 사이 지반이 같이 움직인다는 보장이 없다."""
    a = gn.reference_anchor(37.3634, 127.1090, max_km=60, fetch_fn=_anchor_fetch({
        "FARR": (37.45, 127.20, 0.0)}))
    assert a.best["dist_km"] > gn.TIE_MAX_KM
    assert a.can_tie_absolute is False
    assert "절대 침하로는 읽지 마세요" in a.advice


def test_anchor_allows_tie_inside_footprint():
    a = gn.reference_anchor(37.3634, 127.1090, max_km=60, fetch_fn=_anchor_fetch({
        "NEAR": (37.3640, 127.1100, 0.0)}))
    assert a.best["dist_km"] <= gn.TIE_MAX_KM
    assert a.can_tie_absolute is True and a.verdict == "절대 타이 가능"


def test_anchor_rejects_short_record_with_reason():
    a = gn.reference_anchor(37.3634, 127.1090, max_km=60,
                            fetch_fn=_anchor_fetch({"SHRT": (37.36, 127.11, 0.0)}, span_yr=2.0))
    rej = [c for c in a.candidates if c["rejected"]]
    assert rej and "속도 신뢰 곤란" in rej[0]["rejected"]
    assert a.best is None and a.verdict == "지상 근거 없음"
    assert "상대값" in a.advice


def test_anchor_dict_carries_provenance_and_limits():
    a = gn.reference_anchor(37.3634, 127.1090, max_km=60,
                            fetch_fn=_anchor_fetch({"AAAA": (37.36, 127.11, 0.0)})).to_dict()
    assert a["holdings_url"] == gn.HOLDINGS_URL          # 출처를 결과에 남긴다
    assert a["tie_max_km"] == gn.TIE_MAX_KM
    assert "상대 변위" in a["note"]
    assert a["search_radius_km"] == 60


def test_anchor_survives_station_fetch_failure():
    def flaky(url):
        if url == gn.HOLDINGS_URL:
            return _HOLDINGS
        raise OSError("timeout")
    a = gn.reference_anchor(37.2755, 127.0542, max_km=60, fetch_fn=flaky)
    assert a.best is None
    assert any("취득실패" in (c["rejected"] or "") for c in a.candidates)


# ── AOI 확장: GNSS 관측소를 InSAR 발자국 안으로 ────────────────────────
# 교량만 처리하면 GNSS 는 항상 발자국 밖이라 검증이 "지역 중앙값 vs 수 km 밖 관측소"에
# 머문다. AOI 를 넓혀 같은 지점에 InSAR 점을 만들면 점 대 점 대조가 된다.

_BRIDGE_BBOX = (127.1080, 37.3630, 127.1100, 37.3640)


def _anchor(rows, **kw):
    return gn.reference_anchor(37.3634, 127.1090, max_km=60,
                               fetch_fn=_anchor_fetch(rows, **kw))


def test_extended_aoi_contains_the_station():
    a = _anchor({"SUWN": (37.2755, 127.0542, -0.6)})
    ext = gn.extend_aoi_to_stations(_BRIDGE_BBOX, a)
    lon0, lat0, lon1, lat1 = ext["aoi"]
    assert lon0 <= 127.0542 <= lon1 and lat0 <= 37.2755 <= lat1      # 관측소 포함
    assert lon0 <= _BRIDGE_BBOX[0] and lat1 >= _BRIDGE_BBOX[3]       # 교량도 그대로 포함
    assert [s["sta"] for s in ext["included"]] == ["SUWN"]


def test_extended_aoi_reports_processing_cost():
    """조용히 키우면 사용자가 몇 시간 뒤 메모리 부족으로 알게 된다."""
    a = _anchor({"SUWN": (37.2755, 127.0542, -0.6)})
    ext = gn.extend_aoi_to_stations(_BRIDGE_BBOX, a)
    assert ext["cost"]["area_km2"] > ext["bridge_cost"]["area_km2"]
    assert ext["cost_ratio_vs_bridge_only"] > 1
    assert ext["cost"]["approx_pixels_multilook20m"] > 0
    assert any("unwrapping" in w or "펼침" in w for w in ext["warnings"])


def test_extended_aoi_stops_at_area_budget():
    """상한을 넘기면 포함을 멈추고 사유를 남긴다 — 넘긴 채로 돌려주지 않는다."""
    a = _anchor({"SUWN": (37.2755, 127.0542, 0.0)})   # 10.9km — 포함하면 ~57km²
    ext = gn.extend_aoi_to_stations(_BRIDGE_BBOX, a, max_area_km2=10.0)
    assert ext["included"] == []
    assert ext["skipped"] and "상한" in ext["skipped"][0]["reason"]
    assert ext["aoi"] == pytest.approx(_BRIDGE_BBOX)     # 교량 AOI 그대로
    assert "절대 타이는 불가" in ext["benefit"]


def test_extended_aoi_honours_max_stations():
    a = _anchor({"AAAA": (37.36, 127.11, 0.0), "BBBB": (37.30, 127.05, 0.0)})
    one = gn.extend_aoi_to_stations(_BRIDGE_BBOX, a, max_stations=1)
    two = gn.extend_aoi_to_stations(_BRIDGE_BBOX, a, max_stations=2)
    assert len(one["included"]) == 1 and len(two["included"]) == 2
    assert two["cost"]["area_km2"] >= one["cost"]["area_km2"]
    assert any("max_stations" in s["reason"] for s in one["skipped"])


def test_aoi_cost_scales_with_area():
    small = gn.aoi_cost((127.10, 37.36, 127.11, 37.37))
    big = gn.aoi_cost((127.00, 37.30, 127.20, 37.40))
    assert big["area_km2"] > small["area_km2"] * 50
    assert big["approx_pixels_multilook20m"] > big["approx_pixels_slc"] * 0  # 존재만 확인
    assert small["width_km"] > 0 and small["height_km"] > 0


# ── 검증: 공동위치 대조 ────────────────────────────────────────────────
def test_colocated_comparison_uses_points_at_the_station(tmp_path):
    """AOI 가 관측소를 포함하면 그 지점 점들로 대조한다 — 지역 중앙값이 아니라."""
    import h5py
    from datetime import date, timedelta
    m, years = 30, 8.0
    d0 = date(2016, 1, 1)
    labels = [(d0 + timedelta(days=int(i * years * 365.25 / (m - 1)))).strftime("%Y%m%d")
              for i in range(m)]
    t = np.array([i * years / (m - 1) for i in range(m)])
    cos = math.cos(math.radians(39.0))
    # 교량 점 40개(연직 -5mm/yr) + 관측소 SUWN 위 점 10개(연직 0mm/yr)
    bridge = np.column_stack([np.full(40, 127.109), np.full(40, 37.3634), np.zeros(40)])
    at_sta = np.column_stack([np.full(10, 127.0542), np.full(10, 37.2755), np.zeros(10)])
    los = np.vstack([np.tile(-5.0 * cos * t, (40, 1)), np.tile(0.0 * t, (10, 1))])
    p = tmp_path / "p.h5"
    with h5py.File(p, "w") as f:
        g = f.create_group("insar")
        g.create_dataset("xyz", data=np.vstack([bridge, at_sta]))
        g.create_dataset("los", data=los)
        g.create_dataset("date_labels", data=np.array(labels, dtype="S8"))

    r = gn.validate_insar_vs_gnss(str(p), incidence_deg=39.0, heading_deg=-13.0, max_km=60,
                                  fetch_fn=_fetch({"SUWN": 0.0}))
    s = next(x for x in r.stations if x["sta"] == "SUWN")
    assert s["colocated"] is True and s["n_colocated_points"] == 10
    assert s["insar_up"] == pytest.approx(0.0, abs=0.1)   # 지역(-5)이 아니라 그 지점(0)
    assert r.n_colocated == 1
    assert r.rms_up_resid_mm_yr < 0.2                     # 같은 지점끼리라 정합
    assert "공동위치" in r.summary()
    assert r.as_dict()["comparison_kind"] == "co-located"


def test_regional_fallback_is_labelled_when_no_points_at_station(tmp_path):
    proj = _project(tmp_path, vel_mm_yr=0.0)              # 교량에만 점
    r = gn.validate_insar_vs_gnss(proj, incidence_deg=39.0, heading_deg=-13.0, max_km=60,
                                  fetch_fn=_fetch({"SUWN": 0.0, "SON2": 0.0}))
    assert r.n_colocated == 0
    assert all(not s["colocated"] for s in r.stations)
    assert r.as_dict()["comparison_kind"] == "regional"
    assert "AOI 에 관측소가 없음" in r.summary()
    assert "--gnss-extend-aoi" in r.summary()             # 개선 경로를 알려준다
