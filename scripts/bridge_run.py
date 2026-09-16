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

# 한국어 Windows 콘솔(cp949)에서 '⓪' 같은 글자로 죽지 않게 — --help 조차 못 찍었다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DECK_SEL_M = 30.0
DEFAULT_CLEARANCE_M = 6.0
CLEARANCE_MAX_M = 40.0        # 국내 하천 교량 형하고 상한(잔차고도 폭주 차단)
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
    superstructure: bool = True        # 형식별 상부구조(주탑·케이블·아치·트러스)를 세울지
    max_span_m: float | None = None    # 최대경간장 실측 — 교각 비등간격 배치의 근거
    span_layout: str = "auto"          # auto | equal | measured (proxy_model.span_edges)
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
        if sp.max_span_m:
            b.max_span_m = float(sp.max_span_m)
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


def _warn_stack_predates_build(b: Bridge, epochs) -> None:
    """스택이 준공보다 이른가 — 그러면 교면 PS 가 통째로 버려진다.

    PS 선별은 **첫 쌍의 coherence** 로 한다. 다리가 없던 시절 영상과의 결맞음을 보면
    교면 화소가 전부 탈락한다. 월드컵대교(주경간교 2021 준공)가 그랬다 — 2018~2025
    스택에서 데크 ±30 m 안 PS 가 20000점 중 1점이었고 트윈·PINN 이 불가였다.
    준공 이후(28장)로 다시 쌓으니 54점이 살아났다.

    고치는 법은 SLC 재처리가 아니다 — 언래핑된 간섭도는 그대로 두고 기간만 자르면 된다:
        python scripts/rebuild_track.py --proc <처리폴더> --out <새 트랙> \\
            --lat .. --lon .. --since <준공일> --like <기존 트랙>
    """
    if epochs is None or len(epochs) == 0:
        return
    try:
        from inframon.public_data import find_bridge_csv, nearest_bridge_profile
        prof = nearest_bridge_profile(find_bridge_csv("data"), b.lat, b.lon, max_km=0.5)
        year = int(str((prof.extra or {}).get("completion") or "")[:4])
    except (TypeError, ValueError, AttributeError):
        return
    first = int(min(int(e) for e in epochs)) // 10000
    if year and first and year > first:
        note(b, f"스택 시작({first})이 준공({year})보다 이르다 — 다리가 없던 영상과의 "
                f"결맞음으로 PS 를 고르면 교면이 통째로 탈락한다. 교면 점이 적으면 "
                f"scripts/rebuild_track.py --since {year}-01-01 로 트랙을 다시 쌓을 것")


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
# 본교가 아닌 부속 구조물 — 표준데이터에 본교 기록이 없는 교량에서 이것들이 대신 잡힌다
# (천호대교 → '천호대교 북단U턴차로교' 35 m, 양화대교 → '양화대교북단램프교' 59 m).
# 데크선으로 쓰면 33 m 짜리 선분에 교면 점을 맞추게 되므로 아예 빼는 편이 낫다.
_NOT_MAIN = ("램프", "RAMP", "U턴", "IC", "접속교", "연결로", "고가차도")
_NOT_MAIN_OK: tuple[str, ...] = ()              # 이름 자체에 그 낱말이 든 교량이 있으면 여기에


def _std_main_record(b: Bridge, *, max_km: float = 3.0):
    """전국교량표준데이터에서 그 교량의 **본교** 기록.

    이름 매칭 + 최근접으로 고르면 램프교가 걸린다 — 천호대교에서 '천호대교 북단U턴차로교'
    35 m 가, 양화대교에서 '양화대교북단램프교(F-1)' 59 m 가 잡혔다. 한 이름을 단
    구조물 중 **가장 긴 것**이 본교다. 반경 안에서 그것을 고른다.
    """
    try:
        import csv as _csv

        from inframon.public_data import bridge_profile_from_record, find_bridge_csv
        path = find_bridge_csv("data")
        if not path:
            return None
        want = (b.name or "").strip()
        if not want:
            return None
        best, best_len = None, 0.0
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(_csv.DictReader(fh))
        for r in rows:
            nm = (r.get("교량명") or "").strip()
            if not nm or not (nm in want or want in nm):
                continue
            if any(k in nm for k in _NOT_MAIN) and want not in _NOT_MAIN_OK:
                continue                         # 램프·IC·U턴차로는 본교가 아니다
            try:
                la, lo = float(r["교량시작점위도"]), float(r["교량시작점경도"])
                L = float(r["교량연장"])
            except (KeyError, TypeError, ValueError):
                continue
            d = math.hypot((lo - b.lon) * math.cos(math.radians(b.lat)), la - b.lat) * 111.32
            if d <= max_km and L > best_len:
                best, best_len = r, L
        return bridge_profile_from_record(best) if best is not None else None
    except Exception:                            # noqa: BLE001 — CSV 없으면 그냥 없는 것
        return None


def _std_deck(b: Bridge) -> list[list[float]] | None:
    """전국교량표준데이터의 **교량시점–종점 선분**을 데크선으로.

    한강 장대교는 OSM way 가 토막나 있다 — 한강대교는 '한강대교' 386 m·382 m 두 조각,
    올림픽대교는 차도가 '강동대로' 915 m 로 잡힌다. 연장(841·1470 m)과 어느 것도
    맞지 않아 데크선이 반쪽이 되거나 아예 빠진다. 표준데이터는 시점·종점 좌표를
    실측으로 갖고 있고 그 선분 길이가 연장과 맞으므로, 맞을 때는 이쪽이 더 낫다.

    선분 길이가 등록 연장과 ±35 % 안일 때만 쓴다 — 등록 시점·종점은 접속교를 포함하는
    일이 있어(한강대교 1005 m vs 연장 841 m) 딱 맞지는 않는다. 차이는 출처에 적어 둔다.
    직선 2점이라 곡선교는 근사다 — 그 경우 OSM way 가 있으면 OSM 이 낫다(여기는 폴백).
    """
    prof = _std_main_record(b)
    if prof is None:
        return None
    e = getattr(prof, "extra", None) or {}
    pts = [e.get("lat"), e.get("lon"), e.get("lat_end"), e.get("lon_end")]
    if any(v is None for v in pts):
        return None
    la0, lo0, la1, lo1 = (float(v) for v in pts)
    seg = math.hypot((lo1 - lo0) * math.cos(math.radians(la0)), la1 - la0) * 111_320.0
    # 검증은 **그 기록 자신의 연장**과 한다. 파트너 CSV 연장과 대면 다른 구조물(접속교·램프)
    # 값이 기준이 되어 멀쩡한 주경간교를 거부한다 — 월드컵대교가 그랬다(CSV 352 m vs
    # 주경간교 855 m). 기록 안에서 시점–종점과 연장이 맞으면 그 기록을 믿는다.
    own = prof.length_m
    if not own or seg < 20.0 or abs(seg - own) > 0.35 * own:
        return None
    if b.length_m and abs(own - b.length_m) > 0.35 * b.length_m:
        note(b, f"연장이 출처마다 다르다 — 파트너 CSV {b.length_m:.0f} m vs "
                f"표준데이터 '{prof.name}' {own:.0f} m. 데크선은 표준데이터를 쓰고 연장도 그 값으로 맞춘다")
        b.length_m, b.n_spans = float(own), None
    b.sources["deck"] = (f"전국교량표준데이터 '{prof.name}' 시점–종점 선분 {seg:.0f} m "
                         f"(등록 연장 {own:.0f} m · 차이 {100 * (seg - own) / own:+.0f} %)")
    return [[la0, lo0], [la1, lo1]]


def _to_xy(P: np.ndarray, lat0: float) -> np.ndarray:
    """위경도 → 로컬 미터(동, 북). 교량 한 개 크기라 평면 근사로 충분하다."""
    return np.stack([(P[:, 1] - P[0, 1]) * math.cos(math.radians(lat0)) * 111_320,
                     (P[:, 0] - P[0, 0]) * 111_320], axis=1)


def _open_deck(P: np.ndarray, lat0: float) -> tuple[np.ndarray, bool]:
    """닫힌 way 를 한쪽 차도만 남긴 열린 선으로.

    OSM 에서 교량이 **양방향 차도를 한 바퀴 도는 닫힌 way** 로 들어오는 일이 잦다
    (양화·서강·잠수·영동대교). 그러면 첫점 = 끝점이라 첫→끝 방위가 atan2(0,0)=0° 가
    되고, 등록 연장은 왕복 둘레(양화 3 km)가 된다. 트윈 프록시가 방위 0° 로 놓여
    PS 점과 어긋나는 원인이 이것이다.

    주축(가장 길게 퍼진 방향)으로 투영해 양 끝 꼭짓점을 찾고, 그 사이 한쪽 호만 남긴다.
    곡선 교량의 선형은 그대로 보존된다.
    """
    if len(P) < 3 or not np.allclose(P[0], P[-1]):
        return P, False
    xy = _to_xy(P, lat0)
    xy = xy - xy.mean(axis=0)
    w, V = np.linalg.eigh(np.cov(xy.T))
    t = xy @ V[:, int(np.argmax(w))]
    i, j = int(np.argmin(t)), int(np.argmax(t))
    lo, hi = (i, j) if i < j else (j, i)
    arc = P[lo:hi + 1]
    return (arc, True) if len(arc) >= 2 else (P, False)


def _chord_dev(xy: np.ndarray) -> float:
    """선의 양 끝을 잇는 현에서 가장 멀리 벗어난 거리 / 현 길이."""
    ch = xy[-1] - xy[0]
    chl = float(np.hypot(*ch))
    if chl < 1e-6:
        return 0.0
    u = ch / chl
    n = np.array([-u[1], u[0]])
    return float(np.abs((xy - xy[0]) @ n).max()) / chl


def _trim_ramp(P: np.ndarray, lat0: float, *, tol_deg: float = 30.0,
               keep_frac: float = 0.55, dev_min: float = 0.08) -> tuple[np.ndarray, bool]:
    """데크선 양 끝의 **곡선 접속램프**를 잘라낸다.

    OSM 은 램프와 본교를 한 way 로 잇는 일이 있다 — 청담대교 way 1123 m 중 앞 378 m 가
    남단 곡선 램프이고(현에서 최대 145 m 이탈), 본교는 뒤쪽 745 m 직선이다. 통째로 쓰면
    프록시 데크가 램프 쪽으로 끌려가 교면 PS 20점 중 1점만 부재에 결합됐다.

    구간별 방위를 보고 **길이가중 주방향에서 ±tol_deg 안에 드는 가장 긴 연속 구간**만
    남긴다.

    다만 **현에서 크게 벗어난 선에만** 적용한다(현 대비 dev_min 초과). 한강 교량 데크선의
    현 이탈은 행주 0.0 % · 잠수 0.2 % · 서강 1.2 % · 가양 1.3 % · 원효 1.5 % · 천호 2.2 % ·
    성산 2.8 % · 영동 4.0 % · 양화 5.9 % 인데 청담대교만 12.7 % 다. 게이트가 없으면
    멀쩡한 교량의 데크선까지 잘라 측점이 줄어든다(서강 38→16, 원효 45→15 을 겪었다).
    """
    if len(P) < 3:
        return P, False
    xy = _to_xy(P, lat0)
    if _chord_dev(xy) <= dev_min:
        return P, False                              # 충분히 곧다 — 건드리지 않는다
    d = np.diff(xy, axis=0)
    L = np.hypot(*d.T)
    if (L > 1e-6).sum() < 2 or L.sum() <= 0:
        return P, False
    ang = np.degrees(np.arctan2(d[:, 1], d[:, 0]))
    a2 = np.radians(2 * ang)                        # 방위는 180° 주기 — 2배각으로 평균
    dom = math.degrees(math.atan2(float((L * np.sin(a2)).sum()),
                                  float((L * np.cos(a2)).sum()))) / 2
    good = np.abs(((ang - dom + 90) % 180) - 90) <= tol_deg
    best_i = best_j = 0
    best_len = 0.0
    i = 0
    while i < len(good):
        if not good[i]:
            i += 1
            continue
        j = i
        while j < len(good) and good[j]:
            j += 1
        if float(L[i:j].sum()) > best_len:
            best_i, best_j, best_len = i, j, float(L[i:j].sum())
        i = j
    if best_j <= best_i or best_j - best_i >= len(L):
        return P, False                              # 전 구간이 주방향 — 곡선교라도 그대로
    if best_len < keep_frac * float(L.sum()):
        return P, False                              # 남는 게 너무 짧다 — 건드리지 않는다
    return P[best_i:best_j + 1], True


def _apply_deck(b: Bridge, geom: list[list[float]]) -> None:
    """데크선 확정 — 방위·연장을 여기서만 계산한다(OSM·표준데이터 공통)."""
    P = np.asarray([list(p) for p in geom], float)
    lat0 = float(P[:, 0].mean())
    P, cut = _open_deck(P, lat0)
    P, trimmed = _trim_ramp(P, lat0)
    if trimmed:
        xy_t = _to_xy(P, lat0)
        span_t = float(np.hypot(*np.diff(xy_t, axis=0).T).sum())
        note(b, f"데크선 끝에 방위가 크게 꺾이는 구간이 있었다(곡선 접속램프) — "
                f"본교 {span_t:.0f} m 만 남긴다")
        b.length_m, b.n_spans = span_t, None
        b.sources["deck_trim"] = f"곡선 램프 절단 → 본교 {span_t:.0f} m"
    b.geometry = [list(map(float, p)) for p in P]
    xy = _to_xy(P, lat0)
    b.deck_az_deg = float(math.degrees(math.atan2(xy[-1, 1] - xy[0, 1],
                                                  xy[-1, 0] - xy[0, 0])))
    if cut:
        span = float(np.hypot(*(xy[-1] - xy[0])))
        note(b, f"OSM way 가 양방향 차도를 도는 닫힌 선이었다(첫점=끝점) — "
                f"주축 양 끝으로 한쪽 차도만 남겨 방위 {b.deck_az_deg:.1f}° · "
                f"연장 {span:.0f} m 로 잡는다")
        b.length_m, b.n_spans = span, None
        b.sources["deck_cut"] = f"닫힌 way → 주축 양 끝 절단 · 한쪽 차도 {len(P)}점"


def _covers(outer, inner, *, tol_m: float = 25.0, frac: float = 0.9) -> bool:
    """inner 의 꼭짓점 대부분이 outer 선 위(±tol_m)에 있는가 — 같은 길의 토막인가."""
    A = np.asarray(inner.geometry, float)
    B = np.asarray(outer.geometry, float)
    if len(A) < 2 or len(B) < 2:
        return False
    lat0 = float(B[:, 0].mean())
    k = math.cos(math.radians(lat0))
    P = np.stack([(A[:, 1] - B[0, 1]) * 111_320 * k, (A[:, 0] - B[0, 0]) * 110_540], 1)
    Q = np.stack([(B[:, 1] - B[0, 1]) * 111_320 * k, (B[:, 0] - B[0, 0]) * 110_540], 1)
    best = np.full(len(P), np.inf)
    for i in range(len(Q) - 1):
        a, d = Q[i], Q[i + 1] - Q[i]
        L2 = float(d @ d)
        if L2 < 1e-9:
            continue
        t = np.clip(((P - a) @ d) / L2, 0.0, 1.0)
        best = np.minimum(best, np.hypot(*(P - (a + t[:, None] * d)).T))
    return float((best <= tol_m).mean()) >= frac


def _whole_deck(b: Bridge, road, roads: list):
    """토막난 way 를 **전체 데크**로 넓힌다.

    OSM 은 한 교량을 여러 way 로 쪼개 놓는 일이 많고, 표준데이터·제원 CSV 는 그것을
    '주경간교 / 접속교' 로 또 따로 등록한다. 둘을 연장으로 맞추면 반쪽만 잡힌다 —
    월드컵대교에서 CSV '주경간교' 855 m 에 맞춰 1023 m way(남측 반쪽)를 골랐는데,
    PS 는 하천 위 주경간이 아니라 북단 접속부에 몰려 있어 데크 ±30 m 안이 18점뿐이었다.
    같은 이름 way 중 **고른 way 를 통째로 품는 더 긴 way** 가 있으면 그것이 데크 전체다
    (1492 m · 54점).
    """
    def _closed(c) -> bool:
        G = np.asarray(c.geometry, float)
        return len(G) > 2 and bool(np.allclose(G[0], G[-1]))

    # 닫힌 way 는 **양방향 차도를 한 바퀴 도는 선**이라 더 긴 데크가 아니다 — 삼키면
    # 성산대교가 1079 m → 2999 m 로 부풀고, 한쪽 차도만 잘라 내도 실제 데크와 어긋난다.
    # 넓히는 폭도 2배까지만 — 그 이상은 옆 도로를 물고 온 것으로 본다.
    cand = [c for c in roads
            if c is not road and road.length_m * 1.15 < c.length_m <= road.length_m * 2.0
            and c.name and road.name and (c.name in road.name or road.name in c.name)
            and not _closed(c) and _covers(c, road)]
    if not cand:
        return road
    whole = max(cand, key=lambda c: c.length_m)
    note(b, f"OSM way 가 토막나 있다 — 고른 '{road.name}' {road.length_m:.0f} m 를 "
            f"통째로 품는 {whole.length_m:.0f} m way 가 있어 데크 전체로 넓힌다 "
            f"(연장도 그 값으로 — CSV {b.length_m:.0f} m 는 주경간만이다)")
    b.sources["deck_whole"] = (f"토막 {road.length_m:.0f} m → 포함 way "
                               f"{whole.length_m:.0f} m · 연장을 데크선에 맞춘다")
    b.length_m, b.n_spans = float(whole.length_m), None
    return whole


def refine_type_with_deck(b: Bridge) -> None:
    """데크선이 정해진 뒤 형식을 다시 확인한다 — 나란한 두 다리 중 어느 쪽인가.

    행주대교는 전국교량표준데이터에 **연장이 같은(1460 m) 두 줄**이 있다 — 하류(사장교,
    1995)와 상류교(PSC박스거더교, 2000). ① 제원 단계에는 데크선이 없어 '가장 긴 줄'
    규칙으로 상류교가 잡혔고 형식이 box_girder 가 됐다. 그런데 2024 한강교량 온라인
    안전감시 보고서의 행주대교 계측항목에는 **케이블장력·주탑경사**가 있다 — 사장교다.

    ② 가 끝난 뒤에는 가릴 수 있다. 후보의 시점–종점 선분과 데크선 사이의 수직거리를
    재면 된다(행주 하류 4.6 m vs 상류교 23.4 m). 나란한 교량은 30 m 남짓 떨어져 있어
    이 거리가 갈린다. 형식이 같으면 아무것도 바꾸지 않는다.
    """
    if not b.geometry or len(b.geometry) < 2:
        return
    try:
        from inframon.bridge_specs_csv import (find_spec_csvs, load_specs,
                                               spec_candidates)
        from inframon.public_data import parse_structure_ko
        cands = spec_candidates(load_specs(*find_spec_csvs("data")), b.lat, b.lon,
                                name=b.name)
    except Exception:                            # noqa: BLE001 — 없으면 그대로 둔다
        return
    usable = [s for s in cands
              if s.lat_end is not None and s.lon_end is not None and s.structure_raw]
    types = {parse_structure_ko(str(s.structure_raw))[0] for s in usable}
    if len(usable) < 2 or len(types - {None}) < 2:
        return

    P = np.asarray([list(p) for p in b.geometry], float)
    lat0 = float(P[:, 0].mean())
    n = len(P)

    def _dist(s) -> float:
        # 데크선과 후보 선분을 **같은 원점**으로 옮긴다 — _to_xy 는 첫 점을 원점으로 쓴다
        q = _to_xy(np.vstack([P, [[s.lat, s.lon], [s.lat_end, s.lon_end]]]), lat0)
        xy, a, z = q[:n], q[n], q[n + 1]
        ab = z - a
        L2 = float(ab @ ab)
        if L2 <= 0:
            return float("inf")
        d = []
        for p in xy:
            t = min(1.0, max(0.0, float((p - a) @ ab) / L2))
            d.append(float(np.hypot(*(p - (a + t * ab)))))
        return float(np.median(d))

    ranked = sorted(((_dist(s), s) for s in usable), key=lambda ds: ds[0])
    d0, s0 = ranked[0]
    d1 = ranked[1][0]
    if not np.isfinite(d0) or d0 > 60.0 or d1 < d0 * 2.0:
        return                                   # 갈리지 않으면 건드리지 않는다
    bt, mat = parse_structure_ko(str(s0.structure_raw))
    if not bt or bt == b.bridge_type:
        return
    old = b.bridge_type
    b.bridge_type, b.material = bt, (mat or b.material)
    if s0.max_span_m:
        b.max_span_m = float(s0.max_span_m)
    b.sources["bridge_type"] = (f"CSV '{s0.structure_raw}' → {bt} "
                                f"(데크선에 가장 가까운 '{s0.name}' · {d0:.0f} m)")
    note(b, f"나란한 교량 후보가 {len(usable)}개 — 데크선에서 {d0:.0f} m 인 "
            f"'{s0.name}'({s0.structure_raw}) 로 형식을 고친다: {old} → {bt} "
            f"(다음 후보는 {d1:.0f} m)")


def resolve_deck(b: Bridge) -> None:
    from inframon.insar.osm_bridge import find_bridges_near

    # 데크선을 배치 파일에서 직접 준 경우 — OSM 을 보지 않는다. 보행교(샛강문화다리)처럼
    # 데크가 footway 라 차도 필터에 걸리는 곳, OSM 이름이 보고서와 다른 곳에 필요하다.
    if b.geometry and len(b.geometry) >= 2:
        note(b, f"데크선을 직접 받았다({len(b.geometry)}점) — OSM 조회를 건너뛴다")
        b.sources.setdefault("deck", "배치 파일에 지정한 데크선")
        _apply_deck(b, b.geometry)
        _finish_deck(b)
        return
    try:
        cands = find_bridges_near(b.lat, b.lon, radius_m=250.0)
    except Exception as e:                       # noqa: BLE001
        std = _std_deck(b)
        if std:
            note(b, f"OSM 조회 실패({type(e).__name__}) → 표준데이터 시점–종점 선분 사용")
            _apply_deck(b, std)
            _finish_deck(b)          # 폭도 표준데이터 실측을 쓴다(12 m 가정보다 낫다)
            return
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
        named_roads = [c for c in roads if c.name and b.name and b.name in c.name]
        if fit:
            named = [c for c in fit if c.name and b.name and b.name in c.name]
            if named:
                road = min(named, key=lambda c: abs(c.length_m - b.length_m))
                b.sources["deck_match"] = (f"CSV 연장 {b.length_m:.0f} m 에 맞는 "
                                           f"'{b.name}' way 선택({len(cands)}후보 중)")
            elif named_roads:
                # 연장이 맞는 way 가 있어도 **이름 없는 way** 면 그 교량이 아닐 수 있다 —
                # 행주대교에서 이름 없는 1669 m way 가 CSV 1460 m 에 걸려 잡혔다.
                # 이름이 맞는 way 가 하나라도 있으면 그중 가장 긴 것이 데크다.
                road = max(named_roads, key=lambda c: c.length_m)
                note(b, f"연장 {b.length_m:.0f} m 에 맞는 way 는 이름이 없다 — "
                        f"이름이 맞는 '{road.name}' {road.length_m:.0f} m 를 쓴다")
            else:
                road = min(fit, key=lambda c: abs(c.length_m - b.length_m))
                b.sources["deck_match"] = (f"CSV 연장 {b.length_m:.0f} m 에 맞는 way 선택"
                                           f"(이름 없음 · {len(cands)}후보 중)")
        else:
            std = _std_deck(b)
            if std:
                note(b, f"OSM 어느 way 도 연장 {b.length_m:.0f} m 와 안 맞음 "
                        f"(토막난 way) → 표준데이터 시점–종점 선분 사용")
                _apply_deck(b, std)
                _finish_deck(b)
                return
            named = [c for c in roads if c.name and b.name and b.name in c.name]
            road = max(named or roads, key=lambda c: c.length_m)
            note(b, f"OSM 어느 way 도 CSV 연장 {b.length_m:.0f} m 와 안 맞음 — "
                    f"'{road.name}' {road.length_m:.0f} m 사용")
    else:
        named = [c for c in roads if c.name and b.name and b.name in c.name]
        road = max(named or roads, key=lambda c: c.length_m)
    road = _whole_deck(b, road, roads)
    _apply_deck(b, road.geometry)
    if b.length_m is None:
        b.length_m = float(road.length_m)
        b.sources["length"] = f"OSM way {road.name}"
    b.sources["deck"] = f"OSM '{road.name}' {road.length_m:.0f} m · 방위 {b.deck_az_deg:.1f}°"
    _finish_deck(b, road=road)


def _osm_footway_width(b: Bridge, lat0: float) -> tuple[float, float] | None:
    """OSM 양측 보도 중심선 간격 → (폭, 간격). 보도 way 가 둘 미만이면 None.

    등록 폭이 없는 교량(정자교)에서는 이게 유일한 근거다. 다만 반경 120 m 안의
    '보도'가 그 교량 것이라는 보장이 없다 — 검증은 호출 측에서 한다.
    """
    from inframon.insar.osm_bridge import _overpass_query
    try:
        els = _overpass_query(f"[out:json][timeout:30];way(around:120,{b.lat},{b.lon})"
                              f"[\"bridge\"][\"highway\"=\"footway\"];out geom;")["elements"]
    except Exception:                            # noqa: BLE001 — 조회 실패는 '없음'과 같다
        return None
    offs = []
    for el in els:
        F = np.asarray([[g["lat"], g["lon"]] for g in el.get("geometry", []) if "lat" in g], float)
        if len(F) >= 2:
            offs.append(float(np.mean(F[:, 0] - lat0) * 111_320))
    if len(offs) < 2:
        return None
    gap = float(max(offs) - min(offs))
    return gap + 2 * FOOTWAY_HALF_M, gap


def _finish_deck(b: Bridge, *, road=None) -> None:
    """폭·경간수 마무리 — 데크선을 OSM 에서 얻었든 표준데이터에서 얻었든 같다.

    폭 우선순위: **전국교량표준데이터 실측 교량폭 → OSM 보도 간격 → lanes → 12 m 가정**.
    보도 간격을 먼저 쓰던 시절엔 한강 교량이 무너졌다 — 성수대교 11 m(실측 35 m),
    올림픽대교 109 m(실측 30 m). 반경 120 m 안의 '보도'가 그 교량 것이 아니었다.
    폭은 데크 ±폭/2 로 교면 점을 고르는 기준이라 틀리면 결과가 통째로 바뀐다.
    """
    lat0 = float(np.asarray(b.geometry, float)[:, 0].mean()) if b.geometry else b.lat
    if b.width_m is None:
        w_std = _std_width(b)
        w_osm = _osm_footway_width(b, lat0)
        if w_std:
            b.width_m = w_std
            b.sources["width"] = f"전국교량표준데이터 교량폭 {w_std:g} m"
            if w_osm and abs(w_osm[0] - w_std) > 0.5 * w_std:
                note(b, f"OSM 보도 간격 {w_osm[1]:.1f} m 는 등록 교량폭 {w_std:g} m 와 "
                        f"크게 달라 쓰지 않았다(그 교량 보도가 아닐 수 있다)")
        elif w_osm:
            b.width_m = w_osm[0]
            b.sources["width"] = f"OSM 보도 간격 {w_osm[1]:.1f} m + 보도 반폭"
    if b.width_m is None:
        lanes = (getattr(road, "tags", {}) or {}).get("lanes") if road is not None else None
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


def _std_width(b: Bridge) -> float | None:
    """전국교량표준데이터의 교량폭(실측). 없으면 None."""
    try:
        from inframon.public_data import find_bridge_csv, nearest_bridge_profile
        csv = find_bridge_csv("data")
        prof = nearest_bridge_profile(csv, b.lat, b.lon, max_km=0.5, name=b.name) if csv else None
    except Exception:                            # noqa: BLE001
        return None
    w = getattr(prof, "width_m", None) if prof else None
    return float(w) if w and float(w) > 0 else None


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
def acquire_track(b: Bridge, out: Path, *, count: int = 12,
                  start: str | None = None, end: str | None = None) -> str | None:
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
    _period = {k: v for k, v in (("start", start), ("end", end)) if v}
    prep = run_bridge_pipeline(b.lat, b.lon, out_dir=pdir, mode="full",
                               earthdata_token=token, snap_count=count,
                               bridge_name=b.name, **_period)
    for r in prep.stages:
        mark = {"done": "✅", "partial": "🟡", "skip": "⏭", "error": "❌"}.get(r.status, "·")
        print(f"      {mark} {r.step}: {r.detail[:90]}")
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
    # z≥2 — z=1.1 짜리(월드컵대교 +37.1 ± 33.5 m)가 등록 교량높이 25 m 를 밀어내면 안 된다.
    if g and g.get("ok") and g["diff_m"] > 0 and g["z"] >= 2.0:
        # Sentinel-1 은 B⊥ 가 작아 Δh 가 쉽게 부풀고, 그 값이 그대로 형하고가 되면 트윈이
        # 하늘로 뜬다(천호대교 +73 m → 데크 표고 79 m). 국내 하천 교량 형하고는 40 m 를
        # 넘지 않는다 — 넘으면 채택하지 않고 이유를 남긴다.
        if g["diff_m"] > CLEARANCE_MAX_M:
            note(b, f"잔차고도 집단평균 {g['diff_m']:+.1f} m 는 형하고로 비현실적"
                    f"(> {CLEARANCE_MAX_M:.0f} m) — 채택하지 않고 "
                    f"{_f(b.clearance_m, ' m')} 를 유지한다")
        else:
            b.clearance_m = float(g["diff_m"])
            b.sources["clearance"] = (f"잔차고도 집단평균 {g['diff_m']:+.1f}"
                                      f"±{g['se_diff_m']:.1f} m (z={g['z']:.2f})")
    return g


def _deck_midpoint(b: Bridge) -> tuple[float, float]:
    """데크선의 **호길이 중점**(lat, lon). 데크선이 없으면 조회 좌표를 그대로 쓴다."""
    P = np.asarray(b.geometry or [], float)
    if len(P) < 2:
        return b.lat, b.lon
    lat0 = float(P[:, 0].mean())
    xy = _to_xy(P, lat0)
    seg = np.hypot(*np.diff(xy, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    if cum[-1] <= 0:
        return float(P[:, 0].mean()), float(P[:, 1].mean())
    half = cum[-1] / 2
    k = int(np.searchsorted(cum, half)) - 1
    k = min(max(k, 0), len(P) - 2)
    f = (half - cum[k]) / max(seg[k], 1e-9)
    return (float(P[k, 0] + f * (P[k + 1, 0] - P[k, 0])),
            float(P[k, 1] + f * (P[k + 1, 1] - P[k, 1])))


def _warn_twin_misses_deck(b: Bridge, r: dict) -> None:
    """부재에 묶인 점이 거의 없으면 **조용히 넘어가지 않는다**.

    프록시는 **직선** 이다. 데크선이 크게 굽은 고가차도(동수원고가차도 — 호길이
    1228 m·현 1172 m·편차 14.5 %)에서는 직선 부재가 곡선 위 측점을 따라가지 못해
    결합이 0 이 된다. 부재 위치가 틀린 채로 통계만 나오면 그게 더 나쁘다.

    지금 고칠 수 있는 문제가 아니다(곡선 정렬 프록시가 필요하다). 그러니 **왜 0 인지**
    를 산출물에 남긴다 — 결합 0 을 보고 '점이 없다' 고 읽으면 안 된다.
    """
    n, bound = int(r.get("n_points") or 0), int(r.get("bound") or 0)
    if n <= 0 or bound >= max(1, int(n * 0.2)):
        return
    dev = None
    if b.geometry and len(b.geometry) >= 3:
        P = np.asarray([list(p) for p in b.geometry], float)
        dev = _chord_dev(_to_xy(P, float(P[:, 0].mean())))
    why = (f"데크선이 크게 굽어 있다(현 대비 편차 {dev * 100:.0f} %) — "
           "직선 프록시가 곡선 위 측점을 따라가지 못한다"
           if dev is not None and dev > 0.08 else
           "프록시 위치와 측점이 평면에서 어긋난다")
    note(b, f"⚠ 부재 결합 {bound}/{n} — {why}. "
            f"부재별 통계를 쓰면 안 된다(교면 전체 통계는 유효)")
    b.sources["bind_warn"] = f"결합 {bound}/{n} — {why}"


# ── ⑥ IFC 트윈 ────────────────────────────────────────────────────────────
def build_twin(b: Bridge, sub: Path, out: Path) -> dict:
    from pyproj import Transformer

    from inframon.bim.georef import MapConversion
    from inframon.bim.ifc_io import read_elements, read_map_conversion
    from inframon.bim.ifc_write import write_elements
    from inframon.bim.proxy_model import (BIND_MEMBERS, bridge_elements,
                                          span_edges, superstructure_elements)
    from inframon.insar.gltf_export import (export_insar_gltf, guid_map_from_alignment,
                                            write_3dtiles_tileset, write_web_viewer)
    els = bridge_elements(length_m=b.length_m, width_m=b.width_m, n_spans=b.n_spans,
                          clearance_m=b.clearance_m, max_span_m=b.max_span_m,
                          span_layout=b.span_layout, bridge_type=b.bridge_type,
                          superstructure=b.superstructure, name=b.name)
    _, used, why = span_edges(b.length_m, b.n_spans or 1, max_span_m=b.max_span_m,
                              layout=b.span_layout)
    b.sources["span_layout"] = f"{used} — {why}"
    made = superstructure_elements(lambda *a, **k: None, bridge_type=b.bridge_type,
                                   length_m=b.length_m, width_m=b.width_m,
                                   main_span_m=float(b.max_span_m or 0.0),
                                   z_deck_bot=0.0, z_deck_top=0.0)
    if made:
        b.sources["superstructure"] = made
        note(b, f"형식별 상부구조: {made}")
    # 프록시 원점은 **데크선 중점** 이다. 조회 좌표(b.lat/lon)를 쓰면 그 좌표가 교량
    # 중심에서 벗어난 만큼 부재가 통째로 밀려 PS 점이 부재 밖에 앉는다(청담 ~97 m).
    olat, olon = _deck_midpoint(b)
    e0, n0 = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(olon, olat)
    az = math.radians(b.deck_az_deg or 0.0)
    mc = MapConversion(eastings=e0, northings=n0, orthogonal_height=b.ground_m,
                       x_axis_abscissa=math.cos(az), x_axis_ordinate=math.sin(az),
                       target_crs=CRS, source="proxy_placement")
    ifc = out / f"{b.name}_proxy.ifc"
    write_elements(els, ifc, map_conversion=mc, project_name=f"{b.name} 프록시 교량")
    els2, mc2 = read_elements(ifc), read_map_conversion(ifc)
    # IFC 왕복에서 부재 라벨이 엔티티 타입으로 다시 추론된다 — 케이블(IfcMember)이
    # 'deck' 으로, 주탑(IfcColumn)이 'pier' 로 돌아온다. 그러면 결합 필터가 무력해져
    # 케이블이 데크 측점을 가져간다. 우리가 쓴 라벨을 GUID 로 되돌린다.
    _label = {e.guid: e.member for e in els}
    for e in els2:
        if _label.get(e.guid):
            e.member = _label[e.guid]
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
    # 결합은 **상판·교각·교대만** 본다. associate 가 평면 2D 최근접이라 주탑·케이블·
    # 아치리브를 같이 넣으면 데크 위 측점을 그것들이 가져간다.
    bind = [e for e in els2 if (e.member or "") in BIND_MEMBERS]
    guids, ginfo = guid_map_from_alignment(proj, bind, map_conversion=mc2, ifc_crs=CRS,
                                           max_dist_m=DECK_SEL_M)
    r = export_insar_gltf(proj, out / "twin.glb", value="velocity", element_guids=guids,
                          element_z=ginfo["element_z"], z_source="deck",
                          element_z_datum=b.ground_m)
    _warn_twin_misses_deck(b, r)
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


def run_one(b: Bridge, *, count: int = 12, start: str | None = None,
            end: str | None = None) -> dict:
    out = Path(b.out or f"docs/bridges/{b.name}")
    out.mkdir(parents=True, exist_ok=True)
    print(f"\n━━ {b.name} ({b.lat}, {b.lon}) → {out}")
    tw = pinn = rh = brief = None
    aud = {"verdict": "불가", "reasons": [], "notes": []}
    try:
        if not b.track:
            print("  ⓪ SLC → InSAR (트랙 없음)")
            b.track = acquire_track(b, out, count=count, start=start, end=end)
        if not b.track:
            raise RuntimeError("트랙 없음 — ⓪ 실패")
        with h5py.File(b.track, "r") as f:
            b.n_epochs = int(f["los_mm"].shape[1])
            ep = f["epochs"][()] if "epochs" in f else None
        print("  ① 제원");   resolve_specs(b)
        _warn_stack_predates_build(b, ep)
        print("  ② 데크선"); resolve_deck(b); refine_type_with_deck(b)
        print("  ③ 지면");   resolve_ground(b)
        print("  ④ 점 선택"); sub = select_points(b, out)
        if sub is None:
            # 데크선이 틀리면 점이 하나도 안 잡힌다 — 출처를 바꿔 한 번 더 본다.
            # 월드컵대교가 그랬다: 파트너 CSV 연장 352 m(접속교)에 맞춰 OSM 이 357 m
            # way 를 골랐고, 실제 주경간교(855 m)는 데크 ±30 m 밖이라 0/20000 이었다.
            alt = _std_deck(b)
            if alt and (not b.geometry or alt != [list(x) for x in b.geometry]):
                note(b, "데크 ±30 m 안 0점 → 표준데이터 시점–종점 선분으로 데크선을 바꿔 재시도")
                _apply_deck(b, alt)
                print("  ④ 점 선택(재시도)"); sub = select_points(b, out)
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
    ap.add_argument("--no-superstructure", action="store_true",
                    help="형식별 상부구조(사장교 주탑·케이블 등)를 세우지 않고 상판+교각만")
    ap.add_argument("--span-layout", default="auto", choices=("auto", "equal", "measured"),
                    help="교각 배치 — auto(실측 최대경간장이 있으면 비등간격) · "
                         "equal(연장÷경간수 균등) · measured(주경간 실측 강제)")
    ap.add_argument("--count", type=int, default=12, metavar="N",
                    help="⓪ 에서 내려받아 처리할 SLC 장면 수(기본 12·장당 ~7GB). "
                         "의미 있는 속도·CI 는 25장·1년 이상이 필요하다.")
    ap.add_argument("--start", default=None, metavar="YYYY-MM-DD", help="⓪ SLC 조회 시작일")
    ap.add_argument("--end", default=None, metavar="YYYY-MM-DD", help="⓪ SLC 조회 종료일")
    a = ap.parse_args()
    if a.batch:
        items = json.loads(Path(a.batch).read_text(encoding="utf-8"))
        bridges = [Bridge(**it) for it in items]
        for _b in bridges:                      # 배치 파일에 없으면 CLI 값을 쓴다
            if _b.span_layout == "auto":
                _b.span_layout = a.span_layout
            if a.no_superstructure:
                _b.superstructure = False
    else:
        if not (a.name and a.lat and a.lon):
            ap.error("--name --lat --lon (--track 은 선택: 없으면 SLC 부터 만든다) 또는 --batch")
        bridges = [Bridge(name=a.name, lat=a.lat, lon=a.lon, track=a.track, proc=a.proc,
                          master=a.master, baselines=a.baselines, out=a.out,
                          span_layout=a.span_layout,
                          superstructure=not a.no_superstructure)]
    results = [run_one(b, count=a.count, start=a.start, end=a.end) for b in bridges]
    print("\n━━ 요약")
    print(f"{'교량':<10}{'판정':<10}{'점':>5}{'CRI':>8}  경로")
    for r in results:
        cri = f"{r['cri']:.3f}" if r["cri"] is not None else "—"
        print(f"{r['name']:<10}{r['verdict']:<10}{r['points']:>5}{cri:>8}  {r['out']}")


if __name__ == "__main__":
    main()
