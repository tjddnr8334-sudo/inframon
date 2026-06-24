"""교량 형식 분류 → SARvey/스택 파라미터 교량특화.

도시 지반침하용 SARvey 기본값(grid 200m·densification 2000m·velocity 0.1m/yr 등)은
교량(길이 수백 m, 열팽창 우세, 주변이 물)에 맞지 않는다. 이 모듈은 레시피의
`BridgeTarget`(OSM 태그 + 길이)로 **형식을 분류**하고, 형식·규모·수계(水系) 맥락에
맞춘 파라미터를 유도한다(`sarvey_config.py` 가 소비).

형식별 특성(사용자 정의):
  - 사장교/현수교 : 대형(1km+)·1종·**해상** 가능성 큼 → water mask 강화, velocity/계절 여유,
                    AOI 버퍼 큼, 기준점=주탑·앵커리지(육상 정착부).
  - 거더교/아치교/특수교 : **하천 횡단** 가능성 → 수면 점 제외(하천 water mask), 중규모,
                    기준점=교대(고정단).

분류는 OSM `bridge:structure` 태그 우선, 불충분하면 길이로 폴백한다. water_context 는
휴리스틱이라 manifest 에 근거를 남겨 운영자가 덮어쓸 수 있게 한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# 형식 식별자
CABLE_STAYED = "cable_stayed"
SUSPENSION = "suspension"
GIRDER = "girder"
ARCH = "arch"
TRUSS = "truss"
SPECIAL = "special"

CLASS_KO = {
    CABLE_STAYED: "사장교", SUSPENSION: "현수교", GIRDER: "거더교",
    ARCH: "아치교", TRUSS: "트러스교", SPECIAL: "특수교",
}
# 케이블 지지계(대형·해상·큰 진동·열팽창) — 공통 튜닝 그룹
_CABLE_CLASSES = (CABLE_STAYED, SUSPENSION)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def classify_bridge(tags: dict[str, str], length_m: float | None = None) -> str:
    """OSM 태그(+길이 폴백)로 교량 형식을 분류한다."""
    s = (tags.get("bridge:structure") or "").lower()
    b = (tags.get("bridge") or "").lower()
    if "suspension" in s:
        return SUSPENSION
    if "cable" in s:                       # cable-stayed / cable_stayed
        return CABLE_STAYED
    if "arch" in s:
        return ARCH
    if "truss" in s:
        return TRUSS
    if "cantilever" in s or "movable" in s or tags.get("bridge:movable"):
        return SPECIAL
    if "beam" in s or "girder" in s or b == "viaduct":
        return GIRDER
    # 태그 불충분 → 길이 기반 폴백: 초장대는 케이블 지지계로 추정.
    if length_m and length_m >= 1000:
        return CABLE_STAYED
    return GIRDER


def water_context_for(bridge_class: str, length_m: float | None) -> str:
    """수계 맥락: 'marine'(해상) | 'river'(하천)."""
    if bridge_class in _CABLE_CLASSES:
        return "marine"
    if length_m and length_m >= 2000:      # 초장대 거더/아치 → 해상 가능성
        return "marine"
    return "river"


def _scale(length_m: float | None) -> str:
    if not length_m:
        return "unknown"
    if length_m >= 1000:
        return "large"
    if length_m >= 300:
        return "medium"
    return "small"


def _water_mask(water_context: str) -> dict:
    if water_context == "marine":
        return {
            "apply": True, "context": "marine", "strength": "strong",
            "sources": [
                "OSM natural=coastline",
                "global water mask (GSHHG / OSM water polygons)",
                "DEM ≈ 0 (해수면)",
            ],
            "valid_region": "deck_buffer_only",
            "note": ("해상 교량 — 바다는 완전 비간섭(decorrelation). 데크 버퍼 밖·수면 점을 "
                     "강하게 마스킹하고 기준점은 육상 정착부(주탑/앵커리지)에 둔다."),
        }
    return {
        "apply": True, "context": "river", "strength": "moderate",
        "sources": ["OSM waterway=*", "OSM natural=water 폴리곤"],
        "valid_region": "deck_buffer_only",
        "note": "하천 횡단 — 수면 점을 제외하고 데크 버퍼 안만 유지. 기준점은 교대(고정단)에 둔다.",
    }


@dataclass
class BridgeProfile:
    """형식·규모·수계에서 유도한 교량특화 처리 파라미터."""

    bridge_class: str
    bridge_class_ko: str
    water_context: str               # marine | river
    scale: str                       # small | medium | large | unknown
    length_m: float

    # ── SARvey MTI 파라미터(교량 제원 유도) ──
    grid_size_m: int                 # P1 후보 격자(작을수록 조밀) — 교량은 작게
    max_distance_to_p1_m: int        # densification 연결 반경 — 교량 길이에 맞춤
    num_connections_to_p1: int       # P2→P1 연결 수(조밀 점망)
    coherence_p1: float              # 1차 점 임계
    coherence_p2: float              # 2차(filtering) 임계
    densification_coherence: float   # 조밀화 임계(강반사체라 하향)
    velocity_bound_m_yr: float       # 선형속도 상한(열팽창/진동 고려 확대)
    seasonal_model: bool             # 계절/열팽창 항 권장
    arc_unwrap_coh: float

    # ── 스택/마스크 ──
    aoi_buffer_deg: float            # AOI 확장(대형/해상 크게)
    deck_buffer_m: int               # 데크 중심선 버퍼(이 안의 점만 유효)
    reference_hint: str              # 기준점 위치 힌트
    water_mask: dict
    rationale: str

    def to_dict(self) -> dict:
        return asdict(self)


def profile_for(target) -> BridgeProfile:
    """`BridgeTarget` → 교량특화 프로파일."""
    length = float(getattr(target, "length_m", None) or 0.0)
    tags = dict(getattr(target, "tags", {}) or {})
    cls = classify_bridge(tags, length)
    wc = water_context_for(cls, length)
    scale = _scale(length)
    cable = cls in _CABLE_CLASSES
    marine = wc == "marine"

    # P1 격자: 교량 길이에 비례하되 15~60m. (200m 기본은 교량에 너무 성김)
    grid = int(_clamp(length / 10 if length else 30, 15, 60))
    # densification 반경: 교량 안에서만 연결(길이의 ~1/3, 200~1000m).
    maxd = int(_clamp(length / 3 if length else 300, 200, 1000))
    nconn = 10 if cable else 8
    # 강반사체라 임계 하향해 조밀화. 단 해상은 수면 오점 방지로 P1 은 엄격 유지.
    p1 = 0.90 if marine else 0.85
    p2 = 0.72 if marine else 0.70
    dens = 0.50 if marine else 0.45
    # 열팽창·구조 진동: 선형속도 상한 확대(케이블계는 더 크게). 기본 0.1 → 0.18/0.30.
    vbound = 0.30 if cable else 0.18
    arc = 0.65 if marine else 0.60
    aoi_buf = 0.08 if (cable or scale == "large") else 0.03
    deck_buf = 60 if cable else 30
    ref = "주탑·앵커리지(육상 정착부)" if cable else "교대(abutment, 고정단)"

    rationale = (
        f"{CLASS_KO.get(cls, cls)}·{scale}({length:.0f}m)·{wc} → "
        f"격자 {grid}m·densification {maxd}m({nconn}연결)·velocity {vbound}m/yr"
        f"{'·계절항 권장' if True else ''}; "
        f"{'해상 강 water mask' if marine else '하천 water mask'}, 기준점 {ref}."
    )

    return BridgeProfile(
        bridge_class=cls, bridge_class_ko=CLASS_KO.get(cls, cls),
        water_context=wc, scale=scale, length_m=length,
        grid_size_m=grid, max_distance_to_p1_m=maxd, num_connections_to_p1=nconn,
        coherence_p1=p1, coherence_p2=p2, densification_coherence=dens,
        velocity_bound_m_yr=vbound, seasonal_model=True, arc_unwrap_coh=arc,
        aoi_buffer_deg=aoi_buf, deck_buffer_m=deck_buf, reference_hint=ref,
        water_mask=_water_mask(wc), rationale=rationale,
    )
