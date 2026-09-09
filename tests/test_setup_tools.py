"""외부 도구 자동 준비 — 바깥세상(다운로드·설치기·WSL·CMR)은 전부 가짜로 두고 **판단 흐름**만 본다.

실제로 겪은 것: 타 PC 에서 `doctor` 가 SNAP·snaphu·Earthdata 를 ❌ 로 찍고 "프로그램이 대신
못 하는 것" 이라 했다. 셋 중 사람이 꼭 해야 하는 건 Earthdata 가입뿐이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from inframon import setup_tools as st


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "TOOLS_FILE", tmp_path / "tools.json")
    monkeypatch.setattr(st, "DOWNLOAD_DIR", tmp_path / "dl")
    from inframon.insar import slc_download
    monkeypatch.setattr(slc_download, "TOKEN_FILE", tmp_path / "earthdata_token")
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    monkeypatch.delenv("INFRAMON_EARTHDATA_TOKEN", raising=False)
    monkeypatch.delenv("INFRAMON_SNAP_GPT", raising=False)


# ── SNAP ─────────────────────────────────────────────────────────────────
def test_SNAP_무인설치가_gpt를_만들면_경로를_기록한다(tmp_path, monkeypatch):
    target = tmp_path / "esa-snap"
    monkeypatch.setattr(st.platform, "system", lambda: "Windows")

    def fake_download(url, dest, log=print):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"installer")
        return dest

    def fake_run(args, **kw):
        assert args[1:3] == ["-q", "-dir"], "install4j 무인 옵션"
        assert Path(args[3]) == target
        st.gpt_path_in(target).parent.mkdir(parents=True)
        st.gpt_path_in(target).write_bytes(b"")
        import subprocess
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(st, "download", fake_download)
    monkeypatch.setattr(st, "run", fake_run)
    logs: list[str] = []
    assert st.setup_snap(logs.append, install_dir=target) is True
    assert st.read_tools()["snap_gpt"] == str(st.gpt_path_in(target))
    assert any("✅ SNAP" in ln for ln in logs)


def test_SNAP_설치기가_gpt를_안_만들면_실패로_보고한다(tmp_path, monkeypatch):
    monkeypatch.setattr(st.platform, "system", lambda: "Windows")
    monkeypatch.setattr(st, "download", lambda url, dest, log=print: dest)
    import subprocess
    monkeypatch.setattr(st, "run", lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "boom"))
    logs: list[str] = []
    assert st.setup_snap(logs.append, install_dir=tmp_path / "x") is False
    assert "snap_gpt" not in st.read_tools()
    assert any("설치 실패" in ln for ln in logs)


def test_기록된_gpt_경로를_find_gpt가_먼저_본다(tmp_path, monkeypatch):
    from inframon.insar import snap_backend
    monkeypatch.setattr(snap_backend, "_GPT_CANDIDATES", ())
    fake = tmp_path / "somewhere" / "gpt.exe"
    fake.parent.mkdir()
    fake.write_bytes(b"")
    with pytest.raises(snap_backend.SnapError, match="--tools"):
        snap_backend.find_gpt()
    st.write_tool("snap_gpt", str(fake))
    assert snap_backend.find_gpt() == str(fake)


# ── snaphu ───────────────────────────────────────────────────────────────
def test_WSL이_있으면_root로_apt_설치를_시도한다(monkeypatch):
    from inframon.insar import snap_unwrap
    calls: list[list[str]] = []
    state = {"have": False}
    monkeypatch.setattr(st.platform, "system", lambda: "Windows")
    monkeypatch.setattr(st, "_wsl_distros", lambda: ["Ubuntu"])
    monkeypatch.setattr(snap_unwrap, "find_snaphu",
                        lambda distro=None: snap_unwrap.SnaphuTool("wsl", "/usr/bin/snaphu") if state["have"] else None)

    def fake_run(args, **kw):
        calls.append(args)
        state["have"] = True
        import subprocess
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(st, "run", fake_run)
    assert st.setup_snaphu(lambda s: None) is True
    assert calls and calls[0][:6] == ["wsl", "-d", "Ubuntu", "-u", "root", "--"]
    assert "apt-get install" in " ".join(calls[0])


def test_WSL이_없으면_설치를_걸고_재부팅을_안내한다(monkeypatch):
    from inframon.insar import snap_unwrap
    monkeypatch.setattr(st.platform, "system", lambda: "Windows")
    monkeypatch.setattr(st, "_wsl_distros", lambda: [])
    monkeypatch.setattr(snap_unwrap, "find_snaphu", lambda distro=None: None)
    seen: list[list[str]] = []
    import subprocess
    monkeypatch.setattr(st, "run", lambda args, **kw: (seen.append(args), subprocess.CompletedProcess(args, 0, "", ""))[1])
    logs: list[str] = []
    assert st.setup_snaphu(logs.append) is False
    assert "wsl" in " ".join(seen[0]).lower() and "--install" in " ".join(seen[0])
    assert any("재부팅" in ln for ln in logs)


# ── Earthdata ────────────────────────────────────────────────────────────
def test_토큰은_CMR로_확인한_뒤에만_저장한다(tmp_path, monkeypatch):
    from inframon.insar import slc_download
    monkeypatch.setattr(st, "probe_token", lambda t: (t == "good", "CMR 200" if t == "good" else "CMR 401"))
    logs: list[str] = []
    assert st.setup_earthdata(logs.append, token="bad", open_browser=False) is False
    assert not slc_download.TOKEN_FILE.exists()
    assert st.setup_earthdata(logs.append, token="good", open_browser=False) is True
    assert slc_download.TOKEN_FILE.read_text().strip() == "good"


def test_비대화에서는_토큰을_묻지_않고_방법만_남긴다(monkeypatch):
    logs: list[str] = []
    assert st.setup_earthdata(logs.append, ask=None, open_browser=False) is False
    assert any("earthdata-save" in ln for ln in logs)


def test_대화에서_빈_입력이면_건너뛴다(monkeypatch):
    logs: list[str] = []
    assert st.setup_earthdata(logs.append, ask=lambda p: "", open_browser=False) is False
    assert any("건너뜀" in ln for ln in logs)


def test_이미_있는_토큰은_다시_묻지_않는다(monkeypatch):
    monkeypatch.setenv("EARTHDATA_TOKEN", "x")
    logs: list[str] = []
    assert st.setup_earthdata(logs.append, ask=lambda p: pytest.fail("물으면 안 된다")) is True


# ── 전체 흐름 ────────────────────────────────────────────────────────────
def test_run_setup은_있는_것은_건너뛰고_없는_것만_한다(monkeypatch):
    monkeypatch.setattr(st, "status", lambda: {
        "snap": {"ok": True, "where": "gpt"}, "snaphu": {"ok": False, "where": "없음"},
        "earthdata": {"ok": True, "where": "env"}})
    called: list[str] = []
    monkeypatch.setattr(st, "setup_snap", lambda log: (called.append("snap"), True)[1])
    monkeypatch.setattr(st, "setup_snaphu", lambda log: (called.append("snaphu"), False)[1])
    res = st.run_setup("all", log=lambda s: None, interactive=False)
    assert called == ["snaphu"]
    assert res == {"snap": True, "snaphu": False, "earthdata": True}


def test_run_setup은_골라서_할_수_있다(monkeypatch):
    monkeypatch.setattr(st, "status", lambda: {
        "snap": {"ok": False, "where": "없음"}, "snaphu": {"ok": False, "where": "없음"},
        "earthdata": {"ok": False, "where": "없음"}})
    monkeypatch.setattr(st, "setup_snap", lambda log: True)
    monkeypatch.setattr(st, "setup_snaphu", lambda log: pytest.fail("snaphu 는 안 골랐다"))
    res = st.run_setup("snap", log=lambda s: None, interactive=False)
    assert res == {"snap": True}
