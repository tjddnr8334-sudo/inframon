"""PINN/FEM 구조 검증(benchmark) — 해석해 대비 FEM·EI 복원 오차."""
from __future__ import annotations


from inframon.pinn.benchmark import analytic_ss_frequencies, ei_recovery_benchmark, run_fem_benchmark


def test_analytic_frequencies_n_squared_ratio():
    f = analytic_ss_frequencies(2.1e11 * 0.05, 1.0e4, 110.0, n_modes=3)
    # 단순지지: f2/f1=4, f3/f1=9 (n²)
    assert abs(f[1] / f[0] - 4.0) < 1e-6
    assert abs(f[2] / f[0] - 9.0) < 1e-6


def test_fem_matches_analytic_within_5pct():
    for EI in (1e9, 5e9, 5e10):
        r = run_fem_benchmark(EI, 1.0e4, 110.0, n_modes=3)
        assert r["max_err_pct"] < 5.0, f"EI={EI}: {r['max_err_pct']}%"
        assert len(r["fem_hz"]) == 3


def test_ei_recovery_within_5pct():
    for EI in (1e9, 5e9, 2e10):
        r = ei_recovery_benchmark(EI, 1.0e4, 110.0)
        assert r["err_pct"] < 5.0, f"EI={EI}: {r['err_pct']}%"
