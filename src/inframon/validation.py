"""현장 검증 프레임워크 — InSAR/PINN 결과를 **기준 데이터(계측·상용 FEM)** 와 대조.

⚠️ **실제 기준 데이터(레벨링·GNSS·in-place 센서 변위, 또는 상용 FEM 변위장)는 현장/외부
에서 확보해야 한다.** 이 모듈은 그 기준이 주어졌을 때 InSAR LOS 속도/변위(또는 PINN
연직)를 점별로 정합해 RMSE·MAE·bias·상관(Pearson)·정합률을 계산하는 **비교 계층**이다.
연구 프로토타입을 "검증 가능" 상태로 만든다(README: 현장·상용FEM 검증 미수행).

기준 CSV 형식: `lon,lat,value[,unit]` (value = 속도[mm/yr] 또는 변위[mm]).
계측이 연직이면 project_to_los=True + 입사각으로 LOS 투영해 비교.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Reference:
    """검증 기준점 — 계측 또는 FEM."""

    lonlat: list                # [(lon,lat), ...]
    values: list                # 대응 값(속도 mm/yr 또는 변위 mm)
    kind: str = "velocity"      # velocity | displacement
    vertical: bool = False      # 값이 연직이면 True(LOS 투영 필요)
    source: str = "reference"


@dataclass
class ValidationResult:
    n_reference: int
    n_matched: int
    rmse: float                 # 원 잔차 RMSE(공통 오프셋 포함 — 절대 정합)
    mae: float
    bias: float                 # 평균(InSAR − 기준) = 공통 레퍼런스 프레임 오프셋
    pearson_r: float
    max_dist_m: float
    tolerance_mm: float
    passed: bool                # 판정 근거(아래 rmse_detrended 기준)
    per_point: list = field(default_factory=list)   # [{lon,lat,ref,insar,resid,dist_m}]
    # InSAR 는 **상대** 변위(기준점 대비), 지상 실측은 절대값이라 공통 오프셋(bias)이 섞인다.
    # bias 를 뺀 잔차가 InSAR 의 실제 상대 정확도다 — GNSS 검증에서 배운 것과 같은 원리.
    rmse_detrended: float = float("nan")   # bias 제거 후 RMSE ← **판정·정확도 지표**
    match_dist_median_m: float = float("nan")
    n_reference_unmatched: int = 0
    verdict: str = ""

    def summary(self) -> str:
        s = "✅ 통과" if self.passed else "❌ 초과"
        return (f"검증: 정합 {self.n_matched}/{self.n_reference} · "
                f"RMSE(bias제거) {self.rmse_detrended:.2f} · bias(프레임오프셋) {self.bias:+.2f} · "
                f"r {self.pearson_r:.3f} (허용 {self.tolerance_mm:.1f}mm) {s}")

    def as_dict(self) -> dict:
        return {"n_reference": self.n_reference, "n_matched": self.n_matched,
                "n_reference_unmatched": self.n_reference_unmatched,
                "rmse": round(self.rmse, 3), "rmse_detrended": round(self.rmse_detrended, 3),
                "mae": round(self.mae, 3), "bias": round(self.bias, 3),
                "pearson_r": round(self.pearson_r, 3),
                "match_dist_median_m": round(self.match_dist_median_m, 2),
                "tolerance_mm": self.tolerance_mm, "passed": self.passed,
                "verdict": self.verdict}


def load_reference_csv(path: str | Path, *, kind: str = "velocity",
                       vertical: bool = False, origin: str | None = None) -> Reference:
    """기준 CSV(lon,lat,value[,...]) → Reference. 헤더 lon/lat/value 자동 인식.

    `origin` 을 주면 처음 두 열을 **한국 측량 좌표(X,Y)** 로 보고 그 원점계로 WGS84 변환한다
    (중부원점 등). 지상 실측(수준측량·GNSS·코너리플렉터)은 흔히 측량 좌표로 나오므로,
    변환 없이 InSAR(WGS84)와 정합하려다 다 실패하는 걸 막는다. None 이면 lon/lat 그대로.
    """
    lonlat, values = [], []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise ValueError(f"빈 기준 CSV: {path}")
    hdr = [h.strip().lower() for h in rows[0]]
    _lon_keys = ("lon", "longitude", "x", "easting", "e")
    _lat_keys = ("lat", "latitude", "y", "northing", "n")
    _val_keys = ("value", "velocity", "vel", "disp", "displacement", "v", "z")
    has_header = any(h in _lon_keys + _lat_keys + ("value", "velocity") for h in hdr)
    ix = {}
    if has_header:
        for name, keys in (("lon", _lon_keys), ("lat", _lat_keys), ("val", _val_keys)):
            ix[name] = next((i for i, h in enumerate(hdr) if h in keys), None)
        # 위치 열을 못 찾았으면(예: 헤더가 값 이름만) 처음 두 열을 위치로 가정
        if ix["lon"] is None:
            ix["lon"] = 0
        if ix["lat"] is None:
            ix["lat"] = 1
        if ix["val"] is None:
            ix["val"] = 2
        body = rows[1:]
    else:
        ix = {"lon": 0, "lat": 1, "val": 2}
        body = rows
    for r in body:
        if len(r) <= max(v for v in ix.values() if v is not None):
            continue
        try:
            lonlat.append((float(r[ix["lon"]]), float(r[ix["lat"]])))
            values.append(float(r[ix["val"]]))
        except (ValueError, TypeError):
            continue
    src = str(path)
    if origin is not None:
        # 처음 두 열 = 측량 좌표(X=Easting, Y=Northing) → WGS84 (lon,lat)
        from .korea_crs import to_wgs84
        conv = []
        for x, y in lonlat:
            w = to_wgs84(x, y, origin)
            conv.append((w.lon, w.lat))
        lonlat = conv
        src += f" (원점계 {origin} → WGS84)"
    return Reference(lonlat=lonlat, values=values, kind=kind, vertical=vertical, source=src)


def _dist_m(a, b, lat0):
    return math.hypot((a[0] - b[0]) * math.cos(math.radians(lat0)), a[1] - b[1]) * 111000.0


def validate(insar_lonlat, insar_values, reference: Reference, *,
             insar_incidence=None, max_dist_m: float = 50.0,
             tolerance_mm: float = 5.0, project_to_los: bool = False,
             align_frame: bool = True) -> ValidationResult:
    """InSAR 값(점별) 을 기준점에 최근접 정합해 검증 지표 산출.

    project_to_los=True 이고 reference.vertical 이면 기준 연직값을 cos(입사각)로 LOS 투영해
    비교(입사각 필요). 정합 거리 > max_dist_m 인 기준점은 제외.

    align_frame=True(기본): InSAR 는 국소 기준점 대비 **상대** 변위라 지상 실측(절대)과
    공통 오프셋(bias)이 있다. 판정은 그 bias 를 뺀 잔차(rmse_detrended)로 한다 —
    프레임 오프셋을 InSAR 오차로 오인하지 않기 위함. bias 자체는 따로 보고한다.
    """
    import numpy as np

    il = [(float(p[0]), float(p[1])) for p in insar_lonlat]
    iv = np.asarray(insar_values, dtype=float)
    inc = None if insar_incidence is None else np.asarray(insar_incidence, dtype=float)
    lat0 = float(np.mean([p[1] for p in il])) if il else 0.0

    refs, ins, per, dists = [], [], [], []
    for k, (rp, rv) in enumerate(zip(reference.lonlat, reference.values)):
        # 최근접 InSAR 점
        best_i, best_d = -1, float("inf")
        for i, ip in enumerate(il):
            d = _dist_m(rp, ip, lat0)
            if d < best_d:
                best_d, best_i = d, i
        if best_i < 0 or best_d > max_dist_m:
            continue
        ival = float(iv[best_i])
        rval = float(rv)
        if project_to_los and reference.vertical and inc is not None:
            rval = rval * math.cos(math.radians(float(inc[best_i])))   # 연직 → LOS
        refs.append(rval); ins.append(ival); dists.append(best_d)
        per.append({"lon": rp[0], "lat": rp[1], "ref": round(rval, 3),
                    "insar": round(ival, 3), "resid": round(ival - rval, 3),
                    "dist_m": round(best_d, 1)})

    n = len(refs)
    n_ref = len(reference.values)
    if n == 0:
        r = ValidationResult(n_ref, 0, float("nan"), float("nan"), float("nan"),
                             float("nan"), max_dist_m, tolerance_mm, False, per)
        r.n_reference_unmatched = n_ref
        r.verdict = (f"정합된 기준점 0/{n_ref} — 모두 {max_dist_m:.0f}m 밖. 좌표계가 맞는지"
                     "(측량좌표면 WGS84 변환 필요), max_dist 가 적절한지 확인하세요.")
        return r
    refs = np.asarray(refs); ins = np.asarray(ins); resid = ins - refs
    rmse = float(np.sqrt(np.mean(resid ** 2)))
    mae = float(np.mean(np.abs(resid)))
    bias = float(np.mean(resid))
    # 공통 프레임 오프셋(bias) 제거 후 잔차 — InSAR 의 실제 상대 정확도.
    resid_dt = resid - bias if align_frame else resid
    rmse_dt = float(np.sqrt(np.mean(resid_dt ** 2)))
    r_p = float(np.corrcoef(refs, ins)[0, 1]) if n >= 2 and refs.std() > 0 and ins.std() > 0 else float("nan")
    judge = rmse_dt if align_frame else rmse
    out = ValidationResult(n_ref, n, rmse, mae, bias, r_p, max_dist_m, tolerance_mm,
                           judge <= tolerance_mm, per)
    out.rmse_detrended = rmse_dt
    out.match_dist_median_m = float(np.median(dists))
    out.n_reference_unmatched = n_ref - n
    # 판정 근거를 말로
    if judge <= tolerance_mm:
        out.verdict = (f"프레임 오프셋 {bias:+.2f}mm 제거 후 잔차 {rmse_dt:.2f}mm ≤ 허용 "
                       f"{tolerance_mm:.1f}mm — InSAR 상대 정확도 지상실측과 정합")
    else:
        out.verdict = (f"잔차 {rmse_dt:.2f}mm > 허용 {tolerance_mm:.1f}mm — 정합 부족"
                       f"(bias {bias:+.2f}mm 는 프레임 오프셋이라 별개). 좌표·기준점·보정 점검")
    return out


def validate_project(project_h5: str | Path, reference: Reference, *,
                     max_dist_m: float = 50.0, tolerance_mm: float = 5.0,
                     project_to_los: bool = False) -> ValidationResult:
    """project.h5 의 /insar(LOS 속도·변위)를 기준과 대조. kind=velocity 면 LOS 선형속도."""
    import h5py
    import numpy as np

    with h5py.File(str(project_h5), "r") as f:
        ins = f["insar"]
        xyz = ins["xyz"][()] if "xyz" in ins else None
        lonlat = None
        if "pixel_lonlat" in ins:
            lonlat = ins["pixel_lonlat"][()]
        los = ins["los"][()].astype(float)      # [N,M] mm
        dates = [d.decode() if isinstance(d, bytes) else str(d) for d in ins["date_labels"][()]]
        # 입사각 데이터셋 이름은 파이프라인마다 다르다(incidenceAngle / incidence_deg / incidence).
        # 못 찾으면 연직 투영이 조용히 생략돼 수준측량 연직값을 LOS 와 직접 비교(~29% 오차)하므로
        # 여러 이름을 모두 시도한다.
        inc = None
        for _name in ("incidenceAngle", "incidence_deg", "incidence", "inc_angle"):
            if _name in ins:
                inc = ins[_name][()]
                break
    if lonlat is None and xyz is not None:
        lonlat = xyz[:, :2]                     # 폴백(투영좌표일 수 있음)
    if lonlat is None:
        raise ValueError("project.h5 /insar 에 좌표(pixel_lonlat/xyz)가 없습니다.")
    # 연직(수준측량) 기준을 LOS 투영해 비교하려는데 입사각이 없으면, 조용히 투영을 건너뛰어
    # 연직값을 LOS 와 직접 비교(~29% 낙관)하지 말고 명확히 막는다.
    if project_to_los and reference.vertical and inc is None:
        raise ValueError(
            "연직 기준을 LOS 투영하려면 입사각이 필요합니다. project.h5 /insar 에 "
            "incidenceAngle/incidence_deg 가 없습니다 — 트랙 인제스트 시 입사각을 보존했는지 "
            "확인하거나, 기준을 LOS 로 미리 투영해 vertical=False 로 주세요.")

    if reference.kind == "velocity":
        from datetime import datetime
        d0 = datetime.strptime(dates[0], "%Y%m%d")
        yr = np.array([(datetime.strptime(d, "%Y%m%d") - d0).days for d in dates]) / 365.25
        A = np.vstack([yr, np.ones_like(yr)]).T
        values = np.linalg.lstsq(A, los.T, rcond=None)[0][0]      # mm/yr
    else:
        values = los[:, -1] - los[:, 0]                          # 총 변위 mm
    return validate([(p[0], p[1]) for p in lonlat], values, reference,
                    insar_incidence=inc, max_dist_m=max_dist_m,
                    tolerance_mm=tolerance_mm, project_to_los=project_to_los)
