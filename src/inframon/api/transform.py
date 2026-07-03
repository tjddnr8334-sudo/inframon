"""project.h5 계약 → Bmaps API DTO 변환 (FastAPI 비의존, 단위테스트 가능).

규약 변환을 한곳에 모은다(설계 §5):
  - 좌표 : EPSG:5179 (xyz) → WGS84 (lat, lon)        [insar/geo.reproject]
  - 단위 : 변위(los/longitudinal/vertical·PINN 성분)는 계약상 **이미 mm** — 그대로 통과.
           (생산자 전부 mm: 합성 엔진·import_track_h5 의 los_mm·real_engine 융합. ×1000 금지.)
  - 날짜 : epoch days (dates_ds) → ISO "YYYY-MM-DD"
  - 부재 : 정수 라벨 → 문자열 ("deck|pier|abutment|bearing")

contracts/ 는 성역. 여기서는 ProjectStore 로 *읽어 변환*만 한다.
ProjectStore 는 mode="r" 로 열어 넘긴다(read_meta 가 schema_version major 불일치 시
ContractViolation → app 이 409 로 매핑).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from ..contracts.io import ProjectStore
from ..contracts.schema import (
    MEMBER_TYPES,
    FRAMOutput,
    InSAROutput,
    PINNOutput,
)
from ..insar.geo import reproject

# xyz_ds 의 원천 좌표계(계약 주석에 EPSG:5179 로 박제됨).
SRC_CRS = "EPSG:5179"
WGS84 = "EPSG:4326"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class ResultNotFound(Exception):
    """해당 교량에 요청한 산출물이 없다(파이프라인 미실행/엔진 stub) → app 이 404."""


# ───────────────────────── 원시 변환 헬퍼 ─────────────────────────
def epoch_days_to_iso(days: float) -> str:
    """epoch days(1970-01-01 기준) → 'YYYY-MM-DD'."""
    return (_EPOCH + timedelta(days=float(days))).strftime("%Y-%m-%d")


def member_label(idx: int) -> str:
    """정수 라벨 → 표준 부재 문자열. 범위 밖이면 'unknown'."""
    i = int(idx)
    return MEMBER_TYPES[i] if 0 <= i < len(MEMBER_TYPES) else "unknown"


def xyz_to_latlon(xyz: np.ndarray, to_crs: str = WGS84) -> np.ndarray:
    """[N,3] (EPSG:5179 easting,northing,elev) → [N,3] (lat, lon, elev).

    Bmaps 지도가 EPSG:5179 타일이면 to_crs=SRC_CRS 로 호출 → 재투영 생략(easting/northing 유지).
    """
    xyz = np.asarray(xyz, dtype=np.float64)
    if to_crs == SRC_CRS:
        # 변환 생략: (x, y, z) 그대로 — 호출 측이 좌표계를 안다고 가정.
        return xyz
    lonlat = reproject(xyz[:, :2], SRC_CRS, to_crs)  # always_xy → (lon, lat)
    return np.column_stack([lonlat[:, 1], lonlat[:, 0], xyz[:, 2]])


def _resolve_index(value: Any, n: int) -> int:
    """date 파라미터('latest' | 정수문자열 | int) → 유효 인덱스 [0, n-1]."""
    if value in (None, "", "latest"):
        return n - 1
    try:
        k = int(value)
    except (TypeError, ValueError):
        return n - 1
    return max(0, min(k, n - 1))


# ───────────────────────── 메타 접근 ─────────────────────────
def has_insar(store: ProjectStore) -> bool:
    return store.has_meta("insar")


def _insar(store: ProjectStore) -> InSAROutput:
    if not store.has_meta("insar"):
        raise ResultNotFound("InSAR 산출물이 없습니다(파이프라인 먼저 실행).")
    return store.read_meta("insar", InSAROutput)


def _pinn(store: ProjectStore) -> PINNOutput | None:
    return store.read_meta("pinn", PINNOutput) if store.has_meta("pinn") else None


def _fram(store: ProjectStore) -> FRAMOutput | None:
    return store.read_meta("fram", FRAMOutput) if store.has_meta("fram") else None


# ───────────────────────── DTO 빌더 (§3) ─────────────────────────
def dates_iso(store: ProjectStore) -> list[str]:
    ins = _insar(store)
    return [epoch_days_to_iso(d) for d in store.read_array(ins.dates_ds)]


def summary(store: ProjectStore, *, name: str, bridge_id: str) -> dict[str, Any]:
    """§3.2 탭 헤더 요약 — 경보 + CRI + 기간."""
    ins = _insar(store)
    dates = [epoch_days_to_iso(d) for d in store.read_array(ins.dates_ds)]
    coh = store.read_array(ins.coherence_ds)
    out: dict[str, Any] = {
        "bridge_id": bridge_id,
        "name": name,
        "n_points": ins.n_points,
        "n_dates": ins.n_dates,
        "date_range": [dates[0], dates[-1]] if dates else [],
        "coherence_mean": round(float(np.mean(coh)), 4),
        "warning": None,
        "cri_global_max": None,
    }
    fram = _fram(store)
    if fram is not None:
        w = fram.warning
        out["warning"] = {
            "level": w.level,
            "basis": w.basis,
            "critical_members": list(w.critical_members),
            "function_states": dict(w.function_states),
            "lead_time_days": w.lead_time_days,
            "lead_time_forecast_days": w.lead_time_forecast_days,
        }
        out["cri_global_max"] = round(float(fram.cri_global_max), 4)
    return out


def points(store: ProjectStore, *, metric: str = "los", date: Any = "latest",
           to_crs: str = WGS84) -> dict[str, Any]:
    """§3.3 변위 측점(지도 레이어). metric: 'los' | 'longitudinal'."""
    ins = _insar(store)
    if metric not in ("los", "longitudinal"):
        raise ValueError("metric 은 'los' 또는 'longitudinal' 이어야 합니다.")
    ds = ins.los_ds if metric == "los" else ins.longitudinal_ds

    latlon = xyz_to_latlon(store.read_array(ins.xyz_ds), to_crs)
    member = store.read_array(ins.member_ds)
    coh = store.read_array(ins.coherence_ds)
    pid = store.read_array(ins.point_id_ds)
    disp = store.read_array(ds)  # [N, M] m
    dates = [epoch_days_to_iso(d) for d in store.read_array(ins.dates_ds)]
    k = _resolve_index(date, len(dates))

    fram = _fram(store)
    cri = store.read_array(fram.CRI_ds) if fram is not None else None  # [N, M]
    # 연직 변위(asc+desc 융합 시) — 있으면 측점마다 함께 노출.
    vert = store.read_array(ins.vertical_ds) if ins.vertical_ds else None  # [N, M]

    pts = []
    for i in range(int(ins.n_points)):
        pts.append({
            "point_id": int(pid[i]),
            "lat": round(float(latlon[i, 0]), 7),
            "lon": round(float(latlon[i, 1]), 7),
            "elev": round(float(latlon[i, 2]), 2),
            "member": member_label(member[i]),
            "coherence": round(float(coh[i]), 3),
            "value_mm": round(float(disp[i, k]), 2),
            "vertical_mm": (round(float(vert[i, k]), 2) if vert is not None else None),
            "cri": (round(float(cri[i, k]), 3) if cri is not None else None),
        })
    return {"dates": dates, "metric": metric, "date_index": k,
            "has_vertical": vert is not None, "points": pts}


def points_geojson(store: ProjectStore, *, metric: str = "los", date: Any = "latest",
                   to_crs: str = WGS84) -> dict[str, Any]:
    """§3.3 GeoJSON FeatureCollection(지도 라이브러리 직접 소비)."""
    data = points(store, metric=metric, date=date, to_crs=to_crs)
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},  # GeoJSON=[lon,lat]
        "properties": {k: v for k, v in p.items() if k not in ("lat", "lon")},
    } for p in data["points"]]
    return {
        "type": "FeatureCollection",
        "metric": data["metric"], "date_index": data["date_index"], "dates": data["dates"],
        "features": features,
    }


def point_series(store: ProjectStore, point_id: int) -> dict[str, Any]:
    """§3.4 측점 시계열 — 점 클릭 상세(LOS/종방향/성분/CRI + PINN 지표)."""
    ins = _insar(store)
    pid = store.read_array(ins.point_id_ds)
    matches = np.where(np.asarray(pid).astype(int) == int(point_id))[0]
    if matches.size == 0:
        raise ResultNotFound(f"측점 point_id={point_id} 가 없습니다.")
    i = int(matches[0])

    member = store.read_array(ins.member_ds)
    dates = [epoch_days_to_iso(d) for d in store.read_array(ins.dates_ds)]
    los = store.read_array(ins.los_ds)[i]
    lon_disp = store.read_array(ins.longitudinal_ds)[i]

    out: dict[str, Any] = {
        "point_id": int(point_id),
        "member": member_label(member[i]),
        "dates": dates,
        "los_mm": [round(float(v), 3) for v in los],
        "longitudinal_mm": [round(float(v), 3) for v in lon_disp],
        "vertical_mm": None,
        "components": None,
        "cri": None,
        "EI": None,
        "alpha": None,
    }
    # 연직 변위(asc+desc 융합 시) — 처짐·침하.
    if ins.vertical_ds:
        out["vertical_mm"] = [round(float(v), 3) for v in store.read_array(ins.vertical_ds)[i]]

    pinn = _pinn(store)
    if pinn is not None:
        out["components"] = {
            "thermal_mm": [round(float(v), 3) for v in store.read_array(pinn.comp_thermal_ds)[i]],
            "load_mm": [round(float(v), 3) for v in store.read_array(pinn.comp_load_ds)[i]],
            "settle_mm": [round(float(v), 3) for v in store.read_array(pinn.comp_settle_ds)[i]],
            "anomaly_mm": [round(float(v), 3) for v in store.read_array(pinn.comp_anomaly_ds)[i]],
        }
        out["EI"] = float(store.read_array(pinn.EI_ds)[i])
        out["alpha"] = float(store.read_array(pinn.alpha_ds)[i])

    fram = _fram(store)
    if fram is not None:
        out["cri"] = [round(float(v), 3) for v in store.read_array(fram.CRI_ds)[i]]
    return out


def girder_displacement(store: ProjectStore, *, date: Any = "latest") -> dict[str, Any]:
    """상부거더 **가상센싱 전체 변위장** — PINN 이 관측점 없는 위치까지 채운 종단 프로파일.

    지정 시점의 거더축(고정단 거리[m])을 따른 전체 변위량[mm]·처짐[mm] 프로파일과
    첨두(최대 변위)·중앙경간 시계열을 준다(대시보드 거더 변위 곡선용).
    """
    pinn = _pinn(store)
    if pinn is None or not pinn.vsens_total_ds:
        raise ResultNotFound("가상센싱 거더 변위장이 없습니다(pinn=real 실행 필요).")
    ins = _insar(store)
    dates = [epoch_days_to_iso(d) for d in store.read_array(ins.dates_ds)]
    k = _resolve_index(date, len(dates))
    xl = np.asarray(store.read_array(pinn.vsens_l_from_fixed_ds))     # [V]
    total = np.asarray(store.read_array(pinn.vsens_total_ds))         # [V,M]
    defl = np.asarray(store.read_array(pinn.vsens_deflection_ds))     # [V,M]
    V = int(total.shape[0])
    profile = [{
        "l_from_fixed_m": round(float(xl[i]), 3),
        "total_mm": round(float(total[i, k]), 3),
        "deflection_mm": round(float(defl[i, k]), 3),
    } for i in range(V)]
    peak_i = int(np.argmax(total[:, k]))
    mid = V // 2
    return {
        "n_virtual": V,
        "dates": dates,
        "date_index": k,
        "span_m": round(float(xl[-1]), 3),
        "profile": profile,
        "peak": {"l_from_fixed_m": round(float(xl[peak_i]), 3),
                 "total_mm": round(float(total[peak_i, k]), 3)},
        "midspan_total_mm_series": [round(float(v), 3) for v in total[mid]],
    }


def deck_displacement(store: ProjectStore, *, date: Any = "latest",
                      to_crs: str = WGS84) -> dict[str, Any]:
    """상판(deck) **전체 면 가상센싱 변위 지도** — PINN 이 관측점 없는 상판 위치까지 채운 2D 장.

    PCA 로 세운 상판 격자(G=n_long×n_trans)의 각 격자점에 world 좌표(lat/lon)와 전체
    변위량[mm]·처짐[mm]을 준다(지도 히트맵). 격자 형상(n_long,n_trans)도 함께.
    """
    pinn = _pinn(store)
    if pinn is None or not pinn.deck_total_ds:
        raise ResultNotFound("상판 2D 가상센싱 변위장이 없습니다(pinn=real·측점≥3 필요).")
    ins = _insar(store)
    dates = [epoch_days_to_iso(d) for d in store.read_array(ins.dates_ds)]
    k = _resolve_index(date, len(dates))
    xy = np.asarray(store.read_array(pinn.deck_xy_ds), dtype=np.float64)   # [G,2] (E,N)
    total = np.asarray(store.read_array(pinn.deck_total_ds))              # [G,M]
    defl = np.asarray(store.read_array(pinn.deck_deflection_ds))         # [G,M]
    latlon = xyz_to_latlon(np.column_stack([xy, np.zeros(len(xy))]), to_crs)  # [G,3]
    G = int(total.shape[0])
    nodes = [{
        "lat": round(float(latlon[i, 0]), 7),
        "lon": round(float(latlon[i, 1]), 7),
        "total_mm": round(float(total[i, k]), 3),
        "deflection_mm": round(float(defl[i, k]), 3),
    } for i in range(G)]
    grid = None
    try:
        vs = store.read_json_attr("pinn", "virtual_sensing")
        grid = vs.get("deck") if isinstance(vs, dict) else None
    except (KeyError, ValueError):
        pass
    peak_i = int(np.argmax(total[:, k]))
    return {
        "n_deck": G,
        "n_long": (grid or {}).get("n_long"),
        "n_trans": (grid or {}).get("n_trans"),
        "footprint_m": (grid or {}).get("footprint_m"),
        "dates": dates,
        "date_index": k,
        "nodes": nodes,
        "peak": {"lat": nodes[peak_i]["lat"], "lon": nodes[peak_i]["lon"],
                 "total_mm": nodes[peak_i]["total_mm"]},
    }


def cri(store: ProjectStore) -> dict[str, Any]:
    """§3.5 CRI 시계열(안전성 추세). 시점별 최대 CRI."""
    fram = _fram(store)
    if fram is None:
        raise ResultNotFound("FRAM 결과가 없습니다(fram 엔진 필요).")
    ins = _insar(store)
    cri_arr = store.read_array(fram.CRI_ds)  # [N, M]
    dates = [epoch_days_to_iso(d) for d in store.read_array(ins.dates_ds)]
    return {
        "cri_global_max": round(float(fram.cri_global_max), 4),
        "n_points": int(fram.n_points),
        "n_dates": int(fram.n_dates),
        "dates": dates,
        "cri_max_series": [round(float(x), 4) for x in cri_arr.max(axis=0)],
    }


def function_network(store: ProjectStore, k: int | None = None) -> dict[str, Any]:
    """§3.6 함수망 진단 — 4기능 변동(레이더) + R_ij 결합행렬(시점 k) + N-K 진단."""
    fram = _fram(store)
    pinn = _pinn(store)
    if fram is None or pinn is None:
        raise ResultNotFound("함수망 진단이 없습니다(pinn+fram=real 필요).")

    vfs = np.asarray(store.read_array(pinn.V_func_series_ds))   # [n_func, M]
    rij = np.asarray(store.read_array(fram.resonance_Rij_ds))   # [n_func, n_func, M]
    n_dates = int(vfs.shape[1])
    kk = (n_dates - 1) if k is None else max(0, min(int(k), n_dates - 1))

    out: dict[str, Any] = {
        "func_names": list(pinn.func_names),
        "variability": [float(v) for v in vfs[:, kk]],
        "coupling": [[float(v) for v in row] for row in rij[:, :, kk]],
        "k": kk,
        "n_dates": n_dates,
        "network_resonance_max": None,
        "diagnosis": None,
    }
    if fram.network_resonance_ds is not None and store.has_array(fram.network_resonance_ds):
        nr = store.read_array(fram.network_resonance_ds)
        out["network_resonance_max"] = round(float(np.max(nr)), 4)
    # real FRAM 이 기록한 N-K 진단(driver·임계경로)이 있으면 그대로 노출.
    try:
        out["diagnosis"] = store.read_json_attr("fram", "function_network")
    except (KeyError, ValueError):
        pass
    return out
