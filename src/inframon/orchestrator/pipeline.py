"""오케스트레이션 레이어 — CV → InSAR → PINN → FRAM 순차 실행 (문서 7.1 모드 1).

기본은 전체 재계산이지만 `cfg.resume=True` 면 **증분 재개**한다: 기존 project.h5 의
단계별 fingerprint(직전 매니페스트에 기록)와 현 cfg/engine_mode 를 비교해, 입력이
안 바뀌고 출력이 온전한 단계는 재계산을 건너뛰고(reuse) 디스크의 결과를 그대로
하류로 넘긴다. 한 단계가 다시 계산되면 그 하류는 모두 cascade 로 재계산된다.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..config import PipelineConfig
from ..contracts.io import ProjectStore
from ..contracts.schema import CVOutput, FRAMOutput, InSAROutput, PINNOutput
from . import incremental
from .engines import resolve

logger = logging.getLogger("inframon.pipeline")

_MODELS = {"cv": CVOutput, "insar": InSAROutput, "pinn": PINNOutput, "fram": FRAMOutput}


def _read_prev_fingerprints(project_path: Path, cfg: PipelineConfig) -> dict[str, str]:
    """직전 실행 매니페스트의 단계 fingerprint(없으면 빈 dict)."""
    if not (cfg.resume and project_path.exists()):
        return {}
    try:
        with ProjectStore(project_path, mode="r") as store:
            return store.read_manifest().get("stage_fingerprints", {}) or {}
    except Exception:  # noqa: BLE001 — 손상/구버전 파일이면 전체 재계산으로 안전 폴백
        logger.warning("기존 매니페스트를 읽지 못해 전체 재계산합니다", exc_info=True)
        return {}


def run_pipeline(
    project_path: str | Path = "data/project.h5",
    cfg: PipelineConfig | None = None,
) -> FRAMOutput:
    """4대 엔진을 순차 실행하고 project.h5 에 모든 결과를 적재한다.

    각 단계의 구현(stub/real)은 cfg.engines 에 따라 핫스왑되고, 출력은 공유 심볼
    표로 검증된다(cfg.validate_contracts). cfg.resume 이면 fingerprint 가 같고 출력이
    온전한 단계를 재사용한다. 실행이 끝나면 출처 매니페스트(run_id/cfg/엔진모드/
    단계 fingerprint/데이터셋 해시)를 기록한다.
    """
    cfg = cfg or PipelineConfig()
    project_path = Path(project_path)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]

    prev_fps = _read_prev_fingerprints(project_path, cfg)
    # resume 이고 기존 파일이 있으면 append 로 열어 reuse 단계 결과를 보존한다.
    open_mode = "a" if (cfg.resume and project_path.exists() and prev_fps) else "w"

    runners = {name: resolve(name, cfg.engines[name]) for name in incremental.STAGE_ORDER}
    symbols: dict[str, int] = {}  # 단계 간 공유 차원 심볼(N/M/H/W/F/K)

    logger.info(
        "run %s 시작 — engines=%s, N=%d, M=%d, resume=%s(mode=%s)",
        run_id, cfg.engines, cfg.n_points, cfg.n_dates, cfg.resume, open_mode,
    )

    with ProjectStore(project_path, mode=open_mode) as store:
        plan, fps = incremental.build_plan(
            store, prev_fps, cfg, set(cfg.force_stages)
        )
        if cfg.resume:
            reused = [s for s in incremental.STAGE_ORDER if plan[s] == "reuse"]
            logger.info("run %s 계획 — %s (재사용: %s)", run_id, plan, reused or "없음")

        metas: dict[str, object] = {}

        def step(stage: str, *args):
            """단계 실행 또는 재사용 + 공유 심볼 검증."""
            if plan[stage] == "reuse":
                meta = store.read_meta(stage, _MODELS[stage])
                logger.debug("run %s — %s 재사용", run_id, stage)
            else:
                meta = runners[stage](store, *args)
                logger.debug("run %s — %s 계산", run_id, stage)
            if cfg.validate_contracts:
                store.validate(stage, meta, symbols)
            metas[stage] = meta
            return meta

        cv: CVOutput = step("cv", cfg)
        insar: InSAROutput = step("insar", cv, cfg)
        pinn: PINNOutput = step("pinn", insar, cfg)
        fram: FRAMOutput = step("fram", insar, pinn, cfg)

        if cfg.write_manifest:
            store.write_manifest(
                run_id=run_id,
                created=datetime.now(timezone.utc).isoformat(),
                cfg=asdict(cfg),
                engine_modes=dict(cfg.engines),
                stage_fingerprints=fps,
                dataset_hashes=store.compute_dataset_hashes(),
            )

    logger.info(
        "run %s 완료 — CRI_max=%.4f, level=%s", run_id, fram.cri_global_max, fram.warning.level
    )
    return fram
