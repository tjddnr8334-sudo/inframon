"""Prefect 스케줄링 — 모니터링 사이클을 주기적으로 돌려 자동 진단·경보.

한 사이클(`monitor_cycle`, prefect 불필요·테스트 가능):
  1) (선택) 새 Track H5 가 있으면 preflight 후 인제스트(새 취득 반영)
  2) PINN+FRAM 재계산 (lat/lon 주면 맞춤형 자동수집, 아니면 기존 /insar 위 직접)
  3) 경보 상태 + 직전 대비 **에스컬레이션**(등급 상향) 판정

Prefect 래핑(`build_flow`/`serve_schedule`, prefect 선택 의존 `.[schedule]`)으로 cron·
interval 스케줄·재시도·관측을 붙인다. CLI `--schedule SECONDS`.
실 운용: 외부 SARvey(WSL)가 새 Sentinel-1 취득마다 Track H5 를 갱신 → 이 사이클이
이를 인제스트해 위험을 자동 추적.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

LEVELS = ("정상", "주의", "경고", "위험")


def _escalated(level: str, prev: str | None) -> bool:
    """직전 대비 경보 등급이 상향됐는가."""
    if prev not in LEVELS or level not in LEVELS:
        return False
    return LEVELS.index(level) > LEVELS.index(prev)


def _run_pinn_fram(project_h5: Path, pinn_epochs: int, fram_mode: str) -> None:
    """기존 /insar 위에 PINN(real)+FRAM 을 (재)계산한다."""
    from .config import PipelineConfig
    from .contracts.io import ProjectStore
    from .contracts.schema import InSAROutput
    from .pinn.real_engine import run_pinn_real

    with ProjectStore(project_h5, mode="a") as s:
        insar = s.read_meta("insar", InSAROutput)
        cfg = PipelineConfig(n_points=insar.n_points, n_dates=insar.n_dates)
        cfg.pinn_epochs = pinn_epochs
        pinn = run_pinn_real(s, insar, cfg)
        if fram_mode == "real":
            from .fram.real_engine import run_fram_real as run_fram
        else:
            from .fram.engine import run_fram
        run_fram(s, insar, pinn, cfg)


def monitor_cycle(
    project_h5: str | Path,
    *,
    track_h5: str | Path | None = None,
    lat: float | None = None,
    lon: float | None = None,
    prev_level: str | None = None,
    pinn_epochs: int = 400,
    fram_mode: str = "real",
) -> dict[str, Any]:
    """모니터링 한 사이클: (선택)인제스트 → PINN+FRAM → 경보·에스컬레이션."""
    project_h5 = Path(project_h5)

    if track_h5 is not None:
        from .insar.track_preflight import preflight_track_h5
        rep = preflight_track_h5(track_h5)
        if not rep.is_ready:
            return {"ok": False, "reason": "Track preflight 실패", "errors": rep.errors}
        from .contracts.io import ProjectStore
        from .insar.track_reader import import_track_h5
        with ProjectStore(project_h5, mode="a") as s:
            import_track_h5(s, track_h5)

    if lat is not None and lon is not None:
        from .custom_pinn import run_custom_pinn
        run_custom_pinn(project_h5, lat, lon, pinn_epochs=pinn_epochs, fram_mode=fram_mode)
    else:
        _run_pinn_fram(project_h5, pinn_epochs, fram_mode)

    from .serve import read_monitor
    status = read_monitor(project_h5)
    level = status["level"] if status else "정상"
    return {
        "ok": True,
        "level": level,
        "escalated": _escalated(level, prev_level),
        "cri_global_max": status["cri_global_max"] if status else None,
        "function_states": status["function_states"] if status else {},
        "lead_time_forecast_days": status["lead_time_forecast_days"] if status else None,
    }


def build_flow(project_h5: str | Path, **cycle_kwargs):
    """Prefect flow(재시도·로깅 포함)로 monitor_cycle 을 감싼다. prefect 필요."""
    from prefect import flow, task

    @task(retries=2, retry_delay_seconds=30)
    def _cycle() -> dict[str, Any]:
        return monitor_cycle(project_h5, **cycle_kwargs)

    @flow(name="inframon-monitor", log_prints=True)
    def monitor() -> dict[str, Any]:
        r = _cycle()
        if not r.get("ok"):
            print(f"사이클 실패: {r.get('reason')}")
        elif r.get("escalated"):
            print(f"경보 상향 → {r['level']} (CRI={r.get('cri_global_max')})")
        else:
            print(f"모니터: {r.get('level')}")
        return r

    return monitor


def serve_schedule(project_h5: str | Path, interval_seconds: int = 86400, **cycle_kwargs) -> None:
    """Prefect 로 모니터 flow 를 interval 간격 스케줄 실행(블로킹). prefect 필요."""
    flow = build_flow(project_h5, **cycle_kwargs)
    flow.serve(name="inframon-monitor", interval=interval_seconds)
