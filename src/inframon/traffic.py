"""교량 교통량 자동 수집 (공공 교통 API, 키 필요) → PINN 하중 입력.

InSAR 취득일(date_labels)에 맞춘 **교통량 시계열[M]**을 만든다. `cfg.pinn_traffic` 로
넣으면 PINN 하중 성분이 교통량으로 변조된다(load = traffic(t)·w).

교통 API(공공데이터포털 한국도로공사 교통량·ITS국가교통정보센터 등)는 데이터셋마다
엔드포인트·응답 스키마·키가 달라, 키·엔드포인트·필드맵을 받아 **방어적으로** 파싱한다.
키가 없으면 None → 호출측은 교통량 없이(자유 하중) 폴백.
"""

from __future__ import annotations

from statistics import mean
from typing import Any

import numpy as np


def align_to_dates(daily_counts: dict[str, float], date_labels) -> np.ndarray:
    """{YYYYMMDD: 교통량} 을 취득일 순서[M] 로 정렬. 누락일은 전체 평균."""
    days = [str(d) for d in np.asarray(date_labels).ravel().tolist()]
    vals = {str(k): float(v) for k, v in daily_counts.items() if v is not None}
    if not vals:
        raise ValueError("교통량 데이터가 비었습니다")
    glob = mean(vals.values())
    return np.array([vals.get(d, glob) for d in days], dtype=float)


def fetch_traffic_series(
    date_labels,
    *,
    service_key: str,
    endpoint: str,
    date_field: str,
    count_field: str,
    params: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> np.ndarray | None:
    """공공 교통 API 에서 일별 교통량을 받아 취득일 정렬 배열[M] 반환.

    응답 레코드에서 `date_field`(YYYYMMDD/날짜)·`count_field`(교통량)를 뽑아 정렬한다.
    네트워크/파싱 실패 시 None(호출측 폴백).
    """
    import json
    import urllib.parse
    import urllib.request

    q = {"serviceKey": service_key, "type": "json", "_type": "json", **(params or {})}
    url = endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — 사용자 지정 공공 API
            data = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None

    records = _records(data)
    daily: dict[str, float] = {}
    for rec in records:
        if not isinstance(rec, dict) or date_field not in rec or count_field not in rec:
            continue
        day = str(rec[date_field]).replace("-", "")[:8]
        try:
            daily[day] = float(str(rec[count_field]).replace(",", ""))
        except (TypeError, ValueError):
            continue
    try:
        return align_to_dates(daily, date_labels)
    except ValueError:
        return None


def _records(data: Any) -> list:
    """흔한 중첩(response.body.items.item)에서 레코드 리스트를 끄집어낸다."""
    node = data
    for key in ("response", "body", "items", "item"):
        if isinstance(node, dict) and key in node:
            node = node[key]
    if isinstance(node, list):
        return node
    return [node] if isinstance(node, dict) else []
