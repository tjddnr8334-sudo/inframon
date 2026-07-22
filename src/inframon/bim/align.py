"""project.h5 × BIM 부재 → 정합·연결·부재별 상태 (디지털 트윈 결합).

전체 흐름:

    project.h5 (InSAR·FRAM·잔존수명)
        │  ① 좌표 정합  georef: 지도 CRS → IFC 로컬
        │      · IfcMapConversion 이 있으면 그대로
        │      · 없으면 측량 기준점 쌍으로 Helmert 적합(+RMS 게이트)
        │  ② 부재 연결  elements: 점 → GUID (AABB 내부/최근접, 미연결 허용)
        │  ③ 부재 집계  psets: 강건 통계 → Pset 페이로드
        ▼
    result JSON (+ 선택: ifc_io 로 실 IFC 에 주입)

정합이 실패하면 **여기서 멈춘다**. 좌표가 어긋난 채로 부재에 값을 붙이면 결과가
정상처럼 보이면서 전부 틀린다 — 디지털 트윈에서 가장 위험한 실패 방식이다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np

from ..contracts.io import ProjectStore
from ..contracts.schema import FRAMOutput, InSAROutput, RemainingLifeOutput
from .elements import Element, associate, load_elements
from .georef import AlignmentError, MapConversion, fit_map_conversion, to_ifc_local
from .psets import aggregate_by_element, build_payload


def load_control_points(path: str | Path) -> tuple[np.ndarray, np.ndarray, str | None]:
    """측량 기준점 쌍 파일 → (local [K,2|3], map [K,2|3], target_crs).

    JSON 형식:
      {"target_crs": "EPSG:5186",
       "points": [{"name":"BM1","local":[x,y,z],"map":[E,N,H]}, ...]}
    표고(3번째 성분)는 선택 — 넣으면 3D 연결이 가능해진다.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    pts = raw.get("points", raw if isinstance(raw, list) else [])
    if len(pts) < 2:
        raise AlignmentError(f"기준점이 2개 이상 필요합니다(파일 {path} 에 {len(pts)}개)")
    loc = np.asarray([p["local"] for p in pts], dtype=np.float64)
    mp = np.asarray([p["map"] for p in pts], dtype=np.float64)
    if loc.shape[1] != mp.shape[1]:
        d = min(loc.shape[1], mp.shape[1])       # 한쪽만 표고가 있으면 2D 로 맞춘다
        loc, mp = loc[:, :d], mp[:, :d]
    return loc, mp, raw.get("target_crs") if isinstance(raw, dict) else None


def _read_project(path: str) -> dict:
    """정합에 필요한 것만 읽는다. 없는 그룹은 None(부분 파이프라인 허용)."""
    out: dict = {}
    with ProjectStore(path, mode="r") as s:
        ins = s.read_meta("insar", InSAROutput)
        out["n"] = int(ins.n_points)
        out["xyz"] = np.asarray(s.read_array(ins.xyz_ds), dtype=np.float64)
        out["member"] = np.asarray(s.read_array(ins.member_ds)).ravel()
        out["coherence"] = np.asarray(s.read_array(ins.coherence_ds), dtype=np.float64)
        los = np.asarray(s.read_array(ins.los_ds), dtype=np.float64)
        out["cumulative"] = los[:, -1] - los[:, 0]
        out["velocity"] = (np.asarray(s.read_array("/insar/velocity_mm_yr"), dtype=np.float64)
                           if s.has_array("/insar/velocity_mm_yr") else None)
        out["cri"] = None
        if s.has_meta("fram"):
            fr = s.read_meta("fram", FRAMOutput)
            out["cri"] = np.asarray(s.read_array(fr.CRI_ds), dtype=np.float64).max(axis=1)
        out["rsl"] = out["rate"] = None
        if s.has_meta("life"):
            lf = s.read_meta("life", RemainingLifeOutput)
            out["rsl"] = np.asarray(s.read_array(lf.rsl_lower_ds), dtype=np.float64)
            out["rate"] = np.asarray(s.read_array(lf.rate_ds), dtype=np.float64)
    return out


def _source_crs(xyz: np.ndarray, override: str | None) -> str:
    """점 좌표의 CRS. 지정이 없으면 경위도 범위인지로 추정한다."""
    if override:
        return override
    if np.abs(xyz[:, 0]).max() <= 180.0 and np.abs(xyz[:, 1]).max() <= 90.0:
        return "EPSG:4326"
    return "EPSG:5179"          # 국내 투영 좌표의 기본 가정(다르면 --bim-source-crs)


def align_project_to_bim(
    project_h5: str,
    elements: list[Element] | str | Path,
    *,
    map_conversion: MapConversion | dict | None = None,
    control_points: str | Path | None = None,
    source_crs: str | None = None,
    target_crs: str = "EPSG:5186",
    max_dist_m: float = 5.0,
    tol_m: float = 0.5,
    use_z: bool = False,
    max_rms_m: float = 0.5,
    min_points: int = 3,
    as_of: str | None = None,
) -> dict:
    """project.h5 를 BIM 부재에 정합·연결하고 부재별 상태 페이로드를 만든다.

    좌표 정합 우선순위: `map_conversion`(IFC 에서 읽은 것) > `control_points` 적합.
    둘 다 없으면 정합할 수 없으므로 `AlignmentError`.
    """
    els = load_elements(elements) if isinstance(elements, (str, Path)) else list(elements)
    proj = _read_project(project_h5)

    # ── ① 좌표 정합 ──────────────────────────────────────────────────
    if isinstance(map_conversion, dict):
        map_conversion = MapConversion.from_dict(map_conversion)
    if map_conversion is None:
        if control_points is None:
            raise AlignmentError(
                "좌표 정합 근거가 없습니다 — IFC 의 IfcMapConversion 을 주거나 "
                "측량 기준점 쌍(control_points)을 지정하세요. 정합 없이 부재에 값을 붙이면 "
                "결과가 정상처럼 보이면서 전부 틀립니다.")
        loc, mp, cp_crs = load_control_points(control_points)
        map_conversion = fit_map_conversion(loc, mp, max_rms_m=max_rms_m,
                                            target_crs=cp_crs or target_crs)
    if map_conversion.target_crs is None:
        map_conversion.target_crs = target_crs

    src_crs = _source_crs(proj["xyz"], source_crs)
    local, gmeta = to_ifc_local(proj["xyz"], map_conversion, source_crs=src_crs, use_z=use_z)

    alignment = {**map_conversion.to_dict(), **gmeta,
                 "control_points": (str(control_points) if control_points else None)}

    # ── ② 부재 연결 ──────────────────────────────────────────────────
    assoc = associate(local, els, member=proj["member"], max_dist_m=max_dist_m,
                      tol_m=tol_m, use_z=use_z)

    # ── ③ 부재별 집계 → Pset 페이로드 ────────────────────────────────
    per_el = aggregate_by_element(
        assoc["guid"],
        velocity_mm_yr=proj["velocity"], coherence=proj["coherence"],
        cumulative_mm=proj["cumulative"], cri=proj["cri"],
        rsl_lower_years=proj["rsl"], degradation_rate_mm_yr=proj["rate"],
        member_mismatch=assoc["member_mismatch"], distance_m=assoc["distance_m"],
        min_points=min_points,
    )
    payload = build_payload(per_el, project_h5=project_h5, alignment=alignment,
                            association=assoc["summary"], as_of=as_of or date.today().isoformat())

    return {
        "alignment": alignment,
        "association": assoc["summary"],
        "per_element": per_el,
        "payload": payload,
        "point_guid": assoc["guid"],
        "point_distance_m": assoc["distance_m"],
        "warnings": _warnings(alignment, assoc["summary"], per_el, use_z),
    }


def _warnings(alignment: dict, assoc: dict, per_el: dict, use_z: bool) -> list[str]:
    """조용히 넘어가면 안 되는 것들 — 결과가 그럴듯해 보일수록 중요하다."""
    w: list[str] = []
    fit = alignment.get("fit") or {}
    if fit.get("rms_m") is not None:
        w.append(f"정합 RMS 잔차 {fit['rms_m']}m (기준점 {fit.get('n_control_points')}개)")
    if alignment.get("source") == "control_points" and not fit.get("height_fitted"):
        w.append("기준점에 표고가 없어 수직기준면이 검증되지 않았습니다 — 2D 평면 연결만 유효합니다.")
    if use_z and not fit.get("height_fitted") and alignment.get("source") != "ifc":
        w.append("⚠ 표고 검증 없이 3D 연결을 사용했습니다.")
    frac = assoc.get("assigned_fraction", 0.0)
    if frac < 0.5:
        w.append(f"⚠ 점의 {frac * 100:.0f}% 만 부재에 연결됐습니다 — 정합 오차나 부재 테이블 "
                 "범위를 의심하세요(억지로 붙이지 않았습니다).")
    if assoc.get("n_ambiguous", 0) and assoc.get("dim") == 2:
        w.append(f"2D 투영에서 여러 부재에 동시에 들어간 점 {assoc['n_ambiguous']}개 — "
                 "상판 아래 교각처럼 평면상 겹치는 부재는 z 없이는 갈리지 않습니다"
                 "(부재 특정성·InSAR 라벨로 정했습니다).")
    if assoc.get("n_member_mismatch", 0):
        w.append(f"부재 라벨 불일치 {assoc['n_member_mismatch']}점 — InSAR CV 라벨과 BIM 타입이 "
                 "다릅니다(정합 오차 또는 라벨 오류).")
    miss = assoc.get("elements_without_points") or []
    if miss:
        w.append(f"관측점이 하나도 없는 부재 {len(miss)}개 — 위성 관측 사각(매끈한 면·음영) "
                 "가능성. 그 부재의 상태는 '알 수 없음'이지 '정상'이 아닙니다.")
    sparse = [g for g, r in per_el.items() if r.get("sparse")]
    if sparse:
        w.append(f"점 수가 적어 통계 신뢰가 낮은 부재 {len(sparse)}개(sparse=true).")
    return w


def write_result(result: dict, out_prefix: str | Path) -> dict:
    """정합 결과를 JSON 두 개로 저장 — 부재별 상태 + IFC 주입 페이로드."""
    p = Path(out_prefix)
    p.parent.mkdir(parents=True, exist_ok=True)
    state = p.with_name(p.name + "_elements.json")
    pay = p.with_name(p.name + "_pset.json")
    state.write_text(json.dumps({
        "alignment": result["alignment"], "association": result["association"],
        "warnings": result["warnings"], "elements": result["per_element"],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    pay.write_text(json.dumps(result["payload"], ensure_ascii=False, indent=1), encoding="utf-8")
    return {"elements_json": str(state), "pset_json": str(pay)}
