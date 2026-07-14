"""ASF 로 Sentinel-1 SLC 검색 + 트랙 선별 — InSAR 데이터 선별 C·D 단계.

교량 타깃 bbox(A·B 산출)를 덮는 Sentinel-1 SLC 장면을 ASF 에서 검색하고(C),
편파(VV)·궤도방향으로 거른 뒤 (방향, path, frame) 조합별로 묶어 **취득 최다 트랙**을
고른다(D). 결과는 recipe.TrackSelection 으로 저장돼 SARvey 처리 대상이 된다.

공간(수직) baseline ≤ 150 m 필터는 페어 형성(SARvey) 단계에서 적용된다 — 검색/트랙
선별 단계는 '어느 트랙·프레임을 쓸지'와 'VV·궤도방향'까지만 정한다.

네트워크는 `_asf_geo_search` 한 곳으로 격리(테스트에서 monkeypatch). asf_search 는
옵션 의존성(`pip install asf_search`)이라 함수 내부에서 지연 import 한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Scene:
    """Sentinel-1 SLC 장면 1개(검색 결과)."""

    scene_name: str
    flight_direction: str             # ASCENDING | DESCENDING
    path: int                         # relativeOrbit (track)
    frame: int
    polarization: str                 # 예: "VV", "VV+VH"
    start_time: str                   # ISO 8601
    perpendicular_baseline: float | None = None
    url: str | None = None

    @property
    def date(self) -> str:
        """YYYYMMDD 취득일."""
        return self.start_time[:10].replace("-", "")


@dataclass
class TrackGroup:
    """(궤도방향, path, frame) 조합별 집계."""

    flight_direction: str
    path: int
    frame: int
    n_scenes: int
    first_date: str
    last_date: str

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.flight_direction, self.path, self.frame)


def bbox_to_wkt(bbox: tuple[float, float, float, float]) -> str:
    """(min_lon, min_lat, max_lon, max_lat) → WKT 폴리곤."""
    mn_lon, mn_lat, mx_lon, mx_lat = bbox
    return (
        f"POLYGON(({mn_lon} {mn_lat},{mx_lon} {mn_lat},"
        f"{mx_lon} {mx_lat},{mn_lon} {mx_lat},{mn_lon} {mn_lat}))"
    )


def _asf_geo_search(wkt: str, *, start: str | None = None, end: str | None = None) -> list[dict]:
    """ASF geo_search 호출 → 각 결과의 properties dict 리스트(네트워크 격리 지점)."""
    import asf_search as asf

    opts: dict = {
        "intersectsWith": wkt,
        "platform": "SENTINEL-1",
        "processingLevel": "SLC",
        "beamMode": "IW",
    }
    if start:
        opts["start"] = start
    if end:
        opts["end"] = end
    results = asf.geo_search(**opts)
    return [dict(r.properties) for r in results]


def _to_scene(p: dict) -> Scene | None:
    try:
        path = p.get("pathNumber")
        frame = p.get("frameNumber")
        bperp = p.get("perpendicularBaseline")
        return Scene(
            scene_name=p.get("sceneName") or p.get("fileID") or "?",
            flight_direction=(p.get("flightDirection") or "").upper(),
            path=int(path) if path is not None else -1,
            frame=int(frame) if frame is not None else -1,
            polarization=(p.get("polarization") or "").upper(),
            start_time=p.get("startTime") or "",
            perpendicular_baseline=float(bperp) if bperp not in (None, "") else None,
            url=p.get("url"),
        )
    except (TypeError, ValueError):
        return None


def search_slc(
    bbox: tuple[float, float, float, float],
    *,
    start: str | None = None,
    end: str | None = None,
    polarization: str = "VV",
) -> list[Scene]:
    """bbox 를 덮는 Sentinel-1 SLC 장면을 검색하고 편파로 거른다."""
    props = _asf_geo_search(bbox_to_wkt(bbox), start=start, end=end)
    scenes = [s for p in props if (s := _to_scene(p))]
    if polarization:
        pol = polarization.upper()
        scenes = [s for s in scenes if pol in s.polarization]
    return scenes


def _asf_baseline_stack(reference_scene_name: str) -> list[dict]:
    """ASF baseline stack(기준 장면 대비 수직/시간 baseline) → properties 리스트(네트워크 격리)."""
    import asf_search as asf

    ref = [r for r in asf.granule_search([reference_scene_name])
           if r.properties.get("processingLevel") == "SLC"]
    if not ref:
        return []
    return [dict(p.properties) for p in ref[0].stack()]


def perpendicular_baselines(scene_names: list[str]) -> dict[str, float]:
    """선택 트랙 장면들의 수직 baseline(기준 대비) → {YYYYMMDD: bperp_m}.

    ASF baseline stack 은 한 기준에 대한 각 장면의 perpendicularBaseline 을 준다.
    페어 수직 baseline 은 |bperp_i - bperp_j| 로 근사한다(master 선정용).
    """
    if not scene_names:
        return {}
    out: dict[str, float] = {}
    for p in _asf_baseline_stack(scene_names[0]):
        d = (p.get("startTime") or "")[:10].replace("-", "")
        b = p.get("perpendicularBaseline")
        if d and b is not None:
            out[d] = float(b)
    return out


def filter_slaves_by_baseline(dates, master_date, *, bperp: dict | None = None,
                              max_temporal_days: float = 72.0, max_perp_m: float = 150.0,
                              min_keep: int = 3) -> tuple[list, list]:
    """마스터 대비 **시공간 baseline** 초과 slave 제거(a priori — 처리·다운로드 전).

    · 시간 baseline = |날짜차|(일): 길수록 시간 비간섭(식생·계절) → max_temporal_days 초과 제거.
    · 공간 baseline B⊥ = |bperp_slave − bperp_master|(m): 클수록 기하 비간섭·DEM오차 →
      max_perp_m 초과 제거(S1 궤도관 좁아 대개 ≤150m). bperp 없으면 시간만.
    과다제거 방지: 위반이 없는 slave 우선 유지, 최소 min_keep 미달이면 baseline 작은
    순으로 보충. 반환: (유지 날짜[정렬], 제거 [{date, reason, temporal_days, perp_m}]).
    """
    from datetime import datetime
    md = datetime.strptime(str(master_date), "%Y%m%d")
    bref = (bperp or {}).get(str(master_date))
    rows = []
    for d in dates:
        if str(d) == str(master_date):
            continue
        tb = abs((datetime.strptime(str(d), "%Y%m%d") - md).days)
        pb = (abs(bperp[str(d)] - bref)
              if bperp and str(d) in bperp and bref is not None else None)
        over = []
        if tb > max_temporal_days:
            over.append(f"시간 {tb}일>{max_temporal_days:.0f}")
        if pb is not None and pb > max_perp_m:
            over.append(f"수직 {pb:.0f}m>{max_perp_m:.0f}")
        score = tb / max_temporal_days + (pb / max_perp_m if pb is not None else 0.0)
        rows.append({"date": str(d), "tb": tb, "pb": pb, "over": over, "score": score})

    good = [r for r in rows if not r["over"]]
    bad = sorted((r for r in rows if r["over"]), key=lambda r: r["score"])
    kept = [str(master_date)] + [r["date"] for r in good]
    i = 0
    while len(kept) - 1 < min_keep and i < len(bad):      # min_keep 보충(덜 나쁜 것부터)
        kept.append(bad[i]["date"]); i += 1
    rejected = [{"date": r["date"], "reason": " · ".join(r["over"]),
                 "temporal_days": r["tb"], "perp_m": (round(r["pb"], 1) if r["pb"] is not None else None)}
                for r in bad[i:]]
    return sorted(kept), rejected


def group_tracks(scenes: list[Scene]) -> list[TrackGroup]:
    """(방향, path, frame) 별로 묶어 취득 많은 순으로 정렬."""
    buckets: dict[tuple[str, int, int], list[Scene]] = {}
    for s in scenes:
        buckets.setdefault((s.flight_direction, s.path, s.frame), []).append(s)
    groups = []
    for (fd, path, frame), ss in buckets.items():
        dates = sorted(x.date for x in ss)
        groups.append(TrackGroup(fd, path, frame, len(ss), dates[0], dates[-1]))
    # 취득 최다 → 방향·path·frame 순(결정론적 tie-break)
    groups.sort(key=lambda g: (-g.n_scenes, g.flight_direction, g.path, g.frame))
    return groups


def select_track(
    scenes: list[Scene],
    *,
    orbit_direction: str | None = None,
) -> tuple[TrackGroup | None, list[Scene], list[TrackGroup]]:
    """취득 최다 (방향, path, frame) 트랙을 고른다.

    반환: (선택 트랙, 그 트랙의 장면들[날짜순], 전체 그룹 요약).
    orbit_direction 이 주어지면 그 방향만 후보로 한다(None=자동).
    """
    pool = scenes
    if orbit_direction:
        od = orbit_direction.upper()
        pool = [s for s in scenes if s.flight_direction == od]
    groups = group_tracks(pool)
    if not groups:
        return None, [], []
    best = groups[0]
    chosen = sorted((s for s in pool if (s.flight_direction, s.path, s.frame) == best.key),
                    key=lambda s: s.date)
    return best, chosen, groups
