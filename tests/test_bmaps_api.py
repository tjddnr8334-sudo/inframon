"""Bmaps 연동 API — 레지스트리·변환(순수)·FastAPI 엔드포인트 회귀.

좌표 변환(EPSG:5179→WGS84)은 pyproj 의존이라, 변환 외 검증은 to_crs=SRC_CRS(재투영
생략)로 돌려 pyproj 없이도 실행되게 한다. 실제 재투영은 별도 테스트에서 importorskip.
"""

from __future__ import annotations

import json

import pytest

from inframon.api import transform
from inframon.api.registry import BridgeRegistry, RegistryError
from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.orchestrator.pipeline import run_pipeline

N_POINTS, N_DATES = 30, 14


def _project(path):
    cfg = PipelineConfig(n_points=N_POINTS, n_dates=N_DATES,
                         engines={"cv": "stub", "insar": "stub", "pinn": "stub", "fram": "real"})
    run_pipeline(path, cfg)
    return path


def _registry(tmp_path):
    """B1=정상, B2=파일없음, B3=빈 project(InSAR 산출 없음)."""
    _project(tmp_path / "p.h5")
    with ProjectStore(tmp_path / "empty.h5", mode="w"):
        pass
    reg_path = tmp_path / "bridge_registry.json"
    reg_path.write_text(json.dumps({"bridges": [
        {"bridge_id": "B1", "name": "정자교", "project_h5": "p.h5", "wgs84_center": [37.3, 127.1]},
        {"bridge_id": "B2", "name": "없는교", "project_h5": "missing.h5"},
        {"bridge_id": "B3", "name": "빈교", "project_h5": "empty.h5"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return reg_path


# ───────────────────────── 순수 변환 헬퍼 ─────────────────────────
def test_epoch_and_member():
    assert transform.epoch_days_to_iso(0) == "1970-01-01"
    assert transform.epoch_days_to_iso(31) == "1970-02-01"
    assert transform.member_label(0) == "deck"
    assert transform.member_label(99) == "unknown"


def test_xyz_skip_reproject_when_same_crs():
    import numpy as np
    xyz = np.array([[200000.0, 500000.0, 12.0]])
    out = transform.xyz_to_latlon(xyz, to_crs=transform.SRC_CRS)  # 재투영 생략
    assert out.tolist() == xyz.tolist()


def test_xyz_reproject_to_wgs84():
    pytest.importorskip("pyproj")
    import numpy as np
    # EPSG:5179 (한국) 중부권 근방 좌표 → 위경도가 한반도 범위에 들어와야 한다.
    xyz = np.array([[960000.0, 1940000.0, 10.0]])
    lat, lon, elev = transform.xyz_to_latlon(xyz)[0]
    assert 33.0 < lat < 39.0 and 124.0 < lon < 132.0
    assert elev == 10.0


# ───────────────────────── 레지스트리 ─────────────────────────
def test_registry_loads(tmp_path):
    reg = BridgeRegistry.from_file(_registry(tmp_path))
    assert len(reg) == 3
    assert reg.get("B1").name == "정자교"
    assert reg.get("B1").project_h5.is_absolute()  # 레지스트리 기준 절대해석
    assert reg.get("ZZZ") is None


def test_registry_errors(tmp_path):
    with pytest.raises(RegistryError):
        BridgeRegistry.from_file(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text('{"bridges": [{"name": "x"}]}', encoding="utf-8")  # bridge_id 누락
    with pytest.raises(RegistryError):
        BridgeRegistry.from_file(bad)


# ───────────────────────── 변환 DTO (ProjectStore 직접) ─────────────────────────
def test_transform_dtos(tmp_path):
    out = _project(tmp_path / "p.h5")
    with ProjectStore(out, mode="r") as s:
        summ = transform.summary(s, name="정자교", bridge_id="B1")
        assert summ["n_points"] == N_POINTS and summ["n_dates"] == N_DATES
        assert len(summ["date_range"]) == 2
        assert summ["warning"]["level"] in {"정상", "주의", "경고", "위험"}
        assert 0.0 <= summ["cri_global_max"] <= 1.0

        pts = transform.points(s, metric="los", date="latest", to_crs=transform.SRC_CRS)
        assert len(pts["points"]) == N_POINTS
        assert pts["date_index"] == N_DATES - 1
        p0 = pts["points"][0]
        assert set(p0) >= {"point_id", "lat", "lon", "elev", "member", "coherence", "value_mm", "cri"}
        assert p0["member"] in ("deck", "pier", "abutment", "bearing", "unknown")

        gj = transform.points_geojson(s, date="latest", to_crs=transform.SRC_CRS)
        assert gj["type"] == "FeatureCollection" and len(gj["features"]) == N_POINTS
        assert gj["features"][0]["geometry"]["type"] == "Point"

        ser = transform.point_series(s, p0["point_id"])
        assert len(ser["los_mm"]) == N_DATES and len(ser["longitudinal_mm"]) == N_DATES
        assert ser["components"] is not None and len(ser["components"]["thermal_mm"]) == N_DATES
        assert ser["cri"] is not None and len(ser["cri"]) == N_DATES

        c = transform.cri(s)
        assert len(c["cri_max_series"]) == N_DATES

        fn = transform.function_network(s)
        assert len(fn["func_names"]) >= 1
        assert len(fn["coupling"]) == len(fn["variability"])


def test_points_and_series_expose_vertical(tmp_path):
    """asc+desc 융합 연직(vertical_ds)이 있으면 points/series 에 vertical_mm 으로 노출."""
    import numpy as np

    from inframon.contracts.schema import InSAROutput
    out = _project(tmp_path / "p.h5")
    with ProjectStore(out, mode="a") as s:        # 융합 연직 적재 + 계약 메타 연결(모사)
        ins = s.read_meta("insar", InSAROutput)
        N, M = ins.n_points, ins.n_dates
        vert = np.linspace(-5.0, 0.0, N * M).reshape(N, M).astype("float32")  # mm 규약
        s.write_array("/insar/vertical", vert)
        ins.vertical_ds = "/insar/vertical"
        s.write_meta("insar", ins)
    with ProjectStore(out, mode="r") as s:
        pts = transform.points(s, date="latest", to_crs=transform.SRC_CRS)
        ser = transform.point_series(s, pts["points"][0]["point_id"])
    assert pts["has_vertical"] is True
    assert pts["points"][0]["vertical_mm"] is not None
    assert ser["vertical_mm"] is not None and len(ser["vertical_mm"]) == N_DATES
    # mm 통과(×1000 아님): 적재값 -5..0mm 범위를 그대로 노출
    assert all(-5.001 <= v <= 0.001 for v in ser["vertical_mm"])


def test_points_no_vertical_by_default(tmp_path):
    """단일 궤도(연직 없음) 프로젝트는 has_vertical=False, vertical_mm=None."""
    out = _project(tmp_path / "p.h5")
    with ProjectStore(out, mode="r") as s:
        pts = transform.points(s, date="latest", to_crs=transform.SRC_CRS)
    assert pts["has_vertical"] is False
    assert pts["points"][0]["vertical_mm"] is None


def test_displacement_unit_is_mm_passthrough(tmp_path):
    """회귀: 변위는 계약상 mm — transform 이 ×1000(m→mm)하면 1000배 과대보고.

    end-to-end 시연에서 발견한 버그(los_ds 가 mm 인데 API 가 ×1000 → value_mm 가
    -26412mm 처럼 부풀려짐). value_mm/los_mm/성분이 원시 mm 값을 **그대로** 반영하는지
    (환산 없음) 못박는다. 누군가 ×1000 을 되살리면 exact 비교가 깨진다.
    """
    import numpy as np

    from inframon.contracts.schema import InSAROutput, PINNOutput
    out = _project(tmp_path / "p.h5")
    with ProjectStore(out, mode="r") as s:
        ins = s.read_meta("insar", InSAROutput)
        los = np.asarray(s.read_array(ins.los_ds))           # [N,M] mm
        k = ins.n_dates - 1
        pinn = s.read_meta("pinn", PINNOutput)
        thermal = np.asarray(s.read_array(pinn.comp_thermal_ds))
        pts = transform.points(s, metric="los", date="latest", to_crs=transform.SRC_CRS)
        ser = transform.point_series(s, pts["points"][0]["point_id"])

    # points.value_mm 과 series.los_mm 이 원시 los_ds 값과 동일(×1000 아님)
    assert pts["points"][0]["value_mm"] == round(float(los[0, k]), 2)
    assert ser["los_mm"][k] == round(float(los[0, k]), 3)
    # PINN 성분도 mm 통과
    assert ser["components"]["thermal_mm"][k] == round(float(thermal[0, k]), 3)
    # 교량 변위는 mm 스케일 — ×1000 이면 수천~수만으로 튄다
    assert max(abs(p["value_mm"]) for p in pts["points"]) < 1000.0


def test_transform_missing_point(tmp_path):
    out = _project(tmp_path / "p.h5")
    with ProjectStore(out, mode="r") as s:
        with pytest.raises(transform.ResultNotFound):
            transform.point_series(s, 10_000_000)


# ───────────────────────── FastAPI 엔드포인트 ─────────────────────────
def _client(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from inframon.api.app import create_app
    reg = BridgeRegistry.from_file(_registry(tmp_path))
    return TestClient(create_app(reg, to_crs=transform.SRC_CRS))


def test_health_and_bridges(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/v1/health").json() == {"status": "ok", "bridges": 3}
    bl = {b["bridge_id"]: b for b in client.get("/api/v1/bridges").json()["bridges"]}
    assert bl["B1"]["has_insar"] is True and bl["B1"]["warning_level"] is not None
    assert bl["B2"]["has_insar"] is False  # 파일 없음도 목록엔 관대하게 노출
    assert bl["B3"]["has_insar"] is False  # 빈 project


def test_endpoints_ok(tmp_path):
    client = _client(tmp_path)
    base = "/api/v1/bridges/B1/insar"
    assert client.get(f"{base}/summary").json()["n_dates"] == N_DATES
    pts = client.get(f"{base}/points?metric=los&date=latest").json()
    assert len(pts["points"]) == N_POINTS
    pid = pts["points"][0]["point_id"]
    assert len(client.get(f"{base}/points/{pid}/series").json()["los_mm"]) == N_DATES
    assert len(client.get(f"{base}/cri").json()["cri_max_series"]) == N_DATES
    assert client.get(f"{base}/function-network").status_code == 200
    assert client.get(f"{base}/points.geojson").json()["type"] == "FeatureCollection"


def test_endpoint_errors(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/v1/bridges/NOPE/insar/summary").status_code == 404  # 교량 없음
    assert client.get("/api/v1/bridges/B2/insar/summary").status_code == 404    # 파일 없음
    assert client.get("/api/v1/bridges/B3/insar/summary").status_code == 404    # InSAR 산출 없음
    assert client.get("/api/v1/bridges/B1/insar/points?metric=bogus").status_code == 400


def test_error_envelope_is_uniform(tmp_path):
    """모든 오류가 문서 §3 규약 {"error":{"code","message"}} 로 통일(detail 형 금지)."""
    client = _client(tmp_path)
    cases = [
        ("/api/v1/bridges/NOPE/insar/summary", 404, "not_found"),          # HTTPException 경로
        ("/api/v1/bridges/B1/insar/points?metric=bogus", 400, "bad_request"),
        ("/api/v1/bridges/B1/insar/points/999999/series", 404, "not_found"),  # ResultNotFound 경로
    ]
    for url, status, code in cases:
        r = client.get(url)
        assert r.status_code == status, url
        body = r.json()
        assert "detail" not in body, f"{url}: FastAPI 기본형이 새어나옴"
        assert body["error"]["code"] == code, url
        assert body["error"]["message"], url
