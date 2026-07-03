"""PINN 가상센싱 — 상부거더 전체 변위장 도출(관측점 없는 위치까지). torch 없으면 skip."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("torch")

from inframon.api import transform
from inframon.config import PipelineConfig
from inframon.contracts.array_schema import validate_group
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import PINNOutput
from inframon.cv.engine import run_cv
from inframon.insar.engine import run_insar
from inframon.pinn.real_engine import run_pinn_real


def _run(tmp_path, *, n_points=25, n_dates=10, epochs=60, n_vsens=None, vertical=False):
    cfg = PipelineConfig(n_points=n_points, n_dates=n_dates)
    cfg.pinn_epochs = epochs
    if n_vsens is not None:
        cfg.pinn_virtual_sensors = n_vsens
    store = ProjectStore(tmp_path / "p.h5", mode="w").__enter__()
    cv = run_cv(store, cfg)
    insar = run_insar(store, cv, cfg)
    if vertical:
        rng = np.random.default_rng(0)
        vert = (rng.normal(0, 1.0, size=(n_points, n_dates))
                - np.linspace(0, 3, n_dates)).astype(np.float32)
        store.write_array("/insar/vertical", vert)
        insar.vertical_ds = "/insar/vertical"
    out = run_pinn_real(store, insar, cfg)
    return cfg, store, insar, out


def test_virtual_sensing_fields_present(tmp_path):
    """가상센서 격자[V,M] 필드가 채워지고 기본 V=200 이다."""
    _, store, _, out = _run(tmp_path)
    try:
        assert out.n_virtual == 200
        for ds in (out.vsens_total_ds, out.vsens_deflection_ds, out.vsens_thermal_ds,
                   out.vsens_settle_ds, out.vsens_anomaly_ds):
            a = store.read_array(ds)
            assert a.shape == (200, 10) and np.isfinite(a).all()
        xv = store.read_array(out.vsens_x_ds)
        xl = store.read_array(out.vsens_l_from_fixed_ds)
        assert xv.shape == (200,) and xl.shape == (200,)
        assert xv[0] == pytest.approx(0.0) and xv[-1] == pytest.approx(1.0)
        assert np.all(np.diff(xv) > 0) and np.all(np.diff(xl) >= 0)   # 단조 축
    finally:
        store.__exit__(None, None, None)


def test_virtual_sensor_count_configurable(tmp_path):
    _, store, _, out = _run(tmp_path, n_vsens=64)
    try:
        assert out.n_virtual == 64
        assert store.read_array(out.vsens_total_ds).shape == (64, 10)
    finally:
        store.__exit__(None, None, None)


def test_total_is_vector_magnitude_of_components(tmp_path):
    """전체 변위량 = √(종축²+연직²) ≥ 각 방향 성분 크기, 항상 비음수."""
    _, store, _, out = _run(tmp_path)
    try:
        total = store.read_array(out.vsens_total_ds)
        thermal = store.read_array(out.vsens_thermal_ds)
        anomaly = store.read_array(out.vsens_anomaly_ds)
        defl = store.read_array(out.vsens_deflection_ds)
        settle = store.read_array(out.vsens_settle_ds)
        u_long = thermal + anomaly
        u_vert = defl + settle
        assert (total >= -1e-6).all()
        assert np.allclose(total, np.hypot(u_long, u_vert), atol=1e-4)
        assert (total + 1e-6 >= np.abs(u_long)).all()
        assert (total + 1e-6 >= np.abs(u_vert)).all()
    finally:
        store.__exit__(None, None, None)


def test_virtual_sensing_denser_than_observed(tmp_path):
    """가상센서(200)가 관측점(25)보다 촘촘 → 관측 없는 위치도 변위 도출."""
    _, store, insar, out = _run(tmp_path, n_points=25)
    try:
        assert out.n_virtual > insar.n_points
    finally:
        store.__exit__(None, None, None)


def test_virtual_sensing_contract_validates(tmp_path):
    """계약(배열 레벨) — V 심볼이 결속되고 vsens_* 형상이 검증을 통과한다."""
    _, store, _, out = _run(tmp_path, n_vsens=48)
    try:
        symbols = validate_group(store, "pinn", out)
        assert symbols["V"] == 48
        assert symbols["M"] == 10
    finally:
        store.__exit__(None, None, None)


def test_virtual_sensing_attr_summary(tmp_path):
    _, store, _, _ = _run(tmp_path, vertical=True)
    try:
        vs = store.read_json_attr("pinn", "virtual_sensing")
    finally:
        store.__exit__(None, None, None)
    assert vs["n_virtual"] == 200
    assert vs["vertical_separated"] is True
    assert vs["peak_total_mm"] >= 0.0
    assert 0.0 <= vs["peak_l_from_fixed_m"]
    assert 0 <= vs["peak_date_index"] < 10


def test_api_girder_displacement(tmp_path):
    """API DTO — 종단 프로파일(길이 V) + 첨두 + 중앙경간 시계열(길이 M)."""
    _, store, _, out = _run(tmp_path, n_vsens=80)
    try:
        dto = transform.girder_displacement(store, date="latest")
        assert dto["n_virtual"] == 80
        assert len(dto["profile"]) == 80
        assert len(dto["midspan_total_mm_series"]) == 10
        assert dto["date_index"] == 9
        assert "l_from_fixed_m" in dto["profile"][0] and "total_mm" in dto["profile"][0]
        assert dto["peak"]["total_mm"] >= 0.0
        assert dto["span_m"] > 0.0
    finally:
        store.__exit__(None, None, None)


def test_api_girder_displacement_absent_raises(tmp_path):
    """가상센싱이 없으면(빈 PINN 메타) ResultNotFound."""
    store = ProjectStore(tmp_path / "empty.h5", mode="w").__enter__()
    try:
        cfg = PipelineConfig(n_points=10, n_dates=5)
        cv = run_cv(store, cfg)
        run_insar(store, cv, cfg)
        # PINN 메타를 vsens 없이(구버전 모사) 쓰면 안 되므로 — pinn 메타 자체가 없는 경우
        with pytest.raises(transform.ResultNotFound):
            transform.girder_displacement(store)
    finally:
        store.__exit__(None, None, None)


def test_deck_2d_field_present(tmp_path):
    """상판 2D 면(G=n_long×n_trans) 변위장이 채워지고 격자·좌표가 유효하다."""
    _, store, _, out = _run(tmp_path)
    try:
        vs = store.read_json_attr("pinn", "virtual_sensing")
        d = vs["deck"]
        assert d is not None
        G = d["n_deck"]
        assert G == d["n_long"] * d["n_trans"] == out.n_deck
        total = store.read_array(out.deck_total_ds)
        defl = store.read_array(out.deck_deflection_ds)
        xy = store.read_array(out.deck_xy_ds)
        assert total.shape == (G, 10) and np.isfinite(total).all()
        assert defl.shape == (G, 10) and np.isfinite(defl).all()
        assert xy.shape == (G, 2) and np.isfinite(xy).all()
        assert store.read_array(out.deck_s_ds).shape == (G,)
        assert store.read_array(out.deck_w_ds).shape == (G,)
        assert d["footprint_m"][0] > 0.0        # 종축 길이 > 0
    finally:
        store.__exit__(None, None, None)


def test_deck_grid_configurable(tmp_path):
    _, store, _, out = _run(tmp_path, n_vsens=32)  # deck 은 기본 60×9
    try:
        cfg = PipelineConfig(n_points=25, n_dates=10)
        cfg.pinn_epochs = 60
        cfg.pinn_deck_long = 20
        cfg.pinn_deck_trans = 5
        s2 = ProjectStore(tmp_path / "p2.h5", mode="w").__enter__()
        try:
            cv = run_cv(s2, cfg)
            ins = run_insar(s2, cv, cfg)
            o2 = run_pinn_real(s2, ins, cfg)
            assert o2.n_deck == 20 * 5
            assert s2.read_array(o2.deck_total_ds).shape == (100, 10)
        finally:
            s2.__exit__(None, None, None)
    finally:
        store.__exit__(None, None, None)


def test_deck_contract_validates(tmp_path):
    """계약 — G 심볼 결속 + deck_* 형상(그리고 V·M 공유) 검증 통과."""
    _, store, _, out = _run(tmp_path)
    try:
        symbols = validate_group(store, "pinn", out)
        assert symbols["G"] == out.n_deck
        assert symbols["M"] == 10
    finally:
        store.__exit__(None, None, None)


def test_deck_honors_observed_near_points(tmp_path):
    """상판 격자점이 관측점에 가까우면 그 점의 PINN 총변위에 근접(IDW)."""
    _, store, insar, out = _run(tmp_path, n_points=25)
    try:
        xy_deck = store.read_array(out.deck_xy_ds)          # [G,2]
        total_deck = store.read_array(out.deck_total_ds)    # [G,M]
        xyz = store.read_array(insar.xyz_ds)[:, :2]         # [N,2]
        thermal = store.read_array(out.comp_thermal_ds)
        anomaly = store.read_array(out.comp_anomaly_ds)
        defl = store.read_array(out.deflection_ds)
        settle = store.read_array(out.comp_settle_ds)
        total_pt = np.hypot(thermal + anomaly, defl + settle)  # [N,M] 관측점 PINN 총변위
        # 각 관측점에 가장 가까운 격자점의 마지막 시점 값이 관측점 값과 상관 있게 근접
        k = 9
        diffs = []
        for i in range(len(xyz)):
            g = int(np.argmin(((xy_deck - xyz[i]) ** 2).sum(axis=1)))
            diffs.append(abs(total_deck[g, k] - total_pt[i, k]))
        # 근접 격자 오차의 중앙값이 전체 변위 스케일 대비 작다
        scale = float(np.abs(total_pt[:, k]).mean()) + 1e-6
        assert np.median(diffs) < 1.5 * scale
    finally:
        store.__exit__(None, None, None)


def test_api_deck_displacement(tmp_path):
    """API DTO — 상판 격자 노드(lat/lon+변위) + 격자형상 + 첨두."""
    _, store, _, out = _run(tmp_path)
    try:
        dto = transform.deck_displacement(store, date="latest")
        assert dto["n_deck"] == out.n_deck
        assert len(dto["nodes"]) == out.n_deck
        assert dto["n_long"] and dto["n_trans"]
        n0 = dto["nodes"][0]
        assert "lat" in n0 and "lon" in n0 and "total_mm" in n0
        assert "total_mm" in dto["peak"]
    finally:
        store.__exit__(None, None, None)


def test_api_deck_displacement_absent_raises(tmp_path):
    store = ProjectStore(tmp_path / "empty.h5", mode="w").__enter__()
    try:
        cfg = PipelineConfig(n_points=10, n_dates=5)
        cv = run_cv(store, cfg)
        run_insar(store, cv, cfg)
        with pytest.raises(transform.ResultNotFound):
            transform.deck_displacement(store)
    finally:
        store.__exit__(None, None, None)


def test_backward_compat_pinn_without_vsens_validates(tmp_path):
    """vsens 필드가 None 인 구버전 PINNOutput 도 계약 검증을 통과한다(Optional)."""
    _, store, _, out = _run(tmp_path)
    try:
        # vsens 필드를 모두 None 으로 지운 메타로도 검증 통과(선언만 검사, None 은 생략)
        stripped = out.model_copy(update={
            "n_virtual": None, "vsens_x_ds": None, "vsens_l_from_fixed_ds": None,
            "vsens_total_ds": None, "vsens_deflection_ds": None, "vsens_thermal_ds": None,
            "vsens_settle_ds": None, "vsens_anomaly_ds": None,
            "n_deck": None, "deck_xy_ds": None, "deck_s_ds": None, "deck_w_ds": None,
            "deck_total_ds": None, "deck_deflection_ds": None,
        })
        assert isinstance(stripped, PINNOutput)
        symbols = validate_group(store, "pinn", stripped)
        assert "V" not in symbols and "G" not in symbols   # 결속 안 됨(가상센싱 미기록)
    finally:
        store.__exit__(None, None, None)
