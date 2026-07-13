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
    rmse: float
    mae: float
    bias: float                 # 평균(InSAR − 기준)
    pearson_r: float
    max_dist_m: float
    tolerance_mm: float
    passed: bool                # RMSE ≤ tolerance
    per_point: list = field(default_factory=list)   # [{lon,lat,ref,insar,resid,dist_m}]

    def summary(self) -> str:
        s = "✅ 통과" if self.passed else "❌ 초과"
        return (f"검증: 정합 {self.n_matched}/{self.n_reference} · RMSE {self.rmse:.2f} · "
                f"MAE {self.mae:.2f} · bias {self.bias:+.2f} · r {self.pearson_r:.3f} "
                f"(허용 {self.tolerance_mm:.1f}) {s}")

    def as_dict(self) -> dict:
        return {"n_reference": self.n_reference, "n_matched": self.n_matched,
                "rmse": round(self.rmse, 3), "mae": round(self.mae, 3),
                "bias": round(self.bias, 3), "pearson_r": round(self.pearson_r, 3),
                "tolerance_mm": self.tolerance_mm, "passed": self.passed}


def load_reference_csv(path: str | Path, *, kind: str = "velocity",
                       vertical: bool = False) -> Reference:
    """기준 CSV(lon,lat,value[,...]) → Reference. 헤더 lon/lat/value 자동 인식."""
    lonlat, values = [], []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise ValueError(f"빈 기준 CSV: {path}")
    hdr = [h.strip().lower() for h in rows[0]]
    has_header = any(h in ("lon", "longitude", "lat", "latitude", "value") for h in hdr)
    ix = {}
    if has_header:
        for name, keys in (("lon", ("lon", "longitude")), ("lat", ("lat", "latitude")),
                           ("val", ("value", "velocity", "disp", "displacement"))):
            ix[name] = next((i for i, h in enumerate(hdr) if h in keys), None)
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
    return Reference(lonlat=lonlat, values=values, kind=kind, vertical=vertical, source=str(path))


def _dist_m(a, b, lat0):
    return math.hypot((a[0] - b[0]) * math.cos(math.radians(lat0)), a[1] - b[1]) * 111000.0


def validate(insar_lonlat, insar_values, reference: Reference, *,
             insar_incidence=None, max_dist_m: float = 50.0,
             tolerance_mm: float = 5.0, project_to_los: bool = False) -> ValidationResult:
    """InSAR 값(점별) 을 기준점에 최근접 정합해 검증 지표 산출.

    project_to_los=True 이고 reference.vertical 이면 기준 연직값을 cos(입사각)로 LOS 투영해
    비교(입사각 필요). 정합 거리 > max_dist_m 인 기준점은 제외.
    """
    import numpy as np

    il = [(float(p[0]), float(p[1])) for p in insar_lonlat]
    iv = np.asarray(insar_values, dtype=float)
    inc = None if insar_incidence is None else np.asarray(insar_incidence, dtype=float)
    lat0 = float(np.mean([p[1] for p in il])) if il else 0.0

    refs, ins, per = [], [], []
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
        refs.append(rval); ins.append(ival)
        per.append({"lon": rp[0], "lat": rp[1], "ref": round(rval, 3),
                    "insar": round(ival, 3), "resid": round(ival - rval, 3),
                    "dist_m": round(best_d, 1)})

    n = len(refs)
    if n == 0:
        return ValidationResult(len(reference.values), 0, float("nan"), float("nan"),
                                float("nan"), float("nan"), max_dist_m, tolerance_mm, False, per)
    refs = np.asarray(refs); ins = np.asarray(ins); resid = ins - refs
    rmse = float(np.sqrt(np.mean(resid ** 2)))
    mae = float(np.mean(np.abs(resid)))
    bias = float(np.mean(resid))
    r = float(np.corrcoef(refs, ins)[0, 1]) if n >= 2 and refs.std() > 0 and ins.std() > 0 else float("nan")
    return ValidationResult(len(reference.values), n, rmse, mae, bias, r,
                            max_dist_m, tolerance_mm, rmse <= tolerance_mm, per)


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
        inc = ins["incidenceAngle"][()] if "incidenceAngle" in ins else None
    if lonlat is None and xyz is not None:
        lonlat = xyz[:, :2]                     # 폴백(투영좌표일 수 있음)
    if lonlat is None:
        raise ValueError("project.h5 /insar 에 좌표(pixel_lonlat/xyz)가 없습니다.")

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
