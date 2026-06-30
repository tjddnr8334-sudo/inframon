"""교량 InSAR 가 신뢰성 있으려면 충족해야 할 **조건들**의 카탈로그 + 평가기.

`--check-track`(처리 *출력* H5 검증)의 입력측 짝 — 처리 *전에* 레시피(교량 타깃·트랙 선별·
선별 기준)가 "완벽한 교량 InSAR"의 조건을 만족하는지 게이팅한다. SARvey 를 교량 맞춤으로
완벽히 돌리려면 파라미터 유도(bridge_profile)만으론 부족하고, **궤도-축선 기하·시간 샘플링·
산란체·처리·대기** 조건이 먼저 성립해야 한다. 이 모듈이 그 조건들을 명시·평가한다.

각 조건은 pass/warn/fail/unknown 으로 평가되며, data 가 없으면 unknown(요구사항만 명시 →
체크리스트로 기능). Sentinel-1 IW 공칭 기하로 단일/이중 궤도의 축선·연직 민감도를 계산한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

# ── Sentinel-1 IW 공칭 기하(우측 관측) ──
# 상승: heading≈-12°(348°), look_az=heading+90≈78°(ENE). 하강: heading≈-168°(192°), look_az≈282°(WNW).
LOOK_AZ = {"ASCENDING": 78.0, "DESCENDING": 282.0}
INC_MID_DEG = 39.0           # IW 중앙 입사각(30~46°)
PERP_BASELINE_MAX_M = 150.0  # 공간 baseline 상한(decorrelation 억제)
MIN_SCENES = 25              # SBAS 시계열 견고성 하한
MIN_SPAN_DAYS = 365          # 계절/열팽창 분리 가능 최소 관측기간
MIN_LONGITUDINAL_SENS = 0.30  # 단일궤도 종축(열팽창) 민감도 허용 하한
MIN_LENGTH_M = 60.0          # 교량 길이 하한(이하면 PS 점수 부족 위험, ~수 픽셀)


@dataclass
class Condition:
    id: str
    category: str        # geometry | temporal | scatterer | processing | atmosphere
    title: str
    requirement: str     # 충족돼야 하는 것
    status: str          # pass | warn | fail | unknown
    severity: str        # blocker | important | advisory
    detail: str          # 평가된 구체값
    fix: str             # 충족 방법

    def to_dict(self) -> dict:
        return asdict(self)


def _axis_azimuth_from_bbox(bbox, lat: float) -> tuple[float, str]:
    """bbox(min_lon,min_lat,max_lon,max_lat) 종횡비로 교량 축선 방위각[deg] 추정.

    축정렬 bbox 라 정확한 축선은 OSM way 지오메트리가 필요 → 여기선 긴 변 방향으로 근사.
    반환: (축방위각, 신뢰도 note).
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    dlon_m = (max_lon - min_lon) * 111320.0 * np.cos(np.deg2rad(lat))
    dlat_m = (max_lat - min_lat) * 110540.0
    if dlon_m >= dlat_m:
        return 90.0, "동서 우세(bbox 종횡비 근사)"   # E-W
    return 0.0, "남북 우세(bbox 종횡비 근사)"          # N-S


def axis_azimuth_from_polyline(geometry_latlon) -> float | None:
    """OSM way 절점 폴리라인 [(lat,lon),...] → 주축(PC1) 방위각[deg, 북기준 시계, 0~180).

    bbox 종횡비 근사와 달리 대각·곡선 축도 정확히 잡는다(국소 ENU 미터로 PCA). 축선은 방향
    무관(열팽창은 ±) → [0,180) 로 정규화. 점 < 2 면 None.
    """
    pts = [(float(la), float(lo)) for la, lo in (geometry_latlon or [])]
    if len(pts) < 2:
        return None
    lat = np.array([p[0] for p in pts]); lon = np.array([p[1] for p in pts])
    lat0 = float(lat.mean())
    e = (lon - lon.mean()) * 111320.0 * np.cos(np.deg2rad(lat0))    # 동쪽[m]
    n = (lat - lat0) * 110540.0                                     # 북쪽[m]
    en = np.column_stack([e, n])
    en -= en.mean(axis=0)
    _, vecs = np.linalg.eigh(en.T @ en)
    e1, n1 = vecs[0, -1], vecs[1, -1]                               # PC1 (E, N)
    return float(np.rad2deg(np.arctan2(e1, n1)) % 180.0)


def longitudinal_sensitivity(axis_az_deg: float, look_az_deg: float,
                             inc_deg: float = INC_MID_DEG) -> float:
    """종축(수평 열팽창) LOS 민감도 g = sin(θ)·|cos(A−L)| ∈ [0,1]."""
    return float(np.sin(np.deg2rad(inc_deg)) *
                 abs(np.cos(np.deg2rad(axis_az_deg - look_az_deg))))


def _scene_days(track) -> np.ndarray | None:
    dates = list(getattr(track, "scene_dates", []) or [])
    if len(dates) < 2:
        return None
    from datetime import datetime
    try:
        d0 = datetime.strptime(str(dates[0]), "%Y%m%d")
        return np.array([(datetime.strptime(str(x), "%Y%m%d") - d0).days for x in dates], float)
    except ValueError:
        return None


def _C(id, cat, title, req, status, sev, detail, fix) -> Condition:
    return Condition(id=id, category=cat, title=title, requirement=req,
                     status=status, severity=sev, detail=detail, fix=fix)


def evaluate_conditions(target, track, criteria, profile, *,
                        has_ascending: bool | None = None,
                        has_descending: bool | None = None) -> list[Condition]:
    """레시피/프로파일 → 교량 InSAR 조건 평가 리스트.

    has_ascending/descending 을 명시하면 G2(연직 분리) 를 정확히 평가하고, None 이면
    track.flight_direction 단일 궤도로 가정한다.
    """
    out: list[Condition] = []
    length = float(getattr(target, "length_m", 0) or 0)
    lat = float(getattr(target, "selected_lat", 0) or 0)
    bbox = getattr(target, "bbox", None)
    flight = (getattr(track, "flight_direction", "") or "").upper() if track else ""

    # ───────── 기하(geometry) ─────────
    # 축선: OSM way 폴리라인(PCA, 정밀) 우선, 없으면 bbox 종횡비 근사.
    geom = getattr(target, "geometry", None)
    axis_precise = axis_azimuth_from_polyline(geom)
    if bbox and flight in LOOK_AZ:
        if axis_precise is not None:
            axis_az, axis_note = axis_precise, "OSM way 지오메트리(PCA 정밀)"
        else:
            axis_az, axis_note = _axis_azimuth_from_bbox(bbox, lat)
        g = longitudinal_sensitivity(axis_az, LOOK_AZ[flight])
        st = "pass" if g >= MIN_LONGITUDINAL_SENS else "warn"
        out.append(_C(
            "G1", "geometry", "종축 LOS 민감도(열팽창 관측 가능)",
            f"단일궤도 종축 민감도 g=sin θ·|cos(A−L)| ≥ {MIN_LONGITUDINAL_SENS}",
            st, "important",
            f"축선≈{axis_az:.0f}°({axis_note}) vs {flight} look {LOOK_AZ[flight]:.0f}° → g={g:.2f}",
            "g 낮으면 반대 궤도 추가하거나, 종축이 LOS 에 평행한 궤도를 택. 정확 축선은 OSM way 지오메트리."))
    else:
        out.append(_C("G1", "geometry", "종축 LOS 민감도",
                      f"종축 민감도 ≥ {MIN_LONGITUDINAL_SENS}", "unknown", "important",
                      "bbox·궤도 정보 부족", "교량 타깃·트랙 선별을 먼저 저장."))

    # G2 연직 분리(asc+desc)
    asc = has_ascending if has_ascending is not None else (flight == "ASCENDING")
    desc = has_descending if has_descending is not None else (flight == "DESCENDING")
    if asc and desc:
        out.append(_C("G2", "geometry", "연직 변위 분리(asc+desc)",
                      "상승+하강 두 궤도로 연직(처짐·침하) 분리 가능", "pass", "important",
                      "asc+desc 모두 확보", "유지(fuse_asc_desc 가 연직 U·종축 H 분리)."))
    else:
        out.append(_C("G2", "geometry", "연직 변위 분리(asc+desc)",
                      "연직(처짐·침하)을 분리하려면 상승+하강 둘 다 필요", "warn", "important",
                      f"단일 궤도({flight or '미지정'})만 — LOS 1성분, 연직 미분리",
                      "반대 궤도 트랙도 선별·처리. 단일궤도면 종축 deprojection 만(연직 추정 불가)."))

    # G3 입사각 적정(가정값 안내)
    out.append(_C("G3", "geometry", "입사각 적정(IW 30~46°)",
                  "입사각이 너무 작으면 수평 민감도↓·너무 크면 음영↑", "unknown", "advisory",
                  f"공칭 {INC_MID_DEG}° 가정(실 입사각은 Track H5/메타에서 확인)",
                  "처리 후 Track H5 의 incidenceAngle 로 재평가(--check-track)."))

    # ───────── 시간 샘플링(temporal) ─────────
    days = _scene_days(track) if track else None
    nsc = int(getattr(track, "n_scenes", 0) or (len(days) if days is not None else 0)) if track else 0
    # T1 관측 기간 ≥ 1년
    if days is not None:
        span = float(days[-1] - days[0])
        st = "pass" if span >= MIN_SPAN_DAYS else "warn"
        out.append(_C("T1", "temporal", "관측 기간(계절 분리)",
                      f"열팽창(연주기) 분리 위해 관측기간 ≥ {MIN_SPAN_DAYS}일", st, "important",
                      f"{span:.0f}일 ({nsc}장면)",
                      "기간이 짧으면 SLC 검색 날짜 범위를 1년 이상으로 확장."))
        # T3 계절(월) 커버리지
        from datetime import datetime
        months = {datetime.strptime(str(d), "%Y%m%d").month
                  for d in getattr(track, "scene_dates", [])}
        st3 = "pass" if len(months) >= 8 else "warn"
        out.append(_C("T3", "temporal", "계절(온도) 커버리지",
                      "열팽창 진폭 추정 위해 다양한 계절(월) 포함 — 월 ≥ 8", st3, "advisory",
                      f"{len(months)}개 월 포함", "여름·겨울 장면이 모두 들어오게 날짜범위 조정."))
        # T4 재방문 규칙성
        gaps = np.diff(days)
        med = float(np.median(gaps))
        st4 = "pass" if med <= 24 else "warn"
        out.append(_C("T4", "temporal", "재방문 규칙성",
                      "큰 간격은 위상 aliasing — 중앙 재방문 ≤ 24일(S1 6/12일 기대)", st4, "advisory",
                      f"중앙 간격 {med:.0f}일, 최대 {gaps.max():.0f}일",
                      "결측 구간 장면 추가 또는 두 위성(S1A/B) 병합."))
    else:
        out.append(_C("T1", "temporal", "관측 기간(계절 분리)",
                      f"관측기간 ≥ {MIN_SPAN_DAYS}일", "unknown", "important",
                      "scene_dates 부족", "트랙 선별을 먼저 저장."))
    # T2 장면 수
    if nsc:
        st = "pass" if nsc >= MIN_SCENES else "warn"
        out.append(_C("T2", "temporal", "장면 수(SBAS 견고성)",
                      f"SBAS 시계열 견고성 위해 장면 ≥ {MIN_SCENES}", st, "important",
                      f"{nsc} 장면", "장면이 적으면 더 긴 기간·동일 트랙 추가 취득 포함."))
    # T5 공간 baseline
    perp = getattr(criteria, "perp_baseline_max_m", None)
    if perp is not None:
        st = "pass" if perp <= PERP_BASELINE_MAX_M else "warn"
        out.append(_C("T5", "temporal", "공간 baseline 상한",
                      f"수직 baseline ≤ {PERP_BASELINE_MAX_M:.0f}m(기하 decorrelation 억제)", st,
                      "important", f"기준 {perp:.0f}m",
                      "상한을 150m 이하로. SARvey small-baseline 네트워크가 추가 완화."))

    # ───────── 산란체(scatterer) ─────────
    # S1 길이 vs 해상도(점수 충분)
    if length:
        st = "pass" if length >= MIN_LENGTH_M else "warn"
        out.append(_C("S1", "scatterer", "교량 길이 vs 해상도",
                      f"PS 점수 확보 위해 길이 ≥ {MIN_LENGTH_M:.0f}m(~수 픽셀)", st, "important",
                      f"길이 {length:.0f}m", "짧으면 코너리플렉터 설치 또는 고해상(SM) 모드 검토."))
    # S2 편파
    pol = (getattr(criteria, "polarization", None) or getattr(track, "polarization", "") or "").upper()
    if pol:
        st = "pass" if "VV" in pol else "warn"
        out.append(_C("S2", "scatterer", "편파(인공구조물 반사)",
                      "교량(금속·콘크리트)은 VV 가 표준 — VV 포함", st, "advisory",
                      f"편파 {pol}", "VV 우선. VH 단독이면 PS 밀도 저하 가능."))
    # S3 수면 decorrelation 마스킹
    wm = (profile.water_mask or {}) if profile else {}
    if wm.get("apply"):
        out.append(_C("S3", "scatterer", "수면 decorrelation 마스킹",
                      f"{profile.water_context} 교량 — 수면 점 제외·데크 버퍼만 유지", "pass",
                      "important", f"{wm.get('context')} water mask({wm.get('strength')}), "
                      f"유효={wm.get('valid_region')}",
                      "유지. 정확 데크 마스크는 OSM way 버퍼(deck_buffer_m)로 생성."))

    # ───────── 처리(processing) ─────────
    if profile:
        # P1 격자 vs 길이
        if length:
            ok = profile.grid_size_m <= max(length / 4, 15)
            out.append(_C("P1", "processing", "P1 격자 vs 교량 길이",
                          "P1 후보 격자 ≤ 길이/4(충분한 1차 점)", "pass" if ok else "warn",
                          "advisory", f"격자 {profile.grid_size_m}m vs 길이 {length:.0f}m",
                          "격자를 더 조밀하게(도시용 200m 금지)."))
        # P2 기준점 안정 지반
        out.append(_C("P2", "processing", "기준점 안정 지반",
                      "기준점은 변형 없는 육상 정착부에 — 교량 위 금지", "unknown", "important",
                      f"힌트: {profile.reference_hint}",
                      "기준점을 교대/주탑/앵커리지(안정 지반)에 고정해 상대변위 기준 확보."))
    # P3 DEM 오차 한계(상승 데크)
    out.append(_C("P3", "processing", "DEM 오차(상승 데크)",
                  "교량 데크는 DEM 보다 높아 위상에 DEM 오차 유입 — dem_error 추정 필요", "pass",
                  "advisory", "SARvey dem_error_bound 활성(consistency_check)",
                  "데크 고도가 DEM 에 없으면 dem_error 추정으로 흡수(이미 config 반영)."))

    return out


def conditions_report(target, track, criteria, profile, **kw) -> dict:
    """평가 + 집계. status 카운트와 게이트(blocker fail 있으면 ready=False)."""
    conds = evaluate_conditions(target, track, criteria, profile, **kw)
    counts = {s: sum(c.status == s for c in conds) for s in ("pass", "warn", "fail", "unknown")}
    blockers = [c for c in conds if c.status == "fail" and c.severity == "blocker"]
    return {
        "ready": len(blockers) == 0,
        "counts": counts,
        "n_conditions": len(conds),
        "conditions": [c.to_dict() for c in conds],
        "blockers": [c.id for c in blockers],
    }
