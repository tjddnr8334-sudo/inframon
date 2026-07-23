"""NGL(Nevada Geodetic Lab) 상시 GNSS 로 InSAR 신뢰도 검증.

교량엔 GNSS 가 없지만, **인근(수 km) 상시관측소의 절대 3D 변위**는 InSAR LOS 의 광역
트렌드·궤도·대기 오차를 잡는 최적 기준이다. 교량 좌표 → 반경 내 NGL 관측소 자동탐색 →
일별 ENU 시계열(.tenv3) 다운로드 → InSAR LOS 로 투영 → InSAR 속도와 대조(신뢰도 지표).

NGL 실 엔드포인트(2026 확인):
  · 관측소 목록:  http://geodesy.unr.edu/NGLStationPages/DataHoldings.txt
      컬럼: Sta Lat(deg) Long(deg,0-360) Hgt X Y Z Dtbeg Dtend Dtmod NumSol [OrigName]
  · 시계열(IGS20 24h): http://geodesy.unr.edu/gps_timeseries/IGS20/tenv3/IGS20/<STA>.tenv3
      컬럼: site YYMMMDD decyr MJD week day reflon _e0 east _n0 north u0 up _ant sig_e sig_n sig_u ...
      위치[m] = 정수부(e0/n0/u0) + 소수부(east/north/up).

⚠️ 무키·공개 데이터. 네트워크는 fetch_fn 주입(테스트 격리·오프라인). 좌표는 InSAR 와
동일 프레임 가정(광역 기준 비교이므로 mm/yr 수준에서 유효).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

HOLDINGS_URL = "https://geodesy.unr.edu/NGLStationPages/DataHoldings.txt"
TENV3_URL = "https://geodesy.unr.edu/gps_timeseries/IGS20/tenv3/IGS20/{sta}.tenv3"


def _http_text(url: str, timeout: float = 30.0) -> str:
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — 공개 NGL
        return r.read().decode("utf-8", "replace")


@dataclass
class Station:
    sta: str
    lat: float
    lon: float          # -180..180 로 정규화
    dist_km: float = 0.0


def parse_holdings(text: str) -> list[Station]:
    """DataHoldings.txt → 관측소 목록(경도 0-360 → -180..180)."""
    out: list[Station] = []
    for ln in text.splitlines():
        p = ln.split()
        if len(p) < 3 or not p[0][0].isalnum():
            continue
        try:
            lat = float(p[1]); lon = float(p[2])
        except ValueError:
            continue
        if not (-90 <= lat <= 90):
            continue
        if lon > 180:
            lon -= 360.0
        out.append(Station(sta=p[0], lat=lat, lon=lon))
    return out


def _dist_km(lat0, lon0, lat1, lon1) -> float:
    return math.hypot((lat1 - lat0), (lon1 - lon0) * math.cos(math.radians(lat0))) * 111.0


def nearest_stations(lat: float, lon: float, holdings: list[Station], *,
                     max_km: float = 50.0, k: int = 5) -> list[Station]:
    """(lat,lon) 반경 max_km 내 최근접 GNSS 관측소 k 개(거리순)."""
    near = []
    for s in holdings:
        d = _dist_km(lat, lon, s.lat, s.lon)
        if d <= max_km:
            near.append(Station(s.sta, s.lat, s.lon, round(d, 2)))
    near.sort(key=lambda s: s.dist_km)
    return near[:k]


# tenv3 월 약어 → 월
_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


@dataclass
class GnssSeries:
    sta: str
    decyr: list          # 소수연도
    de_mm: list          # 동(E) 변위[mm], 첫 에폭 기준
    dn_mm: list          # 북(N)
    du_mm: list          # 상(U)
    n_epochs: int
    span_yr: float


def parse_tenv3(text: str, sta: str = "?") -> GnssSeries:
    """tenv3 → 첫 에폭 기준 ENU 변위[mm] 시계열. 위치[m]=정수부+소수부."""
    decyr, e, n, u = [], [], [], []
    for ln in text.splitlines():
        p = ln.split()
        if len(p) < 13 or p[0].upper() in ("SITE", ""):
            continue
        try:
            yr = float(p[2])
            east = float(p[7]) + float(p[8])     # e0 + east [m]
            north = float(p[9]) + float(p[10])   # n0 + north
            up = float(p[11]) + float(p[12])     # u0 + up
        except (ValueError, IndexError):
            continue
        decyr.append(yr); e.append(east); n.append(north); u.append(up)
    if not decyr:
        raise ValueError(f"tenv3 파싱 실패(빈 시계열): {sta}")
    e0, n0, u0 = e[0], n[0], u[0]
    de = [(x - e0) * 1000.0 for x in e]          # mm, 첫 에폭 기준
    dn = [(x - n0) * 1000.0 for x in n]
    du = [(x - u0) * 1000.0 for x in u]
    return GnssSeries(sta=sta, decyr=decyr, de_mm=de, dn_mm=dn, du_mm=du,
                      n_epochs=len(decyr), span_yr=round(decyr[-1] - decyr[0], 3))


def linear_rate_mm_yr(decyr: list, series_mm: list) -> float:
    """최소자승 선형 추세 → 속도[mm/yr]."""
    import numpy as np
    t = np.asarray(decyr, float); y = np.asarray(series_mm, float)
    if t.size < 2 or float(t.max() - t.min()) < 1e-6:
        return 0.0
    A = np.vstack([t - t.mean(), np.ones_like(t)]).T
    return float(np.linalg.lstsq(A, y, rcond=None)[0][0])


def robust_rate_mm_yr(decyr: list, series_mm: list) -> tuple[float, float]:
    """Theil-Sen 로버스트 속도[mm/yr]·잔차 산포(MAD→σ) — 관측소 스텝/이상치 방어.

    NGL tenv3 는 장비교체 등 **스텝**(예 SWON 6.7m 점프)을 포함할 수 있어 최소자승은
    비물리적 속도를 낸다. Theil-Sen(쌍별 기울기 중앙값)+MAD 잔차로 스텝을 걸러낸다.
    """
    import numpy as np
    t = np.asarray(decyr, float); y = np.asarray(series_mm, float)
    if t.size < 3 or float(t.max() - t.min()) < 1e-6:
        return 0.0, 0.0
    try:
        from scipy.stats import theilslopes
        slope = float(theilslopes(y, t)[0])
    except Exception:  # noqa: BLE001 — scipy 없으면 표본 Theil-Sen 폴백
        n = t.size
        idx = np.linspace(0, n - 1, min(n, 150)).astype(int)
        ts, ys = t[idx], y[idx]
        sl = [(ys[j] - ys[i]) / (ts[j] - ts[i])
              for i in range(len(ts)) for j in range(i + 1, len(ts)) if ts[j] != ts[i]]
        slope = float(np.median(sl)) if sl else 0.0
    resid = y - (slope * (t - t.mean()) + np.median(y - slope * (t - t.mean())))
    mad = float(np.median(np.abs(resid - np.median(resid))))
    return slope, 1.4826 * mad          # MAD→σ 등가


def enu_to_los(e, n, u, incidence_deg: float, heading_deg: float) -> float:
    """ENU → 위성 LOS 투영. LOS 단위벡터(지상→위성, 우측관측):

        p_E=-sinθ·cos(α),  p_N= sinθ·sin(α),  p_U= cosθ   (θ=입사각, α=위성 헤딩[N기준 시계]).
    연직 상승(+U)→ +LOS(위성 접근), 기존 validation 의 U·cosθ 와 일관.
    """
    th = math.radians(incidence_deg); al = math.radians(heading_deg)
    pe = -math.sin(th) * math.cos(al)
    pn = math.sin(th) * math.sin(al)
    pu = math.cos(th)
    return e * pe + n * pn + u * pu


# Sentinel-1 대표 헤딩[deg, N기준 시계] — 궤도 방향 미상 시 폴백
S1_HEADING = {"ascending": -12.0, "descending": -168.0}


@dataclass
class GnssLosStation:
    sta: str
    lat: float
    lon: float
    dist_km: float
    los_vel_mm_yr: float
    up_vel_mm_yr: float
    n_epochs: int
    span_yr: float


def gnss_los_velocities(lat: float, lon: float, *, incidence_deg: float,
                        heading_deg: float, max_km: float = 50.0, k: int = 8,
                        min_span_yr: float = 1.0, max_abs_vert: float = 40.0,
                        max_scatter_mm: float = 60.0,
                        fetch_fn=_http_text) -> tuple[list[GnssLosStation], list[str]]:
    """교량 인근 GNSS 관측소별 LOS 속도[mm/yr] — InSAR 대조용 지상 기준.

    로버스트(Theil-Sen) 속도 + 게이팅으로 스텝/이상 관측소 제외. 반환 (유효목록, 제외사유).
    게이트: |수직속도|>max_abs_vert 또는 잔차산포>max_scatter_mm (장비스텝·불량해 신호).
    """
    holdings = parse_holdings(fetch_fn(HOLDINGS_URL))
    out: list[GnssLosStation] = []
    dropped: list[str] = []
    for s in nearest_stations(lat, lon, holdings, max_km=max_km, k=k):
        try:
            ser = parse_tenv3(fetch_fn(TENV3_URL.format(sta=s.sta)), s.sta)
        except Exception as exc:  # noqa: BLE001 — 개별 관측소 실패는 건너뜀
            dropped.append(f"{s.sta}(취득실패:{type(exc).__name__})")
            continue
        if ser.span_yr < min_span_yr:
            dropped.append(f"{s.sta}(관측기간 {ser.span_yr:.1f}yr<{min_span_yr})")
            continue
        ve, _ = robust_rate_mm_yr(ser.decyr, ser.de_mm)
        vn, _ = robust_rate_mm_yr(ser.decyr, ser.dn_mm)
        vu, su = robust_rate_mm_yr(ser.decyr, ser.du_mm)
        if abs(vu) > max_abs_vert or su > max_scatter_mm:      # 스텝·불량 관측소 게이트
            dropped.append(f"{s.sta}(스텝/이상: 수직 {vu:.0f}mm/yr·산포 {su:.0f}mm)")
            continue
        los = enu_to_los(ve, vn, vu, incidence_deg, heading_deg)
        out.append(GnssLosStation(s.sta, s.lat, s.lon, s.dist_km, round(los, 3),
                                  round(vu, 3), ser.n_epochs, ser.span_yr))
    return out, dropped


# 수직 대조 판정 임계[mm/yr]. InSAR 연직 정밀도(장기 시계열 ~0.5~1)와 GNSS Up(~0.5)를
# 감안한 값 — 2 이내면 정합, 5 이내면 허용, 그 이상은 편차.
VERT_OK_MM_YR, VERT_MARGINAL_MM_YR = 2.0, 5.0
# 수직속도 이상치 판정(MAD 배수). 장비 스텝이 남은 관측소를 데이터 기반으로 걸러낸다.
VERT_OUTLIER_MAD = 5.0


def _mad_outliers(values: list[float], k: float = VERT_OUTLIER_MAD) -> list[bool]:
    """중앙값 절대편차 기준 이상치 마스크. 표본이 3개 미만이면 전부 False."""
    import numpy as np

    v = np.asarray(values, dtype=float)
    if v.size < 3:
        return [False] * v.size
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    if mad <= 1e-9:                       # 거의 동일 → 편차가 있는 것만 이상치
        return [bool(abs(x - med) > 1.0) for x in v]
    return [bool(abs(x - med) / (1.4826 * mad) > k) for x in v]


@dataclass
class GnssValidation:
    insar_los_vel_mm_yr: float        # 교량 InSAR LOS 속도(대표=중앙값)
    insar_los_std: float
    incidence_deg: float
    heading_deg: float
    stations: list                    # [{sta,dist_km,gnss_los,up_vel,insar,resid,up_resid,...}]
    n_stations: int
    rms_resid_mm_yr: float            # 관측소별 (InSAR−GNSS) **LOS** RMS — 기준프레임 차 포함
    max_km: float
    dropped: list                     # 게이트로 제외된 관측소·사유
    insar_span_yr: float = 0.0        # InSAR 관측기간(속도 신뢰성 지표)
    # ── 1차 지표: 수직 대조(플레이트-무관) ──
    # GNSS 절대속도는 한반도 플레이트 수평운동(~30mm/yr)을 포함하고 InSAR LOS 는 국소
    # 기준점에 대한 **상대** 값이다. 둘을 LOS 에서 직접 빼면 차이의 대부분이 기준프레임
    # 차이지 InSAR 오차가 아니다 — 그 숫자를 합격/불합격으로 쓰면 안 된다.
    # 플레이트 운동은 거의 수평이므로 **연직 성분**이 유효한 대조축이다.
    insar_up_vel_mm_yr: float | None = None    # LOS/cosθ (단일궤도 연직 가정)
    rms_up_resid_mm_yr: float | None = None    # 이상치 제외 후 수직 잔차 RMS
    n_vertical_used: int = 0
    vertical_outliers: list = field(default_factory=list)

    def summary(self) -> str:
        lines = ["════ InSAR ↔ GNSS(NGL) 신뢰도 지표 ════",
                 f" 교량 InSAR LOS 속도 {self.insar_los_vel_mm_yr:+.2f}±{self.insar_los_std:.2f} mm/yr"
                 f" (θ={self.incidence_deg:.0f}° head={self.heading_deg:.1f}°, 관측 {self.insar_span_yr:.1f}yr)",
                 f" 인근 GNSS {self.n_stations}개 (반경 {self.max_km:.0f}km):"]
        for s in self.stations:
            flag = "  ⊘이상치" if s.get("vertical_outlier") else ""
            up_r = s.get("up_resid")
            up_txt = f"{up_r:+.2f}" if up_r is not None else "—"
            lines.append(f"   {s['sta']} {s['dist_km']:.1f}km: 수직 {s['up_vel']:+.2f} "
                         f"→ InSAR−GNSS(수직) {up_txt} mm/yr"
                         f"  [{s['span_yr']:.0f}yr]{flag}")
            lines.append(f"          (LOS {s['gnss_los']:+.2f}, 차 {s['resid']:+.2f} "
                         "— 기준프레임 차 포함이라 판정에 쓰지 않음)")
        if self.dropped:
            lines.append(" 제외: " + ", ".join(self.dropped))

        # ── 판정은 **수직**으로만 한다 ──
        if self.rms_up_resid_mm_yr is not None:
            if self.rms_up_resid_mm_yr <= VERT_OK_MM_YR:
                verdict = "✅ GNSS 지상기준과 정합(신뢰도↑)"
            elif self.rms_up_resid_mm_yr <= VERT_MARGINAL_MM_YR:
                verdict = "🟡 허용 범위 — 관측기간·기준점 확인 권장"
            else:
                verdict = "⚠️ 수직 편차 큼 — InSAR 기준프레임/보정 점검 필요"
            lines.append(f" 수직 잔차 RMS {self.rms_up_resid_mm_yr:.2f} mm/yr "
                         f"(관측소 {self.n_vertical_used}개) — {verdict}")
            if self.vertical_outliers:
                lines.append("   ⊘ 수직 이상치 제외: " + ", ".join(self.vertical_outliers)
                             + " (장비 스텝 잔재로 판단)")
        elif self.n_stations:
            lines.append(" 수직 대조 불가 — 유효 관측소 부족")
        else:
            lines.append(" ⚠️ 반경 내 유효 GNSS 없음 — 반경 확대(--gnss-km) 권장")

        lines.append(f" InSAR 연직 환산 {self.insar_up_vel_mm_yr:+.2f} mm/yr"
                     f" (LOS/cos{self.incidence_deg:.0f}°, 단일궤도 연직 가정)"
                     if self.insar_up_vel_mm_yr is not None else "")
        lines.append(" ⓘ 유의: (1) GNSS 절대속도는 한반도 플레이트 운동(수평 ~30mm/yr)을 포함하고"
                     " InSAR LOS 는 국소 기준점에 대한 **상대** 값이다. LOS 에서 직접 빼면 차이의"
                     " 대부분이 기준프레임 차이지 InSAR 오차가 아니므로 판정에 쓰지 않는다.")
        lines.append("        (2) 수직 대조도 InSAR 기준점이 안정하다는 가정 위에 있고,"
                     " GNSS 는 교량이 아니라 수 km 떨어진 지반이다 — 광역 정합성 검증이지"
                     " 교량 부재 검증이 아니다.")
        lines.append("        (3) InSAR 관측기간이 짧으면(±산포 큼) 속도 불확실 — 장기 시계열 필요.")
        return "\n".join(x for x in lines if x)

    def as_dict(self) -> dict:
        return {"insar_los_vel_mm_yr": round(self.insar_los_vel_mm_yr, 3),
                "insar_los_std": round(self.insar_los_std, 3),
                "insar_span_yr": self.insar_span_yr,
                "incidence_deg": self.incidence_deg, "heading_deg": self.heading_deg,
                "n_stations": self.n_stations, "rms_resid_mm_yr": round(self.rms_resid_mm_yr, 3),
                "stations": self.stations, "dropped": self.dropped, "max_km": self.max_km,
                # 1차 지표(판정 근거)
                "insar_up_vel_mm_yr": (None if self.insar_up_vel_mm_yr is None
                                       else round(self.insar_up_vel_mm_yr, 3)),
                "rms_up_resid_mm_yr": (None if self.rms_up_resid_mm_yr is None
                                       else round(self.rms_up_resid_mm_yr, 3)),
                "n_vertical_used": self.n_vertical_used,
                "vertical_outliers": self.vertical_outliers,
                "primary_metric": "rms_up_resid_mm_yr",
                "note": ("LOS 잔차는 기준프레임(플레이트 운동) 차를 포함해 판정에 쓰지 않는다. "
                         "판정은 플레이트-무관 수직 잔차로 한다.")}


def validate_insar_vs_gnss(project_h5, *, incidence_deg: float = 39.0,
                           heading_deg: float | None = None, max_km: float = 50.0,
                           k: int = 5, fetch_fn=_http_text) -> GnssValidation:
    """project.h5 /insar LOS 속도를 인근 NGL GNSS LOS 속도와 대조(광역 기준 신뢰도).

    교량 InSAR 점은 GNSS 와 수 km 떨어져 직접 co-location 은 안 되나, **광역 지반 LOS
    속도**를 GNSS 기준으로 비교해 InSAR 기준프레임·대기·궤도 오차의 정합성을 검증한다.
    heading 미지정 시 /insar track_source 의 HEADING, 없으면 S1 상승 폴백.
    """
    import json

    import h5py
    import numpy as np

    from datetime import datetime

    with h5py.File(str(project_h5), "r") as f:
        g = f["insar"]
        xyz = g["xyz"][()].astype(float)
        los = g["los"][()].astype(float)              # [N,M] mm
        dates = [d.decode() if isinstance(d, bytes) else str(d) for d in g["date_labels"][()]]
        head = heading_deg
        if head is None and "track_source" in g.attrs:
            try:
                head = float(json.loads(g.attrs["track_source"])["attrs"]["HEADING"])
            except (KeyError, ValueError, TypeError):
                head = None
    if head is None:
        head = S1_HEADING["ascending"]

    lon0 = float(np.median(xyz[:, 0])); lat0 = float(np.median(xyz[:, 1]))
    d0 = datetime.strptime(dates[0], "%Y%m%d")
    yr = np.array([(datetime.strptime(d, "%Y%m%d") - d0).days for d in dates]) / 365.25
    A = np.vstack([yr - yr.mean(), np.ones_like(yr)]).T
    vel = np.linalg.lstsq(A, los.T, rcond=None)[0][0]          # [N] mm/yr
    insar_vel = float(np.median(vel)); insar_std = float(np.std(vel))
    insar_span = round(float(yr.max() - yr.min()), 2)

    gnss, dropped = gnss_los_velocities(lat0, lon0, incidence_deg=incidence_deg,
                                        heading_deg=head, max_km=max_km, k=k, fetch_fn=fetch_fn)
    # InSAR LOS → 연직(단일궤도 연직 가정). 플레이트 운동은 거의 수평이므로 GNSS Up 과의
    # 대조가 기준프레임에 영향받지 않는 유일한 축이다.
    cos_t = math.cos(math.radians(incidence_deg))
    insar_up = insar_vel / cos_t if abs(cos_t) > 1e-6 else None

    ups = [s.up_vel_mm_yr for s in gnss]
    outlier_mask = _mad_outliers(ups)

    stations, resids, up_resids, outliers = [], [], [], []
    for s, is_out in zip(gnss, outlier_mask):
        r = insar_vel - s.los_vel_mm_yr
        resids.append(r)
        ur = None if insar_up is None else round(insar_up - s.up_vel_mm_yr, 3)
        if is_out:
            outliers.append(f"{s.sta}(수직 {s.up_vel_mm_yr:+.1f}mm/yr, {s.span_yr:.0f}yr)")
        elif ur is not None:
            up_resids.append(ur)
        stations.append({"sta": s.sta, "dist_km": s.dist_km, "gnss_los": s.los_vel_mm_yr,
                         "up_vel": s.up_vel_mm_yr, "insar": round(insar_vel, 3),
                         "resid": round(r, 3), "up_resid": ur,
                         "vertical_outlier": bool(is_out),
                         "n_epochs": s.n_epochs, "span_yr": s.span_yr})
    rms = float(np.sqrt(np.mean(np.square(resids)))) if resids else float("nan")
    rms_up = float(np.sqrt(np.mean(np.square(up_resids)))) if up_resids else None
    return GnssValidation(insar_los_vel_mm_yr=insar_vel, insar_los_std=insar_std,
                          incidence_deg=incidence_deg, heading_deg=round(head, 2),
                          stations=stations, n_stations=len(stations),
                          rms_resid_mm_yr=rms, max_km=max_km, dropped=dropped,
                          insar_span_yr=insar_span,
                          insar_up_vel_mm_yr=(None if insar_up is None else round(insar_up, 3)),
                          rms_up_resid_mm_yr=(None if rms_up is None else round(rms_up, 3)),
                          n_vertical_used=len(up_resids), vertical_outliers=outliers)


# ═══════════════ SLC 처리의 지상 근거 — GNSS 기준앵커 ═══════════════
# InSAR 는 **상대** 변위만 준다. 기준점을 어디에 두느냐가 전 결과의 원점을 정하는데,
# 지금까지 그 근거는 형식별 휴리스틱 문자열(`reference_hint`)뿐이었다. 인근 상시 GNSS
# 관측소는 그 선택을 **관측으로 뒷받침**할 수 있는 유일한 지상 근거다.
#
# 다만 절대 타이(relative → absolute)는 GNSS 가 InSAR 발자국 안에 있을 때만 정당하다.
# 정자교처럼 최근접이 11km 면 그 사이 지반이 같이 움직인다는 보장이 없다. 그래서
# 앵커는 **근거와 기준계 맥락**으로 제공하고, 멀면 자동 타이를 거부한다.

# 절대 타이를 허용할 최대 거리[km]. 이보다 멀면 지역 기준계 정보로만 쓴다.
TIE_MAX_KM = 2.0
# 앵커 후보의 최소 관측기간[년]. 속도를 신뢰하려면 이 정도는 필요하다.
ANCHOR_MIN_SPAN_YR = 5.0


@dataclass
class GnssAnchorCandidate:
    sta: str
    lat: float
    lon: float
    dist_km: float
    span_yr: float
    n_epochs: int
    up_vel_mm_yr: float
    up_scatter_mm: float
    score: float
    rejected: str | None = None


@dataclass
class GnssAnchor:
    """SLC 처리 레시피에 실을 지상 기준 근거."""
    bridge_lat: float
    bridge_lon: float
    max_km: float
    candidates: list          # GnssAnchorCandidate → dict
    best: dict | None         # 최적 앵커(없으면 None)
    can_tie_absolute: bool    # 발자국 안(≤TIE_MAX_KM)이라 절대 타이가 정당한가
    datum_up_mm_yr: float | None   # 지역 연직 기준 속도(앵커의 Up)
    verdict: str
    advice: str

    def to_dict(self) -> dict:
        return {
            "source": "NGL (Nevada Geodetic Laboratory) 상시 GNSS",
            "holdings_url": HOLDINGS_URL,
            "timeseries_url_pattern": TENV3_URL,
            "bridge": {"lat": self.bridge_lat, "lon": self.bridge_lon},
            "search_radius_km": self.max_km,
            "candidates": self.candidates,
            "anchor": self.best,
            "can_tie_absolute": self.can_tie_absolute,
            "tie_max_km": TIE_MAX_KM,
            "datum_up_mm_yr": self.datum_up_mm_yr,
            "verdict": self.verdict,
            "advice": self.advice,
            "note": ("InSAR 는 상대 변위다. 이 블록은 기준점 선정의 지상 근거와 지역 연직 "
                     "기준계를 제공한다. 절대 타이는 GNSS 가 InSAR 발자국 안(≤"
                     f"{TIE_MAX_KM}km)일 때만 정당하다 — 그보다 멀면 사이 지반이 같이 "
                     "움직인다는 보장이 없다."),
        }


def _anchor_score(dist_km: float, span_yr: float, up_vel: float, scatter: float) -> float:
    """앵커 적합도 — 클수록 좋다.

    기준점은 **오래 관측됐고, 연직으로 안 움직이고, 산포가 작고, 가까운** 곳이어야 한다.
    각 항을 물리적으로 의미 있는 스케일로 정규화해 곱이 아니라 합으로 둔다(한 항이 0 이어도
    나머지 정보가 살아 있게).
    """
    s_span = min(span_yr / 10.0, 2.0)             # 10년이면 만점권
    s_stab = 1.0 / (1.0 + abs(up_vel) / 1.0)      # |연직속도| 1mm/yr 에서 0.5
    s_scat = 1.0 / (1.0 + scatter / 10.0)         # 산포 10mm 에서 0.5
    s_dist = 1.0 / (1.0 + dist_km / 10.0)         # 10km 에서 0.5
    return round(s_span + s_stab + s_scat + s_dist, 4)


def reference_anchor(lat: float, lon: float, *, max_km: float = 50.0, k: int = 8,
                     min_span_yr: float = ANCHOR_MIN_SPAN_YR,
                     max_scatter_mm: float = 60.0,
                     fetch_fn=_http_text) -> GnssAnchor:
    """교량 좌표 → SLC 처리에 실을 GNSS 기준앵커 근거.

    `--make-sarvey-config` 가 이 결과를 `processing_manifest.json` 의 `gnss_reference`
    블록으로 실어, SARvey/MintPy 기준점 선정과 결과 해석의 지상 근거로 쓰게 한다.
    """
    holdings = parse_holdings(fetch_fn(HOLDINGS_URL))
    cands: list[GnssAnchorCandidate] = []
    for s in nearest_stations(lat, lon, holdings, max_km=max_km, k=k):
        try:
            ser = parse_tenv3(fetch_fn(TENV3_URL.format(sta=s.sta)), s.sta)
        except Exception as exc:  # noqa: BLE001 — 개별 관측소 실패는 후보에서만 제외
            cands.append(GnssAnchorCandidate(s.sta, s.lat, s.lon, s.dist_km, 0.0, 0, 0.0, 0.0,
                                             0.0, f"취득실패:{type(exc).__name__}"))
            continue
        vu, su = robust_rate_mm_yr(ser.decyr, ser.du_mm)
        rej = None
        if ser.span_yr < min_span_yr:
            rej = f"관측 {ser.span_yr:.1f}yr < {min_span_yr:.0f}yr — 속도 신뢰 곤란"
        elif su > max_scatter_mm:
            rej = f"산포 {su:.0f}mm — 장비 스텝/불량해 의심"
        cands.append(GnssAnchorCandidate(
            s.sta, s.lat, s.lon, s.dist_km, round(ser.span_yr, 2), ser.n_epochs,
            round(vu, 3), round(su, 2),
            0.0 if rej else _anchor_score(s.dist_km, ser.span_yr, vu, su), rej))

    ok = [c for c in cands if c.rejected is None]
    ok.sort(key=lambda c: -c.score)
    best = ok[0] if ok else None
    can_tie = bool(best and best.dist_km <= TIE_MAX_KM)

    if best is None:
        verdict = "지상 근거 없음"
        advice = (f"반경 {max_km:.0f}km 안에 쓸 만한 상시 GNSS 가 없습니다. 기준점은 형식별 "
                  "휴리스틱으로 두되, 결과를 절대 침하로 읽지 마세요(상대값).")
    elif can_tie:
        verdict = "절대 타이 가능"
        advice = (f"{best.sta} 가 {best.dist_km:.1f}km — InSAR 발자국 안입니다. 이 점을 "
                  "기준점 근처에 두고 GNSS 연직속도로 절대 기준을 맞출 수 있습니다.")
    else:
        verdict = "지역 기준계 참고만"
        advice = (f"최근접 {best.sta} 가 {best.dist_km:.1f}km 로 발자국 밖입니다"
                  f"(타이 허용 {TIE_MAX_KM}km). 기준점 선정 근거와 지역 연직 기준"
                  f"({best.up_vel_mm_yr:+.2f} mm/yr)으로만 쓰고, 절대 침하로는 읽지 마세요. "
                  "결과 검증은 --gnss-validate 의 수직 대조로 합니다.")

    def _d(c: GnssAnchorCandidate) -> dict:
        return {"sta": c.sta, "lat": c.lat, "lon": c.lon, "dist_km": c.dist_km,
                "span_yr": c.span_yr, "n_epochs": c.n_epochs,
                "up_vel_mm_yr": c.up_vel_mm_yr, "up_scatter_mm": c.up_scatter_mm,
                "score": c.score, "rejected": c.rejected}

    return GnssAnchor(bridge_lat=round(lat, 6), bridge_lon=round(lon, 6), max_km=max_km,
                      candidates=[_d(c) for c in cands], best=(_d(best) if best else None),
                      can_tie_absolute=can_tie,
                      datum_up_mm_yr=(best.up_vel_mm_yr if best else None),
                      verdict=verdict, advice=advice)
