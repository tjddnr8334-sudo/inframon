"""FRAM 고도화(Phase 5) — 점별 공명 + 실 load + 절대 보정. 계약·결함수정 검증."""

from __future__ import annotations

import numpy as np

from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import FRAMOutput
from inframon.fram.real_engine import _pointwise_resonance, run_fram_real


def test_pointwise_resonance_is_spatial():
    # 점마다 다른 동조 패턴 → 점별로 다른 공명값이 나와야 (broadcast 결함 해소 확인)
    rng = np.random.default_rng(0)
    N, M = 20, 24
    Vi = rng.random((4, N, M))
    # 점 0~9 는 기능들이 동일(완전 동조) → 높은 공명
    for f in range(4):
        Vi[f, :10, :] = Vi[0, :10, :]
    R = _pointwise_resonance(Vi, win=6)
    assert R.shape == (N, M)
    assert R.var() > 0                          # 점별로 다름 (broadcast 아님)
    assert R[:10, 6:].mean() > R[10:, 6:].mean()  # 동조 점들이 더 높은 공명


def _build(tmp_path, engines, n_points=30, n_dates=12, pinn_epochs=None):
    cfg = PipelineConfig(n_points=n_points, n_dates=n_dates, engines=engines)
    if pinn_epochs:
        cfg.pinn_epochs = pinn_epochs
    from inframon.orchestrator.pipeline import run_pipeline
    return cfg, run_pipeline(tmp_path / "p.h5", cfg)


def test_fram_real_fills_contract(tmp_path):
    cfg, fram = _build(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    assert isinstance(fram, FRAMOutput)
    with ProjectStore(tmp_path / "p.h5", mode="r") as store:
        cri = store.read_array(fram.CRI_ds)
        rij = store.read_array(fram.resonance_Rij_ds)
        amp = store.read_array(fram.amplification_ds)
    assert cri.shape == (30, 12)
    assert rij.shape == (4, 4, 12)
    assert amp.shape == (30, 12)
    assert (cri >= 0).all() and (cri <= 1).all()
    assert np.isfinite(cri).all()
    assert 0.0 <= fram.cri_global_max <= 1.0


def test_fram_real_cri_varies_spatially(tmp_path):
    # 점별 공명·절대보정 → CRI 가 점마다 다르고, 마지막 시점 손상 단조성 유지
    cfg, fram = _build(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    with ProjectStore(tmp_path / "p.h5", mode="r") as store:
        cri = store.read_array(fram.CRI_ds)
    assert cri[:, -1].var() > 0                       # 공간적으로 다름
    assert cri[:, -3:].max() >= cri[:, :3].max()      # 주입 손상 누적 → 후반 ≥ 초반


def test_network_resonance_spectral():
    """함수망 공명 = 결합그래프 스펙트럼 반경/normalize: 완전동조=1, 무결합=0, 0.5=0.5."""
    from inframon.fram.real_engine import _network_resonance

    M = 5
    full = np.ones((4, 4, M))                              # 모든 기능쌍 |corr|=1
    assert np.allclose(_network_resonance(full), 1.0, atol=1e-6)
    ident = np.repeat(np.eye(4)[:, :, None], M, axis=2)    # 비대각 0 = 무결합
    assert np.allclose(_network_resonance(ident), 0.0, atol=1e-6)
    half = np.full((4, 4, M), 0.5)                         # 비대각 0.5 → λmax=1.5, /3=0.5
    assert np.allclose(_network_resonance(half), 0.5, atol=1e-6)


def test_fram_real_emits_network_resonance(tmp_path):
    """real FRAM 이 함수망 공명 시계열 [M]∈[0,1] 을 산출한다(stub 은 None)."""
    cfg, fram = _build(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    assert fram.network_resonance_ds is not None
    with ProjectStore(tmp_path / "p.h5", mode="r") as store:
        s = store.read_array(fram.network_resonance_ds)
    assert s.shape == (12,)
    assert (s >= 0).all() and (s <= 1).all() and np.isfinite(s).all()


def test_fram_stub_has_no_network_resonance(tmp_path):
    """stub FRAM 은 network_resonance 를 내지 않는다(None) → 골든·계약 무영향."""
    cfg, fram = _build(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "stub"})
    assert fram.network_resonance_ds is None


def test_fram_real_registered():
    from inframon.orchestrator import engines

    assert engines.resolve("fram", "real") is run_fram_real
    assert "real" in engines.available_modes("fram")


def test_pipeline_pinn_real_fram_real(tmp_path):
    # 실 PINN(실 load) → 실 FRAM 전체 체인
    cfg, fram = _build(tmp_path,
                       {"cv": "stub", "insar": "stub", "pinn": "real", "fram": "real"},
                       n_points=20, n_dates=8, pinn_epochs=50)
    assert isinstance(fram, FRAMOutput)
    assert 0.0 <= fram.cri_global_max <= 1.0


def _inject_vertical_settlement(tmp_path):
    """stub 프로젝트에 '전 점 동일 종축 열팽창 + 한 클러스터 연직 가속 침하'를 주입.

    종축(longitudinal)은 균일 열팽창이라 종축항만으로는 침하 클러스터가 안 드러나고,
    침하는 연직(vertical_ds)에만 실린다 → FRAM 이 vertical 을 소비할 때만 국소화돼야 한다.
    asc+desc 융합 결과(longitudinal=H, vertical=U)를 그대로 모사. (out, cfg, cluster) 반환.
    """
    from inframon.contracts.schema import InSAROutput
    from inframon.orchestrator.pipeline import run_pipeline
    cfg = PipelineConfig(n_points=60, n_dates=20,
                         engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    out = tmp_path / "p.h5"
    run_pipeline(out, cfg)
    rng = np.random.default_rng(3)
    with ProjectStore(out, mode="a") as s:
        ins = s.read_meta("insar", InSAROutput)
        N, M = ins.n_points, ins.n_dates
        dates = np.asarray(s.read_array(ins.dates_ds), float)
        t = (dates - dates[0]) / max(dates[-1] - dates[0], 1.0)        # 0..1
        cluster = np.zeros(N, bool)
        cluster[N // 2 - 6 : N // 2 + 6] = True
        # 종축: 전 점 공통 열팽창(계절) + 소량 점별 노이즈 → 균일(비국소)
        lon = 3.0 * np.sin(2 * np.pi * t)[None, :] + rng.normal(0, 0.2, (N, M))
        # 연직: 클러스터만 가속 침하(mm)
        vert = np.zeros((N, M), np.float32)
        vert[cluster] = (-30.0 * t**2).astype(np.float32)[None, :]
        s.write_array(ins.longitudinal_ds, lon.astype(np.float32))
        s.write_array("/insar/vertical", vert)
        ins.vertical_ds = "/insar/vertical"
        s.write_meta("insar", ins)
    return out, cfg, cluster


def _fram_cri(out, cfg, use_vertical):
    from inframon.contracts.schema import InSAROutput, PINNOutput
    cfg.fram_use_vertical = use_vertical
    with ProjectStore(out, mode="a") as s:
        ins = s.read_meta("insar", InSAROutput)
        pinn = s.read_meta("pinn", PINNOutput)
        run_fram_real(s, ins, pinn, cfg)
        cri = np.asarray(s.read_array("/fram/CRI"))[:, -1]
        vert = np.abs(np.asarray(s.read_array(ins.vertical_ds))[:, -1]) if ins.vertical_ds else None
        used = s.read_json_attr("fram", "vertical_term")["used"]
    return cri, vert, used


def test_fram_consumes_vertical_localizes_settlement(tmp_path):
    """opt-in 연직 융합: vertical_ds 가 있으면 FRAM 이 침하 클러스터에 CRI 를 집중시킨다.

    국소화 진단(단일궤도 deprojection·강계절이 종축 CRI 의 침하 국소화를 흐림)에 대한 보완.
    실 융합 데모에서 '침하 vs CRI 상관 -0.08 → +0.52' 로 확인된 효과를 못박는다.
    """
    out, cfg, cluster = _inject_vertical_settlement(tmp_path)
    cri_off, vert, used_off = _fram_cri(out, cfg, use_vertical=False)
    cri_on, _, used_on = _fram_cri(out, cfg, use_vertical=True)

    assert used_off is False and used_on is True               # 관측 플래그
    r_off = np.corrcoef(vert, cri_off)[0, 1]
    r_on = np.corrcoef(vert, cri_on)[0, 1]
    # 핵심: 침하(|U|)와 CRI 의 상관이 연직 소비로 뚜렷이 개선
    assert r_on > r_off + 0.2 and r_on > 0.3
    # 국소화 대비(클러스터−비클러스터)가 연직 소비로 개선
    contrast_off = cri_off[cluster].mean() - cri_off[~cluster].mean()
    contrast_on = cri_on[cluster].mean() - cri_on[~cluster].mean()
    assert contrast_on > contrast_off + 0.03
    # 연직은 침하 클러스터를 비클러스터보다 더 많이 올린다(경계점은 공간전파로 약간 오름)
    assert (cri_on[cluster] - cri_off[cluster]).mean() > (cri_on[~cluster] - cri_off[~cluster]).mean()


def test_healthy_bridge_stays_low_cri(tmp_path):
    """멀쩡한 교량(작은 선형추세 + 계절 열팽창 + 측정노이즈)은 CRI 가 **낮아야** 한다.

    회귀 방지: 점-대-점 미분이 InSAR 에폭 노이즈(σ~10mm)를 수백 mm/yr 로 폭증시켜
    건강한 교량도 CRI 가 포화(≈0.98)되던 버그를 로버스트 secular 속도로 고쳤다.
    가역 계절 호흡·측정 노이즈는 위험이 아니므로 CRI 가 낮게 유지돼야 한다.
    """
    from inframon.insar.track_reader import write_insar_contract

    rng = np.random.default_rng(7)
    N, M = 80, 24
    dates = np.arange(M, dtype=float) * 24.0                 # ~2년 span(계절 제거 가능)
    t = dates / 365.0
    x = np.linspace(0.0, 120.0, N)
    xyz = np.column_stack([x, np.zeros(N), np.zeros(N)])
    seasonal = 4.0 * np.sin(2 * np.pi * t)[None, :]         # 가역 열팽창(건강)
    trend = (rng.uniform(-1.5, 0.5, N))[:, None] * t[None, :]  # 미세 선형(≤1.5mm/yr)
    los = (seasonal + trend + rng.normal(0.0, 10.0, (N, M))).astype(np.float32)  # σ=10mm 노이즈
    cfg = PipelineConfig(n_points=N, n_dates=M,
                         engines={"cv": "stub", "insar": "stub", "pinn": "real", "fram": "real"})
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        insar = write_insar_contract(
            store, xyz=xyz, member=np.zeros(N, np.int8),
            coherence=np.full(N, 0.7, np.float32), l_from_fixed=np.abs(x - x.mean()).astype(np.float32),
            los=los, longitudinal=los, dates=dates, date_labels=None)
        from inframon.pinn.engine import run_pinn
        pinn = run_pinn(store, insar, cfg)
        fram = run_fram_real(store, insar, pinn, cfg)
        cri = store.read_array(fram.CRI_ds)
    # 핵심: 노이즈·계절만 있는 건강 교량은 CRI 가 낮다(포화하지 않는다).
    assert cri.mean() < 0.3, f"건강 교량 CRI 평균 {cri.mean():.3f} 이 너무 높음(노이즈 오경보)"
    assert cri[:, -1].max() < 0.6, f"건강 교량 최종 최대 CRI {cri[:, -1].max():.3f} 이 위험역"
    assert fram.warning.level != "위험"


def test_short_record_fast_settlement_not_silent(tmp_path):
    """안전 회귀: **짧은 관측(<1년)이라도 실제 급침하를 침묵(CRI 0)하면 안 된다**.

    계절제거 게이트가 짧은 창의 계절램프 오인은 막지만, 그 때문에 관측<1년 교량의 실제
    급변위까지 0 으로 죽이면 붕괴를 놓친다(거짓음성). 짧은 관측은 선형 secular 로라도
    급변위를 잡고 `observation.sufficient=False`(잠정)로 표시한다.
    """
    from inframon.insar.track_reader import write_insar_contract

    N, M = 40, 8
    dates = np.arange(M, dtype=float) * 24.0                 # span 168일 < 270(짧음)
    t = dates / 365.0
    x = np.linspace(0.0, 100.0, N)
    rng = np.random.default_rng(0)
    los = rng.normal(0.0, 3.0, (N, M)).astype(np.float32)
    los[:10] += (-30.0 * t ** 2)[None, :].astype(np.float32)  # 10점 가속 침하(위험)
    cfg = PipelineConfig(n_points=N, n_dates=M,
                         engines={"cv": "stub", "insar": "stub", "pinn": "real", "fram": "real"})
    with ProjectStore(tmp_path / "p.h5", mode="w") as store:
        insar = write_insar_contract(
            store, xyz=np.column_stack([x, np.zeros(N), np.zeros(N)]),
            member=np.zeros(N, np.int8), coherence=np.full(N, 0.8, np.float32),
            l_from_fixed=x.astype(np.float32), los=los, longitudinal=los,
            dates=dates, date_labels=None)
        from inframon.pinn.engine import run_pinn
        pinn = run_pinn(store, insar, cfg)
        fram = run_fram_real(store, insar, pinn, cfg)
        cri = store.read_array(fram.CRI_ds)
        obs = store.read_json_attr("fram", "observation")
    assert cri.max() > 0.2, "짧은 관측이라도 급침하는 감지돼야(침묵 금지)"
    assert cri[:10, -1].mean() > cri[10:, -1].mean()       # 침하 영역이 평균적으로 더 높다
    assert obs["sufficient"] is False and obs["note"]      # 잠정 표시


def test_fram_vertical_absent_is_identical(tmp_path):
    """vertical_ds 없으면 fram_use_vertical 켜고 끔이 CRI 에 무영향(Morandi·골든 게이트 안전)."""
    _build(tmp_path, {"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    cfg = PipelineConfig(engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    out = tmp_path / "p.h5"
    cri_off, vert, used = _fram_cri(out, cfg, use_vertical=False)
    cri_on, _, _ = _fram_cri(out, cfg, use_vertical=True)
    assert used is False and vert is None                       # 적재된 연직 없음
    assert np.array_equal(cri_off, cri_on)                      # 완전 동일
