"""온도(Open-Meteo)·교통량 자동 수집 — 응답 파싱·날짜 정렬 (네트워크 mock)."""

from __future__ import annotations

import json

from inframon import traffic, weather


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


# ── 온도 (Open-Meteo) ──
def test_temperature_series_daily_mean_and_align(monkeypatch):
    payload = {"hourly": {
        "time": ["2024-01-07T00:00", "2024-01-07T12:00", "2024-01-19T00:00"],
        "temperature_2m": [0.0, 10.0, -4.0]}}    # 0107 평균 5, 0119 = -4
    monkeypatch.setattr(weather.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload))
    out = weather.fetch_temperature_series(37.36, 127.11, ["20240107", "20240119"])
    assert out.shape == (2,)
    assert out[0] == 5.0 and out[1] == -4.0


def test_temperature_missing_day_uses_global_mean(monkeypatch):
    payload = {"hourly": {"time": ["2024-01-07T00:00"], "temperature_2m": [8.0]}}
    monkeypatch.setattr(weather.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload))
    out = weather.fetch_temperature_series(37.36, 127.11, ["20240107", "20240219"])
    assert out[0] == 8.0 and out[1] == 8.0           # 누락일 → 전체 평균(8)


def test_temperature_network_fail_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("net")

    monkeypatch.setattr(weather.urllib.request, "urlopen", boom)
    try:
        weather.fetch_temperature_series(0, 0, ["20240107", "20240119"])
        raise AssertionError("예외가 나야 함")
    except RuntimeError:
        pass


# ── 교통량 ──
def test_align_to_dates():
    out = traffic.align_to_dates({"20240107": 5000, "20240119": 9000},
                                 ["20240107", "20240113", "20240119"])
    assert out[0] == 5000 and out[2] == 9000
    assert out[1] == 7000                            # 누락일 → 평균(7000)


def test_fetch_traffic_series_parses(monkeypatch):
    import urllib.request
    payload = {"response": {"body": {"items": {"item": [
        {"ymd": "20240107", "trfl": "12,345"}, {"ymd": "20240119", "trfl": "8000"}]}}}}
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload))
    out = traffic.fetch_traffic_series(
        ["20240107", "20240119"], service_key="K", endpoint="https://x",
        date_field="ymd", count_field="trfl")
    assert out is not None
    assert out[0] == 12345.0 and out[1] == 8000.0


def test_fetch_traffic_network_fail_returns_none(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("net")))
    assert traffic.fetch_traffic_series(["20240107", "20240119"], service_key="K",
                                        endpoint="https://x", date_field="d",
                                        count_field="c") is None
