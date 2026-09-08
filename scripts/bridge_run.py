#!/usr/bin/env python3
"""교량 하나당 **좌표 하나**로 끝까지 — 임의 교량 배치 실행.

지금까지 청양교·정자교를 스크립트에 상수를 박아 하나씩 했다. 방법은 같으니 자동으로 간다:

  ① 제원     좌표 → 파트너 실측 CSV(연장·경간·폭) → 없으면 OSM(폭은 보도 간격) → 없으면 사유
  ② 데크선   OSM 차도 중심선 + 양측 보도 → 방위·폭
  ③ 지면     DEM(Open-Meteo) 표고
  ④ 점 선택  트랙에서 쉬프트 보정(heading 정규화·δh) 후 데크 ±30 m
  ⑤ 잔차고도 SNAP star 산출물(.dim)이 있으면 B⊥ 로 추정 → δh 를 관측값으로
  ⑥ IFC 트윈 프록시 IFC4 → 되읽어 결합 → .glb · 뷰어 · 3D Tiles
  ⑦ PINN·CRI 같은 점으로 가상센싱 + 위험도
  ⑧ 브리프   4단 그림 (a)(b)(c)(d) · 속도 95% CI QC · 교대 기준점
  ⑨ 감사     산출물 감사 → 보고 가능/조건부/불가
  ⑩ 결과 문서 out/<교량>/결과.md

없는 것은 없다고 적고 넘어간다 — 멈추지 않는다. 각 단계의 근거가 결과.md 에 남는다.

    python scripts/bridge_run.py --name 내곡교 --lat 37.746953 --lon 128.887733 \\
        --track data/naegok_track_asc.h5 --out docs/bridges/내곡교
    python scripts/bridge_run.py --name 청양교 --lat 36.450655 --lon 126.80732     # 트랙 없음 →
        # ⓪ SLC 검색·다운로드(Earthdata 토큰) → SNAP 처리 → 언래핑(실패 시 자동 재시도) 후 계속
    python scripts/bridge_run.py --batch bridges.json      # [{name,lat,lon,track?,proc?,master?}, ...]

트랙이 없으면 ⓪ 단계가 `inframon.pipeline_bridge.run_bridge_pipeline(mode="full")` 을 불러
SLC 부터 만든다. 오래 걸린다(다운로드 수 GB · SNAP 수십 분) — 진행 상황을 단계별로 찍는다.
`--doctor` 로 Earthdata 토큰·SNAP·snaphu 가 있는지 먼저 본다.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DECK_SEL_M = 30.0
DEFAULT_CLEARANCE_M = 6.0
FOOTWAY_HALF_M = 1.5
CRS = "EPSG:5186"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


@dataclass
class Bridge:
    name: str
    lat: float
    lon: float
    track: str | None = None           # 없으면 ⓪ SLC→InSAR 부터 만든다
    proc: str | None = None            # SNAP star 처리 폴더(잔차고도용)
    master: str | None = None          # star 기준일 YYYYMMDD
    baselines: str | None = None       # SARvey ifg_network 기선 JSON(잔차고도용)
    out: str | None = None
    # 아래는 자동으로 채운다
    length_m: float | None = None
    n_spans: int | None = None
    width_m: float | None = None
    clearance_m: float | None = None
    ground_m: float | None = None
    deck_az_deg: float | None = None
    bridge_type: str = "girder"        # #11 형식별 PINN — CSV 상부구조형식에서
    material: str | None = None
    n_epochs: int | None = None
    geometry: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    sources: dict = field(default_factory=dict)


def note(b: Bridge, msg: str) -> None:
    b.notes.append(msg)
    print(f"      · {msg}")


# ── ① 제원 ──────────────────────────────────────────────────────────────
def resolve_specs(b: Bridge) -> None:
    from inframon.bridge_specs_csv import lookup
    try:
        sp = lookup(b.lat, b.lon, name=b.name)
    except Exception as e:                       # noqa: BLE001
        sp = None
        note(b, f"제원 CSV 조회 실패: {type(e).__name__}")
    if sp and sp.length_m:
        b.length_m, b.n_spans, b.width_m = sp.length_m, sp.n_spans, sp.width_m
        b.sources["specs"] = f"파트너 실측 CSV '{sp.name}' ({sp.dist_m:.0f} m)"
        if b.width_m is None:
            note(b, "CSV 에 폭 없음 → OSM 보도 간격으로")
        # #11 형식별 PINN: CSV 상부구조형식 → bridge_type (PSCI·박스·라멘·아치·사장·현수…)
        if sp.structure_raw:
            from inframon.public_data import parse_structure_ko
            bt, mat = parse_structure_ko(str(sp.structure_raw))
            if bt:
                b.bridge_type, b.material = bt, (mat or sp.material)
                b.sources["bridge_type"] = f"CSV '{sp.structure_raw}' → {bt}"
        elif sp.material:
            b.material = sp.material
        # #12 형하고: 표준데이터 '교량높이'가 있으면 그것
        try:
            from inframon.public_data import find_bridge_csv, nearest_bridge_profile
            from inframon.insar.deck_z import clearance_from_profile
            csv = find_bridge_csv("data")
            prof = nearest_bridge_profile(csv, b.lat, b.lon, max_km=0.3) if csv else None
            c = clearance_from_profile(prof) if prof else None
            if c:
                b.clearance_m = float(c)
                b.sources["clearance"] = f"전국교량표준데이터 교량높이 {c:g} m"
        except Exception:                    # noqa: BLE001 — 없으면 뒤에서 가정
            pass
    else:
        note(b, "제원 CSV 에 없음 → OSM 연장으로")


def _fill_defaults(b: Bridge) -> None:
    """데크선을 못 구했을 때도 트윈·PINN 이 돌 수 있게 — 가정임을 적는다."""
    if b.length_m is None:
        b.length_m = 60.0
        note(b, "연장 정보 없음 → 60 m 가정")
    if b.width_m is None:
        b.width_m = 12.0
        note(b, "폭 정보 없음 → 12 m 가정")
    if b.n_spans is None:
        b.n_spans = max(1, int(round(b.length_m / 30.0)))
        note(b, f"경간수 없음 → 30 m 규칙으로 {b.n_spans} 가정")


# ── ② 데크선 ─────────────────────────────────────────────────────────────
def resolve_deck(b: Bridge) -> None:
    from inframon.insar.osm_bridge import _overpass_query, find_bridges_near
    try:
        cands = find_bridges_near(b.lat, b.lon, radius_m=250.0)
    except Exception as e:                       # noqa: BLE001
        note(b, f"OSM 조회 실패: {type(e).__name__} — 데크선 없음")
        _fill_defaults(b)
        return
    if not cands:
        note(b, "OSM 에 교량 way 없음")
        _fill_defaults(b)
        return
    # 어느 way 가 이 교량인가 — CSV 연장을 알면 **연장이 맞는 것**을 고른다. 이름만 믿으면
    # 같은 이름의 접속 고가부(내곡교 401 m)가 잡혀 실교량(162 m)을 놓친다.
    # 보도(footway)는 데크선이 아니다 — 양측 보도가 차도와 같은 연장이라 연장 매칭에 걸린다.
    # 차도 way 를 먼저, 그 안에서 연장 → 이름 순으로 고른다.
    def _is_road(c) -> bool:
        return (getattr(c, "tags", {}) or {}).get("highway") not in ("footway", "path", "cycleway")
    roads = [c for c in cands if _is_road(c)] or cands
    if b.length_m:
        fit = [c for c in roads if abs(c.length_m - b.length_m) <= 0.25 * b.length_m]
        if fit:
            named = [c for c in fit if c.name and b.name and b.name in c.name]
            road = min(named or fit, key=lambda c: abs(c.length_m - b.length_m))
            b.sources["deck_match"] = (f"CSV 연장 {b.length_m:.0f} m 에 맞는 way 선택"
                                       f"({len(cands)}후보 중)")
        else:
            named = [c for c in roads if c.name and b.name and b.name in c.name]
            road = max(named or roads, key=lambda c: c.length_m)
            note(b, f"OSM 어느 way 도 CSV 연장 {b.length_m:.0f} m 와 안 맞음 — "
                    f"'{road.name}' {road.length_m:.0f} m 사용")
    else:
        named = [c for c in roads if c.name and b.name and b.name in c.name]
        road = max(named or roads, key=lambda c: c.length_m)
    b.geometry = [list(p) for p in road.geometry]
    if b.length_m is None:
        b.length_m = float(road.length_m)
        b.sources["length"] = f"OSM way {road.name}"
    P = np.asarray(b.geometry, float)
    lat0 = P[:, 0].mean()
    dx = (P[-1, 1] - P[0, 1]) * math.cos(math.radians(lat0)) * 111_320
    dy = (P[-1, 0] - P[0, 0]) * 111_320
    b.deck_az_deg = float(math.degrees(math.atan2(dy, dx)))
    b.sources["deck"] = f"OSM '{road.name}' {road.length_m:.0f} m · 방위 {b.deck_az_deg:.1f}°"
    if b.width_m is None:
        try:
            els = _overpass_query(f"[out:json][timeout:30];way(around:120,{b.lat},{b.lon})"
                                  f"[\"bridge\"][\"highway\"=\"footway\"];out geom;")["elements"]
            offs = []
            for el in els:
                F = np.asarray([[g["lat"], g["lon"]] for g in el.get("geometry", []) if "lat" in g], float)
                if len(F) >= 2:
                    offs.append(float(np.mean(F[:, 0] - lat0) * 111_320))
            if len(offs) >= 2:
                b.width_m = float(max(offs) - min(offs)) + 2 * FOOTWAY_HALF_M
                b.sources["width"] = f"OSM 보도 간격 {max(offs) - min(offs):.1f} m + 보도 반폭"
        except Exception:                        # noqa: BLE001
            pass
    if b.width_m is None:
        lanes = getattr(road, "tags", {}).get("lanes")
        if lanes:
            b.width_m = float(lanes) * 3.5 + 3.0
            b.sources["width"] = f"OSM lanes={lanes} × 3.5 m + 여유(추정)"
            note(b, f"폭 추정 {b.width_m:.1f} m — 실측 아님")
        else:
            b.width_m = 12.0
            note(b, "폭 정보 없음 → 12 m 가정")
    if b.n_spans is None:
        b.n_spans = max(1, int(round((b.length_m or 30) / 30.0)))
        note(b, f"경간수 없음 → 30 m 규칙으로 {b.n_spans} 가정")


# ── ③ 지면·형하고 ─────────────────────────────────────────────────────────
def resolve_ground(b: Bridge) -> None:
    from inframon.insar.deck_z import _ground_elevation
    g = _ground_elevation(np.array([[b.lon, b.lat]]))
    if g is None:
        with h5py.File(b.track, "r") as f:
            if "height" in f:
                ll = np.asarray(f["pixel_lonlat"][()], float)
                d = np.hypot((ll[:, 0] - b.lon) * math.cos(math.radians(b.lat)) * 111_320,
                             (ll[:, 1] - b.lat) * 111_320)
                h = np.asarray(f["height"][()], float)[d <= 150]
                if h.size:
                    g = float(np.nanmedian(h))
                    b.sources["ground"] = "트랙 height 중앙값(150 m 내)"
    else:
        b.sources["ground"] = "Open-Meteo DEM"
    b.ground_m = g if g is not None else 0.0
    if g is None:
        note(b, "지면 표고 없음 → 0 m")
    if b.clearance_m is None:
        b.clearance_m = DEFAULT_CLEARANCE_M
        note(b, f"형하고 {DEFAULT_CLEARANCE_M} m 가정(표준데이터 교량높이 없음) — 잔차고도로 대체 시도")


# ── ⓪ 트랙이 없으면 SLC 검색·다운로드 → InSAR 처리 → 언래핑 ──────────────────
def acquire_track(b: Bridge, out: Path) -> str | None:
    """`run_bridge_pipeline(mode="full")` 로 SLC 부터 트랙까지. 단계별 진행을 그대로 찍는다.

    외부 의존: Earthdata 토큰(다운로드) · SNAP gpt(처리) · snaphu(언래핑). 없으면 어떤 것이
    없는지 말하고 None 을 돌려준다 — 프로그램이 대신 설치·가입해 줄 수는 없다.
    """
    from inframon.doctor import run_doctor
    rep = run_doctor()
    missing = [k for k in ("earthdata", "snap_gpt", "snaphu") if not rep.tools.get(k, {}).get("ok")]
    if missing:
        for k in missing:
            note(b, rep.tools[k]["hint"])
        note(b, "트랙을 만들 수 없다 — 위 도구·자격을 준비한 뒤 다시 실행")
        return None
    from inframon.insar.slc_download import find_earthdata_token
    from inframon.pipeline_bridge import run_bridge_pipeline
    token, _ = find_earthdata_token()
    pdir = out / "pipeline"
    print(f"      SLC 검색·다운로드 → SNAP → 언래핑 (오래 걸린다) → {pdir}")
    prep = run_bridge_pipeline(b.lat, b.lon, out_dir=pdir, mode="full",
                               earthdata_token=token, snap_count=12)
    for r in prep.stages:
        mark = {"done": "✅", "partial": "🟡", "skip": "⏭", "error": "❌"}.get(r.status, "·")
        print(f"      {mark} {r.stage}: {r.detail[:90]}")
    prep.write_json(pdir / "pipeline_report.json")
    eng = prep.context.get("insar_engine") or {}
    track = eng.get("track_h5")
    if not track or not Path(track).exists():
        note(b, "⓪ InSAR 처리가 트랙을 만들지 못했다 — pipeline_report.json 참조")
        return None
    b.sources["track"] = f"⓪ 자동 생성 {Path(track).name} ({eng.get('n_points')}점)"
    # star 네트워크 산출물이 있으면 잔차고도용으로 잡아 둔다
    snap = prep.context.get("snap") or {}
    if snap.get("master") and (pdir / f"snaphu_{snap['master']}_" ).parent.exists():
        b.proc, b.master = str(pdir), str(snap["master"])
    return str(track)


# ── ④ 점 선택 ─────────────────────────────────────────────────────────────
def select_points(b: Bridge, out: Path) -> Path | None:
    from inframon.insar.chainage import _signed_offset
    from inframon.insar.deck_geometry import project_to_polyline
    from inframon.insar.geolocation import apply_correction
    from inframon.insar.track_reader import normalize_heading_deg

    with h5py.File(b.track, "r") as f:
        ll = np.asarray(f["pixel_lonlat"][()], float)
        los = np.asarray(f["los_mm"][()], float)
        coh = np.asarray(f["coh"][()], float)
        inc = np.asarray(f["incidenceAngle"][()], float) if "incidenceAngle" in f else np.full(len(ll), 39.0)
        ep = f["epochs"][()]
        heading = normalize_heading_deg(float(f.attrs.get("HEADING", 0.0) or 0.0))
        extra = {k: f[k][()] for k in ("height", "dem_error", "amplitude_dispersion",
                                       "residual_height_m", "residual_height_sigma_m") if k in f}
        attrs = dict(f.attrs)
    # 처리 오프셋(B): 지면 점 vs OSM 도로선. 유의하면 전 점군에서 뺀다 — A(δh/tanθ) 보정 전에.
    try:
        from inframon.insar import ground_offset as go
        roads = go.fetch_roads(b.lat, b.lon, cache=out / "osm_roads_500m.json")
        off = go.estimate(ll, roads, center_lat=b.lat, center_lon=b.lon)
        ll = go.apply(ll, off, center_lat=b.lat)
        b.sources["ground_offset"] = off.describe()
        if not off.significant and off.n_points >= go.MIN_POINTS:
            note(b, "처리 오프셋 ≈ 0 → 교량 위 점의 횡방향 치우침은 산란체 위치(보도·난간)로 본다")
    except Exception as e:                       # noqa: BLE001 — OSM 없어도 진행
        note(b, f"처리 오프셋 추정 불가({type(e).__name__}) — 0 으로 두고 진행")
    corr = apply_correction(ll, np.full(len(ll), float(b.clearance_m)), inc, heading,
                            crs_is_lonlat=True, set_height=False)
    ll1 = np.asarray(corr["xyz"], float)[:, :2]
    if len(b.geometry) >= 2:
        st, of = project_to_polyline(ll1, b.geometry)
        of = _signed_offset(ll1, b.geometry, of)
        m = (np.abs(of) <= DECK_SEL_M) & (st >= -5) & (st <= (b.length_m or 0) + 5)
    else:
        d = np.hypot((ll1[:, 0] - b.lon) * math.cos(math.radians(b.lat)) * 111_320,
                     (ll1[:, 1] - b.lat) * 111_320)
        m = d <= max(DECK_SEL_M, (b.length_m or 60) / 2 + DECK_SEL_M)
        note(b, "데크선 없어 반경으로 선택")
    n = int(m.sum())
    b.sources["points"] = (f"쉬프트 {np.mean(corr['shift_m']):.1f} m(heading {heading:.1f}°) 보정 후 "
                           f"데크 ±{DECK_SEL_M:.0f} m 안 {n}/{len(ll)}")
    if n < 3:
        note(b, f"데크 ±{DECK_SEL_M:.0f} m 안 점 {n}개 — 트윈·PINN 불가")
        return None
    sub = out / "track_deck.h5"
    with h5py.File(sub, "w") as o:
        o["pixel_lonlat"] = ll1[m]
        o["src_index"] = np.where(m)[0].astype(np.int64)     # 원 트랙 인덱스(잔차고도 매칭용)
        o["los_mm"] = los[m].astype(np.float32)
        o["coh"] = coh[m].astype(np.float32)
        o["incidenceAngle"] = inc[m].astype(np.float32)
        o["epochs"] = ep
        for k, v in extra.items():
            o[k] = np.asarray(v)[m]
        for k, v in attrs.items():
            o.attrs[k] = v
        o.attrs["HEADING"] = heading
        o.attrs["geolocation_correction"] = json.dumps(
            {"applied": True, "dh_m": b.clearance_m, "heading_deg": heading,
             "mean_shift_m": float(np.mean(corr["shift_m"]))}, ensure_ascii=False)
    return sub


# ── ⑤ 잔차고도 ────────────────────────────────────────────────────────────
def residual_height(b: Bridge, sub: Path) -> dict | None:
    if not ((b.proc and b.master) or b.baselines):
        note(b, "SNAP star 처리 폴더도 SARvey 기선 JSON 도 없음 → 잔차고도 불가(브리프 (b) 비움)")
        return None
    from inframon.insar.chainage import _signed_offset
    from inframon.insar.deck_geometry import project_to_polyline
    from inframon.insar.residual_height import run_sarvey, run_snap_star
    # 집단 검정에는 지면 점이 필요하다 — 데크 ±30 m 부분집합에는 지면 점이 거의 없다.
    # 그래서 **원 트랙 전체**(교량 주변 ±150 m)로 추정하고, 값을 부분집합 점에 옮긴다.
    full = sub.parent / "track_rh_full.h5"
    with h5py.File(b.track, "r") as f:
        ll_all = np.asarray(f["pixel_lonlat"][()], float)
    d = np.hypot((ll_all[:, 0] - b.lon) * math.cos(math.radians(b.lat)) * 111_320,
                 (ll_all[:, 1] - b.lat) * 111_320)
    near = d <= max(150.0, (b.length_m or 60) / 2 + 100)
    with h5py.File(b.track, "r") as f, h5py.File(full, "w") as o:
        for k in f.keys():
            a = np.asarray(f[k][()])
            o[k] = a[near] if a.shape[:1] == (len(near),) else a
        for k, v in f.attrs.items():
            o.attrs[k] = v
    ll = ll_all[near]
    on = None
    if len(b.geometry) >= 2 and b.width_m:
        # 원 좌표는 쉬프트 전이다 — 교면 위 판정은 보정 후 좌표로 해야 맞다(④와 같은 보정)
        from inframon.insar.geolocation import apply_correction
        from inframon.insar.track_reader import normalize_heading_deg
        with h5py.File(b.track, "r") as f:
            inc_n = (np.asarray(f["incidenceAngle"][()], float)[near] if "incidenceAngle" in f
                     else np.full(len(ll), 39.0))
            hd = normalize_heading_deg(float(f.attrs.get("HEADING", 0.0) or 0.0))
        ll_c = np.asarray(apply_correction(ll, np.full(len(ll), float(b.clearance_m)), inc_n, hd,
                                           crs_is_lonlat=True, set_height=False)["xyz"], float)[:, :2]
        st, of = project_to_polyline(ll_c, b.geometry)
        of = _signed_offset(ll_c, b.geometry, of)
        on = (np.abs(of) <= b.width_m / 2) & (st >= -2) & (st <= (b.length_m or 0) + 2)
    try:
        if b.baselines:
            rh, g = run_sarvey(full, b.baselines, on_deck=on)
        else:
            rh, g = run_snap_star(full, b.proc, b.master, on_deck=on)
    except Exception as e:                       # noqa: BLE001
        note(b, f"잔차고도 실패: {e}")
        return None
    # 부분집합(track_deck.h5) 점에 좌표로 매칭해 옮긴다 — 브리프 (b) 가 읽는다
    with h5py.File(sub, "a") as f:
        ls = np.asarray(f["pixel_lonlat"][()], float)
        # 부분집합 좌표는 쉬프트 보정본이라 원좌표와 다르다 → 최근접 매칭(화소/2 이내)
        from scipy.spatial import cKDTree
        k = math.cos(math.radians(b.lat)) * 111_320
        tree = cKDTree(np.column_stack([ll[:, 0] * k, ll[:, 1] * 111_320]))
        # 부분집합은 보정 후 좌표 → 보정 전으로 되돌릴 수 없으니 원 트랙의 같은 인덱스를 쓴다
        idx_sub = np.asarray(f["src_index"][()]) if "src_index" in f else None
        if idx_sub is not None:
            pos = {int(i): j for j, i in enumerate(np.where(near)[0])}
            rows = np.array([pos.get(int(i), -1) for i in idx_sub])
            ok = rows >= 0
            rh_sub = np.full(len(ls), np.nan); sg_sub = np.full(len(ls), np.nan)
            rh_sub[ok] = rh.dh_m[rows[ok]]; sg_sub[ok] = rh.sigma_m[rows[ok]]
        else:
            _, rows = tree.query(np.column_stack([ls[:, 0] * k, ls[:, 1] * 111_320]))
            rh_sub, sg_sub = rh.dh_m[rows], rh.sigma_m[rows]
        for key, val in (("residual_height_m", rh_sub), ("residual_height_sigma_m", sg_sub)):
            if key in f:
                del f[key]
            f[key] = val.astype(np.float32)
        f.attrs["residual_height"] = json.dumps({**rh.meta, "group_test": g or {}}, ensure_ascii=False)
    if g and g.get("ok") and g["z"] < -2:
        note(b, g["verdict"])
    if g and g.get("ok") and g["diff_m"] > 0 and g["z"] >= 1.0:
        b.clearance_m = float(g["diff_m"])
        b.sources["clearance"] = f"잔차고도 집단평균 {g['diff_m']:+.1f}±{g['se_diff_m']:.1f} m (z={g['z']:.2f})"
    return g


# ── ⑥ IFC 트윈 ────────────────────────────────────────────────────────────
def build_twin(b: Bridge, sub: Path, out: Path) -> dict:
    from pyproj import Transformer

    from inframon.bim.georef import MapConversion
    from inframon.bim.ifc_io import read_elements, read_map_conversion
    from inframon.bim.ifc_write import write_elements
    from inframon.bim.proxy_model import bridge_elements
    from inframon.insar.gltf_export import (export_insar_gltf, guid_map_from_alignment,
                                            write_3dtiles_tileset, write_web_viewer)
    els = bridge_elements(length_m=b.length_m, width_m=b.width_m, n_spans=b.n_spans,
                          clearance_m=b.clearance_m, name=b.name)
    e0, n0 = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(b.lon, b.lat)
    az = math.radians(b.deck_az_deg or 0.0)
    mc = MapConversion(eastings=e0, northings=n0, orthogonal_height=b.ground_m,
                       x_axis_abscissa=math.cos(az), x_axis_ordinate=math.sin(az),
                       target_crs=CRS, source="proxy_placement")
    ifc = out / f"{b.name}_proxy.ifc"
    write_elements(els, ifc, map_conversion=mc, project_name=f"{b.name} 프록시 교량")
    els2, mc2 = read_elements(ifc), read_map_conversion(ifc)
    ej = out / f"{b.name}_elements.json"
    ej.write_text(json.dumps({"elements": [
        {"guid": e.guid, "name": e.name, "ifc_type": e.ifc_type, "member": e.member,
         "bbox_min": list(e.bbox_min), "bbox_max": list(e.bbox_max), "extra": e.extra}
        for e in els2]}, ensure_ascii=False, indent=1), encoding="utf-8")
    # 프로젝트 h5 (계약)
    proj = out / "project.h5"
    from inframon.contracts.io import ProjectStore
    from inframon.insar.track_reader import import_track_h5
    with ProjectStore(proj, mode="w") as store:
        import_track_h5(store, sub, geometry_latlon=b.geometry or None)
    guids, ginfo = guid_map_from_alignment(proj, ej, map_conversion=mc2, ifc_crs=CRS,
                                           max_dist_m=DECK_SEL_M)
    r = export_insar_gltf(proj, out / "twin.glb", value="velocity", element_guids=guids,
                          element_z=ginfo["element_z"], z_source="deck",
                          element_z_datum=b.ground_m)
    write_web_viewer(out / "twin.glb", elements_json=ej, map_conversion=mc2, ifc_crs=CRS)
    write_3dtiles_tileset(out / "twin.glb")
    return {"ifc": str(ifc), "elements": len(els2), "points": r["n_points"], "bound": r["bound"],
            "proj": str(proj), "mc": mc2, "ej": str(ej), "guids": guids, "ginfo": ginfo}


# ── ⑦ PINN·CRI ────────────────────────────────────────────────────────────
def run_pinn(b: Bridge, tw: dict, out: Path) -> dict | None:
    from inframon.custom_pinn import run_custom_pinn
    from inframon.insar.gltf_export import export_insar_gltf, write_web_viewer
    from inframon.structure import BridgeProfile
    prof = BridgeProfile(name=b.name, bridge_type=b.bridge_type,
                         material=b.material or "concrete",
                         length_m=b.length_m, width_m=b.width_m,
                         source=b.sources.get("specs", "osm"),
                         extra={"n_spans": b.n_spans, "clearance_m": b.clearance_m})
    try:
        summ = run_custom_pinn(tw["proj"], b.lat, b.lon, bridge_name=b.name, bridge_profile=prof)
    except Exception as e:                       # noqa: BLE001
        note(b, f"PINN 실패: {type(e).__name__}: {str(e)[:80]}")
        return None
    export_insar_gltf(tw["proj"], out / "twin_cri.glb", value="cri", fram_project=tw["proj"],
                      element_guids=tw["guids"], element_z=tw["ginfo"]["element_z"],
                      z_source="deck", element_z_datum=b.ground_m)
    write_web_viewer(out / "twin_cri.glb", elements_json=tw["ej"], map_conversion=tw["mc"],
                     ifc_crs=CRS)
    return {"cri": float(summ["cri_global_max"]), "warning": summ["warning_level"]}


# ── ⑧ 브리프 그림 ─────────────────────────────────────────────────────────
def brief_figure(b: Bridge, sub: Path, out: Path) -> Path | None:
    if len(b.geometry) < 2:
        return None
    cache = out / "deck_polyline.json"
    cache.write_text(json.dumps({"name": b.name, "length_m": b.length_m,
                                 "geometry": b.geometry}, ensure_ascii=False), encoding="utf-8")
    png = out / "brief.png"
    r = subprocess.run([sys.executable, str(ROOT / "scripts/make_brief_figure.py"), str(sub),
                        "--bridge", b.name, "--lat", str(b.lat), "--lon", str(b.lon),
                        "--height", str(b.clearance_m), "--width", str(b.width_m),
                        "--cache", str(cache), "--offset", str(DECK_SEL_M),
                        "--meta", f"연장 {b.length_m:.0f} m · 폭 {b.width_m:.1f} m · {b.n_spans}경간",
                        "--out", str(png)], env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        note(b, f"브리프 그림 실패: {r.stderr.strip().splitlines()[-1][:100] if r.stderr else '?'}")
        return None
    return png


# ── ⑨ 감사 ────────────────────────────────────────────────────────────────
def audit(b: Bridge, proj: str) -> dict:
    from inframon.audit import audit_artifact
    a = audit_artifact(proj, target=(b.lat, b.lon))
    return {"verdict": a.verdict, "reasons": a.reasons, "notes": a.notes}


# ── ⑩ 결과 문서 ───────────────────────────────────────────────────────────
def _events_md(b: Bridge) -> str:
    """알려진 사고 교량이면 결과 문서에 먼저 적는다."""
    try:
        from inframon.known_events import check
        ep = los = None
        if b.track and Path(b.track).exists():
            sub = Path(b.out or f"docs/bridges/{b.name}") / "track_deck.h5"
            src = sub if sub.exists() else Path(b.track)
            with h5py.File(src, "r") as f:
                ep, los = f["epochs"][()], f["los_mm"][()]
        evs = check(b.lat, b.lon, epochs=ep, los=los)
    except Exception:                            # noqa: BLE001
        return ""
    if not evs:
        return ""
    lines = ["## ⚠ 알려진 사고 교량 — 결과를 읽기 전에", ""]
    for ec in evs:
        lines.append(f"- {ec.describe()}")
        if ec.event.get("note"):
            lines.append(f"  - {ec.event['note']}")
    return "\n".join(lines)


def _f(v, unit: str = "") -> str:
    return f"{v:.1f}{unit}" if isinstance(v, (int, float)) and v == v else "?"


def write_results(b: Bridge, out: Path, tw, pinn, rh, aud, brief) -> None:
    src = "\n".join(f"| {k} | {v} |" for k, v in b.sources.items())
    notes = "\n".join(f"- {n}" for n in b.notes) or "- (없음)"
    md = f"""# {b.name} — 좌표 하나로 끝까지 (bridge_run)

`{b.lat}, {b.lon}` · 트랙 `{Path(b.track).name if b.track else '없음(⓪ 실패)'}`

## 무엇을 어디서 가져왔나

| 항목 | 출처 |
|---|---|
{src}

## 제원(적용값)

| 연장 | 경간 | 폭 | 형하고(δh) | 지면 | 데크 방위 | 형식(PINN PDE) | 시점 |
|---|---|---|---|---|---|---|---|
| {_f(b.length_m, ' m')} | {b.n_spans or '?'} | {_f(b.width_m, ' m')} | {_f(b.clearance_m, ' m')} | {_f(b.ground_m, ' m')} | {_f(b.deck_az_deg, '°')} | {b.bridge_type} · {b.material or '?'} | {b.n_epochs or '?'} |

## 결과

| | 값 |
|---|---|
| IFC 트윈 | {f"부재 {tw['elements']} · 점 {tw['points']} · 결합 {tw['bound']}" if tw else "불가"} |
| 잔차고도 | {f"교면 위 − 밖 {rh['diff_m']:+.1f} ± {rh['se_diff_m']:.1f} m (z={rh['z']:.2f}) — {rh['verdict']}" if rh and rh.get('ok') else "없음(SNAP star 산출물 필요)"} |
| PINN · CRI | {f"CRI {pinn['cri']:.3f} · {pinn['warning']}" if pinn else "불가"} |
| 감사 | **{aud['verdict']}** {'· ' + ' · '.join(aud['reasons']) if aud['reasons'] else ''} |

{chr(10).join('ⓘ ' + n for n in aud['notes'])}

## 그림

{'![brief](brief.png)' if brief else '(브리프 그림 없음 — 데크선 없음)'}

3D: `twin.viewer.html` (속도) · `twin_cri.viewer.html` (CRI) — 더블클릭

## 적어 둘 것

{notes}

{_events_md(b)}

## 이 파이프라인이 원리상 못 하는 것 (모든 교량 공통)

- **EI(강성) 관측 식별** — InSAR 는 상대 변위라 자중 처짐을 못 본다. EI·f₁ 은 설계 제원 기반이다(감사 ⓘ).
- **데크 위 PS 밀도** — Sentinel-1 화소 ~11 m. 프로그램으로 늘릴 수 없다. 고해상도 SAR·코너리플렉터가 답이다.
- **쉬프트 방향(각도)** — 궤도 heading 이 정한다. 크기 δh 만 잔차고도로 관측값이 된다.
- **쉬프트의 세 층** — A 기하(δh/tanθ, 보정) · B 처리 오프셋(지면 점 vs OSM 도로선으로 추정, 유의하면 제거) · C 산란체 위치(보도·난간 — 쉬프트가 아님). B 는 OSM 기준 상대값이라 3~5 m 아래는 못 본다.
- **시점 수** — 속도 95 % CI 는 시점 수와 기간이 정한다({b.n_epochs or '?'}시점). 브리프 기준(≥100장)에 못 미치면 판정을 보류한다.
"""
    (out / "결과.md").write_text(md, encoding="utf-8")


def run_one(b: Bridge) -> dict:
    out = Path(b.out or f"docs/bridges/{b.name}")
    out.mkdir(parents=True, exist_ok=True)
    print(f"\n━━ {b.name} ({b.lat}, {b.lon}) → {out}")
    tw = pinn = rh = brief = None
    aud = {"verdict": "불가", "reasons": [], "notes": []}
    try:
        if not b.track:
            print("  ⓪ SLC → InSAR (트랙 없음)")
            b.track = acquire_track(b, out)
        if not b.track:
            raise RuntimeError("트랙 없음 — ⓪ 실패")
        with h5py.File(b.track, "r") as f:
            b.n_epochs = int(f["los_mm"].shape[1])
        print("  ① 제원");   resolve_specs(b)
        print("  ② 데크선"); resolve_deck(b)
        print("  ③ 지면");   resolve_ground(b)
        print("  ④ 점 선택"); sub = select_points(b, out)
        if sub is not None:
            print("  ⑤ 잔차고도"); rh = residual_height(b, sub)
            print("  ⑥ IFC 트윈"); tw = build_twin(b, sub, out)
            print(f"      부재 {tw['elements']} · 점 {tw['points']} · 결합 {tw['bound']}")
            print("  ⑦ PINN·CRI"); pinn = run_pinn(b, tw, out)
            if pinn:
                print(f"      CRI {pinn['cri']:.3f} · {pinn['warning']}")
            print("  ⑧ 브리프"); brief = brief_figure(b, sub, out)
            print("  ⑨ 감사");   aud = audit(b, tw["proj"])
            print(f"      {aud['verdict']}")
    except Exception:                            # noqa: BLE001 — 한 교량이 죽어도 배치는 간다
        note(b, "예외: " + traceback.format_exc().strip().splitlines()[-1])
    print("  ⑩ 결과 문서"); write_results(b, out, tw, pinn, rh, aud, brief)
    (out / "bridge.json").write_text(json.dumps(asdict(b), ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    return {"name": b.name, "verdict": aud["verdict"], "points": tw["points"] if tw else 0,
            "cri": pinn["cri"] if pinn else None, "out": str(out)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name"); ap.add_argument("--lat", type=float); ap.add_argument("--lon", type=float)
    ap.add_argument("--track"); ap.add_argument("--proc"); ap.add_argument("--master")
    ap.add_argument("--baselines", help="SARvey ifg_network 기선 JSON(잔차고도용)")
    ap.add_argument("--out"); ap.add_argument("--batch")
    a = ap.parse_args()
    if a.batch:
        items = json.loads(Path(a.batch).read_text(encoding="utf-8"))
        bridges = [Bridge(**it) for it in items]
    else:
        if not (a.name and a.lat and a.lon):
            ap.error("--name --lat --lon (--track 은 선택: 없으면 SLC 부터 만든다) 또는 --batch")
        bridges = [Bridge(name=a.name, lat=a.lat, lon=a.lon, track=a.track, proc=a.proc,
                          master=a.master, baselines=a.baselines, out=a.out)]
    results = [run_one(b) for b in bridges]
    print("\n━━ 요약")
    print(f"{'교량':<10}{'판정':<10}{'점':>5}{'CRI':>8}  경로")
    for r in results:
        cri = f"{r['cri']:.3f}" if r["cri"] is not None else "—"
        print(f"{r['name']:<10}{r['verdict']:<10}{r['points']:>5}{cri:>8}  {r['out']}")


if __name__ == "__main__":
    main()
