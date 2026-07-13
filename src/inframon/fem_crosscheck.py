"""상용 FEM 교차검증 — PINN 식별 구조응답을 독립 FEM/해석 벤치마크와 대조.

상용 FEM(MIDAS Civil·ABAQUS·SAP2000 등) 빔 모델이 **교량 설계제원**으로 산출할
고유진동수·정적처짐을 독립적으로 계산(경계별 닫힌형 Euler-Bernoulli 해 + PINN 이 쓰는
consistent-mass FEM 과 무관한 해석식)해 PINN 결과와 교차검증한다.

세 축으로 대조한다:
  1) **솔버 검증** — PINN 의 FEM 고유진동수 vs 동일 EI·경계의 해석식 → 구현 자체 검증(≈일치).
  2) **설계 기준(상용 FEM 등가)** — 기하 EI(E·I from 단면)·실 경계로 설계 고유진동수·처짐 산출.
  3) **식별 vs 설계** — EI 식별값/기하값 비(강성상태·SHM 지표), 1차모드 물리타당성.

⚠️ 특정 상용 SW 의 출력이 아니라 **동등한 물리기반 벤치마크**다. 실제 MIDAS/ABAQUS
익스포트(점별 변위·모달) CSV 가 오면 `validation.py` 로 점별 정합 대조한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# 경계조건별 β_nL (Euler-Bernoulli 고유치) — ω_n = (β_nL)²·√(EI/(m·L⁴))
_BETA_L = {
    "simply_supported": [math.pi, 2 * math.pi, 3 * math.pi],
    "fixed": [4.730041, 7.853205, 10.995608],        # 양단고정
    "continuous": [3.926602, 7.068583, 10.210176],   # 고정-핀(2경간 연속 근사)
    "cantilever": [1.875104, 4.694091, 7.854757],
}
# 경계별 등분포하중 UDL 최대처짐 계수: δ_max = coef · q·L⁴ / EI
_DEFL_COEF = {
    "simply_supported": 5.0 / 384.0,
    "fixed": 1.0 / 384.0,
    "continuous": 1.0 / 185.0,     # 고정-핀 최대처짐(≈0.577L 지점)
    "cantilever": 1.0 / 8.0,
}


def _effective_boundary(boundary: str) -> str:
    """다경간 연속교(continuous)의 내부경간은 고정단으로 근사 — FEM 모달과 일치시킨다."""
    return "fixed" if boundary == "continuous" else boundary


def analytical_frequencies(EI: float, m_per_len: float, L: float,
                           boundary: str = "simply_supported",
                           n_modes: int = 3) -> list[float]:
    """경계별 Euler-Bernoulli 보 고유진동수[Hz] 닫힌형 해. f_n=(β_nL)²/(2π)·√(EI/(m·L⁴))."""
    bl = _BETA_L.get(_effective_boundary(boundary), _BETA_L["simply_supported"])[:n_modes]
    c = math.sqrt(EI / (m_per_len * L ** 4))
    return [(b ** 2) / (2 * math.pi) * c for b in bl]


def midspan_deflection_mm(q_N_m: float, EI: float, L: float,
                          boundary: str = "simply_supported") -> float:
    """등분포하중 q[N/m] 하 최대 정적처짐[mm]. δ=coef·qL⁴/EI."""
    coef = _DEFL_COEF.get(_effective_boundary(boundary), _DEFL_COEF["simply_supported"])
    return coef * q_N_m * L ** 4 / EI * 1000.0


@dataclass
class FemCrosscheckResult:
    boundary: str
    span_m: float
    m_per_len: float
    EI_geometric: float | None       # 설계(단면 E·I)
    EI_identified: float             # PINN 식별
    ei_ratio: float | None           # 식별/설계
    freq_design: list                # 기하 EI·실 경계(상용 FEM 등가)
    freq_identified: list            # 식별 EI·경계
    freq_pinn_fem: list              # PINN 의 FEM 출력
    solver_error_pct: list           # PINN FEM vs 해석식(동일 식별 EI) — 구현검증
    deflection_design_mm: float | None
    deflection_identified_mm: float
    first_mode_plausible: bool       # 설계 1차모드가 통상범위(0.2~10Hz)
    identified_reliable: bool        # 식별 EI 가 클립 경계·비물리 아님
    design_deflection_ratio: float | None    # δ_design / L (사용성; L/400 초과면 모델부적합)
    model_plausible: bool            # 스팬·경계 모델이 물리적으로 타당
    assessment: str
    per_mode: list = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "════ 상용 FEM 교차검증 ════",
            f" 경계 {self.boundary} · 스팬 {self.span_m:.0f}m · 질량 {self.m_per_len/1e3:.1f}t/m",
        ]
        if self.EI_geometric:
            lines.append(f" EI  설계(기하) {self.EI_geometric:.3e} · 식별 {self.EI_identified:.3e}"
                         f" · 비 {self.ei_ratio:.2f}×")
        lines.append(" 고유진동수[Hz]  (설계 | 식별 | PINN-FEM | 솔버오차%)")
        for m in self.per_mode:
            fd = f"{m['design']:.3f}" if m["design"] is not None else "  -  "
            lines.append(f"   {m['mode']}차: {fd} | {m['identified']:.3f} | "
                         f"{m['pinn_fem']:.3f} | {m['solver_err_pct']:.2f}%")
        if self.deflection_design_mm is not None:
            ratio = f" (L/{1/self.design_deflection_ratio:.0f})" if self.design_deflection_ratio else ""
            lines.append(f" 설계하중 최대처짐  설계 {self.deflection_design_mm:.2f}mm{ratio} · "
                         f"식별 {self.deflection_identified_mm:.3f}mm")
        lines.append(f" 판정: {self.assessment}")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "boundary": self.boundary, "span_m": self.span_m,
            "EI_geometric": self.EI_geometric, "EI_identified": self.EI_identified,
            "ei_ratio": None if self.ei_ratio is None else round(self.ei_ratio, 4),
            "freq_design": [round(f, 4) for f in self.freq_design],
            "freq_identified": [round(f, 4) for f in self.freq_identified],
            "freq_pinn_fem": [round(f, 4) for f in self.freq_pinn_fem],
            "solver_error_pct": [round(e, 3) for e in self.solver_error_pct],
            "deflection_design_mm": None if self.deflection_design_mm is None
            else round(self.deflection_design_mm, 3),
            "deflection_identified_mm": round(self.deflection_identified_mm, 4),
            "first_mode_plausible": self.first_mode_plausible,
            "identified_reliable": self.identified_reliable,
            "design_deflection_ratio": None if self.design_deflection_ratio is None
            else round(self.design_deflection_ratio, 5),
            "model_plausible": self.model_plausible,
            "assessment": self.assessment,
        }


# EI 식별 물리 클립 경계(pinn/real_engine._identify_EI_from_pde 와 동일)
_EI_FLOOR, _EI_CEIL = 1.0e6, 1.0e14


def crosscheck(*, EI_identified: float, EI_geometric: float | None,
               m_per_len: float, span_m: float, q_N_m: float,
               boundary: str = "simply_supported",
               freq_pinn_fem: list | None = None,
               n_modes: int = 3) -> FemCrosscheckResult:
    """PINN 식별값과 설계(기하 EI) FEM 벤치마크를 교차검증."""
    L = float(max(span_m, 5.0))
    freq_identified = analytical_frequencies(EI_identified, m_per_len, L, boundary, n_modes)
    freq_design = (analytical_frequencies(EI_geometric, m_per_len, L, boundary, n_modes)
                   if EI_geometric else [])
    freq_pinn_fem = list(freq_pinn_fem or [])[:n_modes]

    # 1) 솔버 검증: PINN FEM(식별 EI·동일 경간·경계) vs 해석식(식별 EI) — 구현 일치 검증
    ana_ref = analytical_frequencies(EI_identified, m_per_len, L, boundary, n_modes)
    solver_err = []
    for i in range(len(freq_pinn_fem)):
        ref = ana_ref[i] if i < len(ana_ref) else float("nan")
        solver_err.append(abs(freq_pinn_fem[i] - ref) / ref * 100.0 if ref else float("nan"))

    ei_ratio = (EI_identified / EI_geometric) if EI_geometric else None
    defl_design = (midspan_deflection_mm(q_N_m, EI_geometric, L, boundary)
                   if EI_geometric else None)
    defl_ident = midspan_deflection_mm(q_N_m, EI_identified, L, boundary)

    f1_design = freq_design[0] if freq_design else (freq_identified[0] if freq_identified else 0.0)
    first_mode_plausible = 0.2 <= f1_design <= 10.0
    at_bound = EI_identified >= 0.99 * _EI_CEIL or EI_identified <= 1.01 * _EI_FLOOR
    ratio_ok = ei_ratio is None or 0.2 <= ei_ratio <= 5.0
    identified_reliable = (not at_bound) and ratio_ok
    # 설계하중 처짐 사용성(L/400 통상 한계). 단일 SS 스팬 모델이 실제 다경간 연속을 못 담으면 초과.
    defl_ratio = (defl_design / 1000.0 / L) if defl_design else None
    model_plausible = defl_ratio is None or defl_ratio <= 1.0 / 400.0

    per_mode = []
    for i in range(max(len(freq_design), len(freq_identified), len(freq_pinn_fem))):
        per_mode.append({
            "mode": i + 1,
            "design": freq_design[i] if i < len(freq_design) else None,
            "identified": freq_identified[i] if i < len(freq_identified) else float("nan"),
            "pinn_fem": freq_pinn_fem[i] if i < len(freq_pinn_fem) else float("nan"),
            "solver_err_pct": solver_err[i] if i < len(solver_err) else float("nan"),
        })

    # 판정 — 솔버검증 → 모델타당성 → EI 신뢰성 → 강성상태 순
    solver_ok = all((e < 5.0) for e in solver_err if e == e) if solver_err else True
    if not solver_ok:
        assessment = "⚠️ FEM 솔버-해석식 불일치(구현 점검 필요)"
    elif not model_plausible:
        assessment = (f"⚠️ 설계하중 처짐 L/{1/defl_ratio:.0f} (>L/400 사용성한계) — 단일경간 "
                      f"모델이 실제 다경간 연속교를 못 담음. 최대경간·연속경계로 재모델 권장")
    elif at_bound:
        assessment = ("⚠️ 식별 EI 가 물리 클립 경계 — 관측창이 짧거나 준강체 응답이라 EI "
                      "식별이 부정확. 설계(기하) FEM 값을 신뢰기준으로 사용 권장")
    elif ei_ratio is not None and ei_ratio < 0.7:
        assessment = f"⚠️ 식별 강성이 설계 대비 {ei_ratio*100:.0f}% — 강성저하 의심(추가 점검)"
    elif ratio_ok and EI_geometric:
        assessment = f"✅ 식별 EI 가 설계 대비 {ei_ratio:.2f}× — 설계 FEM 과 정합"
    else:
        assessment = "설계 EI 미상(단면 폭 없음) — 솔버 검증만 수행"
    # 설계 1차모드 물리범위 이탈(경간 추정·단면제원 근사 신호) — 부가 경고
    if freq_design and not first_mode_plausible:
        assessment += f" · ⚠️설계 1차 {f1_design:.1f}Hz 비정상(경간수·단면제원 근사 점검)"

    return FemCrosscheckResult(
        boundary=boundary, span_m=L, m_per_len=m_per_len,
        EI_geometric=EI_geometric, EI_identified=EI_identified, ei_ratio=ei_ratio,
        freq_design=freq_design, freq_identified=freq_identified, freq_pinn_fem=freq_pinn_fem,
        solver_error_pct=solver_err, deflection_design_mm=defl_design,
        deflection_identified_mm=defl_ident, first_mode_plausible=first_mode_plausible,
        identified_reliable=identified_reliable, design_deflection_ratio=defl_ratio,
        model_plausible=model_plausible, assessment=assessment, per_mode=per_mode)


def crosscheck_project(project_h5, *, boundary: str | None = None,
                       n_modes: int = 3) -> FemCrosscheckResult:
    """project.h5 의 /pinn(식별 EI·기하 EI·설계하중·단면·고유진동수)로 FEM 교차검증."""
    import json

    import h5py

    from .structure import MATERIAL_DENSITY, MATERIAL_RHO_A

    with h5py.File(str(project_h5), "r") as f:
        if "pinn" not in f:
            raise ValueError("project.h5 에 /pinn 이 없습니다 — 먼저 PINN 을 실행하세요.")
        g = f["pinn"]
        inp = json.loads(g.attrs["inputs"]) if "inputs" in g.attrs else {}
        freq_pinn = [float(x) for x in g["natural_freq"][()]] if "natural_freq" in g else []

    EI_id = float(inp.get("EI_global") or 0.0)
    EI_geom = inp.get("geometric_EI_Nm2")
    EI_geom = float(EI_geom) if EI_geom else None
    # 다경간 연속교는 단일 경간(structural_span_m)으로 모달·설계 처짐을 평가. 없으면 연장.
    span = float(inp.get("structural_span_m") or inp.get("span_m") or 0.0)
    q = float(inp.get("q_effective_N_m") or inp.get("load_per_len") or 1.0e4)
    area = inp.get("section_area_m2")
    material = inp.get("material", "steel")
    # 질량/길이 ρA: 단면적×재료밀도(정밀), 없으면 재료 대푯값
    if area:
        m_per_len = MATERIAL_DENSITY.get(material, 7850.0) * float(area)
    else:
        m_per_len = MATERIAL_RHO_A.get(material, 1.0e4)
    bnd = boundary or inp.get("boundary") or "simply_supported"

    return crosscheck(EI_identified=EI_id, EI_geometric=EI_geom, m_per_len=m_per_len,
                      span_m=span, q_N_m=q, boundary=bnd, freq_pinn_fem=freq_pinn,
                      n_modes=n_modes)
