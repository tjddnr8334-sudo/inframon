"""교량 좌표 → **프레임 자동선정 + 취득** (ASF 조회 → 최적 프레임 다운로드).

`--snap-insar` 에 내장되는 자동 취득 로직. 점 타깃(교량)이 S1 프레임 가장자리에 걸리면
burst 커버리지 밖이 되므로(→ snap-windows-backend 참고), 교량을 덮는 모든 SLC 를 ASF 에서
조회해 **프레임별 footprint 중심성**으로 순위를 매기고, 상위 후보의 기준영상을 받아
**burst 포함(contained) 검증** 후 그 프레임의 스택을 내려받는다.

네트워크는 `_geo_search`/`_download_urls` 두 곳으로 격리(테스트 monkeypatch). 자격증명은
slc_download.build_session(토큰>ID·PW>~/.netrc) 재사용.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .snap_backend import (
    SnapError,
    _edge_margin_km,
    _point_in_poly,
    find_bridge_burst,
)


class AcquireError(RuntimeError):
    """프레임 조회·선정·다운로드 실패."""


@dataclass
class FrameCandidate:
    direction: str          # ASCENDING|DESCENDING
    path: int               # relative orbit
    frame: int
    centrality_km: float    # 교량이 footprint 안쪽 margin[km](밖이면 음수), 프레임 중앙값
    scenes: list[dict] = field(default_factory=list)   # [{date,name,url,bytes}]

    @property
    def n_scenes(self) -> int:
        return len(self.scenes)

    def label(self) -> str:
        d = "ASC" if self.direction == "ASCENDING" else "DESC"
        return f"{d} path{self.path} frame{self.frame}"


# ── 네트워크 격리 지점 ────────────────────────────────────────────────────
def _geo_search(lat: float, lon: float, start: str, end: str) -> list[dict]:
    """ASF geo_search(교량점 교차 S1 IW SLC) → 속성 dict 리스트."""
    import asf_search as asf

    opts = asf.ASFSearchOptions(
        intersectsWith=f"POINT({lon} {lat})",
        platform=asf.PLATFORM.SENTINEL1, processingLevel="SLC", beamMode="IW",
        start=start, end=end,
    )
    out = []
    for r in asf.geo_search(opts=opts):
        p = r.properties
        if "VV" not in (p.get("polarization") or "").upper():
            continue
        out.append({
            "date": p["startTime"][:10], "name": p["sceneName"], "url": p.get("url"),
            "bytes": p.get("bytes"), "direction": p.get("flightDirection"),
            "path": p.get("pathNumber"), "frame": p.get("frameNumber"),
            "geometry": r.geometry,
        })
    return out


def _download_urls(urls: list[str], out_dir: str, session) -> None:
    import asf_search as asf

    asf.download_urls(urls=urls, path=out_dir, session=session)


# ── 중심성 / 프레임 순위 ──────────────────────────────────────────────────
def _footprint_poly(geometry: dict) -> list[tuple[float, float]]:
    """GeoJSON Polygon → [(lon,lat),...] (외곽 링)."""
    coords = geometry["coordinates"][0]
    return [(float(c[0]), float(c[1])) for c in coords]


def _centrality_km(lat: float, lon: float, geometry: dict) -> float:
    """교량이 footprint 안이면 +가장자리 margin[km], 밖이면 −거리[km]."""
    try:
        poly = _footprint_poly(geometry)
    except (KeyError, TypeError, IndexError):
        return float("-inf")
    m = _edge_margin_km(lon, lat, poly)
    return m if _point_in_poly(lon, lat, poly) else -m


def search_frames(lat: float, lon: float, *, start: str, end: str,
                  search_fn=_geo_search, min_scenes: int = 10) -> list[FrameCandidate]:
    """교량을 덮는 프레임 후보 조회 → 순위. **장면수 충분(≥min_scenes) 우선, 그 다음 중심성.**

    (중심성만으론 잘 덮지만 장면 2장뿐인 프레임이 뽑혀 시계열 불가 → 장면수 게이팅.)
    """
    scenes = search_fn(lat, lon, start, end)
    groups: dict[tuple, dict[str, dict]] = {}      # key → {date: scene}(날짜 중복제거)
    cents: dict[tuple, list[float]] = {}
    for s in scenes:
        key = (s["direction"], s["path"], s["frame"])
        groups.setdefault(key, {}).setdefault(s["date"], s)
        cents.setdefault(key, []).append(_centrality_km(lat, lon, s["geometry"]))
    cands: list[FrameCandidate] = []
    for (d, p, f), bydate in groups.items():
        items = sorted(bydate.values(), key=lambda x: x["date"])
        med = statistics.median(cents[(d, p, f)]) if cents[(d, p, f)] else float("-inf")
        cands.append(FrameCandidate(d, p, f, med, items))
    # 장면수 충분(≥min_scenes) 우선 → 그 다음 중심성 → 장면수. (커버리지 밖 음수는 뒤로)
    cands.sort(key=lambda c: (c.n_scenes >= min_scenes, c.centrality_km, c.n_scenes),
               reverse=True)
    return cands


# ── 취득(선정 → 다운로드 → burst 검증) ──────────────────────────────────
@dataclass
class AcquireResult:
    frame: FrameCandidate
    slc_dir: str
    downloaded: list[str]
    contained: bool
    burst: object          # BurstLoc
    considered: list[str]  # 검증한 프레임 라벨(순위순)


def acquire(
    lat: float, lon: float, out_dir: str | Path,
    *, count: int = 8, start: str, end: str, min_scenes: int = 5,
    username: str | None = None, password: str | None = None, token: str | None = None,
    search_fn=_geo_search, download_fn=_download_urls, session=None, verify: bool = True,
) -> AcquireResult:
    """교량을 잘 덮는 프레임을 골라 count 장 다운로드. 상위 후보부터 기준영상 burst 포함을
    검증(verify)해 contained=True 인 첫 프레임 채택. slc_dir 은 `<out_dir>/SLC`.
    """
    cands = [c for c in search_frames(lat, lon, start=start, end=end, search_fn=search_fn)
             if c.n_scenes >= min_scenes]
    if not cands:
        raise AcquireError(f"교량({lat},{lon})을 덮는 프레임(장면≥{min_scenes})을 못 찾음")

    if session is None and (verify or True):
        from .slc_download import build_session
        session, _ = build_session(username=username, password=password, token=token)

    out = Path(out_dir); slc_dir = out / "SLC"; slc_dir.mkdir(parents=True, exist_ok=True)
    considered: list[str] = []
    for cand in cands:
        considered.append(f"{cand.label()} (중심성 {cand.centrality_km:+.1f}km, {cand.n_scenes}장)")
        picked = cand.scenes[:count]
        ref = picked[0]
        # 기준영상만 먼저 받아 burst 포함 검증
        ref_zip = slc_dir / f"{ref['name']}.zip"
        if not (ref_zip.exists() and ref_zip.stat().st_size > 0):
            download_fn([ref["url"]], str(slc_dir), session)
        burst = find_bridge_burst(str(ref_zip), lat, lon)
        if verify and not burst.contained:
            # 이 프레임은 커버리지 밖 → 다음 후보(기준영상은 남겨둠)
            continue
        # 채택: 나머지 장면 다운로드
        rest = [s["url"] for s in picked[1:]
                if not (slc_dir / f"{s['name']}.zip").exists()]
        if rest:
            download_fn(rest, str(slc_dir), session)
        got = [str(slc_dir / f"{s['name']}.zip") for s in picked
               if (slc_dir / f"{s['name']}.zip").exists()]
        return AcquireResult(cand, str(slc_dir), got, burst.contained, burst, considered)

    raise AcquireError("모든 후보 프레임이 burst 커버리지 밖입니다("
                       "다른 궤도/기간을 넓혀 재조회하세요). 검토: " + "; ".join(considered))
