"""한계상태 임계값 해석 — 부재별 기본값 + 사용자 override + **출처 라벨**.

기본값은 편의를 위한 것이지 규정이 아니다. 잔존수명 숫자는 임계값에 선형으로
비례하므로, 어떤 값을 어디서 가져다 썼는지 모르면 결과를 해석할 수 없다.
그래서 이 모듈은 값과 함께 **출처 문자열을 항상 같이** 돌려주고,
`estimator` 는 그것을 `assumptions` 로 저장해 UI 에 노출한다.
"""

from __future__ import annotations

import numpy as np

from ..contracts.schema import MEMBER_TYPES

# ── 기본 한계값 (출처 라벨과 1:1) ───────────────────────────────────────
DEFAULTS: dict[str, float] = {
    # 설계공용수명[년] — 잔존수명 상한(이보다 긴 값은 표시 의미가 없다)
    "design_life_years": 100.0,
    # 상판 누적 처짐 한계 = span/ratio. 사용성 처짐 규정을 **누적 연직변위**에 대한
    # 대리 지표로 쓴다(엄밀히는 활하중 처짐 규정) — 아래 NOTE 참조.
    "deck_deflection_ratio": 800.0,
    # 교각·교대 절대 침하 허용치[mm]. 지반조건 의존이라 사용자 지정이 원칙.
    "settlement_mm": 25.0,
    # 부등침하 각변위 한계[rad] = 1/500. 절대침하보다 구조적으로 지배적이다.
    "angular_distortion": 1.0 / 500.0,
    # 강성 열화 허용비 EI/EI0 (채널 A, P2에서 사용)
    "ei_limit_ratio": 0.80,
}

SOURCES: dict[str, str] = {
    "design_life_years": "기본가정 100년(도로교설계기준 설계공용수명) — 사용자 지정 권장",
    "deck_deflection_ratio": "기본가정 L/800(도로교설계기준 사용성 처짐) — 누적 연직변위에 대한 대리 지표",
    "settlement_mm": "기본가정 25mm — 지반조건 의존, 사용자 지정 권장",
    "angular_distortion": "기본가정 1/500(부등침하 각변위) — 사용자 지정 권장",
    "ei_limit_ratio": "기본가정 EI/EI0≥0.80 — 사용자 지정 권장",
}

# NOTE(정직성): 상판의 L/800 은 본래 *활하중 재하 시 처짐* 규정이다. InSAR 는 6~12일
# 간격 스냅샷이라 활하중 처짐을 관측하지 못하고 누적 변위만 본다. 여기서는 그 규정을
# 누적 연직변위의 사용성 대리 한계로 전용하며, 이 사실을 assumptions 에 명시한다.

_MEMBER_IDX = {name: i for i, name in enumerate(MEMBER_TYPES)}


def resolve(user: dict | None = None) -> tuple[dict[str, float], dict[str, str]]:
    """기본값에 사용자 지정을 덮어쓰고, 값별 출처 라벨을 함께 돌려준다."""
    vals = dict(DEFAULTS)
    srcs = dict(SOURCES)
    for k, v in (user or {}).items():
        if k not in DEFAULTS:
            continue
        if v is None:
            continue
        vals[k] = float(v)
        srcs[k] = "사용자 지정"
    return vals, srcs


def point_limits(member: np.ndarray, span_m: float, limits: dict[str, float],
                 *, span_known: bool = True) -> tuple[np.ndarray, str]:
    """부재 라벨 [N] → 점별 절대 변위 한계 [N] (mm) 와 적용 근거.

    - deck            : span/ratio (m→mm) — **지간을 실제로 알 때만**
    - pier/abutment   : 절대 침하 허용치
    - bearing / 미상  : 절대 침하 허용치(보수적으로 동일 적용)

    `span_known=False`(지간을 점군 범위로 *추정*만 한 경우)면 상판에도 L/ratio 를
    적용하지 않는다. 실 데이터에서 점군은 주변 건물까지 포함해 신장이 교량 지간의
    10배가 되기도 하고, 그러면 L/800 이 수 미터짜리 무의미한 한계가 된다.
    모르는 값으로 만든 큰 한계는 "안전"이 아니라 **검출 불능**이다.
    """
    mem = np.asarray(member).ravel().astype(int)
    settle_mm = float(limits["settlement_mm"])
    out = np.full(mem.shape, settle_mm, dtype=np.float64)
    deck_i = _MEMBER_IDX.get("deck")
    if not span_known:
        return out, ("지간 미상(점군 범위는 교량 지간이 아님) → 상판에도 절대 침하 한계 적용. "
                     "정확한 L/800 을 쓰려면 지오참조된 CV 기하나 지간 입력이 필요합니다.")
    deck_mm = float(span_m) * 1000.0 / float(limits["deck_deflection_ratio"])
    if deck_i is not None and deck_mm > 0:
        out[mem == deck_i] = deck_mm
    return out, f"상판 L/{limits['deck_deflection_ratio']:.0f} = {deck_mm:.1f}mm, 그 외 절대 침하 {settle_mm:.0f}mm"


def describe(limits: dict[str, float], sources: dict[str, str], *,
             span_m: float, span_known: bool = True, extra: dict | None = None) -> dict:
    """assumptions 블록 — UI 가 그대로 표시할 수 있는 형태."""
    out = {
        "span_m": round(float(span_m), 2),
        # 지간을 모르면 상판에 L/ratio 를 적용하지 않으므로 값을 내걸지 않는다
        # (적용하지 않은 한계를 표시하면 그걸 썼다고 오해된다).
        "deck_limit_mm": (round(float(span_m) * 1000.0 / float(limits["deck_deflection_ratio"]), 2)
                          if span_known else None),
        "values": {k: float(v) for k, v in limits.items()},
        "sources": dict(sources),
        "notes": [
            "L/800 은 본래 활하중 처짐 규정 — InSAR 는 활하중 처짐을 관측하지 못하므로 "
            "누적 연직변위의 사용성 대리 한계로 전용했다.",
            "이 결과는 안전 판정이 아니라 점검 우선순위 스크리닝이다.",
        ],
    }
    if extra:
        out.update(extra)
    return out
