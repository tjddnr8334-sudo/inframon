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

BRIDGE_TYPES = ("girder", "box_girder", "rahmen", "cable_stayed", "suspension",
                "arch", "truss", "continuous_girder")

# 형식 → 재료 추론(OSM material 태그 없을 때). PSC box·아치·라멘은 콘크리트가 표준.
_TYPE_MATERIAL = {
    "box_girder": "prestressed_concrete", "rahmen": "reinforced_concrete",
    "arch": "reinforced_concrete", "truss": "steel",
    "cable_stayed": "steel", "suspension": "steel", "girder": "steel",
}
# 형식 → 단면높이/스팬 비(대표). 거더 L/20, box L/18, 트러스 L/10, 아치 L/30, 사장·현수 데크 L/40.
_TYPE_DEPTH_RATIO = {
    "girder": 1 / 20, "box_girder": 1 / 18, "rahmen": 1 / 22, "truss": 1 / 10,
    "arch": 1 / 30, "cable_stayed": 1 / 40, "suspension": 1 / 45,
}


def infer_structural_defaults(bridge_type: str, *, has_material_tag: bool,
                              length_m: float | None, max_span_m: float | None) -> dict:
    """형식(+경간)으로 재료·단면높이·경계·자중을 추론(명시값 없을 때 채움).

    - 재료: material 태그 없으면 형식별 표준(box/아치/라멘→콘크리트, 트러스/케이블→강재).
    - 단면높이: 형식별 (최대경간 또는 스팬)×비율, [0.4, 8]m 클램프.
    - 경계: 라멘→fixed, 다경간(연장/최대경간>1.5)→continuous, 그 외 simply_supported.
    - 자중 q = ρA·g (재료 밀도 반영).
    """
    out: dict = {}
    if not has_material_tag:
        out["material"] = _TYPE_MATERIAL.get(bridge_type, "steel")
    mat = out.get("material", "steel") if not has_material_tag else None
    span = max_span_m or length_m
    if span:
        ratio = _TYPE_DEPTH_RATIO.get(bridge_type, 1 / 20)
        out["section_depth_m"] = round(min(max(span * ratio, 0.4), 8.0), 2)
    if bridge_type == "rahmen":
        out["boundary"] = "fixed"
    elif length_m and max_span_m and max_span_m > 0 and length_m / max_span_m > 1.5:
        out["boundary"] = "continuous"
    else:
        out["boundary"] = "simply_supported"
    rho = MATERIAL_RHO_A.get(mat or "steel", 1.0e4)
    out["load_per_len"] = round(rho * 9.81, 1)          # 자중 [N/m]
    return out

# 재료별 영률 E [Pa] (대표값)
MATERIAL_E = {
    "steel": 2.1e11,
    "concrete": 3.0e10,
    "prestressed_concrete": 3.4e10,
    "reinforced_concrete": 2.7e10,
}
# 재료별 단위길이 질량 ρA [kg/m] (대표값 — 폭/단면 없을 때 폴백)
MATERIAL_RHO_A = {
    "steel": 1.0e4,
    "concrete": 2.4e4,
    "prestressed_concrete": 2.4e4,
    "reinforced_concrete": 2.4e4,
}
# 재료 밀도 ρ [kg/m³] — 폭·높이 알 때 단면적 A 로 ρA=ρ·A 정밀화
MATERIAL_DENSITY = {
    "steel": 7850.0, "concrete": 2500.0,
    "prestressed_concrete": 2500.0, "reinforced_concrete": 2500.0,
}
# 형식별 단면 충실도(총 폭×높이 대비 실제 재료 단면적 비) — 박스·트러스는 속이 비어 작다.
_TYPE_AREA_FACTOR = {
    "box_girder": 0.10, "girder": 0.12, "rahmen": 0.45, "arch": 0.30,
    "truss": 0.05, "cable_stayed": 0.15, "suspension": 0.12,
}
# 형식별 단면2차모멘트 효율(직사각형 wd³/12 대비) — 박스·트러스는 플랜지가 멀어 효율↑.
_TYPE_I_FACTOR = {
    "box_girder": 0.65, "girder": 0.45, "rahmen": 0.55, "arch": 0.50,
    "truss": 0.70, "cable_stayed": 0.40, "suspension": 0.35,
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

    def section_area_m2(self) -> float | None:
        """단면적 A [m²] — 폭·높이·형식 충실도. 폭 미상이면 None."""
        if self.width_m and self.section_depth_m:
            fac = _TYPE_AREA_FACTOR.get(self.bridge_type, 0.15)
            return max(float(self.width_m), 0.5) * float(self.section_depth_m) * fac
        return None

    def second_moment_I_m4(self) -> float | None:
        """단면2차모멘트 I [m⁴] — 직사각형 wd³/12 × 형식효율. 폭 미상이면 None."""
        if self.width_m and self.section_depth_m:
            rect = float(self.width_m) * float(self.section_depth_m) ** 3 / 12.0
            return rect * _TYPE_I_FACTOR.get(self.bridge_type, 0.5)
        return None

    def geometric_EI(self) -> float | None:
        """기하 EI = E·I [N·m²] — 제원 기반(식별 EI 와 비교/사전값). 폭 미상이면 None."""
        I = self.second_moment_I_m4()
        return self.youngs() * I if I is not None else None

    def rho_a(self) -> float:
        """단위길이 질량 ρA [kg/m] — 폭·높이 알면 ρ·A(정밀), 없으면 재료 대표값(폴백)."""
        if self.mass_per_len is not None:
            return float(self.mass_per_len)
        A = self.section_area_m2()
        if A is not None:
            return MATERIAL_DENSITY.get(self.material, 7850.0) * A
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
