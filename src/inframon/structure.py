"""교량 구조 프로파일 — 교량마다 다른 제원으로 맞춤형 PINN을 돌리기 위한 그릇.

PINN real 은 지금까지 하드코딩된 가정(강재 E=2.1e11·단면·질량·자중)을 썼다. 이 모듈은
그 가정을 **교량 제원**(공공데이터/OSM 또는 사용자 입력)으로 대체한다. PINN 은
`resolve_profile(cfg, xyz)` 로 프로파일을 받아 E/단면/질량/자중/경계조건을 쓴다.

미지정이면 강재 거더교 기본값(기존 동작과 동일) → 골든 회귀 안전.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel, Field

BRIDGE_TYPES = ("girder", "cable_stayed", "suspension", "arch", "truss", "continuous_girder")

# 재료별 영률 E [Pa] (대표값)
MATERIAL_E = {
    "steel": 2.1e11,
    "concrete": 3.0e10,
    "prestressed_concrete": 3.4e10,
    "reinforced_concrete": 2.7e10,
}
# 재료별 단위길이 질량 ρA [kg/m] (대표값 — 폭/단면 따라 큰 편차)
MATERIAL_RHO_A = {
    "steel": 1.0e4,
    "concrete": 2.4e4,
    "prestressed_concrete": 2.4e4,
    "reinforced_concrete": 2.4e4,
}


class BridgeProfile(BaseModel):
    """한 교량의 구조 제원 — 맞춤형 PINN 입력. 모든 값은 선택(미지정 시 기본 가정)."""

    name: str | None = None
    bridge_type: str = "girder"          # BRIDGE_TYPES
    material: str = "steel"
    length_m: float | None = None        # 스팬[m] (None 이면 xyz 에서 추정)
    width_m: float | None = None
    youngs_modulus: float | None = None  # E[Pa] (None 이면 material 표)
    section_depth_m: float = 1.0         # 단면 높이[m] (변형률 = -y·곡률, y=depth/2)
    mass_per_len: float | None = None    # ρA[kg/m] (None 이면 material 표)
    load_per_len: float = 1.0e4          # 자중 등 분포하중 q[N/m]
    boundary: str = "simply_supported"   # simply_supported / continuous / fixed
    source: str = "default"              # default / osm / data_go_kr / manual
    extra: dict[str, Any] = Field(default_factory=dict)

    def youngs(self) -> float:
        if self.youngs_modulus is not None:
            return float(self.youngs_modulus)
        return MATERIAL_E.get(self.material, 2.1e11)

    def rho_a(self) -> float:
        if self.mass_per_len is not None:
            return float(self.mass_per_len)
        return MATERIAL_RHO_A.get(self.material, 1.0e4)

    def half_depth(self) -> float:
        return max(self.section_depth_m, 1e-3) / 2.0


def _span_from_xyz(xyz: np.ndarray) -> float:
    """xyz 의 최대 평면 확장 → 스팬[m] 추정 (lon/lat 로 보이면 degree→m)."""
    xy = np.asarray(xyz)[:, :2]
    ext = float(max(np.ptp(xy[:, 0]), np.ptp(xy[:, 1])))
    return ext * 111000.0 if ext < 1.0 else ext


def resolve_profile(cfg: Any, xyz: np.ndarray | None = None) -> BridgeProfile:
    """cfg.bridge_profile(dict 또는 BridgeProfile)에서 프로파일을 만든다.

    미지정이면 강재 거더교 기본값. length_m 가 비면 xyz 에서 스팬을 채운다.
    """
    spec = getattr(cfg, "bridge_profile", None)
    if isinstance(spec, BridgeProfile):
        prof = spec.model_copy()
    elif isinstance(spec, dict):
        prof = BridgeProfile.model_validate(spec)
    else:
        prof = BridgeProfile()
    if prof.length_m is None and xyz is not None:
        prof = prof.model_copy(update={"length_m": _span_from_xyz(xyz)})
    return prof
