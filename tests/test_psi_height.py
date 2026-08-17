"""PSI 잔차높이 Δh 역산 — 합성 주입→복원 왕복검증(무편향)·속도 분리·절대고도."""

from __future__ import annotations

from datetime import datetime as dt

import h5py
import numpy as np

from inframon.insar.psi_height import estimate_residual_height

# 실 CHYG 스택과 유사한 B⊥·에폭(29장, 스프레드 ~54m)
_DATES = ["20180102", "20180713", "20180911", "20181122", "20190202", "20190403",
          "20190614", "20190906", "20191117", "20200116", "20200328", "20200608",
          "20201018", "20201217", "20210227", "20210919", "20220210", "20220423",
          "20220914", "20221207", "20230205", "20230418", "20230816", "20231015"]
_BPERP = [159, 76, 50, 106, 59, -2, 75, 12, -30, 40, -55, 88, 20, 7, -40, 33,
          -12, 60, -88, 45, 15, -70, 30, -20]


def _synth_h5(tmp_path, dh_true, v_true, noise_mm=7.3, seed=0):
    N = len(dh_true)
    rng = np.random.default_rng(seed)
    days = np.array([(dt.strptime(x, "%Y%m%d") - dt.strptime(_DATES[0], "%Y%m%d")).days
                     for x in _DATES], float)
    bp = np.array(_BPERP, float)
    bpair = bp - bp[int(np.argmin(np.abs(bp)))]
    R, sinth = 880e3, np.sin(np.radians(39.0))
    los = np.zeros((N, len(_DATES)))
    for k in range(len(_DATES)):
        topo = bpair[k] / (R * sinth) * 1000.0 * dh_true       # mm
        los[:, k] = topo + v_true * days[k] / 365.0 + rng.normal(0, noise_mm, N)
    p = tmp_path / "syn.h5"
    with h5py.File(p, "w") as f:
        f.create_dataset("los_mm", data=los.astype("float32"))
        f.create_dataset("epochs", data=np.array([int(d) for d in _DATES]))
        f.create_dataset("incidenceAngle", data=np.full(N, 39.0, "float32"))
        f.create_dataset("pixel_lonlat",
                         data=np.column_stack([np.full(N, 126.8), np.full(N, 36.45)]))
    return p


def test_residual_height_roundtrip_unbiased(tmp_path):
    """알려진 Δh(±40m)·v(±10) 주입 → 무편향 복원(기울기~1), 오차=이론 정밀도."""
    rng = np.random.default_rng(1)
    N = 400
    dh_true = rng.uniform(-40, 40, N)
    v_true = rng.uniform(-10, 10, N)
    h5 = _synth_h5(tmp_path, dh_true, v_true)
    r = estimate_residual_height(h5, {d: b for d, b in zip(_DATES, _BPERP)})
    dh, v = r["dh_m"], r["velocity_mm_yr"]
    m = np.isfinite(dh)
    slope = np.polyfit(dh_true[m], dh[m], 1)[0]
    assert 0.9 < slope < 1.1                                    # Δh 무편향
    assert np.corrcoef(dh_true[m], dh[m])[0, 1] > 0.7
    # v 는 Δh 와 분리돼 정확히 복원
    assert 0.9 < np.polyfit(v_true[m], v[m], 1)[0] < 1.1
    assert np.corrcoef(v_true[m], v[m])[0, 1] > 0.95
    # 오차 ≈ 예측 σ_Δh (이론 자기일관)
    err = np.std(dh[m] - dh_true[m])
    assert abs(err - np.nanmedian(r["sigma_dh_m"])) < 5.0


def test_zero_height_returns_near_zero(tmp_path):
    """Δh=0(평지 산란체) 주입 → 복원 Δh 는 0 주변(구조물 없음=null)."""
    N = 300
    h5 = _synth_h5(tmp_path, np.zeros(N), np.zeros(N))
    r = estimate_residual_height(h5, {d: b for d, b in zip(_DATES, _BPERP)})
    assert abs(np.nanmedian(r["dh_m"])) < 5.0                   # 중앙 ~0
    assert r["bperp_spread_m"] > 20                             # B⊥ 스프레드 존재


def test_gltf_psi_z_source(tmp_path):
    """z_source='psi': 산란체 절대고도(DEM+Δh)를 glb Z로."""
    from inframon.insar.gltf_export import export_insar_gltf
    N = 12
    p = tmp_path / "t.h5"
    with h5py.File(p, "w") as f:
        g = f.create_group("insar")
        g.create_dataset("pixel_lonlat", data=np.column_stack([
            126.80 + np.random.default_rng(0).random(N) * 0.01,
            36.45 + np.random.default_rng(1).random(N) * 0.01]))
        g.create_dataset("epochs", data=np.array([20200101 + i for i in range(6)]))
        g.create_dataset("los_mm", data=np.random.default_rng(2).normal(0, 3, (N, 6)).astype("float32"))
        g.create_dataset("los_velocity_mm_yr", data=np.zeros(N, "float32"))
        g.create_dataset("coh", data=np.full(N, 0.8, "float32"))
    elev = np.full(N, 105.0)                                    # 절대고도 105m
    r = export_insar_gltf(p, tmp_path / "twin.glb", value="velocity",
                          z_source="psi", psi_elev=elev)
    assert r["georef"]["z_source"] == "psi_residual_height"
    import json
    import struct
    b = (tmp_path / "twin.glb").read_bytes()
    jlen = struct.unpack("<II", b[12:20])[0]
    gltf = json.loads(b[20:20 + jlen])
    assert abs(gltf["accessors"][0]["min"][1] - 105.0) < 1e-3   # Y=고도 105m
