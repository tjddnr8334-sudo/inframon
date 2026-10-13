"""보수 CRI — 교량 제원(형식·종별·경간·재료)으로 CRI 의 기준 스케일을 정한다.

기본 CRI(``real_engine``)는 식이 같고 **스케일이 모든 교량에 같다**: 변위속도 10 mm/yr,
공간기울기 6 mm/yr/점, 가속 8 mm/yr². 교량 제원은 PINN 의 성분 분해와 경보 임계 배율에만 들어간다.
짧은 연속 콘크리트교와 긴 단순 강교가 같은 속도에서 같은 점수를 받는다.

보수 CRI 는 식을 그대로 두고 스케일만 **그 교량의 허용 변위**에서 끌어온다.

  허용 각변위 β_allow = min( 구조 한계(형식), 관리 기준 1/500, 재료 한계(연속교만) )
    구조 한계   단순 0.008 · 연속 0.004                    AASHTO LRFD C10.5.2.2
    재료 한계   β = σ_allow / (3·E·(d/L))                  양단고정 보의 지점침하 모멘트 M = 6EIδ/L²,
                                                           σ = M·(d/2)/I, δ = β·L 에서 유도
      σ_allow   콘크리트: 휨인장강도 f_r = 0.63·√fck [MPa]   KDS 14 20 30
                강재: 허용응력의 10 % 를 침하 몫으로 남김       가정
      정정 구조(단순교)는 지점침하가 응력을 만들지 않으므로 재료 한계를 적용하지 않는다.
  허용 침하 D_allow = min( 25 mm, β_allow × 경간 )
  스케일  속도 = D_allow / T_h   ·  각변위 = β_allow  ·  가속 = 2·D_allow / T_h²     (T_h = 10년)

보수적으로 잡은 것:
  · 한계는 여러 값 중 **가장 작은 것**. 형식을 모르면 연속교로, 재료를 모르면 가장 불리한 재료로.
  · 콘크리트 크리프에 의한 침하 응력 완화(통상 절반 안팎)를 **무시**한다.
  · 속도는 추정값이 아니라 **95 % 신뢰구간의 먼 쪽 끝**(잔차 자기상관 보정).
  · PINN 분해가 없어 구할 수 없는 기능 결합 항은 **상한**(완전 동조)으로 둔다.
  · 종별·점검등급·공용연수 배율은 임계를 낮추는 쪽으로만 쓴다(3종·기타라고 임계를 올리지 않는다).

정직하게 적어 둘 것: 폭은 이 식에서 결과를 바꾸지 않는다 — 보 이론에서 지점침하 응력은 I 가
약분돼 E·(d/L)·β 로만 정해진다. 폭은 횡방향 비틀림(좌우 침하차/폭)에서 의미가 있는데, 광역
시계열은 교량당 한 줄이라 그 값을 볼 수 없다. fck·강종은 대장에 없어 **기본값(가정)** 이다.
재료 한계·강재 10 %·T_h 는 법정 수치가 아니다 — 전부 ``opts`` 로 바꿀 수 있고 출처가 같이 나온다.
"""

from __future__ import annotations

import math

import numpy as np

from ..region.grade5 import CONTINUOUS_TYPES, STRUCT_BETA, span_of
from ..structure import MATERIAL_E
from .real_engine import age_alert_factor, grade_alert_factor, inspection_alert_factor

LEVELS = ("정상", "주의", "경고", "위험")
DEFAULTS = {
    "horizon_years": 10.0,          # 허용값을 소진하는 기준 기간 — 엔진의 10년 예측과 같게
    "mgmt_beta": 1.0 / 500.0,
    "mgmt_settlement_mm": 25.0,
    "fck_mpa": {"reinforced_concrete": 21.0, "prestressed_concrete": 35.0},
    "steel_allow_mpa": 140.0,       # 허용응력(SS400 급, 낮은 쪽)
    "steel_reserve": 0.10,          # 그중 침하 몫
    "weights": (0.4, 0.3, 0.15, 0.15),
    "thresholds": (0.3, 0.6, 0.85),
    "gate_ratio": 0.6,              # 활동도 게이트 스케일 / 속도 스케일 — 기본 엔진의 6/10
    "ci_z": 1.96,
}
SOURCES = {
    "struct_beta": "AASHTO LRFD C10.5.2.2 (Moulton et al. 1985) — 단순교 0.008 · 연속교 0.004 rad",
    "mgmt": "관리 기준 1/500 · 25 mm — inframon life.limits 기본 가정(법정 수치 아님)",
    "material_beta": "재료 한계 β = σ_allow/(3·E·d/L) — 양단고정 보 지점침하 모멘트 6EIδ/L² 에서 유도(가정, 크리프 완화 무시)",
    "concrete": "콘크리트 휨인장강도 f_r = 0.63√fck (KDS 14 20 30) · fck 기본값은 가정(RC 21 · PSC 35 MPa)",
    "steel": "강재 허용응력 140 MPa 의 10 % 를 침하 몫으로 — 가정",
    "horizon": "기준 기간 10년 — 엔진의 10년 예측과 같게(가정)",
    "formula": "CRI = 0.4·A + 0.3·R_couple + 0.15·R_spatial + 0.15·R_div — inframon FRAM(설계 문서 5.5), 가중치·임계는 미보정",
}

# 형식 이름 → (재료, 형고/경간 비). 이름에 들어 있는 낱말로 고른다(위에서부터 먼저 맞는 것).
_TYPE_RULES = (
    ("현수", "steel", 1 / 45), ("사장", "steel", 1 / 40), ("엑스트라도즈드", "prestressed_concrete", 1 / 35),
    ("트러스", "steel", 1 / 10), ("아치", "reinforced_concrete", 1 / 30),
    ("PSC박스", "prestressed_concrete", 1 / 18), ("강박스", "steel", 1 / 18), ("RC박스", "reinforced_concrete", 1 / 18),
    ("PSC중공슬래브", "prestressed_concrete", 1 / 25), ("PSC슬래브", "prestressed_concrete", 1 / 25),
    ("슬래브", "reinforced_concrete", 1 / 25), ("라멘", "reinforced_concrete", 1 / 22),
    ("PSCI", "prestressed_concrete", 1 / 17), ("프리플렉스", "steel", 1 / 20),
    ("RCT", "reinforced_concrete", 1 / 15), ("강I", "steel", 1 / 20), ("강플레이트", "steel", 1 / 20),
)
_MATERIALS = ("reinforced_concrete", "prestressed_concrete", "steel")


def _opts(opts: dict | None) -> dict:
    o = {**DEFAULTS, **{k: v for k, v in (opts or {}).items() if k in DEFAULTS and v is not None}}
    o["fck_mpa"] = {**DEFAULTS["fck_mpa"], **((opts or {}).get("fck_mpa") or {})}
    return o


def _sigma_allow_pa(material: str, o: dict) -> float:
    if material == "steel":
        return float(o["steel_allow_mpa"]) * float(o["steel_reserve"]) * 1e6
    return 0.63 * math.sqrt(float(o["fck_mpa"][material])) * 1e6


def material_beta(material: str, depth_ratio: float, o: dict) -> float:
    """연속교에서 지점침하가 허용 응력 증분에 닿는 각변위. 경간은 약분된다(형고/경간 비만 남는다)."""
    return _sigma_allow_pa(material, o) / (3.0 * MATERIAL_E[material] * depth_ratio)


def allowables(typ: str | None, cls: str | None, *, span_m=None, length_m=None, width_m=None,
               inspect_grade=None, build_year=None, as_of_year=None, opts: dict | None = None) -> dict:
    """교량 한 개의 허용값·스케일·임계와 그 근거."""
    o = _opts(opts)
    t = (typ or "").strip()
    rule = next(((m, r) for key, m, r in _TYPE_RULES if key in t), None)
    known = rule is not None
    continuous = (t in CONTINUOUS_TYPES) or not known          # 형식 미상 → 연속(불리한 쪽)
    span = span_of(t or None, span_m, length_m)
    if known:
        material, ratio = rule
        b_mat = material_beta(material, ratio, o) if continuous else None
    else:                                                        # 재료 미상 → 가장 불리한 재료
        material, ratio = min(((m, 1 / 18) for m in _MATERIALS), key=lambda mr: material_beta(mr[0], mr[1], o))
        b_mat = material_beta(material, ratio, o)
    b_struct = STRUCT_BETA["continuous" if continuous else "simple"]
    cands = {"구조 한계": b_struct, "관리 기준": float(o["mgmt_beta"])}
    if b_mat is not None:
        cands["재료 한계"] = b_mat
    governs = min(cands, key=cands.get)
    beta = cands[governs]
    d_beta = beta * span * 1000.0
    d_allow = min(float(o["mgmt_settlement_mm"]), d_beta)
    th = float(o["horizon_years"])
    # 임계 배율 — 낮추는 쪽으로만(3종·기타라고 올리지 않는다)
    ef = (min(grade_alert_factor(cls), 1.0) * inspection_alert_factor(inspect_grade)
          * age_alert_factor(build_year, as_of_year))
    return {
        "type": t or "미상", "type_known": known, "structure": "continuous" if continuous else "simple",
        "material": material, "depth_ratio": ratio, "span_m": round(span, 1),
        "width_m": width_m, "class": cls or "기타",
        "beta_struct": b_struct, "beta_mgmt": float(o["mgmt_beta"]),
        "beta_material": None if b_mat is None else round(b_mat, 6),
        "beta_allow": round(beta, 6), "governs": governs,
        "d_allow_mm": round(d_allow, 2),
        "d_governs": "침하 25 mm" if d_allow < d_beta else f"{governs} × 경간",
        "horizon_years": th,
        "vel_scale": d_allow / th, "gate_scale": d_allow / th * float(o["gate_ratio"]),
        "accel_scale": 2.0 * d_allow / th ** 2, "grad_scale": beta * 1000.0 / th,   # mm/yr/m
        "alert_factor": round(ef, 3),
        "thresholds": tuple(round(min(x * ef, 1.0), 3) for x in o["thresholds"]),
        "weights": tuple(o["weights"]), "ci_z": float(o["ci_z"]),
    }


def _sat(x: float, scale: float) -> float:
    return float(x / (x + scale)) if scale > 0 else 1.0


def fit_series(t_years, mm, *, z: float = 1.96) -> dict | None:
    """시계열 한 줄 → 속도·그 상한·가속. 계절(연주기)을 함께 적합해 가역 열거동을 뺀다.

    상한 = |기울기| + z·표준오차. 잔차의 1차 자기상관 ρ 로 표준오차를 √((1+ρ)/(1−ρ)) 배 키운다
    (InSAR 시계열은 서로 독립이 아니라, 그대로 두면 구간이 실제보다 좁다).
    """
    t = np.asarray(t_years, float)
    y = np.asarray(mm, float)
    ok = np.isfinite(t) & np.isfinite(y)
    t, y = t[ok], y[ok]
    if t.size < 8 or float(t.max() - t.min()) < 0.75:
        return None
    tc = t - t.mean()
    ph = 2.0 * np.pi * t
    X = np.column_stack([np.ones_like(tc), tc, np.sin(ph), np.cos(ph)])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ beta
    dof = max(t.size - X.shape[1], 1)
    s2 = float(res @ res) / dof
    se = math.sqrt(max(s2 * float(np.linalg.pinv(X.T @ X)[1, 1]), 0.0))
    rho = float(np.clip(np.corrcoef(res[:-1], res[1:])[0, 1], 0.0, 0.95)) if res.size > 3 and res.std() > 0 else 0.0
    se *= math.sqrt((1.0 + rho) / (1.0 - rho))
    Xq = np.column_stack([X, tc ** 2])
    bq, *_ = np.linalg.lstsq(Xq, y, rcond=None)
    worsening = bq[4] != 0 and np.sign(bq[4]) == np.sign(bq[1])     # 변위 크기가 점점 빨리 커지는가
    return {"rate": float(beta[1]), "se": se, "rate_upper": abs(float(beta[1])) + z * se,
            "accel": 2.0 * abs(float(bq[4])) if worsening else 0.0, "rho": rho,
            "noise_mm": math.sqrt(s2), "n": int(t.size), "years": float(t.max() - t.min())}


def cri_bridge(fit: dict | None, allow: dict, *, d_10y_mm=None, beta_now=None, beta_10y=None,
               zone_rates=None, length_m=None, conservative: bool = True) -> dict:
    """교량 한 개의 CRI(교량 단위). ``conservative=False`` 면 같은 식을 추정값·기본 결합으로 계산한다(비교용).

    A         10년 뒤 변위 수요 / 허용 침하     수요 = max(속도 상한 × T_h, 엔진의 10년 예측)
    R_couple  기능 결합 — 시계열 한 줄로는 PINN 분해가 없다 → 상한(완전 동조) × 활동도
    R_spatial 10년 뒤 각변위 / 허용 각변위      각변위 = max(엔진 값, 양 끝 구간 속도차 × T_h / 연장)
    R_div     가속 / (T_h 안에 허용값을 혼자 소진하는 가속)
    """
    th = allow["horizon_years"]
    w1, w2, w3, w4 = allow["weights"]
    v = 0.0 if fit is None else (fit["rate_upper"] if conservative else abs(fit["rate"]))
    demand = max(v * th, abs(float(d_10y_mm or 0.0)))
    A = _sat(demand, allow["d_allow_mm"])
    act = _sat(v, allow["gate_scale"])
    R_couple = act if conservative else 0.5 * act
    b = max(abs(float(beta_10y or 0.0)), abs(float(beta_now or 0.0)))
    zr = [float(x) for x in (zone_rates or []) if x is not None]
    if len(zr) >= 2 and length_m:
        b = max(b, (max(zr) - min(zr)) * th / 1000.0 / float(length_m))
    R_spatial = _sat(b, allow["beta_allow"])
    R_div = _sat(0.0 if fit is None else fit["accel"], allow["accel_scale"])
    cri = float(np.clip(w1 * A + w2 * R_couple + w3 * R_spatial + w4 * R_div, 0.0, 1.0))
    lo, mid, hi = allow["thresholds"]
    idx = 3 if cri >= hi else 2 if cri >= mid else 1 if cri >= lo else 0
    return {"cri": round(cri, 4), "level": LEVELS[idx], "idx": idx,
            "A": round(A, 4), "R_couple": round(R_couple, 4), "R_spatial": round(R_spatial, 4),
            "R_div": round(R_div, 4), "rate_used": round(v, 3), "demand_mm": round(demand, 2),
            "beta_used": round(b, 6)}
