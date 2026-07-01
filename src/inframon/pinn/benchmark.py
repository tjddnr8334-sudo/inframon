"""PINN/FEM 구조 검증 — 상용 SW(SAP2000/MIDAS) 없이 해석해로 교차검증.

단순지지 Euler-Bernoulli 보의 고유진동수 **닫힌해**와 내부 FEM(Hermite 보요소)을 비교한다.
합성 EI 복원 검증도 제공. 무료 대안(OpenSees·PyNite·anastruct)으로도 같은 비교 가능.
"""
from __future__ import annotations

import numpy as np

from .real_engine import _fem_beam_frequencies


def analytic_ss_frequencies(EI: float, m_per_len: float, L: float, n_modes: int = 3) -> list[float]:
    """단순지지 보 고유진동수 닫힌해 fₙ = (n²π)/(2L²)·√(EI/ρA) [Hz]."""
    c = np.sqrt(EI / m_per_len)
    return [(k ** 2 * np.pi) / (2.0 * L ** 2) * c for k in range(1, n_modes + 1)]


def run_fem_benchmark(EI: float, m_per_len: float, L: float, n_modes: int = 3) -> dict:
    """해석해 vs 내부 FEM 고유진동수 비교 → 오차%. FEM 모달 솔버 검증."""
    ana = analytic_ss_frequencies(EI, m_per_len, L, n_modes)
    fem = [float(x) for x in _fem_beam_frequencies(EI, m_per_len, L, n_modes=n_modes)]
    m = min(len(ana), len(fem))
    err = [abs(fem[i] - ana[i]) / (ana[i] + 1e-12) * 100.0 for i in range(m)]
    return {"analytic_hz": ana[:m], "fem_hz": fem[:m], "err_pct": err,
            "max_err_pct": float(max(err)) if err else float("nan"),
            "EI": EI, "m_per_len": m_per_len, "L": L}


def ei_recovery_benchmark(EI_true: float, m_per_len: float, L: float,
                          q: float = 1.0e4) -> dict:
    """알려진 EI → 정적 처짐 합성 → 비차원 PDE 균형으로 EI 복원 → 오차%.

    단순지지 등분포하중 처짐 w(x)=q x(L³−2Lx²+x³)/(24 EI). 4차도함수는 상수(q/EI)라
    비차원 균형 EI = q·L⁴/(w_scale·d4_hat) 로 역산되는지 확인(식별식 자기일관성).
    """
    xs = np.linspace(0, L, 41)
    w = q * xs * (L ** 3 - 2 * L * xs ** 2 + xs ** 3) / (24.0 * EI_true + 1e-30)   # [m]
    w_scale = float(np.max(np.abs(w)) + 1e-30)
    xh = xs / L
    wh = w / w_scale
    d4 = np.gradient(np.gradient(np.gradient(np.gradient(wh, xh), xh), xh), xh)
    d4_hat = float(np.mean(np.abs(d4[3:-3])))                # 경계 제외 평균
    EI_rec = q * L ** 4 / (w_scale * d4_hat + 1e-30)
    err = abs(EI_rec - EI_true) / (EI_true + 1e-12) * 100.0
    return {"EI_true": EI_true, "EI_recovered": float(EI_rec), "err_pct": float(err)}


if __name__ == "__main__":
    # 예: 강재 거더 EI=2e11*0.05, ρA=1e4, L=110m
    r = run_fem_benchmark(2.1e11 * 0.05, 1.0e4, 110.0)
    print("FEM vs 해석해:", [round(x, 3) for x in r["fem_hz"]], "vs",
          [round(x, 3) for x in r["analytic_hz"]], "max오차 %.2f%%" % r["max_err_pct"])
    print("EI 복원:", ei_recovery_benchmark(5.0e9, 1.0e4, 110.0))
