"""VLM 확장 서브패키지 — 안전성 평가 백엔드 확장점(플러그인).

inframon 은 VLM 이 삼킬 **패키지**(vlm_package)와 **지식그래프**(kg)를 산출한다. 실제
시방서 RAG·VLM 추론은 타 팀 파트지만, 그 모델이 **끼워질 소켓**을 여기서 규약으로 제공한다:
`VLMBackend` 프로토콜 + register/resolve 레지스트리(엔진 핫스왑과 동일 패턴). 기본
`template` 백엔드는 LLM 없이 grounded 컨텍스트를 구조화해 돌려주는 스텁이다.
"""

from __future__ import annotations

from .backend import (
    VLMAssessment,
    VLMBackend,
    available_backends,
    register_backend,
    resolve_backend,
    run_vlm_assessment,
)

__all__ = [
    "VLMAssessment", "VLMBackend", "available_backends",
    "register_backend", "resolve_backend", "run_vlm_assessment",
]
