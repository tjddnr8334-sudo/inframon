"""부재별 집계 → IFC Property Set 페이로드.

연결이 끝나면 부재 하나에 InSAR 점이 여러 개 붙는다. 그 점들을 **부재 상태 한 벌**로
줄여 IFC Pset 으로 주입한다.

두 가지 결정을 명시한다.

- **시계열은 IFC 에 넣지 않는다.** IfcPropertySingleValue 는 스칼라 상태를 담는 그릇이고,
  201시점 × 수천 점을 밀어 넣으면 IFC 가 뷰어에서 열리지 않는다. IFC 에는 **현재 상태 +
  출처 키**만 넣고, 시계열은 project.h5(트윈 데이터 레이어)에 남긴 뒤 키로 되짚는다.
- **집계는 강건 통계로.** 부재당 점 수가 적고(수 개) 이상치가 섞이므로 평균이 아니라
  중앙값을 쓰고, 위험 쪽(최댓값·최소 잔존수명)은 별도로 함께 보고한다. 평균 하나로
  줄이면 국소 이상이 사라진다.
"""

from __future__ import annotations

from datetime import date

import numpy as np

# IFC 에 주입할 Pset 이름. buildingSMART 표준 Pset 이 아니라 사용자 정의이므로
# `Pset_` 대신 벤더 접두사를 쓰는 게 규약에 맞다.
PSET_NAME = "Inframon_Monitoring"


def _robust(v: np.ndarray) -> dict:
    """유한값 강건 요약. 비면 전부 None."""
    x = np.asarray(v, dtype=np.float64).ravel()
    x = x[np.isfinite(x)]
    if x.size == 0:
        return {"median": None, "max_abs": None, "p95_abs": None, "n": 0}
    return {"median": round(float(np.median(x)), 4),
            "max_abs": round(float(np.max(np.abs(x))), 4),
            "p95_abs": round(float(np.percentile(np.abs(x), 95)), 4),
            "n": int(x.size)}


def aggregate_by_element(
    guid: np.ndarray,
    *,
    velocity_mm_yr: np.ndarray | None = None,
    coherence: np.ndarray | None = None,
    cumulative_mm: np.ndarray | None = None,
    cri: np.ndarray | None = None,
    rsl_lower_years: np.ndarray | None = None,
    degradation_rate_mm_yr: np.ndarray | None = None,
    member_mismatch: np.ndarray | None = None,
    distance_m: np.ndarray | None = None,
    min_points: int = 3,
) -> dict[str, dict]:
    """GUID 별 상태 집계. 반환: {guid: {지표…}}.

    `min_points` 미만이면 값을 지우지 않고 `sparse=True` 로 표시한다 —
    점 1개짜리 부재의 값은 참고는 되지만 통계로 신뢰할 수 없다.
    """
    g = np.asarray(guid, dtype=object).ravel()
    out: dict[str, dict] = {}
    for key in np.unique(g):
        if not key:                                   # 미연결 점
            continue
        m = g == key
        rec: dict = {"n_points": int(m.sum())}
        if velocity_mm_yr is not None:
            rec["velocity_mm_yr"] = _robust(np.asarray(velocity_mm_yr)[m])
        if cumulative_mm is not None:
            rec["cumulative_mm"] = _robust(np.asarray(cumulative_mm)[m])
        if coherence is not None:
            c = np.asarray(coherence, float)[m]
            c = c[np.isfinite(c)]
            rec["coherence_median"] = round(float(np.median(c)), 4) if c.size else None
        if cri is not None:
            x = np.asarray(cri, float)[m]
            x = x[np.isfinite(x)]
            rec["cri_max"] = round(float(x.max()), 4) if x.size else None
        if rsl_lower_years is not None:
            x = np.asarray(rsl_lower_years, float)[m]
            fin = x[np.isfinite(x)]
            # 부재의 잔존수명은 그 부재에서 **가장 이른** 값이다(가장 약한 곳이 지배).
            rec["rsl_lower_years"] = round(float(fin.min()), 3) if fin.size else None
            rec["rsl_censored_fraction"] = round(float(np.mean(~np.isfinite(x))), 3)
        if degradation_rate_mm_yr is not None:
            rec["degradation_rate_mm_yr"] = _robust(np.asarray(degradation_rate_mm_yr)[m])
        if member_mismatch is not None:
            rec["member_mismatch_points"] = int(np.asarray(member_mismatch)[m].sum())
        if distance_m is not None:
            d = np.asarray(distance_m, float)[m]
            d = d[np.isfinite(d)]
            rec["assoc_distance_m_median"] = round(float(np.median(d)), 3) if d.size else None
        rec["sparse"] = bool(rec["n_points"] < min_points)
        out[str(key)] = rec
    return out


def _flat_properties(rec: dict) -> dict:
    """중첩 집계 → IfcPropertySingleValue 로 넣을 평평한 key: value.

    IFC Pset 은 스칼라만 담으므로 여기서 평탄화한다. 이름은 뷰어에서 바로 읽히도록
    단위를 붙여 둔다.
    """
    p: dict = {"PointCount": rec.get("n_points"), "Sparse": rec.get("sparse")}
    v = rec.get("velocity_mm_yr") or {}
    if v.get("median") is not None:
        p["VelocityMedian_mm_per_yr"] = v["median"]
        p["VelocityMaxAbs_mm_per_yr"] = v["max_abs"]
    c = rec.get("cumulative_mm") or {}
    if c.get("max_abs") is not None:
        p["CumulativeMaxAbs_mm"] = c["max_abs"]
    for src, dst in (("coherence_median", "CoherenceMedian"),
                     ("cri_max", "CRIMax"),
                     ("rsl_lower_years", "RemainingLifeLower_yr"),
                     ("rsl_censored_fraction", "RemainingLifeCensoredFraction"),
                     ("assoc_distance_m_median", "AssociationDistanceMedian_m"),
                     ("member_mismatch_points", "MemberMismatchPointCount")):
        if rec.get(src) is not None:
            p[dst] = rec[src]
    r = rec.get("degradation_rate_mm_yr") or {}
    if r.get("max_abs") is not None:
        p["DegradationRateMaxAbs_mm_per_yr"] = r["max_abs"]
    return p


def build_payload(
    per_element: dict[str, dict],
    *,
    project_h5: str,
    alignment: dict,
    association: dict,
    as_of: str | None = None,
    pset_name: str = PSET_NAME,
) -> dict:
    """IFC 주입용 페이로드 — {guid: {pset_name: {prop: value}}} + 출처 메타.

    각 부재에 `SourceProject`(시계열이 있는 project.h5)와 `SourceGroup` 을 넣어
    IFC 에서 트윈 데이터 레이어로 되짚을 수 있게 한다. IFC 는 상태, 시계열은 h5.
    """
    stamp = as_of or date.today().isoformat()
    elements = {}
    for guid, rec in per_element.items():
        props = _flat_properties(rec)
        props["SourceProject"] = str(project_h5)
        props["SourceGroups"] = "/insar,/fram,/life"
        props["UpdatedAt"] = stamp
        elements[guid] = {pset_name: props}
    return {
        "pset_name": pset_name,
        "as_of": stamp,
        "n_elements": len(elements),
        "elements": elements,
        "provenance": {
            "project_h5": str(project_h5),
            "alignment": alignment,
            "association": association,
            "note": ("IFC 에는 현재 상태만 주입한다. 시계열은 project.h5 에 남으며 "
                     "SourceProject/SourceGroups 로 되짚는다 — Pset 에 시계열을 넣으면 "
                     "IFC 가 뷰어에서 열리지 않는다."),
        },
    }
