"""Bmaps InSAR 탭용 FastAPI 앱 (읽기 전용, 다중 교량) — 설계 §3.

엔드포인트(베이스 `/api/v1`):
  GET /health
  GET /bridges
  GET /bridges/{bridge_id}/insar/summary
  GET /bridges/{bridge_id}/insar/points        (?metric=los|longitudinal&date=latest|<idx>)
  GET /bridges/{bridge_id}/insar/points.geojson (동일 쿼리)
  GET /bridges/{bridge_id}/insar/points/{point_id}/series
  GET /bridges/{bridge_id}/insar/cri
  GET /bridges/{bridge_id}/insar/function-network

오류 규약: 404=교량/산출물 없음, 409=schema_version 불일치, 503=project.h5 읽기 실패.
매 요청마다 project.h5 를 새로 읽어 최신 상태 반영(파이프라인 갱신 즉시 반영).
fastapi 선택 의존(`pip install -e .[serve]`).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from ..contracts.array_schema import ContractViolation
from ..contracts.io import ProjectStore
from . import transform
from .registry import BridgeEntry, BridgeRegistry
from .transform import ResultNotFound, WGS84


def create_app(registry: BridgeRegistry, *, to_crs: str = WGS84,
               allow_origins: tuple[str, ...] = ("*",)):
    """FastAPI 앱 생성. to_crs=transform.SRC_CRS 면 좌표 재투영 생략(Bmaps 가 5179 타일일 때)."""
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    app = FastAPI(title="inframon — Bmaps InSAR 변위 분석 API", version="1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=list(allow_origins),
        allow_methods=["GET"], allow_headers=["*"],
    )

    # HTTPException(404 교량/파일 없음·400 파라미터·503 읽기 실패)을 문서 §3 오류 규약
    # {"error":{"code","message"}} 으로 통일(FastAPI 기본형 {"detail":...} 대신).
    _HTTP_CODE = {400: "bad_request", 404: "not_found", 409: "schema_mismatch",
                  503: "unavailable"}

    @app.exception_handler(HTTPException)
    def _on_http(_req, exc: HTTPException) -> JSONResponse:  # noqa: ANN001
        code = _HTTP_CODE.get(exc.status_code, "error")
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"code": code, "message": str(exc.detail)}})

    # 계약 위반(schema_version major 불일치) → 409.
    @app.exception_handler(ContractViolation)
    def _on_contract(_req, exc: ContractViolation) -> JSONResponse:  # noqa: ANN001
        return JSONResponse(status_code=409,
                            content={"error": {"code": "schema_mismatch", "message": str(exc)}})

    # 산출물 없음 → 404.
    @app.exception_handler(ResultNotFound)
    def _on_notfound(_req, exc: ResultNotFound) -> JSONResponse:  # noqa: ANN001
        return JSONResponse(status_code=404,
                            content={"error": {"code": "not_found", "message": str(exc)}})

    def _entry(bridge_id: str) -> BridgeEntry:
        e = registry.get(bridge_id)
        if e is None:
            raise HTTPException(status_code=404, detail=f"bridge_id={bridge_id!r} 없음")
        return e

    @contextmanager
    def _store(bridge_id: str) -> Iterator[ProjectStore]:
        e = _entry(bridge_id)
        if not e.project_h5.exists():
            raise HTTPException(status_code=404, detail=f"project.h5 없음: {e.project_h5}")
        try:
            s = ProjectStore(e.project_h5, mode="r")
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"project.h5 읽기 실패: {exc}") from exc
        try:
            yield s
        finally:
            s.close()

    def _brief(e: BridgeEntry) -> dict[str, Any]:
        brief: dict[str, Any] = {
            "bridge_id": e.bridge_id, "name": e.name,
            "wgs84_center": list(e.wgs84_center) if e.wgs84_center else None,
            "last_run_utc": e.last_run_utc,
            "warning_level": None, "cri_global_max": None, "has_insar": False,
        }
        if not e.project_h5.exists():
            return brief
        try:
            with ProjectStore(e.project_h5, mode="r") as s:
                if not transform.has_insar(s):
                    return brief
                brief["has_insar"] = True
                summ = transform.summary(s, name=e.name, bridge_id=e.bridge_id)
                brief["cri_global_max"] = summ["cri_global_max"]
                if summ["warning"]:
                    brief["warning_level"] = summ["warning"]["level"]
        except (OSError, ContractViolation, ResultNotFound):
            pass  # 목록은 한 교량 실패로 전체가 죽지 않게 관대하게.
        return brief

    # ───────────────────────── 라우트 ─────────────────────────
    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "bridges": len(registry)}

    @app.get("/api/v1/bridges")
    def bridges() -> dict[str, Any]:
        return {"bridges": [_brief(e) for e in registry.list()]}

    @app.get("/api/v1/bridges/{bridge_id}/insar/summary")
    def summary(bridge_id: str) -> dict[str, Any]:
        e = _entry(bridge_id)
        with _store(bridge_id) as s:
            return transform.summary(s, name=e.name, bridge_id=e.bridge_id)

    @app.get("/api/v1/bridges/{bridge_id}/insar/points")
    def points(bridge_id: str, metric: str = "los", date: str = "latest") -> dict[str, Any]:
        with _store(bridge_id) as s:
            try:
                return transform.points(s, metric=metric, date=date, to_crs=to_crs)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/bridges/{bridge_id}/insar/points.geojson")
    def points_geojson(bridge_id: str, metric: str = "los", date: str = "latest") -> dict[str, Any]:
        with _store(bridge_id) as s:
            try:
                return transform.points_geojson(s, metric=metric, date=date, to_crs=to_crs)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/bridges/{bridge_id}/insar/points/{point_id}/series")
    def point_series(bridge_id: str, point_id: int) -> dict[str, Any]:
        with _store(bridge_id) as s:
            return transform.point_series(s, point_id)

    @app.get("/api/v1/bridges/{bridge_id}/insar/cri")
    def cri(bridge_id: str) -> dict[str, Any]:
        with _store(bridge_id) as s:
            return transform.cri(s)

    @app.get("/api/v1/bridges/{bridge_id}/insar/function-network")
    def function_network(bridge_id: str, k: int | None = None) -> dict[str, Any]:
        with _store(bridge_id) as s:
            return transform.function_network(s, k)

    return app


def serve_api(registry: str | BridgeRegistry, *, host: str = "127.0.0.1", port: int = 8000,
              to_crs: str = WGS84, allow_origins: tuple[str, ...] = ("*",)) -> None:
    """uvicorn 으로 실행(블로킹). registry 는 파일경로 또는 BridgeRegistry."""
    import uvicorn

    reg = BridgeRegistry.from_file(registry) if isinstance(registry, str) else registry
    uvicorn.run(create_app(reg, to_crs=to_crs, allow_origins=allow_origins), host=host, port=port)
