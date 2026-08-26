"""교량 맞춤형 PINN 오케스트레이션 — 위치 하나로 제원·온도·교통량 자동수집→실행.

이미 `/insar` 계약이 있는 project.h5(실 Track 인제스트 후)에 대해:
  1) `bridge_info` 로 교량 제원(BridgeProfile) 자동 구성 (OSM 무키 / data.go.kr 키)
  2) `weather` 로 취득일별 기온 (Open-Meteo, 무키)
  3) `traffic` 로 교통량 (공공 교통 API, 키)  — 키 없으면 생략
  4) 그 제원·외생으로 PINN(real, 형식별 PDE) + FRAM 을 기존 /insar 위에 실행

수집 실패 항목은 폴백한다(제원=강재 거더 기본, 온도=계절 가정, 교통량=자유 하중).
기존 /insar 는 보존하고 /pinn·/fram 만 (재)계산한다 — 실데이터 변위를 다시 안 받는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from .config import PipelineConfig
from .contracts.io import ProjectStore
from .contracts.schema import InSAROutput

if TYPE_CHECKING:
    from .structure import BridgeProfile


# 이 거리 안이면 '같은 교량'으로 신뢰. 넘으면 다른 교량일 수 있어 경고한다
# (S1 프레임 기준 수백 m 는 이웃 교량이 흔히 들어오는 거리다).
BRIDGE_MATCH_TRUST_M = 150.0


def _load_profile(src) -> "BridgeProfile":
    """사용자 제원 — BridgeProfile 객체 / dict / JSON 경로 무엇이든 받는다."""
    from .structure import BridgeProfile
    if isinstance(src, BridgeProfile):
        prof = src
    else:
        data = src if isinstance(src, dict) else json.loads(
            Path(src).read_text(encoding="utf-8"))
        prof = BridgeProfile(**data)
    if prof.source in (None, "", "default"):
        prof = prof.model_copy(update={"source": "manual"})
    return prof


def _match_warnings(prof, requested_name: str | None, max_km: float) -> list[str]:
    """CSV 최근접 매칭이 '의도한 그 교량'이 아닐 수 있는 사유 목록(없으면 빈 리스트)."""
    warns: list[str] = []
    dist = prof.extra.get("match_dist_m")
    try:
        dist_f = float(dist)
    except (TypeError, ValueError):
        dist_f = None
    if requested_name and prof.name and str(prof.name).strip() != str(requested_name).strip():
        warns.append(
            f"이름 불일치: 요청 '{requested_name}' vs 표준데이터 '{prof.name}' — "
            "표준데이터에 대상 교량이 없어 이웃 교량이 잡혔을 수 있습니다.")
    if dist_f is not None and dist_f > BRIDGE_MATCH_TRUST_M:
        warns.append(
            f"매칭 거리 {dist_f:.0f}m > {BRIDGE_MATCH_TRUST_M:.0f}m — 다른 교량의 제원"
            "(스팬·형식·재료)으로 PINN 이 돌 수 있습니다. "
            f"--bridge-csv-max-km 를 줄이거나(현재 {max_km}km) 제원을 직접 지정하세요.")
    return warns


def run_custom_pinn(
    project_h5: str | Path,
    lat: float,
    lon: float,
    *,
    bridge_name: str | None = None,
    radius_m: float = 200.0,
    bridge_csv: str | Path | None = None,
    bridge_csv_max_km: float = 1.0,
    bridge_profile: "BridgeProfile | dict | str | Path | None" = None,
    data_go_kr_key: str | None = None,
    data_go_kr_endpoint: str | None = None,
    data_go_kr_params: dict[str, str] | None = None,
    data_go_kr_field_map: dict[str, str] | None = None,
    traffic_ex_key: str | None = None,          # 한국도로공사 EX API 인증키(turnkey)
    traffic_key: str | None = None,
    traffic_endpoint: str | None = None,
    traffic_date_field: str | None = None,
    traffic_count_field: str | None = None,
    traffic_params: dict[str, str] | None = None,
    fram_mode: str = "real",
    reference_range: dict | bool | None = True,   # CRI 정상범위(dict) / True=패키지기본 / None=끔
    pinn_epochs: int = 600,
    pinn_virtual_sensors: int = 200,
    pinn_deck_long: int = 60,
    pinn_deck_trans: int = 9,
) -> dict[str, Any]:
    """위치의 교량을 자동 프로파일링해 맞춤형 PINN+FRAM 실행. 수집·결과 요약 반환."""
    project_h5 = Path(project_h5)
    collected: dict[str, Any] = {}

    with ProjectStore(project_h5, mode="a") as store:
        if not store.has_meta("insar"):
            raise ValueError("project.h5 에 /insar 가 없습니다 — 먼저 Track 을 인제스트하세요.")
        insar = store.read_meta("insar", InSAROutput)
        date_labels = None
        if store.has_array("/insar/date_labels"):
            date_labels = [str(d) for d in store.read_array("/insar/date_labels").astype(str)]

        # 1) 교량 제원 — 사용자 지정(bridge_profile) > 표준데이터 CSV(최근접) > OSM/API.
        #    CSV 자동탐색은 CLI 레이어(__main__)에서 하고, 여기선 명시적으로 받은 것만 쓴다.
        prof = None
        official_grade = None
        if bridge_profile is not None:
            # 표준데이터에 없는 교량(도시관리·신설 등)에서 설계도서·실측 제원을 직접 넣는 경로.
            # 최근접 매칭이 이웃 교량을 집어오는 위험을 원천 차단한다.
            prof = _load_profile(bridge_profile)
            official_grade = prof.extra.get("grade")
            collected["bridge_csv"] = f"사용자 지정 제원({getattr(bridge_profile, 'name', bridge_profile)})"
        elif bridge_csv:
            from .public_data import nearest_bridge_profile
            prof = nearest_bridge_profile(bridge_csv, lat, lon, max_km=bridge_csv_max_km)
            if prof is not None:
                official_grade = prof.extra.get("grade")     # 공식 시설물종별등급(추정보다 우선)
                _dl = prof.extra.get("design_load")
                collected["bridge_csv"] = (
                    f"전국교량표준데이터 최근접 {prof.name}"
                    f"(거리 {prof.extra.get('match_dist_m')}m, 설계활하중 {_dl or '-'}, "
                    f"점검 {prof.extra.get('inspect_grade') or '-'})")
                # ⚠️ '최근접'은 '그 교량'이 아니다 — 표준데이터에 없는 도시관리 교량이면
                # 수백 m 떨어진 **다른 교량** 제원(스팬·형식·재료)으로 PINN 이 돌아
                # 구조 해석이 통째로 틀린다. 이름·거리 불일치를 반드시 표면화한다.
                collected["bridge_match_warnings"] = _match_warnings(
                    prof, bridge_name, bridge_csv_max_km)
            else:
                collected["bridge_csv"] = f"CSV 내 {bridge_csv_max_km}km 이내 교량 없음 → OSM 폴백"
        if prof is None:
            from .bridge_info import fetch_bridge_profile
            prof = fetch_bridge_profile(
                lat, lon, name=bridge_name, radius_m=radius_m,
                data_go_kr_key=data_go_kr_key, data_go_kr_endpoint=data_go_kr_endpoint,
                data_go_kr_params=data_go_kr_params, data_go_kr_field_map=data_go_kr_field_map)
        collected["profile_source"] = prof.source

        # 2) 온도 (Open-Meteo, 무키)
        temperature = None
        if date_labels:
            try:
                from .weather import fetch_temperature_series
                temperature = fetch_temperature_series(lat, lon, date_labels)
                collected["temperature"] = f"{len(temperature)}일 (Open-Meteo ERA5)"
            except Exception as exc:  # noqa: BLE001 — 수집 실패는 계절가정 폴백
                collected["temperature"] = f"실패→계절가정 폴백 ({exc})"
        else:
            collected["temperature"] = "취득일(date_labels) 없음 → 계절가정"

        # 3) 교통량 — 한국도로공사 EX API(turnkey, 키만) 우선, 없으면 generic 엔드포인트
        traffic_series = None
        if traffic_ex_key and date_labels:
            from .traffic import fetch_ex_daily_traffic
            traffic_series = fetch_ex_daily_traffic(date_labels, key=traffic_ex_key)
            collected["traffic"] = (
                f"한국도로공사 EX 일자별 전국 교통량 {len(traffic_series)}일"
                if traffic_series is not None else "EX API 실패/빈응답 → 자유하중 폴백")
        elif traffic_key and traffic_endpoint and traffic_date_field and traffic_count_field and date_labels:
            from .traffic import fetch_traffic_series
            traffic_series = fetch_traffic_series(
                date_labels, service_key=traffic_key, endpoint=traffic_endpoint,
                date_field=traffic_date_field, count_field=traffic_count_field,
                params=traffic_params)
            collected["traffic"] = "수집됨" if traffic_series is not None else "실패→자유하중 폴백"
        else:
            collected["traffic"] = "키 없음 → 자유하중(설계활하중 DB등급 반영)"

        # 4) cfg + 실행 (기존 /insar 위)
        cfg = PipelineConfig(n_points=insar.n_points, n_dates=insar.n_dates)
        cfg.bridge_profile = prof.model_dump()
        from .insar.bridge_meta import bridge_grade, max_span_estimate
        _span = max_span_estimate(prof.bridge_type, prof.length_m)
        # ⑪ 종별 → FRAM 경보차등. 공식 시설물종별등급구분(CSV) 있으면 추정보다 우선.
        cfg.bridge_grade = official_grade or bridge_grade(prof.length_m, _span)
        collected["bridge_grade"] = (
            f"{cfg.bridge_grade}(공식)" if official_grade else f"{cfg.bridge_grade}(추정)")
        try:                                            # ③ 지형(산지/해상)→ FRAM 환경 경보차등
            from .insar.bridge_meta import terrain_class
            from .insar.bridge_profile import water_context_for
            _water = water_context_for(prof.bridge_type, prof.length_m)
            _terr, _relief = terrain_class(lat, lon, _water)
            cfg.bridge_terrain = _terr
            collected["terrain"] = f"{_terr}(기복{_relief}m)" if _relief else _terr
        except Exception as exc:  # noqa: BLE001 — 표고 조회 실패 시 지형 미반영(폴백)
            collected["terrain"] = f"실패({exc})"
        # 상태·노후화 → FRAM 경보차등: 안전점검결과(A~E)·준공연도(공용연수). CSV 있을 때만.
        _insp = prof.extra.get("inspect_grade")
        _built = prof.extra.get("completion")
        cfg.bridge_inspect_grade = _insp
        cfg.bridge_build_year = _built
        collected["inspect_grade"] = _insp or "-"
        collected["build_year"] = _built or "-"
        cfg.pinn_epochs = pinn_epochs
        cfg.pinn_virtual_sensors = pinn_virtual_sensors
        cfg.pinn_deck_long = pinn_deck_long
        cfg.pinn_deck_trans = pinn_deck_trans
        if temperature is not None:
            cfg.pinn_temperature = np.asarray(temperature, dtype=float)
        if traffic_series is not None:
            cfg.pinn_traffic = np.asarray(traffic_series, dtype=float)

        from .pinn.real_engine import run_pinn_real
        pinn = run_pinn_real(store, insar, cfg)
        try:                                    # 가상센싱 상부거더 전체 변위장 요약
            collected["girder_virtual_sensing"] = store.read_json_attr("pinn", "virtual_sensing")
        except (KeyError, ValueError):
            collected["girder_virtual_sensing"] = None
        if fram_mode == "real":
            from .fram.real_engine import run_fram_real as run_fram
        else:
            from .fram.engine import run_fram
        # CRI 정상범위(reference range): 건강 인구 대비 판독 등급. True=패키지 기본치.
        if reference_range:
            from .fram.reference_range import default_reference_range
            cfg.fram_reference_range = (reference_range if isinstance(reference_range, dict)
                                        else default_reference_range().to_dict())
        fram = run_fram(store, insar, pinn, cfg)
        try:
            collected["reference_range"] = store.read_json_attr("fram", "reference_range")
        except (KeyError, ValueError):
            collected["reference_range"] = None

    return {
        "bridge_name": prof.name or bridge_name,
        "bridge_type": prof.bridge_type,
        "material": prof.material,
        "span_m": prof.length_m,
        "profile": prof.model_dump(),
        "collected": collected,
        "n_points": insar.n_points, "n_dates": insar.n_dates,
        "cri_global_max": float(fram.cri_global_max),
        "warning_level": fram.warning.level,
        "warning_basis": fram.warning.basis,
        "reference_range": collected.get("reference_range"),
        "critical_members": list(fram.warning.critical_members),
    }
