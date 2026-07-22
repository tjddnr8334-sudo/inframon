"""BIM(IFC) 좌표 정합 — 지도 CRS ↔ IFC 로컬 엔지니어링 좌표계.

디지털 트윈에서 위성 관측과 BIM 을 "합친다"는 것의 90% 는 **좌표계 정합**이다.
InSAR 점은 지리/투영 좌표(EPSG:4326·5179·5186…)에 있고, IFC 는 원점·회전·표고
기준이 제각각인 **로컬 엔지니어링 좌표계**에 있다. 이 변환이 틀리면 뒤의 부재
연결·Pset 주입은 전부 조용히 틀린다.

두 경로를 지원한다.

1. **IfcMapConversion** — IFC4 표준(`IfcCoordinateOperation`). 파일에 있으면 그대로 쓴다.
2. **관측점 정합(Helmert)** — IfcMapConversion 이 없을 때(국내 실무에서 흔하다) 측량
   기준점 쌍으로 상사변환을 최소자승 적합한다. **RMS 잔차를 반드시 같이 보고**하고,
   임계 초과면 정합 실패로 처리한다 — 조용히 틀린 정합보다 실패가 낫다.

표고 주의: IFC `OrthogonalHeight` 는 보통 수직기준면(예 인천만 평균해면) 기준이고
InSAR z 는 DEM(타원체고 또는 지오이드고)에서 온다. 한국의 지오이드고는 ~25m 라
그냥 합치면 수십 미터가 어긋난다. 그래서 **표고는 기본적으로 정합에 쓰지 않고**,
기준점에 표고가 있어 오프셋이 적합된 경우에만 3D 를 허용한다.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

# 상사변환 적합의 기본 허용 잔차[m]. 교량 규모(수십~수백 m)에서 이 이상이면
# 기준점 대응이 잘못됐거나 좌표계 가정이 틀린 것이다.
DEFAULT_MAX_RMS_M = 0.5


class AlignmentError(ValueError):
    """좌표 정합 실패 — 기준점 부족·잔차 초과·CRS 불명."""


@dataclass
class MapConversion:
    """IFC4 `IfcMapConversion` — IFC 로컬 → 지도 CRS.

    E = Eastings  + Scale·(x·a − y·o)
    N = Northings + Scale·(x·o + y·a)
    H = OrthogonalHeight + Scale·z
    여기서 (a, o) = (XAxisAbscissa, XAxisOrdinate) 는 로컬 X축의 지도상 방향(정규화).
    """
    eastings: float = 0.0
    northings: float = 0.0
    orthogonal_height: float = 0.0
    x_axis_abscissa: float = 1.0        # cos θ
    x_axis_ordinate: float = 0.0        # sin θ
    scale: float = 1.0
    target_crs: str | None = None       # IfcProjectedCRS.Name (예 "EPSG:5186")
    source: str = "unknown"             # ifc | control_points | manual
    fit: dict = field(default_factory=dict)   # 적합 품질(잔차 등)

    # ── 축 방향 정규화 ──
    def _axis(self) -> tuple[float, float]:
        a, o = float(self.x_axis_abscissa), float(self.x_axis_ordinate)
        n = math.hypot(a, o)
        if n == 0:
            raise AlignmentError("XAxisAbscissa·XAxisOrdinate 가 모두 0 — 회전축이 정의되지 않습니다")
        return a / n, o / n

    @property
    def rotation_deg(self) -> float:
        a, o = self._axis()
        return math.degrees(math.atan2(o, a))

    def to_map(self, local: np.ndarray) -> np.ndarray:
        """IFC 로컬 [N,2|3] → 지도 CRS [N,2|3]."""
        p = np.atleast_2d(np.asarray(local, dtype=np.float64))
        a, o = self._axis()
        s = float(self.scale)
        e = self.eastings + s * (p[:, 0] * a - p[:, 1] * o)
        n = self.northings + s * (p[:, 0] * o + p[:, 1] * a)
        if p.shape[1] >= 3:
            h = self.orthogonal_height + s * p[:, 2]
            return np.stack([e, n, h], axis=1)
        return np.stack([e, n], axis=1)

    def to_local(self, map_xy: np.ndarray) -> np.ndarray:
        """지도 CRS [N,2|3] → IFC 로컬 [N,2|3] (to_map 의 정확한 역변환)."""
        p = np.atleast_2d(np.asarray(map_xy, dtype=np.float64))
        a, o = self._axis()
        s = float(self.scale)
        if s == 0:
            raise AlignmentError("scale 이 0 입니다")
        de = (p[:, 0] - self.eastings) / s
        dn = (p[:, 1] - self.northings) / s
        x = de * a + dn * o                     # R⁻¹ = Rᵀ (회전은 직교)
        y = -de * o + dn * a
        if p.shape[1] >= 3:
            z = (p[:, 2] - self.orthogonal_height) / s
            return np.stack([x, y, z], axis=1)
        return np.stack([x, y], axis=1)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rotation_deg"] = round(self.rotation_deg, 6)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MapConversion":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


def fit_map_conversion(
    local_pts: np.ndarray,
    map_pts: np.ndarray,
    *,
    fix_scale: bool = True,
    max_rms_m: float = DEFAULT_MAX_RMS_M,
    target_crs: str | None = None,
) -> MapConversion:
    """측량 기준점 쌍으로 IfcMapConversion 을 최소자승 적합(2D 상사변환).

    Args:
        local_pts: [K,2|3] IFC 로컬 좌표
        map_pts:   [K,2|3] 대응하는 지도 CRS 좌표
        fix_scale: True 면 축척을 1 로 고정(강체). BIM 과 측량 모두 미터계이므로
                   보통 축척은 1 이어야 하고, 자유롭게 두면 기준점 오차를 축척이
                   흡수해 정합이 그럴듯해 보이면서 실제로는 틀어진다.
        max_rms_m: 허용 RMS 잔차[m]. 초과하면 AlignmentError.

    표고(3열)가 양쪽에 있으면 높이 오프셋도 적합하고 `fit["height_fitted"]=True` 로
    표시한다 — 이때만 3D 부재 연결이 정당하다.
    """
    L = np.atleast_2d(np.asarray(local_pts, dtype=np.float64))
    M = np.atleast_2d(np.asarray(map_pts, dtype=np.float64))
    if L.shape[0] != M.shape[0]:
        raise AlignmentError(f"기준점 수 불일치 — local {L.shape[0]} vs map {M.shape[0]}")
    k = L.shape[0]
    if k < 2:
        raise AlignmentError(f"2D 상사변환에는 기준점이 최소 2개 필요합니다(입력 {k}개)")

    lc, mc = L[:, :2].mean(axis=0), M[:, :2].mean(axis=0)
    dl, dm = L[:, :2] - lc, M[:, :2] - mc
    denom = float((dl ** 2).sum())
    if denom <= 0:
        raise AlignmentError("기준점이 한 점에 겹쳐 있습니다 — 회전을 결정할 수 없습니다")

    a = float((dl[:, 0] * dm[:, 0] + dl[:, 1] * dm[:, 1]).sum() / denom)
    o = float((dl[:, 0] * dm[:, 1] - dl[:, 1] * dm[:, 0]).sum() / denom)
    scale = math.hypot(a, o)
    if scale <= 0:
        raise AlignmentError("적합된 축척이 0 입니다 — 기준점 대응을 확인하세요")
    if fix_scale:
        a, o, scale = a / scale, o / scale, 1.0
    else:
        a, o = a / scale, o / scale

    tx = float(mc[0] - scale * (lc[0] * a - lc[1] * o))
    ty = float(mc[1] - scale * (lc[0] * o + lc[1] * a))

    height_fitted = L.shape[1] >= 3 and M.shape[1] >= 3
    h0 = float(np.mean(M[:, 2] - scale * L[:, 2])) if height_fitted else 0.0

    mc_obj = MapConversion(eastings=tx, northings=ty, orthogonal_height=h0,
                           x_axis_abscissa=a, x_axis_ordinate=o, scale=scale,
                           target_crs=target_crs, source="control_points")

    pred = mc_obj.to_map(L[:, :2])
    resid = np.linalg.norm(pred - M[:, :2], axis=1)
    rms = float(np.sqrt(np.mean(resid ** 2)))
    fit = {"n_control_points": int(k), "rms_m": round(rms, 4),
           "max_residual_m": round(float(resid.max()), 4),
           "residuals_m": [round(float(r), 4) for r in resid],
           "fix_scale": bool(fix_scale), "height_fitted": bool(height_fitted),
           "rotation_deg": round(mc_obj.rotation_deg, 4)}
    if height_fitted:
        hres = np.abs(M[:, 2] - (h0 + scale * L[:, 2]))
        fit["height_rms_m"] = round(float(np.sqrt(np.mean(hres ** 2))), 4)
    mc_obj.fit = fit

    if rms > max_rms_m:
        raise AlignmentError(
            f"정합 RMS 잔차 {rms:.3f}m > 허용 {max_rms_m}m — 기준점 대응이나 좌표계 가정이 "
            f"틀렸을 가능성이 큽니다(점별 잔차 {fit['residuals_m']}). "
            "조용히 틀린 정합보다 실패가 낫습니다.")
    return mc_obj


def to_ifc_local(
    xyz: np.ndarray,
    mc: MapConversion,
    *,
    source_crs: str,
    use_z: bool = False,
) -> tuple[np.ndarray, dict]:
    """InSAR 점 좌표(임의 CRS) → IFC 로컬 좌표. (좌표, 메타) 반환.

    `source_crs` 가 `mc.target_crs` 와 다르면 pyproj 로 재투영한다.
    `use_z=True` 는 높이 오프셋이 실제로 적합된 경우에만 허용한다 — 수직기준면이
    다르면(타원체고 vs 표고) 수십 미터가 어긋나 부재 연결이 통째로 틀어진다.
    """
    P = np.atleast_2d(np.asarray(xyz, dtype=np.float64))
    meta: dict = {"source_crs": source_crs, "target_crs": mc.target_crs,
                  "reprojected": False, "use_z": bool(use_z)}

    xy = P[:, :2]
    if mc.target_crs and source_crs and source_crs.upper() != mc.target_crs.upper():
        try:
            from pyproj import Transformer
            tr = Transformer.from_crs(source_crs, mc.target_crs, always_xy=True)
            x, y = tr.transform(xy[:, 0], xy[:, 1])
            xy = np.stack([np.asarray(x), np.asarray(y)], axis=1)
            meta["reprojected"] = True
        except ImportError as exc:
            raise AlignmentError(
                f"{source_crs} → {mc.target_crs} 재투영에 pyproj 가 필요합니다") from exc
        except Exception as exc:  # noqa: BLE001 — CRS 코드 오류 등
            raise AlignmentError(f"CRS 재투영 실패({source_crs}→{mc.target_crs}): {exc}") from exc

    if use_z:
        if not mc.fit.get("height_fitted") and mc.source == "control_points":
            raise AlignmentError(
                "표고 오프셋이 적합되지 않아 3D 정합을 쓸 수 없습니다 — 기준점에 표고를 넣거나 "
                "use_z=False(2D 평면 연결)로 진행하세요. "
                "수직기준면 불일치(타원체고 vs 표고)는 한국에서 ~25m 오차가 됩니다.")
        if P.shape[1] < 3:
            raise AlignmentError("use_z=True 인데 입력 좌표에 z 가 없습니다")
        return mc.to_local(np.column_stack([xy, P[:, 2]])), meta
    return mc.to_local(xy), meta
