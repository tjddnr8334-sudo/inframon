"""엔진 구현 레지스트리 — STUB ↔ real 핫스왑 스위치.

각 엔진(cv/insar/pinn/fram)은 `run_*(store, ..., cfg)` 라는 동일한 계약 시그니처를
지키는 한, 여러 구현(stub/real)을 가질 수 있다. `PipelineConfig.engines` 가
단계별로 어떤 구현을 쓸지 정하고, pipeline 은 `resolve()` 로 함수를 받아 호출한다.

새 실구현을 붙이는 법::

    from inframon.orchestrator import engines
    engines.register("insar", "real", run_insar_real)

이렇게 등록만 하면 `--engine insar=real` 로 켤 수 있고, pipeline.py 는 바뀌지 않는다.
계약(run_* 시그니처)이 안정적이므로 다운스트림 엔진은 영향받지 않는다.
"""

from __future__ import annotations

from typing import Callable

from ..config import ENGINE_MODES, ENGINE_NAMES
from ..cv.engine import run_cv
from ..cv.real_engine import run_cv_real
from ..fram.engine import run_fram
from ..fram.real_engine import run_fram_real
from ..insar.engine import run_insar
from ..insar.real_engine import run_insar_real
from ..pinn.engine import run_pinn
from ..pinn.real_engine import run_pinn_real

# (엔진명, 모드) -> run 함수. stub 은 전부, real 은 구현된 것만 등록한다.
_REGISTRY: dict[tuple[str, str], Callable] = {
    ("cv", "stub"): run_cv,
    ("cv", "real"): run_cv_real,        # Phase 2 (영상처리: Otsu→CC→PCA→부재할당)
    ("insar", "stub"): run_insar,
    ("insar", "real"): run_insar_real,  # Phase 3 1차 증분 (Track H5 → CV 정합)
    ("pinn", "stub"): run_pinn,
    ("pinn", "real"): run_pinn_real,    # Phase 4 (PyTorch PINN + Euler-Bernoulli + FEM)
    ("fram", "stub"): run_fram,
    ("fram", "real"): run_fram_real,    # Phase 5 (점별 공명 + 실 load + 절대 보정)
}


def register(engine: str, mode: str, fn: Callable) -> None:
    """엔진의 한 구현을 등록한다(실구현을 붙일 때 사용)."""
    if engine not in ENGINE_NAMES:
        raise ValueError(f"engine must be one of {ENGINE_NAMES}, got {engine!r}")
    if mode not in ENGINE_MODES:
        raise ValueError(f"mode must be one of {ENGINE_MODES}, got {mode!r}")
    _REGISTRY[(engine, mode)] = fn


def resolve(engine: str, mode: str) -> Callable:
    """선택된 (엔진, 모드)의 run 함수를 돌려준다.

    아직 등록되지 않은 구현을 고르면 NotImplementedError 로 친절히 안내한다.
    """
    try:
        return _REGISTRY[(engine, mode)]
    except KeyError:
        available = sorted(m for (e, m) in _REGISTRY if e == engine)
        raise NotImplementedError(
            f"엔진 {engine!r} 의 {mode!r} 구현이 아직 없습니다. "
            f"사용 가능한 모드: {available}. "
            f"실구현을 붙였다면 engines.register({engine!r}, {mode!r}, fn) 로 등록하세요."
        ) from None


def available_modes(engine: str) -> list[str]:
    """해당 엔진에 등록된 구현 모드 목록."""
    return sorted(m for (e, m) in _REGISTRY if e == engine)
