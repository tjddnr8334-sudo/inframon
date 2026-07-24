"""대시보드 data_root 폴백 — 없는 드라이브(F:\\ 등)를 조용히 쓰다 크래시하지 않게.

`app.py` 는 streamlit 을 import 하므로, streamlit 을 최소 목으로 끼워 함수만 불러온다.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


@pytest.fixture()
def app(monkeypatch):
    """streamlit 을 목으로 세우고 dashboard.app 을 로드한다(세션 상태만 필요)."""
    st = types.ModuleType("streamlit")
    st.session_state = {}
    # app 모듈이 import 시점에 참조하는 최소 표면만 채운다.
    for name in ("cache_data", "cache_resource"):
        setattr(st, name, lambda *a, **k: (lambda f: f))
    monkeypatch.setitem(sys.modules, "streamlit", st)
    sys.modules.pop("inframon.dashboard.app", None)
    import inframon.dashboard.app as app
    app.st.session_state = {}
    return app


def test_missing_drive_falls_back_to_data(app, monkeypatch, tmp_path):
    """config 에 없는 드라이브가 남아 있어도 크래시 대신 작업폴더 data/ 로 폴백."""
    monkeypatch.setattr(app, "_config_load", lambda: {"data_root": "F:\\inframon"})
    monkeypatch.delenv("INFRAMON_DATA_ROOT", raising=False)
    # F:\ 가 실제로 없어야 이 테스트가 의미 있다(CI·개발기 모두 없음).
    assert not Path("F:\\inframon").exists()
    assert app.data_root() == "data"


def test_usable_config_dir_is_honoured(app, monkeypatch, tmp_path):
    """실재하고 쓸 수 있는 경로면 그대로 쓴다(폴백은 '못 쓸 때'만)."""
    target = tmp_path / "myroot"
    monkeypatch.setattr(app, "_config_load", lambda: {"data_root": str(target)})
    monkeypatch.delenv("INFRAMON_DATA_ROOT", raising=False)
    assert app.data_root() == str(target)
    assert target.exists()                       # 접근 시 만들어진다


def test_session_beats_config(app, monkeypatch, tmp_path):
    sess = tmp_path / "sess"
    app.st.session_state = {"data_root": str(sess)}
    monkeypatch.setattr(app, "_config_load", lambda: {"data_root": str(tmp_path / "cfg")})
    monkeypatch.delenv("INFRAMON_DATA_ROOT", raising=False)
    assert app.data_root() == str(sess)


def test_recipe_dir_falls_back_when_session_path_unusable(app, monkeypatch, tmp_path):
    """세션에 남은 recipe_dir 이 없는 드라이브여도 데이터 루트 하위로 폴백."""
    monkeypatch.setattr(app, "data_root", lambda: str(tmp_path))
    app.st.session_state = {"recipe_dir": "F:\\inframon\\insar_recipe"}
    assert app._recipe_dir() == str(tmp_path / "insar_recipe")


def test_usable_dir_predicate(app, tmp_path):
    assert app._usable_dir(str(tmp_path / "new")) is True
    assert app._usable_dir("F:\\definitely\\missing\\drive") is False
