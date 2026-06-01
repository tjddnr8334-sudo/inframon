"""맞춤형 PINN 오케스트레이션 — 자동수집(mock)→기존 /insar 위 PINN+FRAM 관통."""

from __future__ import annotations

import h5py
import numpy as np
import pytest

pytest.importorskip("torch")

from inframon.contracts.io import ProjectStore
from inframon.insar.track_reader import import_track_h5
from inframon.structure import BridgeProfile


def _project_with_insar(tmp_path, n=10, m=5):
    """실 Track 인제스트로 date_labels 포함 /insar 를 만든다."""
    track = tmp_path / "track.h5"
    epochs = np.array([20240107, 20240119, 20240131, 20240212, 20240224][:m], dtype=np.int32)
    with h5py.File(track, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack(
            [np.arange(n, dtype=float), np.zeros(n)]))
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=np.random.default_rng(0).normal(0, 2, (n, m)).astype(np.float32))
        f.create_dataset("coh", data=np.full(n, 0.8, dtype=np.float32))
    proj = tmp_path / "p.h5"
    with ProjectStore(proj, mode="w") as s:
        import_track_h5(s, track)
    return proj, len(epochs)


def test_run_custom_pinn_end_to_end(tmp_path, monkeypatch):
    proj, m = _project_with_insar(tmp_path)

    # 네트워크 수집을 mock — 사장교 프로파일 + 계절 온도
    import inframon.bridge_info as bi
    import inframon.weather as wx
    monkeypatch.setattr(bi, "fetch_bridge_profile", lambda *a, **k: BridgeProfile(
        name="테스트사장교", bridge_type="cable_stayed", material="steel",
        length_m=400.0, source="osm"))
    monkeypatch.setattr(wx, "fetch_temperature_series",
                        lambda lat, lon, dl, **k: 15.0 + 10.0 * np.sin(np.linspace(0, 6, len(dl))))

    from inframon.custom_pinn import run_custom_pinn
    summary = run_custom_pinn(proj, 37.3634, 127.1090, bridge_name="테스트사장교", pinn_epochs=60)

    # 요약
    assert summary["bridge_type"] == "cable_stayed"
    assert summary["span_m"] == 400.0
    assert "Open-Meteo" in summary["collected"]["temperature"]
    assert "자유하중" in summary["collected"]["traffic"]            # 키 없음
    assert 0.0 <= summary["cri_global_max"] <= 1.0

    # /pinn 이 형식별 PDE + 온도 구동으로 돌았는지
    with ProjectStore(proj, mode="r") as s:
        inp = s.read_json_attr("pinn", "inputs")
        assert s.has_meta("fram")
    assert inp["pde_form"] == "cable_stayed"
    assert inp["pde_foundation_k"] is not None and inp["pde_foundation_k"] >= 0
    assert inp["temperature_driven"] is True
    assert inp["bridge_type"] == "cable_stayed"


def test_run_custom_pinn_requires_insar(tmp_path):
    from inframon.custom_pinn import run_custom_pinn
    empty = tmp_path / "empty.h5"
    with ProjectStore(empty, mode="w"):
        pass
    with pytest.raises(ValueError, match="/insar"):
        run_custom_pinn(empty, 37.0, 127.0)


def test_run_custom_pinn_temperature_fail_falls_back(tmp_path, monkeypatch):
    proj, m = _project_with_insar(tmp_path)
    import inframon.bridge_info as bi
    import inframon.weather as wx
    monkeypatch.setattr(bi, "fetch_bridge_profile",
                        lambda *a, **k: BridgeProfile(source="default"))
    monkeypatch.setattr(wx, "fetch_temperature_series",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net")))

    from inframon.custom_pinn import run_custom_pinn
    summary = run_custom_pinn(proj, 37.0, 127.0, pinn_epochs=40)
    assert "폴백" in summary["collected"]["temperature"]            # 온도 실패 → 계절가정
    assert 0.0 <= summary["cri_global_max"] <= 1.0                  # 그래도 끝까지 관통
