"""InSAR 점을 **교량 위에** 올린다 — 점별 고도(z) 결정.

트윈을 만들 때 z 를 주지 않으면 점이 전부 0m 에 깔린다. 실제로 청양교 산출물이 그랬다:
48개 점이 z=0 인데 그 지점 지면 표고는 93m — 즉 **교량보다 93m 아래 지하에** 점군이
떠 있었다. 3D 로 보면 다리와 데이터가 따로 논다.

여기서는 쓸 수 있는 고도 원천을 **좋은 것부터** 시도하고, 무엇을 썼는지 반드시 남긴다.

  ① IFC 부재 상단 Z      가장 정확 — BIM 이 있으면 그 데크 레벨에 얹는다
  ② PSI 잔차높이(DEM+Δh) 순수 InSAR — 산란체 실고도. B⊥ 가 있어야 한다
  ③ 지형 표고 + 형하고    DEM(지면) + 교량높이(표준데이터) = 데크 근사. **좌표만 있으면 된다**
  ④ 트랙 height          SARvey/MiaplPy 가 준 지오메트리 고도(대개 지면)
  ⑤ 평면(0m)            위가 다 없을 때만. "얹지 못했다"는 사실을 남긴다

③이 이 프로젝트의 기본값이다 — 임의 교량에서 IFC 도 B⊥ 도 없는 게 정상이기 때문이다.
지면에 형하고를 더하면 데크 높이의 실용적 근사가 되고, 근거(표준데이터 교량높이)가 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

# 형하고(교량높이)를 모를 때의 기본 가정[m]. 국내 도로교 최소 형하공간(4.5m) 근처.
DEFAULT_CLEARANCE_M = 4.5


@dataclass
class DeckZ:
    """점별 고도와 그 근거."""

    z: np.ndarray
    source: str                       # ifc_element_top | psi_residual_height | dem+clearance | track_height | flat
    detail: str = ""
    ground_m: float | None = None     # 대표 지면 표고
    clearance_m: float | None = None  # 더한 형하고
    meta: dict = field(default_factory=dict)

    def describe(self) -> str:
        bits = [self.source]
        if self.ground_m is not None:
            bits.append(f"지면 {self.ground_m:.0f}m")
        if self.clearance_m:
            bits.append(f"형하고 +{self.clearance_m:.1f}m")
        if self.z.size:
            bits.append(f"z {np.nanmin(self.z):.0f}~{np.nanmax(self.z):.0f}m")
        return " · ".join(bits) + (f" ({self.detail})" if self.detail else "")


def deck_elevation(lonlat, *, element_z=None, psi_elev=None, track_height=None,
                   clearance_m: float | None = None, dem_fn=None,
                   allow_network: bool = True,
                   element_z_datum: float | None = None) -> DeckZ:
    """점별 데크 고도[m]. 좋은 원천부터 시도하고 무엇을 썼는지 남긴다.

    lonlat: [N,2] (lon, lat). 나머지는 있으면 쓰고 없으면 다음 후보로 내려간다.

    `element_z` 는 **IFC 로컬 z**(대개 지면 0 기준)다. 절대고도로 쓰려면 그 원점의
    표고(`element_z_datum`, 보통 IfcMapConversion 의 OrthogonalHeight)를 더해야 한다.
    안 더하면 점이 다시 지면 아래로 내려간다 — 청양교에서 12.1m(로컬)를 절대고도로 써
    93m 만큼 어긋났다.
    """
    lonlat = np.asarray(lonlat, dtype=float)
    n = lonlat.shape[0]

    if element_z is not None and np.isfinite(np.asarray(element_z, float)).any():
        ez = np.asarray(element_z, float)
        z = np.nan_to_num(ez, nan=float(np.nanmedian(ez)))
        if element_z_datum is None:
            # 원점 표고를 모르면 로컬 z 를 절대고도로 쓸 수 없다. 지면 표고를 구해 더한다.
            g = _ground_elevation(lonlat, dem_fn=dem_fn, allow_network=allow_network)
            if g is None:
                return DeckZ(z=z, source="ifc_element_top",
                             detail="IFC 부재 상단(로컬 z — 원점 표고 미상, 절대고도 아님)")
            element_z_datum = g
        z = z + float(element_z_datum)
        return DeckZ(z=z, source="ifc_element_top", ground_m=float(element_z_datum),
                     detail=f"IFC 부재 상단 + 원점 표고 {element_z_datum:.0f}m")

    if psi_elev is not None and np.isfinite(np.asarray(psi_elev, float)).any():
        z = np.nan_to_num(np.asarray(psi_elev, float), nan=0.0)
        return DeckZ(z=z, source="psi_residual_height", detail="DEM + PSI Δh(순수 InSAR)")

    clear = float(clearance_m) if clearance_m else None

    if track_height is not None:
        th = np.asarray(track_height, float)
        if th.size == n and np.isfinite(th).any():
            add = clear if clear is not None else DEFAULT_CLEARANCE_M
            z = np.nan_to_num(th, nan=float(np.nanmedian(th))) + add
            return DeckZ(z=z, source="track_height", clearance_m=add,
                         ground_m=float(np.nanmedian(th)),
                         detail="트랙 지오메트리 고도 + 형하고")

    ground = _ground_elevation(lonlat, dem_fn=dem_fn, allow_network=allow_network)
    if ground is not None:
        add = clear if clear is not None else DEFAULT_CLEARANCE_M
        z = np.full(n, ground + add, dtype=float)
        return DeckZ(z=z, source="dem+clearance", ground_m=ground, clearance_m=add,
                     detail=("표준데이터 교량높이" if clear is not None
                             else f"형하고 미상 → 기본 {DEFAULT_CLEARANCE_M}m 가정"))

    return DeckZ(z=np.zeros(n), source="flat",
                 detail="고도 원천 없음 — 점이 지면(0m)에 깔린다. 교량 위가 아니다")


def _ground_elevation(lonlat, *, dem_fn=None, allow_network: bool = True) -> float | None:
    """대표 지면 표고[m]. 교량 규모(수십~수백 m)에서는 한 점 대표값으로 충분하다."""
    if lonlat.size == 0:
        return None
    lat = float(np.median(lonlat[:, 1]))
    lon = float(np.median(lonlat[:, 0]))
    fn = dem_fn
    if fn is None:
        if not allow_network:
            return None
        from .bridge_meta import _fetch_elevation as fn        # Open-Meteo(키 불필요)
    try:
        got = fn([lat], [lon])
    except Exception:  # noqa: BLE001 — 표고 조회 실패는 트윈을 막지 않는다
        return None
    vals = [v for v in (got or []) if v is not None and np.isfinite(v)]
    return float(vals[0]) if vals else None


def clearance_from_profile(profile: Any) -> float | None:
    """교량 제원에서 형하고를 찾는다 — 표준데이터 '교량높이'·'하부통과제한높이'.

    ⚠️ 교량높이는 거더 단면높이가 아니라 **형하공간·교각높이** 성격이다(public_data 주석).
    데크 레벨 근사에는 그래서 이 값이 맞다.
    """
    ex = getattr(profile, "extra", None) or {}
    for key in ("height_m", "clearance_m"):
        v = ex.get(key)
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0.5 <= f <= 100.0:                 # 형하고로 말이 되는 범위
            return f
    return None
