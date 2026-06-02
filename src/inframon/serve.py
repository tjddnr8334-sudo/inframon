"""FRAM 실시간 서빙 (FastAPI) — project.h5 의 경보·CRI·함수망을 HTTP 로 노출.

모니터링 대시보드·알림 시스템이 폴링하는 읽기 전용 엔드포인트:
  GET /health            — 헬스 체크
  GET /status            — 경보(등급·기능상태·리드타임·근거) + 함수망 요약
  GET /cri               — CRI 요약(최대·시점별 최대 시계열)
  GET /function-network  — 6측면 함수망 진단(driver·임계경로)

매 요청마다 project.h5 를 새로 읽어 최신 상태를 반환한다(파일이 갱신되면 즉시 반영).
fastapi 선택 의존(`pip install -e .[serve]`). CLI `--serve --out project.h5 [--port N]`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def read_monitor(project_h5: str | Path) -> dict[str, Any] | None:
    """project.h5 의 FRAM 모니터링 상태를 dict 로 (없으면 None)."""
    from .contracts.io import ProjectStore
    from .contracts.schema import FRAMOutput

    p = Path(project_h5)
    if not p.exists():
        return None
    with ProjectStore(p, mode="r") as s:
        if not s.has_meta("fram"):
            return None
        fram = s.read_meta("fram", FRAMOutput)
        cri = s.read_array(fram.CRI_ds)
        w = fram.warning
        try:
            fnet = s.read_json_attr("fram", "function_network")
        except Exception:  # noqa: BLE001 — 함수망 attr 이 없으면 None
            fnet = None
    return {
        "level": w.level,
        "basis": w.basis,
        "critical_members": list(w.critical_members),
        "function_states": dict(w.function_states),
        "lead_time_days": w.lead_time_days,
        "lead_time_forecast_days": w.lead_time_forecast_days,
        "cri_global_max": float(fram.cri_global_max),
        "n_points": fram.n_points,
        "n_dates": fram.n_dates,
        "cri_max_series": [float(x) for x in cri.max(axis=0)],
        "function_network": fnet,
    }


def create_app(project_h5: str | Path):
    """FastAPI 앱 생성(읽기 전용). fastapi 미설치면 ImportError."""
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="inframon FRAM monitor", version="0.1")
    path = str(project_h5)

    def _monitor() -> dict[str, Any]:
        data = read_monitor(path)
        if data is None:
            raise HTTPException(status_code=404, detail="FRAM 결과가 없습니다(파이프라인 먼저 실행)")
        return data

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "project": path, "has_fram": read_monitor(path) is not None}

    @app.get("/status")
    def status() -> dict[str, Any]:
        m = _monitor()
        return {k: m[k] for k in (
            "level", "basis", "critical_members", "function_states",
            "lead_time_days", "lead_time_forecast_days", "cri_global_max")}

    @app.get("/cri")
    def cri() -> dict[str, Any]:
        m = _monitor()
        return {"cri_global_max": m["cri_global_max"], "n_points": m["n_points"],
                "n_dates": m["n_dates"], "cri_max_series": m["cri_max_series"]}

    @app.get("/function-network")
    def function_network() -> dict[str, Any]:
        m = _monitor()
        if m["function_network"] is None:
            raise HTTPException(status_code=404, detail="함수망 진단이 없습니다(fram=real 필요)")
        return m["function_network"]

    return app


def serve(project_h5: str | Path, host: str = "127.0.0.1", port: int = 8000) -> None:
    """uvicorn 으로 서버 실행(블로킹). fastapi·uvicorn 필요."""
    import uvicorn

    uvicorn.run(create_app(project_h5), host=host, port=port)
