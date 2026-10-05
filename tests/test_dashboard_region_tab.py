"""⑥ 광역 모니터링 탭 — 렌더 스모크(가짜 streamlit). 엔진 결과가 없거나 있을 때 예외 없이 그려져야 한다."""

from __future__ import annotations

import json
import sys
import types

import pytest


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return _st_func(name)


def _st_func(name):
    def f(*a, **k):
        if name in ("columns",):
            n = a[0] if isinstance(a[0], int) else len(a[0])
            return [_Ctx() for _ in range(n)]
        if name in ("selectbox", "radio"):
            opts = list(a[1]) if len(a) > 1 else list(k.get("options", []))
            return opts[0] if opts else None
        if name == "text_input":
            return k.get("value", "")
        if name == "button":
            return False
        if name == "expander":
            return _Ctx()
        return None
    return f


@pytest.fixture()
def tab(monkeypatch):
    st = types.ModuleType("streamlit")
    st.session_state = {}
    for name in ("subheader", "caption", "selectbox", "text_input", "info", "markdown", "columns", "metric",
                 "dataframe", "success", "warning", "error", "radio", "line_chart", "expander", "button"):
        setattr(st, name, _st_func(name))
    monkeypatch.setitem(sys.modules, "streamlit", st)
    pytest.importorskip("pandas")
    sys.modules.pop("inframon.dashboard.region_tab", None)
    import inframon.dashboard.region_tab as tab
    return tab


def test_tab_renders_without_results(tab, tmp_path, monkeypatch):
    monkeypatch.setattr(tab, "_region_configs", lambda repo: [])
    tab.tab_region(str(tmp_path))                     # 결과 없음 → 안내만, 예외 없음


def test_tab_renders_with_results(tab, tmp_path, monkeypatch):
    root = tmp_path / "res"
    root.mkdir()
    (root / "state.json").write_text(json.dumps({"title": "강릉시", "bridges": [
        {"id": "r1", "n": "A교", "c": "강릉시", "lat": 37.7, "lon": 128.9, "cls": "2종",
         "r": {"lv": 2, "rn": 0.6, "r10": 1.2, "dn": 30, "bn": 0.0, "nps": 7, "tob": 7.8}}]}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(tab, "_region_configs", lambda repo: [])
    monkeypatch.setattr(tab.st, "text_input", lambda *a, **k: str(root))
    tab.tab_region(str(tmp_path))
