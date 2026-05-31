"""증분 재개(incremental resume) — 입력이 안 바뀐 단계는 재계산을 건너뛴다.

각 단계의 fingerprint::

    fp(stage) = sha256(stage, engine_mode[stage], cfg_subset, parent_fp)[:16]

parent_fp 를 사슬로 엮으므로 상류 단계가 바뀌면 하류 fingerprint 가 자동으로
달라져 함께 무효화된다. 직전 실행의 fingerprint 와 같고 출력이 파일에 온전히
남아 있으면(meta 존재 + 배열 계약 통과) 그 단계를 재사용(reuse)한다.

cfg_subset 은 `vars(cfg)` 에서 제어 플래그·engines 를 뺀 전체다(보수적). 이유:
  - cv real 은 `getattr(cfg, "cv_backend", ...)` 같은 **동적 속성**을 읽는다.
    `asdict(cfg)` 는 선언된 dataclass 필드만 잡아 이런 값을 놓치므로(→ stale
    재사용 버그), 인스턴스 __dict__ 전체를 보는 `vars(cfg)` 를 쓴다.
  - cfg 값이 하나라도 바뀌면 전 단계를 다시 돈다(보수적이지만 절대 stale 없음).

그래도 고가치 두 경우는 정확히 바뀐 단계만 다시 돈다:
  (1) 크래시 복구 — cfg 동일, 출력만 유실 → 출력 유효성 검사가 잡아 재계산.
  (2) 엔진 한 개 핫스왑(--engine fram=real) — engines 는 단계별 engine_mode 로
      fingerprint 에 따로 반영되어, 그 단계와 하류만 무효화된다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 순환 import 방지 — 런타임엔 함수 내부에서 import
    from ..config import PipelineConfig
    from ..contracts.io import ProjectStore

logger = logging.getLogger("inframon.pipeline")

STAGE_ORDER = ("cv", "insar", "pinn", "fram")

# fingerprint·재사용 판정에서 제외할 cfg 키 — 제어 플래그(데이터 입력 아님)와
# engines(단계별 engine_mode 로 따로 반영).
_EXCLUDE = frozenset(
    {"engines", "validate_contracts", "write_manifest", "resume", "force_stages"}
)


def cfg_subset(cfg: PipelineConfig) -> dict:
    """fingerprint 에 들어갈 cfg 값(제어 플래그·engines 제외, 동적 속성 포함)."""
    return {k: v for k, v in vars(cfg).items() if k not in _EXCLUDE}


def _fp(stage: str, engine_mode: str, subset_json: str, parent_fp: str) -> str:
    raw = f"{stage}\x00{engine_mode}\x00{subset_json}\x00{parent_fp}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def stage_fingerprints(cfg: PipelineConfig) -> dict[str, str]:
    """단계별 fingerprint(parent 사슬 포함)를 계산한다."""
    subset_json = json.dumps(
        cfg_subset(cfg), sort_keys=True, default=str, ensure_ascii=False
    )
    fps: dict[str, str] = {}
    parent = ""
    for stage in STAGE_ORDER:
        parent = _fp(stage, cfg.engines[stage], subset_json, parent)
        fps[stage] = parent
    return fps


def _outputs_valid(store: ProjectStore, stage: str) -> bool:
    """단계 출력이 파일에 온전히 있고 배열 계약을 통과하는지(재사용 가능 여부)."""
    from ..contracts.schema import CVOutput, FRAMOutput, InSAROutput, PINNOutput

    models = {"cv": CVOutput, "insar": InSAROutput, "pinn": PINNOutput, "fram": FRAMOutput}
    if not store.has_meta(stage):
        return False
    try:
        meta = store.read_meta(stage, models[stage])
        store.validate(stage, meta)  # 누락/형상/dtype 깨졌으면 여기서 걸림
    except Exception:  # noqa: BLE001 — 어떤 손상이든 "재사용 불가"로 안전하게 처리
        return False
    return True


def build_plan(
    store: ProjectStore,
    prev_fps: dict[str, str],
    cfg: PipelineConfig,
    force: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """각 단계의 실행 계획(compute/reuse)과 fingerprint 를 결정한다.

    반환: (plan{stage: "compute"|"reuse"}, fps{stage: fingerprint}).
    한 단계가 compute 로 정해지면 이후 모든 하류 단계도 compute(cascade)된다.
    """
    fps = stage_fingerprints(cfg)
    plan: dict[str, str] = {}
    cascade = False
    for stage in STAGE_ORDER:
        if (
            cascade
            or stage in force
            or prev_fps.get(stage) != fps[stage]
            or not _outputs_valid(store, stage)
        ):
            plan[stage] = "compute"
            cascade = True
        else:
            plan[stage] = "reuse"
    return plan, fps
