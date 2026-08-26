"""⓪ 시작 탭 — 새 컴퓨터 온보딩 화면의 환경 점검.

핵심 계약: **어떤 하위 시스템이 죽어도 이 화면은 살아남아야 한다.** 새 PC 에서 처음
켰을 때 진단 화면 자체가 예외로 죽으면 사용자는 손쓸 방법이 없다.
"""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture()
def app(monkeypatch):
    st = types.ModuleType("streamlit")
    st.session_state = {}
    for name in ("cache_data", "cache_resource"):
        setattr(st, name, lambda *a, **k: (lambda f: f))
    monkeypatch.setitem(sys.modules, "streamlit", st)
    pytest.importorskip("pandas")
    sys.modules.pop("inframon.dashboard.app", None)
    import inframon.dashboard.app as app
    app.st.session_state = {}
    return app


def test_env_checks_rows_are_wellformed(app):
    rows = app._env_checks()
    assert rows, "점검 항목이 비면 화면이 빈다"
    for r in rows:
        assert set(r) == {"name", "ok", "why", "fix"}
        assert isinstance(r["ok"], bool) and r["name"] and r["why"] is not None


def test_env_checks_cover_all_three_lanes(app):
    """레인 3종이 모두 보여야 '무엇을 설치하면 시작되는지'가 드러난다."""
    names = " ".join(r["name"] for r in app._env_checks())
    assert "레인 A" in names and "레인 B" in names and "레인 C" in names


def test_env_checks_offer_fix_for_blocked_items(app):
    for r in app._env_checks():
        if not r["ok"]:
            assert r["fix"], f"막힌 항목에 해결 방법이 없다: {r['name']}"


def test_env_checks_survive_toolchain_failure(app, monkeypatch):
    """WSL 조회가 터져도(권한·환경) 진단 화면은 계속 그려져야 한다."""
    def boom(*a, **k):
        raise OSError("wsl not available")
    monkeypatch.setattr("inframon.insar.toolchain.wsl_status", boom, raising=False)
    names = [r["name"] for r in app._env_checks()]
    assert any("레인 B" in n for n in names)          # 실패해도 항목은 남는다


def test_env_checks_survive_slc_store_failure(app, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("config broken")
    monkeypatch.setattr("inframon.insar.slc_store.get_slc_dir", boom, raising=False)
    assert app._env_checks()                          # 예외 없이 반환


def test_start_tab_is_first_section(app):
    """처음 켠 사용자가 만나는 화면이어야 하므로 맨 앞."""
    import inspect
    src = inspect.getsource(app.main)
    assert '"⓪ 시작"' in src
    i0, i1 = src.index('"⓪ 시작"'), src.index('"① InSAR"')
    assert i0 < i1
    assert callable(app.tab_start)
