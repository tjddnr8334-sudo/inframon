"""SNAP 레인 위상 언래핑(snaphu) — 준비·경로·명령 파싱.

실제 언래핑은 SLC·gpt·snaphu 가 있어야 하므로 여기서는 **그 앞뒤의 판단**을 고정한다:
도구 탐지, Windows→WSL 경로 변환, snaphu.conf 가 시키는 명령 읽기, 실패 시 안내.
"""

from __future__ import annotations

import pytest

from inframon.insar import snap_unwrap as su


def test_wsl_path_translation():
    assert su.to_wsl_path(r"E:\프로그램\data\x") == "/mnt/e/프로그램/data/x"
    assert su.to_wsl_path("C:/a/b") == "/mnt/c/a/b"


def test_parse_snaphu_command_reads_the_conf(tmp_path):
    """파일 이름·너비를 추측하지 않고 conf 가 적어준 명령을 그대로 쓴다."""
    conf = tmp_path / "snaphu.conf"
    conf.write_text(
        "# SNAPHU CONFIG FILE\n"
        "#\n"
        "#    snaphu -f snaphu.conf Phase_ifg_VV_12Aug2025_24Aug2025.snaphu.img 1024\n"
        "#\n"
        "STATCOSTMODE DEFO\n", encoding="utf-8")
    cmd = su.parse_snaphu_command(conf)
    assert cmd[0] == "snaphu" and cmd[-1] == "1024"
    assert "Phase_ifg_VV_12Aug2025_24Aug2025.snaphu.img" in cmd


def test_parse_snaphu_command_explains_when_absent(tmp_path):
    conf = tmp_path / "snaphu.conf"
    conf.write_text("STATCOSTMODE DEFO\n", encoding="utf-8")
    with pytest.raises(su.UnwrapError, match="실행 명령을 찾지 못"):
        su.parse_snaphu_command(conf)


def test_missing_conf_points_at_export_stage(tmp_path):
    with pytest.raises(su.UnwrapError, match="SnaphuExport"):
        su.run_snaphu(tmp_path)


def test_install_hint_is_actionable():
    """도구가 없을 때는 '없다' 가 아니라 '이렇게 설치하라' 를 준다(이식성 원칙)."""
    hint = su.install_hint()
    assert "apt-get install" in hint and "conda install" in hint


def test_no_snaphu_blocks_instead_of_returning_wrapped(tmp_path, monkeypatch):
    """snaphu 가 없으면 래핑 산출을 조용히 내놓지 않고 멈춘다."""
    conf = tmp_path / "snaphu.conf"
    conf.write_text("#    snaphu -f snaphu.conf x.img 10\n", encoding="utf-8")
    monkeypatch.setattr(su, "find_snaphu", lambda *a, **k: None)
    with pytest.raises(su.UnwrapError, match="snaphu 를 찾지 못"):
        su.run_snaphu(tmp_path)


def test_wsl_invocation_translates_cwd(tmp_path, monkeypatch):
    """WSL 로 건너갈 때 작업 폴더가 /mnt 경로로 바뀌어야 snaphu 가 파일을 찾는다."""
    conf = tmp_path / "snaphu.conf"
    conf.write_text("#    snaphu -f snaphu.conf x.img 10\n", encoding="utf-8")
    (tmp_path / "UnwPhase_x.hdr").write_text("hdr", encoding="utf-8")
    (tmp_path / "UnwPhase_x.img").write_bytes(b"data")
    seen = {}

    class _R:
        returncode = 0
        stdout = ""

    def _fake_run(args, **kw):
        seen["args"] = args
        return _R()

    monkeypatch.setattr(su.subprocess, "run", _fake_run)
    hdr = su.run_snaphu(tmp_path, tool=su.SnaphuTool(kind="wsl", path="/usr/bin/snaphu"))
    inner = seen["args"][-1]
    assert inner.startswith("cd '/mnt/") and "snaphu -f snaphu.conf" in inner
    assert hdr.name == "UnwPhase_x.hdr"


def test_is_available_reports_state():
    ok, msg = su.is_available()
    assert isinstance(ok, bool) and isinstance(msg, str) and msg


def test_hdr_without_img_is_not_success(tmp_path, monkeypatch):
    """SnaphuExport 가 .hdr 를 미리 깔아둔다 — .img 가 없으면 실패다(실행에서 겪은 함정)."""
    (tmp_path / "snaphu.conf").write_text("#    snaphu -f snaphu.conf x.img 10",
                                          encoding="utf-8")
    (tmp_path / "UnwPhase_x.hdr").write_text("hdr", encoding="utf-8")   # .img 없음

    class _R:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(su.subprocess, "run", lambda *a, **k: _R())
    with pytest.raises(su.UnwrapError, match="UnwPhase"):
        su.run_snaphu(tmp_path, tool=su.SnaphuTool(kind="native", path="snaphu"))


# ── snaphu 입력 준비: 엔디안·비유한값 (실행에서 겪은 두 함정) ──
def _envi(tmp_path, name, values, *, big=True, samples=4):
    import numpy as np
    img = tmp_path / name
    np.asarray(values, dtype=">f4" if big else "<f4").tofile(img)
    img.with_suffix(".hdr").write_text(
        f"ENVI\nsamples = {samples}\nlines = 1\nbands = 1\ndata type = 4\n"
        f"byte order = {1 if big else 0}\n", encoding="utf-8")
    return img


def _conf(tmp_path, phase_name, corr_name):
    (tmp_path / "snaphu.conf").write_text(
        f"#    snaphu -f snaphu.conf {phase_name} 4\nSTATCOSTMODE DEFO\n"
        f"CORRFILE\t\t{corr_name}\n", encoding="utf-8")


def test_prepare_converts_big_endian_phase(tmp_path):
    """위상이 빅엔디안이면 snaphu 는 **죽지 않고 쓰레기 값으로** 언래핑한다 — 반드시 바꾼다."""
    import numpy as np

    _envi(tmp_path, "Phase.img", [0.1, 0.2, 0.3, 0.4])
    _envi(tmp_path, "coh.img", [0.9, 0.8, 0.7, 0.6])
    _conf(tmp_path, "Phase.img", "coh.img")
    fixed = su.prepare_snaphu_inputs(tmp_path)
    assert any("Phase.img" in f for f in fixed) and any("coh.img" in f for f in fixed)
    got = np.fromfile(tmp_path / "Phase.img", dtype="<f4")
    assert np.allclose(got, [0.1, 0.2, 0.3, 0.4], atol=1e-6)
    assert "byte order = 0" in (tmp_path / "Phase.hdr").read_text(encoding="utf-8")


def test_prepare_replaces_nonfinite(tmp_path):
    """nodata 의 NaN/Inf 는 snaphu 가 거부한다 — 위상 0, coherence 0 으로 낮춘다."""
    import numpy as np

    _envi(tmp_path, "Phase.img", [np.nan, 1.0, np.inf, -1.0], big=False)
    _envi(tmp_path, "coh.img", [np.nan, 2.0, 0.5, -3.0], big=False)
    _conf(tmp_path, "Phase.img", "coh.img")
    su.prepare_snaphu_inputs(tmp_path)
    ph = np.fromfile(tmp_path / "Phase.img", dtype="<f4")
    coh = np.fromfile(tmp_path / "coh.img", dtype="<f4")
    assert np.isfinite(ph).all() and ph[0] == 0.0 and ph[2] == 0.0
    assert np.isfinite(coh).all() and coh.min() >= 0.0 and coh.max() <= 1.0


def test_prepare_is_idempotent_and_quiet_when_clean(tmp_path):
    """이미 리틀엔디안·유한하면 아무것도 건드리지 않는다(재실행 안전)."""
    _envi(tmp_path, "Phase.img", [0.1, 0.2, 0.3, 0.4], big=False)
    _envi(tmp_path, "coh.img", [0.9, 0.8, 0.7, 0.6], big=False)
    _conf(tmp_path, "Phase.img", "coh.img")
    assert su.prepare_snaphu_inputs(tmp_path) == []


def test_prepare_finds_phase_from_command_not_just_conf(tmp_path):
    """위상 파일은 conf 의 INFILE 이 아니라 명령줄 위치인자로 온다 — 그걸 놓치면 안 된다."""
    _envi(tmp_path, "Phase.img", [0.1, 0.2, 0.3, 0.4])
    _envi(tmp_path, "coh.img", [0.9, 0.8, 0.7, 0.6], big=False)
    (tmp_path / "snaphu.conf").write_text(
        "#    snaphu -f snaphu.conf Phase.img 4\nCORRFILE\t\tcoh.img\n", encoding="utf-8")
    fixed = su.prepare_snaphu_inputs(tmp_path)
    assert any("Phase.img" in f for f in fixed)


# ── 산출 밴드 자기기술: 순서를 외워 쓰다 위상·coherence 를 뒤바꾸지 않게 ──
def _tif(tmp_path, bands):
    import numpy as np
    import rasterio

    arr = np.stack([np.asarray(b, dtype="float32") for b in bands])
    p = tmp_path / "out.tif"
    with rasterio.open(p, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2],
                       count=arr.shape[0], dtype="float32") as ds:
        ds.write(arr)
    return p


def test_label_bands_identifies_roles_in_unwrap_order(tmp_path):
    """언래핑 레인 TC 산출은 [coh, 위상, 입사각] 순서다 — 역할을 값으로 알아낸다."""
    import numpy as np

    rng = np.random.default_rng(0)
    coh = rng.uniform(0.0, 1.0, (8, 8))
    phase = rng.uniform(-30.0, 40.0, (8, 8))
    inc = np.full((8, 8), 38.7) + rng.normal(0, 0.1, (8, 8))
    p = _tif(tmp_path, [coh, phase, inc])
    assert su.label_bands(p) == ["coherence", "phase", "incidence"]
    assert su.band_index(p, "phase") == 2 and su.band_index(p, "coherence") == 1


def test_label_bands_identifies_roles_in_wrapped_order(tmp_path):
    """기존 래핑 레인은 [위상, coh, 입사각] — 같은 함수가 그대로 알아본다."""
    import numpy as np

    rng = np.random.default_rng(1)
    phase = rng.uniform(-3.14, 3.14, (8, 8)) * 2      # coherence 로 오인되지 않게
    coh = rng.uniform(0.0, 1.0, (8, 8))
    inc = np.full((8, 8), 38.7)
    p = _tif(tmp_path, [phase, coh, inc])
    assert su.label_bands(p) == ["phase", "coherence", "incidence"]


def test_label_bands_refuses_to_guess(tmp_path):
    """구분이 안 되면 추측하지 않고 알린다."""
    import numpy as np

    a = np.full((4, 4), 0.5)
    with pytest.raises(su.UnwrapError, match="확정하지 못"):
        su.label_bands(_tif(tmp_path, [a, a]))
