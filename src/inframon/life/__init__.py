"""잔존수명(RSL) 추정 — FRAM 뒤에 붙는 opt-in 후처리 스테이지.

4엔진 계약을 건드리지 않고 `/life` 그룹에만 쓴다. 설계: docs/잔존수명_설계.md
"""

from .estimator import estimate_remaining_life, summarize

__all__ = ["estimate_remaining_life", "summarize"]
