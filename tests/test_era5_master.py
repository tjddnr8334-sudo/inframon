"""master 선정(E) — baseline(기대 coherence) × ERA5(강수·습도·온도). 네트워크 없이 검증.

combined = rho × dry_score. dry=(1-norm강수)(1-norm습도)(1-norm온도). 과도 장면은 임계로 소거.
"""

from __future__ import annotations

import pytest

from inframon.insar import era5_master
from inframon.insar.recipe import load_master_selection, save_master_selection

# 3일: 12일=건조/저습/저온, 24일=비많음/고습/고온, 05일=중간
CANNED = {
    "hourly": {
        "time": ["2023-01-12T00:00", "2023-01-12T12:00",
                 "2023-01-24T00:00", "2023-01-24T12:00",
                 "2023-02-05T00:00", "2023-02-05T12:00"],
        "precipitation": [0.0, 0.0, 1.0, 2.0, 0.5, 0.5],
        "relative_humidity_2m": [40.0, 50.0, 80.0, 90.0, 60.0, 70.0],
        "temperature_2m": [0.0, 2.0, 12.0, 14.0, 6.0, 8.0],  # 일평균 1 / 13 / 7 °C
    }
}
DATES = ["20230112", "20230124", "20230205"]


def test_daily_aggregate():
    agg = era5_master._daily_aggregate(CANNED["hourly"])
    assert agg["20230124"] == (3.0, 85.0, 13.0)   # (총강수, 평균습도, 평균기온)


def test_master_combines_dry_and_baseline(monkeypatch):
    monkeypatch.setattr(era5_master, "_fetch_era5_archive", lambda *a, **k: CANNED)
    sel = era5_master.select_master(37.33, 127.11, DATES, scene_names=["s12", "s24", "s05"])
    # baseline(시간) 비슷 → 건조도가 결정 → 가장 건조한 20230112
    assert sel.selected_master == "20230112"
    assert sel.master_scene == "s12"
    assert sel.used_baseline is False
    by = {w.date: w for w in sel.scenes}
    assert by["20230112"].dry_score == 1.0       # 가장 건조·저습·저온 → dry=1
    assert by["20230124"].dry_score == 0.0       # 가장 습함·고온 → dry=0
    assert by["20230112"].temp_c == 1.0          # 온도 집계·기록됨
    assert by["20230124"].temp_c == 13.0
    assert by["20230112"].rho > 0                # 기대 coherence 계산됨
    assert by["20230112"].combined >= by["20230205"].combined


def test_temperature_lowers_dry_score(monkeypatch):
    """같은 강수·습도라도 더 더운 날의 dry_score 가 낮아야 한다(수증기↑)."""
    canned = {"hourly": {
        "time": ["2023-03-01T00:00", "2023-03-10T00:00", "2023-03-20T00:00"],
        "precipitation": [0.0, 0.0, 0.0],
        "relative_humidity_2m": [50.0, 50.0, 50.0],
        "temperature_2m": [5.0, 15.0, 25.0],   # 강수·습도 동일, 온도만 차이
    }}
    monkeypatch.setattr(era5_master, "_fetch_era5_archive", lambda *a, **k: canned)
    sel = era5_master.select_master(37.33, 127.11, ["20230301", "20230310", "20230320"])
    by = {w.date: w for w in sel.scenes}
    assert by["20230301"].dry_score > by["20230310"].dry_score > by["20230320"].dry_score
    assert sel.selected_master == "20230301"      # 가장 시원한 날


def test_exclude_excessive_precip(monkeypatch):
    """precip_max_mm 초과 장면은 소거되고 master 에서 빠진다."""
    monkeypatch.setattr(era5_master, "_fetch_era5_archive", lambda *a, **k: CANNED)
    sel = era5_master.select_master(37.33, 127.11, DATES, precip_max_mm=2.0)
    by = {w.date: w for w in sel.scenes}
    assert by["20230124"].excluded is True        # 총 강수 3.0 > 2.0
    assert "강수" in by["20230124"].exclude_reason
    assert by["20230112"].excluded is False
    assert sel.n_excluded == 1
    assert sel.selected_master != "20230124"


def test_exclude_excessive_temperature(monkeypatch):
    """temp_max_c 초과(과도한 고온) 장면 소거."""
    monkeypatch.setattr(era5_master, "_fetch_era5_archive", lambda *a, **k: CANNED)
    sel = era5_master.select_master(37.33, 127.11, DATES, temp_max_c=10.0)
    by = {w.date: w for w in sel.scenes}
    assert by["20230124"].excluded is True        # 평균 13°C > 10
    assert "기온" in by["20230124"].exclude_reason
    assert sel.selected_master in ("20230112", "20230205")


def test_all_excluded_raises(monkeypatch):
    monkeypatch.setattr(era5_master, "_fetch_era5_archive", lambda *a, **k: CANNED)
    with pytest.raises(ValueError, match="남는 master 후보가 없"):
        era5_master.select_master(37.33, 127.11, DATES, humidity_max_pct=0.0)


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
