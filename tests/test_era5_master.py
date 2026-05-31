"""master 선정(E) — baseline(기대 coherence) × ERA5(강수·습도). 네트워크 없이 검증.

combined = rho × dry_score. rho=시·공간 baseline 기대 coherence, dry=(1-norm강수)(1-norm습도).
"""

from __future__ import annotations

import pytest

from inframon.insar import era5_master
from inframon.insar.recipe import load_master_selection, save_master_selection

# 3일: 12일=건조/저습, 24일=비많음/고습, 05일=중간
CANNED = {
    "hourly": {
        "time": ["2023-01-12T00:00", "2023-01-12T12:00",
                 "2023-01-24T00:00", "2023-01-24T12:00",
                 "2023-02-05T00:00", "2023-02-05T12:00"],
        "precipitation": [0.0, 0.0, 1.0, 2.0, 0.5, 0.5],
        "relative_humidity_2m": [40.0, 50.0, 80.0, 90.0, 60.0, 70.0],
    }
}
DATES = ["20230112", "20230124", "20230205"]


def test_daily_aggregate():
    agg = era5_master._daily_aggregate(CANNED["hourly"])
    assert agg["20230124"] == (3.0, 85.0)


def test_master_combines_dry_and_baseline(monkeypatch):
    monkeypatch.setattr(era5_master, "_fetch_era5_archive", lambda *a, **k: CANNED)
    sel = era5_master.select_master(37.33, 127.11, DATES, scene_names=["s12", "s24", "s05"])
    # baseline(시간) 비슷 → 건조도가 결정 → 가장 건조한 20230112
    assert sel.selected_master == "20230112"
    assert sel.master_scene == "s12"
    assert sel.used_baseline is False
    by = {w.date: w for w in sel.scenes}
    assert by["20230112"].dry_score == 1.0       # 가장 건조 → dry=1
    assert by["20230124"].dry_score == 0.0       # 가장 습함 → dry=0
    assert by["20230112"].rho > 0                # 기대 coherence 계산됨
    assert by["20230112"].combined >= by["20230205"].combined


def test_perp_baseline_changes_master(monkeypatch):
    monkeypatch.setattr(era5_master, "_fetch_era5_archive", lambda *a, **k: CANNED)
    # 20230112 에 매우 큰 수직 baseline → 기대 coherence 붕괴 → master 가 바뀌어야
    perp = {"20230112": 500.0, "20230124": 0.0, "20230205": 10.0}
    sel = era5_master.select_master(37.33, 127.11, DATES, perp_baselines=perp, perp_crit_m=300.0)
    assert sel.used_baseline is True
    by = {w.date: w for w in sel.scenes}
    assert by["20230112"].rho == 0.0             # 큰 perp 로 다른 장면과 coherence 0
    assert sel.selected_master == "20230205"     # 건조하진 않아도 baseline 으로 12일 탈락


def test_select_master_requires_dates():
    with pytest.raises(ValueError):
        era5_master.select_master(37.33, 127.11, [])


def test_master_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(era5_master, "_fetch_era5_archive", lambda *a, **k: CANNED)
    sel = era5_master.select_master(37.33, 127.11, DATES)
    path = save_master_selection(tmp_path / "master_selection_era5.json", sel)
    loaded = load_master_selection(path)
    assert loaded.selected_master == "20230112"
    assert len(loaded.scenes) == 3
    import json
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["selected_master"] == "20230112"   # inventory.py 호환 키
