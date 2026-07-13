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
    """흔한 중첩(response.body.items.item / EX list)에서 레코드 리스트를 끄집어낸다."""
    if isinstance(data, dict) and isinstance(data.get("list"), list):
        return data["list"]                       # 한국도로공사 EX API 규약
    node = data
    for key in ("response", "body", "items", "item"):
        if isinstance(node, dict) and key in node:
            node = node[key]
    if isinstance(node, list):
        return node
    return [node] if isinstance(node, dict) else []


# 한국도로공사 EX OpenAPI(data.ex.co.kr) — 일자별 전국 교통량(apiId=0617)
EX_NATIONAL_TRAFFIC_URL = "https://data.ex.co.kr/openapi/trafficapi/nationalTrafficVolumn"


def fetch_ex_daily_traffic(
    date_labels,
    *,
    key: str,
    endpoint: str = EX_NATIONAL_TRAFFIC_URL,
    ex_div_code: str | None = "00",     # 도공(00) 기본; None 이면 전체(도공+민자)
    timeout: float = 15.0,
) -> np.ndarray | None:
    """한국도로공사 EX API 로 InSAR 취득일별 전국 교통량 시계열[M] 수집.

    EX 의 `nationalTrafficVolumn` 은 sumDate 당 (exDivCode·tcsType·carType 분해된)
    레코드를 주므로, **취득일마다 1회 호출**해 trafficVolumn 을 합산한다. 전국 집계라
    공간 국소화는 안 되지만 **일자별 시간변조(주중/주말/명절/계절)** 로는 유효 —
    PINN 하중 시간변조(load=traffic(t)·w)에 그대로 쓴다. 실패·빈 응답은 None(폴백).
    """
    import json
    import urllib.parse
    import urllib.request

    days = [str(d).replace("-", "")[:8] for d in np.asarray(date_labels).ravel().tolist()]
    daily: dict[str, float] = {}
    for day in days:
        q = {"key": key, "type": "json", "sumDate": day}
        if ex_div_code:
            q["exDivCode"] = ex_div_code
        url = endpoint + "?" + urllib.parse.urlencode(q)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — 공식 EX API
                data = json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 — 개별일 실패는 건너뛰고 평균 폴백
            continue
        total = 0.0
        got = False
        for rec in _records(data):
            if not isinstance(rec, dict) or "trafficVolumn" not in rec:
                continue
            try:
                total += float(str(rec["trafficVolumn"]).replace(",", ""))
                got = True
            except (TypeError, ValueError):
                continue
        if got:
            daily[day] = total
    try:
        return align_to_dates(daily, date_labels)
    except ValueError:
        return None
