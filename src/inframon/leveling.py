"""수준측량 성과표(회차별 표고) → InSAR 검증 기준(연직 침하속도).

수준측량은 각 측점의 **표고[m]** 를 여러 회차에 걸쳐 잰다. 표고가 낮아지면 침하다.
표고[mm] 대 시간[년] 을 로버스트(쌍별 기울기 중앙값=Theil–Sen)로 회귀해 **연직
속도[mm/yr]** 를 낸다(침하=음수). 측점 위치는 한국 측량좌표(중부원점 등)라 WGS84 로
변환해 InSAR 점과 정합한다.

InSAR 는 LOS·상대변위, 수준측량은 연직·절대값이다. 그래서 검증 시 ①입사각으로 연직
→LOS 투영하고 ②공통 프레임 오프셋(수준원점 vs InSAR 국소기준의 차)을 제거한 잔차로
판정한다 — 둘 다 `validation.validate` 가 처리한다(이 모듈은 기준을 만들 뿐).

기대 형식(wide CSV, 한 행 = 한 측점):
    측점, X, Y, 20200501, 20210501, 20220501 ...
    P1, 201234.5, 448765.2, 12.480, 12.476, 12.471
- 위치 열: x/y (또는 easting/northing·종거/횡거·tm_x/tm_y). 대소문자 무시.
- 표고 열: 헤더가 날짜(YYYYMMDD / YYYY-MM-DD / YYYY.MM.DD)면 시간축 자동 인식.
  헤더가 '1차','2차' 처럼 날짜가 아니면 `dates=[...]` 로 회차 순서대로 준다.
- 측점 이름 열(측점/point/no/name)은 선택 — 있으면 per_point 라벨에 쓴다.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from .validation import Reference

_X_KEYS = ("x", "easting", "e", "종거", "tm_x", "tmx", "tm-x")
_Y_KEYS = ("y", "northing", "n", "횡거", "tm_y", "tmy", "tm-y")
_ID_KEYS = ("측점", "점번호", "point", "point_id", "id", "name", "no", "번호", "측점번호")
_DATE_FORMATS = ("%Y%m%d", "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d")


def _parse_date(s: str):
    """헤더/문자열을 날짜로. 못 읽으면 None."""
    s = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _decimal_years(dts):
    """datetime 목록 → 첫 날짜 기준 경과 연수[년]."""
    d0 = min(dts)
    return [(d - d0).days / 365.25 for d in dts]


def robust_slope(t_years, y_mm) -> float:
    """쌍별 기울기 중앙값(Theil–Sen). 2점이면 단순 기울기.

    회차가 적은(2~4회) 수준측량에 맞춘 경량 버전 — `life.theil_sen`(≥4시점) 과 달리
    2시점부터 동작한다. 이상 회차 1개가 있어도 중앙값이라 덜 흔들린다.
    """
    import numpy as np

    t = np.asarray(t_years, float)
    y = np.asarray(y_mm, float)
    ok = np.isfinite(t) & np.isfinite(y)
    t, y = t[ok], y[ok]
    if t.size < 2:
        return float("nan")
    slopes = []
    for i in range(t.size):
        for j in range(i + 1, t.size):
            dt = t[j] - t[i]
            if dt > 0:
                slopes.append((y[j] - y[i]) / dt)
    return float(np.median(slopes)) if slopes else float("nan")


def _read_rows(path):
    """CSV 읽기 — utf-8 우선, 실패 시 cp949(한국 성과표 관행) 폴백."""
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            with open(path, newline="", encoding=enc) as f:
                return list(csv.reader(f))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"인코딩을 읽을 수 없습니다(utf-8/cp949 시도): {path}")


def load_leveling_csv(path: str | Path, *, origin: str,
                      dates: list[str] | None = None,
                      min_rounds: int = 2) -> Reference:
    """수준측량 성과표(회차별 표고 m) → 연직 침하속도[mm/yr] 검증 기준(WGS84).

    Args:
        path:       wide CSV(위 형식). 표고 단위는 m 로 가정, 내부에서 mm 로 변환.
        origin:     측점 X,Y 의 원점계(중부원점 등) — WGS84 변환용.
        dates:      표고 열이 날짜 헤더가 아닐 때, 회차 순서의 날짜 목록.
        min_rounds: 이보다 회차(유효 표고)가 적은 측점은 제외(속도 추정 불가).

    Returns:
        Reference(kind="velocity", vertical=True) — 값은 연직속도[mm/yr](침하 음수).
    """
    from .korea_crs import to_wgs84

    rows = _read_rows(path)
    if len(rows) < 2:
        raise ValueError(f"성과표에 데이터 행이 없습니다: {path}")
    hdr = [h.strip() for h in rows[0]]
    hl = [h.lower() for h in hdr]

    def _find(keys):
        return next((i for i, h in enumerate(hl) if h in keys), None)

    ix_x, ix_y = _find(_X_KEYS), _find(_Y_KEYS)
    ix_id = _find(_ID_KEYS)
    if ix_x is None or ix_y is None:
        raise ValueError(f"위치 열(X,Y)을 찾지 못했습니다. 헤더: {hdr} — "
                         "열 이름을 x,y(또는 종거/횡거·easting/northing)로 맞춰주세요.")

    # 표고(회차) 열: 헤더가 날짜면 그 날짜, 아니면 dates 로 매핑
    date_cols = [(i, _parse_date(h)) for i, h in enumerate(hdr)]
    dated = [(i, d) for i, d in date_cols if d is not None]
    if dated:
        elev_ix = [i for i, _ in dated]
        elev_dt = [d for _, d in dated]
    else:
        # 날짜 헤더가 없다 → 위치·이름 아닌 나머지 수치열을 회차로 보고 dates 필요
        used = {ix_x, ix_y} | ({ix_id} if ix_id is not None else set())
        elev_ix = [i for i in range(len(hdr)) if i not in used]
        if not dates or len(dates) != len(elev_ix):
            raise ValueError(
                f"표고 열 헤더가 날짜가 아닙니다(회차 열 {len(elev_ix)}개). "
                f"dates 로 각 회차 날짜를 순서대로 주세요(예: --leveling-dates "
                "2020-05-01,2021-05-01,...).")
        elev_dt = [_parse_date(d) for d in dates]
        if any(d is None for d in elev_dt):
            raise ValueError(f"dates 를 날짜로 읽지 못했습니다: {dates}")
    if len(elev_ix) < min_rounds:
        raise ValueError(f"회차가 {len(elev_ix)}개뿐입니다(≥{min_rounds} 필요).")

    t_years = _decimal_years(elev_dt)
    span_yr = max(t_years) - min(t_years)

    lonlat, values, n_skip = [], [], 0
    for r in rows[1:]:
        if len(r) <= max(ix_x, ix_y, max(elev_ix)):
            n_skip += 1
            continue
        try:
            x = float(re.sub(r"[, ]", "", r[ix_x]))
            y = float(re.sub(r"[, ]", "", r[ix_y]))
        except (ValueError, TypeError):
            n_skip += 1
            continue
        elev_mm, tt = [], []
        for k, i in enumerate(elev_ix):
            cell = r[i].strip() if i < len(r) else ""
            try:
                elev_mm.append(float(cell) * 1000.0)   # m → mm
                tt.append(t_years[k])
            except (ValueError, TypeError):
                continue                                # 결측 회차 건너뜀
        if len(elev_mm) < min_rounds:
            n_skip += 1
            continue
        vel = robust_slope(tt, elev_mm)                 # mm/yr, 침하=음수
        w = to_wgs84(x, y, origin)
        lonlat.append((w.lon, w.lat))
        values.append(vel)

    if not values:
        raise ValueError(
            f"유효 측점이 없습니다(건너뜀 {n_skip}). 위치 열·표고 단위(m)·"
            "원점계·회차 날짜를 확인하세요.")
    src = (f"수준측량 성과표 {Path(path).name} · {len(values)}측점 · "
           f"{len(elev_ix)}회차 {min(elev_dt).date()}~{max(elev_dt).date()} "
           f"(경간 {span_yr:.1f}년) · 원점계 {origin}→WGS84")
    return Reference(lonlat=lonlat, values=values, kind="velocity",
                     vertical=True, source=src)
