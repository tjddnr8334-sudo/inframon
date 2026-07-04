"""VLM 안전성 평가 백엔드 — 확장점(Protocol + 레지스트리).

VLM 팀이 실제 모델(시방서 RAG·비전 추론)을 붙이는 **소켓**. 엔진 핫스왑
(`orchestrator/engines.py`)과 동일한 register/resolve 패턴이라 파이프라인·CLI 는
백엔드 종류에 무관하게 동작한다.

새 백엔드 붙이는 법::

    from inframon.vlm import register_backend
    register_backend("claude", lambda: MyClaudeBackend(api_key=...))

그러면 `run_vlm_assessment(pkg_dir, backend="claude")` 로 켤 수 있다. 계약(evaluate 의
입력=VLM 패키지 폴더, 출력=VLMAssessment)이 안정적이므로 소비단은 영향받지 않는다.

⚠️ 최종 안전성 판정은 시방서+VLM 파트의 몫이다. 기본 `template` 백엔드는 LLM 이 아니라
패키지의 grounded 컨텍스트를 구조화해 돌려주는 스텁(코드 판정 아님, 면책 명시).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

ASSESSMENT_SCHEMA = "inframon.vlm_assessment/1.0"

DISCLAIMER = ("이 평가는 inframon 이 산출한 데이터 패키지에 대한 것으로, 대한민국 "
              "시방서/법규 기반의 최종 안전성 코드 판정이 아니다. 최종 판정은 시방서 "
              "RAG+VLM 파트가 수행한다. CRI/경보는 참고용 내부 물리지표다.")


@dataclass
class VLMAssessment:
    """VLM 백엔드 평가 결과(자기기술). 실제 판정 백엔드도 이 형식을 채운다."""
    backend: str
    bridge_id: str
    is_code_judgment: bool = False        # 시방서 코드 판정 여부(스텁=False)
    grounded_context: dict[str, Any] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    verdict: str | None = None            # 백엔드가 판정을 내리면 채움(스텁=None)
    disclaimer: str = DISCLAIMER
    schema: str = ASSESSMENT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "backend": self.backend, "bridge_id": self.bridge_id,
            "is_code_judgment": self.is_code_judgment, "verdict": self.verdict,
            "grounded_context": self.grounded_context, "findings": self.findings,
            "disclaimer": self.disclaimer,
        }


@runtime_checkable
class VLMBackend(Protocol):
    """VLM 평가 백엔드 규약. 실 모델은 이 프로토콜만 만족하면 끼워진다."""
    name: str

    def evaluate(self, package_dir: Path) -> VLMAssessment:
        """VLM 패키지 폴더(manifest·summary·narrative·figures·knowledge_graph)를 읽어 평가."""
        ...


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class TemplateBackend:
    """기본 백엔드 — LLM 아님. 패키지의 grounded 컨텍스트를 구조화한 스텁 평가.

    실제 VLM 이 연결되기 전까지 파이프라인 말단이 동작하도록 하는 자리표시자.
    핫스팟·부재·PINN 가상센싱·위험참고를 요약하되 **판정하지 않는다**(verdict=None).
    """

    name = "template"

    def evaluate(self, package_dir: Path) -> VLMAssessment:
        package_dir = Path(package_dir)
        summary = _read_json(package_dir / "summary.json")
        bid = summary.get("bridge_id", "")
        obs = summary.get("observation", {})
        ch = summary.get("channels_present", {})
        findings: list[str] = []
        for h in summary.get("settlement_hotspots", [])[:3]:
            findings.append(
                f"측점 {h.get('point_id')}({h.get('member')}) 누적 "
                f"{h.get('cumulative_disp_mm')}mm·속도 {h.get('rate_mm_per_yr')}mm/yr "
                "— 시방서 대비 평가 필요(참고 신호).")
        vs = summary.get("virtual_sensing")
        if vs:
            g = vs.get("girder") or {}
            findings.append(
                f"PINN 가상센싱 상부거더 첨두 변위 {g.get('peak_total_mm')}mm "
                f"(스팬 {g.get('span_m')}m) — 상판 전체 변위장으로 허용처짐 대비 검토 대상.")
        rr = summary.get("risk_reference")
        if rr:
            findings.append(
                f"내부 위험참고: CRI_max {rr.get('cri_global_max')}, 경보 "
                f"'{rr.get('warning_level')}' — 시방서 판정 아님.")
        return VLMAssessment(
            backend=self.name, bridge_id=bid, is_code_judgment=False, verdict=None,
            grounded_context={
                "observation": obs,
                "channels_present": ch,
                "hotspot_count": len(summary.get("settlement_hotspots", [])),
                "has_virtual_sensing": bool(vs),
            },
            findings=findings,
        )


# ── 레지스트리 (engines.register/resolve 와 동일 패턴) ──
_BACKENDS: dict[str, Callable[[], VLMBackend]] = {
    "template": TemplateBackend,
}


def register_backend(name: str, factory: Callable[[], VLMBackend]) -> None:
    """VLM 백엔드 팩토리를 등록한다(실 모델을 붙일 때)."""
    if not name or not callable(factory):
        raise ValueError("name(비어있지 않음)과 factory(callable)가 필요합니다.")
    _BACKENDS[name] = factory


def resolve_backend(name: str = "template") -> VLMBackend:
    """등록된 백엔드 인스턴스를 돌려준다. 없으면 친절히 안내."""
    try:
        backend = _BACKENDS[name]()
    except KeyError:
        raise NotImplementedError(
            f"VLM 백엔드 {name!r} 가 없습니다. 사용 가능: {sorted(_BACKENDS)}. "
            f"실 모델을 붙였다면 register_backend({name!r}, factory) 로 등록하세요."
        ) from None
    if not isinstance(backend, VLMBackend):
        raise TypeError(f"백엔드 {name!r} 가 VLMBackend 프로토콜(name·evaluate)을 만족하지 않습니다.")
    return backend


def available_backends() -> list[str]:
    """등록된 VLM 백엔드 이름 목록."""
    return sorted(_BACKENDS)


def run_vlm_assessment(package_dir: str | Path, *, backend: str = "template",
                       write: bool = True) -> dict[str, Any]:
    """VLM 패키지 폴더를 백엔드로 평가하고 assessment.json 을 쓴다. 결과 dict 반환."""
    package_dir = Path(package_dir)
    if not (package_dir / "summary.json").exists():
        raise FileNotFoundError(
            f"VLM 패키지가 아닙니다(summary.json 없음): {package_dir}. "
            "먼저 --export-vlm 으로 패키지를 만드세요.")
    assessment = resolve_backend(backend).evaluate(package_dir).to_dict()
    if write:
        (package_dir / "assessment.json").write_text(
            json.dumps(assessment, ensure_ascii=False, indent=2), encoding="utf-8")
    return assessment
