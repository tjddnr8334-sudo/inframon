#!/usr/bin/env python3
"""교량 산출물에서 시계열을 꺼내는 공용 함수 — 분석 스크립트들이 같이 쓴다.

예전에는 이 함수들이 `make_trend_agree.py` 안에 있었는데, 그 스크립트의 분석(점마다
기울기를 재서 보고서와 맞춰 보기)은 폐기했다. 점을 고르면 원하는 답이 나오고,
애초에 그 점들이 교면 위가 아니었기 때문이다(`make_deck_vs_ground.py` 참조).
분석은 버리되 자료를 꺼내는 부분은 죄가 없으므로 여기로 옮겨 둔다.

  dec_year        '20220330' → 2022.24…  달력에 고정된 연도소수
  load_points     project.h5 → LOS·시간·측점·부재·입사각
  report_series   보고서 GNSS/처짐 월별값 → (시간, 값, 계측종류, 읽은법)
  slope_ci        직선 기울기와 95% 반폭

**시간축 주의** — `insar/dates` 는 첫 촬영일부터의 경과일이다. 그대로 365.25 로 나눠
연주기를 맞추면 첫 촬영일의 연중 위치만큼 위상이 통째로 밀린다(가양·샛강 5.6개월,
월드컵 8.2개월). 그래서 여기서는 언제나 `date_labels` 를 `dec_year` 로 읽는다.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import numpy as np


def dec_year(s: str) -> float:
    """'YYYYMMDD' → 연도소수(달력 고정). 윤년은 366 으로 나눈다."""
    y, m, d = int(s[:4]), int(s[4:6]), int(s[6:8])
    doy = date(y, m, d).timetuple().tm_yday
    return y + (doy - 0.5) / (366.0 if y % 4 == 0 else 365.0)


def slope_ci(t: np.ndarray, Y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """[N,K] 계열마다 직선 기울기와 95% 반폭. CI 가 0 을 품으면 '추세 없음'."""
    Y = np.atleast_2d(np.asarray(Y, float))
    A = np.vstack([np.ones_like(t), t]).T
    c, *_ = np.linalg.lstsq(A, Y.T, rcond=None)
    resid = Y.T - A @ c
    sig = np.sqrt(np.sum(resid ** 2, axis=0) / max(len(t) - 2, 1))
    return c[1], 1.96 * sig / (np.std(t) * np.sqrt(len(t)))


def load_points(folder: Path):
    """project.h5 → (los[N,K], t[K] 연도소수, 측점[N], 부재[N], 입사각중앙값)."""
    import h5py

    p = folder / "project.h5"
    if not p.exists():
        return None
    with h5py.File(p, "r") as f:
        if "insar" not in f:
            return None
        g = f["insar"]
        los = np.asarray(g["los"][()], float)
        lab = [s.decode() if isinstance(s, bytes) else str(s)
               for s in g["date_labels"][()]]
        st = (np.asarray(g["deck_station"][()], float) if "deck_station" in g
              else np.full(los.shape[0], np.nan))
        mem = (np.asarray(g["member"][()]).astype(int) if "member" in g
               else np.zeros(los.shape[0], int))
        inc = (float(np.nanmedian(np.asarray(g["incidence_deg"][()], float)))
               if "incidence_deg" in g else 39.0)
    return los, np.asarray([dec_year(s) for s in lab]), st, mem, inc


def report_series(auto: dict, eye: dict, name: str):
    """보고서 월별값 → (t, v, 계측종류, 읽은법). 없으면 None.

    자동 판독(그래프 픽셀)이 있으면 그쪽을, 없으면 눈으로 읽은 값을 쓴다.
    눈으로 읽은 쪽은 키가 '2024' 이기도 하고 'DP_..._2024' 이기도 해서 끝 네 자리를
    연도로 읽는다.
    """
    for c in auto.get("charts", []):
        if c.get("bridge") != name:
            continue
        ts, vs = [], []
        for y, arr in (c.get("monthly") or {}).items():
            for i, v in enumerate(arr):
                if v is not None:
                    ts.append(int(y) + (i + 0.5) / 12)
                    vs.append(float(v))
        if len(ts) >= 6:
            return (np.asarray(ts), np.asarray(vs),
                    f"{c.get('dir', '')}변위 ({c.get('sensor', '')})", "자동판독")
    e = eye.get(name, {})
    if e.get("status") == "read":
        ts, vs = [], []
        for y, arr in (e.get("monthly") or {}).items():
            m = re.search(r"(\d{4})$", str(y))
            if not m:
                continue
            for i, v in enumerate(arr):
                if v is not None:
                    ts.append(int(m.group(1)) + (i + 0.5) / 12)
                    vs.append(float(v))
        if len(ts) >= 6:
            o = np.argsort(ts)
            return (np.asarray(ts)[o], np.asarray(vs)[o],
                    e.get("quantity", "계측"), "눈으로 읽음")
    return None
