"""교량 InSAR 신뢰성 조건 — 기하 민감도 계산·시간샘플링 게이팅·asc+desc 연직 분리."""

from __future__ import annotations

import numpy as np

from inframon.insar.bridge_conditions import (
    LOOK_AZ,
    axis_azimuth_from_polyline,
    conditions_report,
    evaluate_conditions,
    longitudinal_sensitivity,
)
from inframon.insar.bridge_profile import profile_for
from inframon.insar.recipe import BridgeTarget, SelectionCriteria, TrackSelection


def _target(length=200.0, ew=True, lat=37.32):
    """동서(ew=True) 또는 남북 축의 합성 교량 타깃. bbox 종횡비로 축선 결정."""
    if ew:
        bbox = (127.10, lat, 127.105, lat + 0.0003)     # lon 넓음 → E-W 축
    else:
        bbox = (127.10, lat, 127.1003, lat + 0.005)     # lat 넓음 → N-S 축
    return BridgeTarget(name="x", name_ko="시험교", selected_lat=lat, selected_lon=127.102,
                        osm_type="way", osm_id=1, bbox=bbox, length_m=length,
                        distance_m=0.0, tags={"bridge:structure": "beam"}, confirmed=True)


def _track(direction="ASCENDING", n=30, span_days=400, pol="VV"):
    from datetime import datetime, timedelta
    d0 = datetime(2024, 1, 1)
    step = max(1, span_days // max(n - 1, 1))
    dates = [(d0 + timedelta(days=i * step)).strftime("%Y%m%d") for i in range(n)]
    return TrackSelection(flight_direction=direction, path=1, frame=1, polarization=pol,
                          n_scenes=n, first_date=dates[0], last_date=dates[-1],
                          scene_dates=dates, scene_names=[f"S1_{d}" for d in dates])


def test_longitudinal_sensitivity_geometry():
    """E-W 축은 상승궤도(look 78°)에 잘 보이고, N-S 축은 안 보인다."""
    g_ew = longitudinal_sensitivity(90.0, LOOK_AZ["ASCENDING"])    # 축∥look
    g_ns = longitudinal_sensitivity(0.0, LOOK_AZ["ASCENDING"])     # 축⊥look
    assert g_ew > 0.5 and g_ns < 0.2
    # 민감도는 [0,1]
    assert 0 <= g_ns <= 1 and 0 <= g_ew <= 1


def test_axis_azimuth_from_polyline_pca():
    """폴리라인 PCA 축선 — bbox 근사가 못 잡는 대각·연속 방위를 정확히."""
    ew = [(37.32, 127.10 + 0.001 * i) for i in range(6)]     # 동서 → 90°
    ns = [(37.32 + 0.001 * i, 127.10) for i in range(6)]     # 남북 → 0/180
    ne = [(37.32 + 0.0007 * i, 127.10 + 0.0009 * i) for i in range(6)]  # 대각 → ~45°
    assert abs(axis_azimuth_from_polyline(ew) - 90.0) < 2
    assert axis_azimuth_from_polyline(ns) % 180 < 2 or axis_azimuth_from_polyline(ns) > 178
    assert 35 < axis_azimuth_from_polyline(ne) < 55
    assert axis_azimuth_from_polyline([(37.0, 127.0)]) is None     # 점<2
    assert axis_azimuth_from_polyline([]) is None


def test_precise_axis_overrides_bbox_in_g1():
    """geometry 있으면 G1 이 폴리라인 PCA 축선을 쓴다(bbox 근사보다 정확).

    실 정자교 재현: bbox 근사는 축선≈0°(N-S)로 보고 상승 g=0.13(warn)인데, 실제 절점은
    축선≈29° 라 상승 g≈0.41(pass). 정밀 축선이 잘못된 warn 을 바로잡는다.
    """
    # 축선 ~29° 폴리라인(정자교 유사) — bbox 종횡비는 남북 우세로 보임
    geom = [(37.3215 + 0.0009 * i, 127.108 + 0.0005 * i) for i in range(8)]
    t = _target(ew=False)                       # bbox 는 N-S 우세
    t.geometry = geom
    crit = SelectionCriteria(perp_baseline_max_m=120.0)
    c = {x.id: x for x in evaluate_conditions(t, _track("ASCENDING"), crit, profile_for(t))}
    assert "PCA 정밀" in c["G1"].detail
    assert c["G1"].status == "pass"             # 정밀 축선으로 pass(근사면 warn 이었을 것)


def _conds(target, track):
    crit = SelectionCriteria(perp_baseline_max_m=120.0)
    return {c.id: c for c in evaluate_conditions(target, track, crit, profile_for(target))}


def test_g1_warns_when_axis_perpendicular_to_los():
    # N-S 축 + 상승궤도 → 종축 민감도 낮음 → G1 warn
    c = _conds(_target(ew=False), _track("ASCENDING"))
    assert c["G1"].status == "warn"
    # E-W 축 → pass
    c2 = _conds(_target(ew=True), _track("ASCENDING"))
    assert c2["G1"].status == "pass"


def test_g2_vertical_needs_asc_and_desc():
    crit = SelectionCriteria()
    prof = profile_for(_target())
    # 단일 궤도 → warn
    single = {c.id: c for c in evaluate_conditions(_target(), _track("ASCENDING"), crit, prof)}
    assert single["G2"].status == "warn"
    # asc+desc 명시 → pass
    both = {c.id: c for c in evaluate_conditions(
        _target(), _track("ASCENDING"), crit, prof,
        has_ascending=True, has_descending=True)}
    assert both["G2"].status == "pass"


def test_temporal_gates_short_span_and_few_scenes():
    # 짧은 기간(<365일) + 적은 장면(<25) → T1/T2 warn
    c = _conds(_target(), _track("ASCENDING", n=10, span_days=120))
    assert c["T1"].status == "warn" and c["T2"].status == "warn"
    # 충분 → pass
    c2 = _conds(_target(), _track("ASCENDING", n=30, span_days=400))
    assert c2["T1"].status == "pass" and c2["T2"].status == "pass"


def test_short_bridge_scatterer_warn():
    c = _conds(_target(length=40.0), _track("ASCENDING"))   # 40m < 60m
    assert c["S1"].status == "warn"


def test_report_counts_and_ready():
    crit = SelectionCriteria(perp_baseline_max_m=120.0)
    rep = conditions_report(_target(ew=True), _track("ASCENDING"), crit, profile_for(_target()))
    # blocker fail 없음 → ready
    assert rep["ready"] is True
    assert rep["n_conditions"] == sum(rep["counts"].values())
    assert rep["counts"]["pass"] >= 5


def test_unknown_when_recipe_missing():
    # 트랙/타깃 없이 평가 → unknown 다수, 크래시 없음
    crit = SelectionCriteria()
    conds = evaluate_conditions(_target(), None, crit, profile_for(_target()))
    ids = {c.id: c.status for c in conds}
    assert ids["G1"] == "unknown"        # bbox 있으나 track flight 없음 → unknown
    assert any(s == "unknown" for s in ids.values())
