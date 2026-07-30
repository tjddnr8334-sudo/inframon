"""OpenSees 독립 FE 교차검증 — PINN 구조 역산 검증.

openseespy 는 선택 의존이고 Windows 에서 DLL 로드가 RuntimeError 로 실패할 수 있어
(ImportError 아님) importorskip 대신 예외를 잡아 skip 한다. 실제 실행은 Linux/WSL CI.
모듈 자체는 lazy import 라 openseespy 없이도 임포트된다(아래 no-op 테스트로 확인).
"""

from __future__ import annotations

import pytest

from inframon import fem_opensees as fo


def _openseespy_or_skip():
    try:
        import openseespy.opensees  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — ImportError(Linux 미설치)+RuntimeError(Win DLL)
        pytest.skip(f"openseespy 사용 불가: {exc}")


def test_module_imports_without_openseespy():
    # lazy import 라 openseespy 없이도 모듈·순수함수는 동작해야
    A, Iz = fo.rect_section(1.0, 2.0)
    assert A == pytest.approx(2.0)
    assert Iz == pytest.approx(1.0 * 2.0 ** 3 / 12.0)


def test_require_openseespy_message_when_absent():
    # 없을 때 친절한 안내 메시지(설치·WSL 언급)
    try:
        import openseespy.opensees  # noqa: F401
    except Exception:  # noqa: BLE001
        with pytest.raises(RuntimeError, match="openseespy"):
            fo._require_openseespy()


def test_opensees_matches_closed_form_deflection():
    _openseespy_or_skip()
    # 단순지지 등분포 E-B 처짐 = 5qL⁴/384EI
    L, E, b, h, rho, q = 40.0, 3.0e10, 1.0, 1.0, 2400.0, 1.0e4
    A, Iz = fo.rect_section(b, h)
    resp = fo.opensees_beam(L=L, E=E, Iz=Iz, A=A, rho=rho, shear=False,
                            q_N_m=q, n_elem=24)
    w_mid = 5 * q * L ** 4 / (384 * E * Iz)
    assert abs(resp.w_m).max() == pytest.approx(w_mid, rel=1e-3)


def test_clean_ei_recovery_exact():
    _openseespy_or_skip()
    # 깨끗한 형상 → PINN 식별 공식이 EI 를 정확히 회수(전단은 4차도함수 0 기여)
    for h in (1.0, 2.0, 4.0):
        r = fo.crosscheck(L=40.0, b=1.0, h=h, shear=True)
        assert r.ei_err_pct < 1.0


def test_eb_control_group_agrees():
    _openseespy_or_skip()
    # 순환논리 방지 sanity: OpenSees도 E-B면 PINN E-B 진동수와 자명히 일치
    r = fo.crosscheck(L=40.0, b=1.0, h=3.0, shear=False)
    assert r.f1_err_pct < 0.5
    assert r.ei_err_pct < 0.5


def test_shear_model_error_grows_with_depth():
    _openseespy_or_skip()
    # 전단 모델오차(진동수)는 깊은 보에서 커진다 — E-B 가정의 한계
    slim = fo.crosscheck(L=40.0, b=1.0, h=0.5, shear=True)     # L/h=80
    deep = fo.crosscheck(L=40.0, b=1.0, h=5.0, shear=True)     # L/h=8
    assert deep.f1_err_pct > slim.f1_err_pct
    assert slim.f1_err_pct < 0.2                                # 슬렌더는 거의 일치


def test_timoshenko_correction_reduces_deep_beam_error():
    _openseespy_or_skip()
    # 깊은 보(L/h=8): E-B×Timoshenko보정이 완전 Timoshenko(전단+회전관성) 정해에
    # E-B 단독보다 더 가까워야 한다.
    from inframon.pinn.real_engine import _fem_beam_frequencies, _timoshenko_factors
    from inframon.structure import BridgeProfile
    L, E, rho, b, h = 40.0, 3.0e10, 2400.0, 1.0, 5.0
    A, Iz = fo.rect_section(b, h)
    resp = fo.opensees_beam(L=L, E=E, Iz=Iz, A=A, rho=rho, shear=True, rotary=True,
                            n_elem=40)
    f_os = resp.freqs_hz[0]
    f_eb = _fem_beam_frequencies(E * Iz, rho * A, L, "simply_supported")[0]
    prof = BridgeProfile(bridge_type="girder", youngs_modulus=E,
                         section_depth_m=h, width_m=b, mass_per_len=rho * A)
    f_corr = f_eb * _timoshenko_factors(prof, L, 1)[0]
    assert abs(f_corr - f_os) < abs(f_eb - f_os)           # 보정이 더 정확
    assert abs(f_corr - f_os) / f_os < 0.01                # 1% 이내


def test_full_pinn_recovers_ei_accurately():
    _openseespy_or_skip()
    pytest.importorskip("torch")
    pytest.importorskip("scipy")
    # 형상기반(x⁴계수) 식별 후: 절대 EI 를 정확히 회수하고 잡음에도 강건해야
    # (수정 전에는 NN autograd spectral bias 로 ~2.5× 과대였다).
    for nz in (0.0, 1.0):
        r = fo.crosscheck_via_pinn(L=40.0, b=1.0, h=2.0, noise_mm=nz,
                                   epochs=500, seed=1)
        s = r.EI_recovered / r.EI_true
        assert 0.7 < s < 1.3, f"잡음 {nz}mm 에서 EI 배율 {s:.2f} (0.7~1.3 밖)"
        assert r.f1_err_pct < 5.0                  # 진동수도 정확
