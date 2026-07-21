"""CLI 진입점.

사용:
  python -m inframon --demo
  python -m inframon --demo --out data/project.h5
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import PipelineConfig
from .insar.inventory import build_scene_manifest, inspect_insar_data, write_inventory
from .insar.track_reader import import_track_h5
from .orchestrator.pipeline import run_pipeline


def main() -> None:
    # Windows 콘솔(cp949)에서도 한글/특수문자 출력이 깨지지 않도록 UTF-8 강제
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    # 내부 플래그: 데스크톱 런처가 서버 자식 프로세스를 띄울 때 사용(사용자용 아님).
    from .desktop import RUN_SERVER_FLAG

    if RUN_SERVER_FLAG in sys.argv:
        from .desktop import _run_streamlit_server

        idx = sys.argv.index(RUN_SERVER_FLAG)
        port = int(sys.argv[idx + 1])
        _run_streamlit_server(port)
        return
    p = argparse.ArgumentParser(prog="inframon", description="인프라 모니터링 통합 파이프라인 (Phase 0)")
    p.add_argument("--demo", action="store_true", help="더미 데이터로 전체 파이프라인 실행")
    p.add_argument("--out", default="data/project.h5", help="결과 HDF5 경로")
    p.add_argument("--points", type=int, default=None, help="InSAR 측정점 수 N")
    p.add_argument("--dates", type=int, default=None, help="취득 시점 수 M")
    p.add_argument("--inspect-data", default=None, help="실제 InSAR 데이터 루트 점검 후 종료")
    p.add_argument("--ingest-data", default=None, help="실제 InSAR 데이터 인벤토리를 --out HDF5에 기록")
    p.add_argument("--import-track-h5", default=None, help="Track 결과 HDF5를 /insar 계약으로 변환")
    p.add_argument("--check-track", default=None, metavar="TRACK_H5",
                   help="Track 결과 HDF5 투입 전 사전검증(preflight) 후 종료(ready=0/not=1)")
    p.add_argument("--insar-conditions", default=None, metavar="RECIPE_DIR",
                   help="교량 InSAR 신뢰성 조건(기하·시간샘플링·산란체·처리)을 레시피로 평가 후 종료. "
                        "SARvey 교량 맞춤의 전제조건 게이팅(--check-track 의 입력측 짝).")
    p.add_argument("--insar-progress", default=None, metavar="WORK_DIR",
                   help="WSL2 InSAR 처리(SLC→ISCE2→MiaplPy→SARvey) 진행판을 시각화 후 종료. "
                        "--recipe 로 기대 장면수, --watch N 으로 N초마다 실시간 갱신.")
    p.add_argument("--watch", type=int, default=None, metavar="SEC",
                   help="--insar-progress 를 SEC 초마다 갱신(Ctrl+C 종료).")
    p.add_argument("--export-csv", default=None, metavar="CSV",
                   help="--out 의 project.h5 를 KAIA 변위 CSV(점×시점 롱포맷)로 내보내고 종료. "
                        "--bridge-id 로 교량ID, --srs 로 좌표계 지정.")
    p.add_argument("--bridge-id", default="", metavar="ID",
                   help="--export-csv/--export-vlm 의 bridge_id(Bmaps 교량관리번호). 기본 빈값.")
    p.add_argument("--export-vlm", default=None, metavar="DIR",
                   help="--out 의 project.h5 를 VLM 입력 패키지 폴더(manifest·csv·summary·"
                        "narrative·figures)로 내보내고 종료. --bridge-id/--srs/--zip/--no-figures.")
    p.add_argument("--zip", action="store_true", help="--export-vlm 패키지를 .zip 으로도 묶는다.")
    p.add_argument("--no-figures", action="store_true",
                   help="--export-vlm 에서 figures PNG 생성을 생략(matplotlib 불필요).")
    p.add_argument("--export-kg", default=None, metavar="JSON",
                   help="--out 의 project.h5 를 지식그래프(nodes/edges + triples + JSON-LD)로 "
                        "내보내고 종료(KG/VLM 확장점). --bridge-id/--srs.")
    p.add_argument("--vlm-eval", default=None, metavar="DIR",
                   help="VLM 패키지 폴더(--export-vlm 산출)를 VLM 백엔드로 평가해 assessment.json "
                        "을 쓰고 종료. --vlm-backend 로 백엔드 선택(기본 template).")
    p.add_argument("--vlm-backend", default="template", metavar="NAME",
                   help="--vlm-eval 백엔드 이름(기본 template). 실 모델은 register_backend 로 등록.")
    p.add_argument("--download-slc", default=None, metavar="RECIPE_DIR",
                   help="레시피(processing_manifest.json)의 선별 SLC 를 Earthdata 자격으로 자동 "
                        "다운로드하고 종료. --slc-out/--slc-limit, 자격은 --earthdata-*.")
    p.add_argument("--slc-out", default=None, metavar="DIR",
                   help="--download-slc 저장 폴더(기본 <RECIPE_DIR>/SLC).")
    p.add_argument("--slc-limit", type=int, default=0, metavar="N",
                   help="--download-slc 처음 N장만(기본 0=전체 선별 장면).")
    p.add_argument("--earthdata-user", default=None, help="Earthdata 사용자 ID.")
    p.add_argument("--earthdata-pass", default=None, help="Earthdata 비밀번호.")
    p.add_argument("--earthdata-token", default=None, help="Earthdata 토큰(우선). 없으면 user/pass·~/.netrc.")
    p.add_argument("--insar-tools", action="store_true",
                   help="InSAR F코어 처리도구(ISCE2/MiaplPy/SARvey) 설치 상태를 감지·안내하고 종료.")
    p.add_argument("--doctor", nargs="?", const="", default=None, metavar="PATH",
                   help="환경·데이터 준비도 진단 후 종료. PATH 가 폴더면 인벤토리, .h5 면 preflight 포함")
    p.add_argument("--bridge-csv", default=None, metavar="CSV",
                   help="전국교량표준데이터 CSV(data.go.kr/15081953). --custom-pinn 이 "
                        "최근접 교량의 실 제원·공식 종별등급을 사용.")
    p.add_argument("--traffic-ex-key", default=None, metavar="KEY",
                   help="한국도로공사 EX API 인증키(data.ex.co.kr, apiId=0617 일자별 전국 교통량). "
                        "--custom-pinn 이 취득일별 교통량을 PINN 하중 시간변조로 사용. "
                        "미지정시 환경변수 EX_API_KEY 사용.")
    p.add_argument("--custom-pinn", default=None, metavar="LAT,LON",
                   help="--out 의 /insar 위에 교량 맞춤형 PINN 실행 — 위치로 제원(OSM)·온도"
                        "(Open-Meteo) 자동수집 후 형식별 PDE. 키 불필요 경로.")
    p.add_argument("--pipeline", default=None, metavar="LAT,LON",
                   help="표준 교량 파이프라인(①교량→③ROI→②④트랙→⑤ERA5→⑥~⑫) 순서대로 실행/계획하고 상태 보고.")
    p.add_argument("--pipeline-mode", default="plan", choices=["plan", "full"],
                   help="--pipeline: plan(경량단계만)|full(SNAP·PINN·FRAM 전체 실행).")
    p.add_argument("--pipeline-adi", action="store_true",
                   help="--pipeline full: ⑨ PS/DS 를 진폭분산 ADI 로(쌍별 진폭 ~20분 추가). 기본 코히런스 1차.")
    p.add_argument("--export-bim", default=None, metavar="H5,OUT_PREFIX",
                   help="InSAR/PSI H5 → BIM/IFC 오버레이(GeoJSON+CSV). 값(LOS속도·연직·누적)+"
                        "값별 색+EPSG:5186 투영좌표. 예: data/psi.h5,data/bridge_bim")
    p.add_argument("--bim-crs", default="EPSG:5186",
                   help="--export-bim IFC 좌표계(기본 EPSG:5186 한국 중부원점).")
    p.add_argument("--bim-incidence", type=float, default=39.0, metavar="DEG",
                   help="--export-bim LOS→연직 투영 입사각(H5에 없을 때, 기본 39°).")
    p.add_argument("--bim-fram", default=None, metavar="PROJECT_H5",
                   help="--export-bim CRI 색: /fram/CRI 있는 project.h5 를 InSAR 점에 최근접 매핑.")
    p.add_argument("--gnss-validate", default=None, metavar="PROJECT_H5",
                   help="InSAR LOS 속도를 인근 NGL 상시 GNSS 와 대조(광역 기준 신뢰도 검증).")
    p.add_argument("--gnss-km", type=float, default=50.0, metavar="KM",
                   help="--gnss-validate GNSS 탐색 반경(기본 50km).")
    p.add_argument("--gnss-incidence", type=float, default=39.0, metavar="DEG",
                   help="--gnss-validate InSAR 입사각(기본 39° S1 IW 중앙).")
    p.add_argument("--gnss-heading", type=float, default=None, metavar="DEG",
                   help="--gnss-validate 위성 헤딩 override(기본 /insar 저장값 또는 S1 상승).")
    p.add_argument("--fem-crosscheck", default=None, metavar="PROJECT_H5",
                   help="상용 FEM 교차검증 — /pinn 식별 EI·고유진동수를 설계(기하 EI) FEM "
                        "벤치마크와 대조(솔버검증·강성상태·처짐).")
    p.add_argument("--fem-boundary", default=None,
                   choices=["simply_supported", "continuous", "fixed", "cantilever"],
                   help="--fem-crosscheck 경계조건 override (기본: /pinn 저장값 또는 단순지지).")
    p.add_argument("--fit-reference-range", default=None, metavar="GLOB_OR_LIST",
                   help="실측 건강 교량 project.h5 들(glob 또는 콤마목록)의 /fram/CRI 로 CRI "
                        "정상범위(reference range)를 적합해 --reference-out JSON 으로 저장. "
                        "현장 인구 기준치(합성 기본치 교체).")
    p.add_argument("--reference-out", default="data/reference_range.json", metavar="JSON",
                   help="--fit-reference-range 저장 경로(기본 data/reference_range.json).")
    p.add_argument("--reference-clean", action="store_true",
                   help="--fit-reference-range: 패키지 기본 정상범위의 정상범위 밖(경고·위험) 점을 "
                        "제외하고 건강 점만으로 적합(오염 방어).")
    p.add_argument("--validate", default=None, metavar="PROJECT_H5,REFERENCE_CSV",
                   help="현장 검증: project.h5 의 InSAR 결과를 기준 CSV(계측·FEM: lon,lat,value)와 대조(RMSE·bias·r).")
    p.add_argument("--validate-kind", default="velocity", choices=["velocity", "displacement"],
                   help="--validate 기준값 종류(기본 velocity[mm/yr]).")
    p.add_argument("--validate-vertical", action="store_true",
                   help="--validate 기준이 연직값이면(레벨링 등) 입사각으로 LOS 투영 후 비교.")
    p.add_argument("--validate-dist", type=float, default=50.0, metavar="M",
                   help="--validate 정합 최대거리[m] (기본 50).")
    p.add_argument("--validate-tol", type=float, default=5.0, metavar="MM",
                   help="--validate 통과 허용 RMSE[mm] (기본 5).")
    p.add_argument("--snap-insar", default=None, metavar="SLC_DIR",
                   help="SNAP(Windows 네이티브) 백엔드로 SLC_DIR 의 S1 SLC 처리 → Track H5 "
                        "(WSL/ISCE2 불필요). --snap-target 또는 --snap-bridges 필요.")
    p.add_argument("--snap-target", default=None, metavar="LAT,LON",
                   help="--snap-insar 단일 교량 대상 좌표.")
    p.add_argument("--snap-auto", default=None, metavar="LAT,LON",
                   help="프레임 자동선정: 교량 좌표로 ASF 조회→최적 프레임 선택→SLC 다운로드"
                        "→burst 포함 검증→SNAP 처리. Earthdata 자격 필요(--earthdata-*/~/.netrc).")
    p.add_argument("--snap-count", type=int, default=8, metavar="N",
                   help="--snap-auto 다운로드 장면 수(기본 8).")
    p.add_argument("--snap-start", default="2024-01-01", metavar="YYYY-MM-DD",
                   help="--snap-auto 조회 시작일(기본 2024-01-01).")
    p.add_argument("--snap-end", default="2025-07-01", metavar="YYYY-MM-DD",
                   help="--snap-auto 조회 종료일(기본 2025-07-01).")
    p.add_argument("--snap-bridges", default=None, metavar="JSON",
                   help="--snap-insar 배치: [{name,lat,lon},...] JSON 파일. 같은 burst 는 코레지 1회 재사용.")
    p.add_argument("--snap-dem", default="SRTM 1Sec HGT", metavar="NAME",
                   help="--snap-insar DEM 이름(SNAP, 기본 SRTM 1Sec HGT).")
    p.add_argument("--snap-era5-master", action="store_true",
                   help="⑤ ERA5(강수·습도·온도) 대기안정도로 master 선정 + 악천후 씬 소거(era5_master 연동).")
    p.add_argument("--snap-fuse", default=None, metavar="ASC_H5,DESC_H5",
                   help="⑦ 상승·하강 SNAP Track 연직분해(→--out). 하강 부족/기하특이 시 단일 폴백.")
    p.add_argument("--snap-gpt", default=None, metavar="PATH",
                   help="gpt 실행파일 경로(기본 자동탐지: C:\\Program Files\\esa-snap\\bin\\gpt.exe).")
    p.add_argument("--app", action="store_true",
                   help="대시보드를 전용 데스크톱 창에 띄운다(더블클릭 실행용). pywebview 필요")
    p.add_argument("--serve", action="store_true",
                   help="--out 의 FRAM 결과를 FastAPI 로 실시간 서빙(읽기전용). --port 로 포트")
    p.add_argument("--port", type=int, default=8000, help="--serve/--serve-api 포트 (기본 8000)")
    p.add_argument("--serve-api", action="store_true",
                   help="Bmaps 연동 다중 교량 InSAR API 서빙(읽기전용). --registry 로 교량 목록 지정")
    p.add_argument("--registry", default=None, metavar="JSON",
                   help="--serve-api 의 bridge_registry.json. 없으면 --out 을 단일 교량으로 노출")
    p.add_argument("--srs", default="wgs84", choices=["wgs84", "5179"],
                   help="--serve-api 좌표계: wgs84(기본, 재투영) | 5179(Bmaps 가 5179 타일일 때 재투영 생략)")
    p.add_argument("--cors-origin", action="append", default=[], metavar="URL",
                   help="--serve-api CORS 허용 출처(반복 가능). 미지정 시 전체 허용(*)")
    p.add_argument("--schedule", type=int, default=None, metavar="SECONDS",
                   help="--out 모니터링(PINN+FRAM 재계산·경보)을 SECONDS 간격 Prefect 스케줄 실행")
    p.add_argument(
        "--engine",
        action="append",
        default=[],
        metavar="NAME=MODE",
        help="엔진 구현 선택 (예: --engine insar=real). 반복 지정 가능. 기본 전부 stub.",
    )
    p.add_argument("--insar-source", default=None, help="insar=real 이 소비할 Track 결과 H5(기준/주 궤도)")
    p.add_argument("--insar-source-desc", default=None,
                   help="asc+desc 융합용 반대 궤도 Track H5. 있으면 연직+종축 분리, 불가 시 단일 폴백")
    p.add_argument("--insar-dem", default=None,
                   help="Track 에 점별 고도가 없을 때 z 를 샘플링할 DEM GeoTIFF(ISCE2용 DEM 등)")
    p.add_argument("--insar-corrections", action="store_true",
                   help="LOS 시계열에 정확도 보정(기준점 정합+고도상관 성층대기) 적용 후 저장. "
                        "insar=real 과 --import-track-h5 양쪽에 적용, /insar/velocity_mm_yr 기록")
    p.add_argument("--ref-min-coherence", type=float, default=0.9,
                   help="--insar-corrections 기준점 후보 최소 시간결맞음(부족 시 최고 coh 폴백)")
    p.add_argument("--insar-thermal", action="store_true",
                   help="열팽창(온도) 보정 — los=a+b·t+c·T 로 계절 열변형 분리(--insar-corrections 필요)")
    p.add_argument("--insar-temp-csv", default=None, metavar="CSV",
                   help="열팽창 보정 온도원: date,temp_C CSV(결정론적). 취득일별 기온[°C]")
    p.add_argument("--insar-fetch-temp", action="store_true",
                   help="온도 CSV 없을 때 ERA5(Open-Meteo, 키불필요·네트워크)로 취득일 온도 조회")
    p.add_argument("--resume", action="store_true",
                   help="기존 --out 에서 입력이 안 바뀐 단계는 재계산 생략(증분 재개)")
    p.add_argument("--force-stage", action="append", default=[], metavar="STAGE",
                   help="--resume 중에도 강제 재계산할 단계(cv/insar/pinn/fram, 하류 cascade). 반복 가능")
    p.add_argument("--make-sarvey-config", default=None, metavar="RECIPE_DIR",
                   help="레시피 4종 → SARvey 번들(processing_manifest.json + sarvey_config.json) 생성 후 종료")
    p.add_argument("--insar-process", default=None, choices=["demo", "real"],
                   help="InSAR 처리 파이프라인(F) 실행. demo=합성으로 어디서나, real=Linux/WSL plan")
    p.add_argument("--recipe", default="data/insar_recipe", help="--insar-process 의 레시피 폴더")
    p.add_argument("--work", default="data/insar_work", help="--insar-process real 의 작업 폴더")
    p.add_argument("--fuse-tracks", nargs="+", default=None, metavar="TRACK_H5",
                   help="여러 방법-트랙(A/B/C/D) Track H5 를 CS 합의 융합 → --out 에 융합 Track H5 저장 "
                        "후 종료. 트랙 간 일치도(검증) 리포트 출력.")
    p.add_argument("--support-zone", default=None, metavar="H5",
                   help="지지부 ZONE 점 추출·침하속도(project.h5 또는 track H5). 교량 선형은 --recipe 의 "
                        "bridge_target.json 에서. 후 종료.")
    p.add_argument("--support-piers", type=int, default=3, help="--support-zone 교각 수(추정)")
    p.add_argument("--support-buffer", type=float, default=30.0, help="--support-zone buffer[m]")
    args = p.parse_args()

    cfg = PipelineConfig()
    if args.points is not None:
        cfg.n_points = args.points
    if args.dates is not None:
        cfg.n_dates = args.dates
    for spec in args.engine:
        name, sep, mode = spec.partition("=")
        if not sep:
            p.error(f"--engine 형식은 NAME=MODE 입니다: {spec!r}")
        name, mode = name.strip(), mode.strip()
        if name not in cfg.engines:
            p.error(f"--engine 이름은 {tuple(cfg.engines)} 중 하나여야 합니다: {name!r}")
        cfg.engines[name] = mode
    if args.insar_source is not None:
        cfg.insar_source_h5 = args.insar_source
    if args.insar_source_desc is not None:
        cfg.insar_source_desc_h5 = args.insar_source_desc
    if args.insar_dem is not None:
        cfg.insar_dem_geotiff = args.insar_dem
    cfg.insar_apply_corrections = args.insar_corrections
    cfg.insar_ref_min_coherence = args.ref_min_coherence
    cfg.insar_thermal_correction = args.insar_thermal
    cfg.insar_temperature_csv = args.insar_temp_csv
    cfg.insar_fetch_temperature = args.insar_fetch_temp
    cfg.resume = args.resume
    cfg.force_stages = tuple(args.force_stage)
    try:
        cfg.validate()
    except ValueError as exc:
        p.error(str(exc))

    if args.support_zone:
        import h5py
        import numpy as np

        from .insar.recipe import load_bridge_target
        from .insar.support_zone import support_velocity, support_zone
        try:
            tgt = load_bridge_target(f"{args.recipe}/bridge_target.json")
        except Exception as exc:  # noqa: BLE001
            p.error(f"교량 타깃 로드 실패({args.recipe}/bridge_target.json): {exc}")
        nodes = [(la, lo) for la, lo in (tgt.geometry or [])]
        if len(nodes) < 2:
            p.error("교량 선형(geometry) 절점이 2개 미만 — 지지부 위치 산정 불가.")
        with h5py.File(args.support_zone, "r") as f:
            if "/insar/xyz" in f:
                xyz = f["/insar/xyz"][()]; los = f["/insar/los"][()]
                lonlat = xyz[:, :2]
                days = (f["/insar/dates"][()] if "/insar/dates" in f
                        else np.arange(los.shape[1], dtype=float))
            else:                                     # track H5
                lonlat = f["pixel_lonlat"][()]; los = f["los_mm"][()]
                ep = [str(int(e)) for e in f["epochs"][()]]
                from datetime import datetime
                d0 = datetime.strptime(ep[0], "%Y%m%d")
                days = np.array([(datetime.strptime(e, "%Y%m%d") - d0).days for e in ep], float)
        r = support_zone(lonlat, nodes, n_piers=args.support_piers, buffer_m=args.support_buffer)
        v = support_velocity(los, np.asarray(days, float), r["mask"])
        print(f"교량: {tgt.name}  (선형 {len(nodes)}절점)")
        print(f"지지부(교대2+교각{args.support_piers}) {args.support_buffer:.0f}m 이내 점: "
              f"{r['n_support_points']}개")
        for s in r["supports"]:
            print(f"  {s['kind']:9s} @ {s['lat']:.5f},{s['lon']:.5f}: "
                  f"{s['n']}점 · 최근접 {s['nearest_m']:.1f}m")
        if v["n"]:
            print(f"LOS 속도(침하): 평균 {v['mean_mm_yr']:+.2f} mm/yr "
                  f"(범위 {v['min_mm_yr']:+.1f}~{v['max_mm_yr']:+.1f})")
        else:
            print("지지부 buffer 내 점 없음 — buffer 확대 또는 데이터 보강 필요.")
        return

    if args.schedule:
        try:
            from .schedule import serve_schedule
        except ImportError:
            p.error("스케줄링에는 prefect 가 필요합니다: `pip install -e .[schedule]`")
        print(f"FRAM 모니터 스케줄: {args.schedule}초 간격  (project: {args.out})")
        print("  매 사이클 PINN+FRAM 재계산 → 경보 에스컬레이션 (Ctrl+C 종료)")
        serve_schedule(args.out, interval_seconds=args.schedule)
        return

    if args.app:
        from .desktop import run_app

        sys.exit(run_app())

    if args.serve:
        try:
            from .serve import serve
        except ImportError:
            p.error("서빙에는 fastapi·uvicorn 이 필요합니다: `pip install -e .[serve]`")
        print(f"FRAM 실시간 모니터: http://127.0.0.1:{args.port}  (project: {args.out})")
        print("  GET /health · /status · /cri · /function-network   (Ctrl+C 종료)")
        serve(args.out, port=args.port)
        return

    if args.serve_api:
        try:
            from .api.app import serve_api
            from .api.registry import BridgeRegistry, RegistryError
            from .api.transform import SRC_CRS, WGS84
        except ImportError:
            p.error("Bmaps API 서빙에는 fastapi·uvicorn 이 필요합니다: `pip install -e .[serve]`")
        if args.registry:
            try:
                reg = BridgeRegistry.from_file(args.registry)
            except RegistryError as exc:
                p.error(str(exc))
            print(f"Bmaps InSAR API: http://127.0.0.1:{args.port}  ({len(reg)}개 교량, {args.registry})")
        else:
            # 레지스트리 없으면 --out 을 단일 교량으로 노출(기존 단일교량 흐름 호환).
            reg = BridgeRegistry.single("default", "default", args.out)
            print(f"Bmaps InSAR API: http://127.0.0.1:{args.port}  (단일 교량: {args.out})")
        print("  GET /api/v1/health · /bridges · /bridges/{id}/insar/{summary,points,cri,...}  (Ctrl+C 종료)")
        to_crs = SRC_CRS if args.srs == "5179" else WGS84
        origins = tuple(args.cors_origin) if args.cors_origin else ("*",)
        serve_api(reg, port=args.port, to_crs=to_crs, allow_origins=origins)
        return

    if args.snap_insar or args.snap_auto:
        import json as _json
        from pathlib import Path as _Path

        from .insar.snap_backend import SnapError, run_batch
        from .insar.snap_backend import run as snap_run

        out_dir = str(_Path(args.out).parent if args.out.endswith(".h5") else args.out)

        # 프레임 자동선정: 조회→선정→다운로드→burst 검증
        if args.snap_auto:
            from .insar.snap_acquire import AcquireError, acquire
            try:
                a_lat, a_lon = (float(v) for v in args.snap_auto.split(","))
            except ValueError:
                p.error("--snap-auto 형식은 LAT,LON 입니다 (예: 37.3219,127.1083)")
            acq_dir = args.snap_insar or str(_Path(out_dir) / "auto_acquire")
            try:
                acq = acquire(a_lat, a_lon, acq_dir, count=args.snap_count,
                              start=args.snap_start, end=args.snap_end,
                              username=args.earthdata_user, password=args.earthdata_pass,
                              token=args.earthdata_token)
            except (AcquireError, SnapError) as exc:
                p.error(str(exc))
            print("=" * 56)
            print("  프레임 자동선정 완료")
            print("=" * 56)
            print(f"  선정 프레임 : {acq.frame.label()}  "
                  f"(중심성 {acq.frame.centrality_km:+.1f}km, {acq.frame.n_scenes}장 중 {len(acq.downloaded)} 다운)")
            print(f"  burst      : {acq.burst.subswath}#{acq.burst.burst_index} "
                  f"({'포함' if acq.contained else '⚠️ 밖'})")
            for c in acq.considered[:-1]:
                print(f"    · 건너뜀 : {c}")
            print("-" * 56)
            args.snap_insar = acq.slc_dir            # 다운로드 폴더로 처리 계속
            if not args.snap_target and not args.snap_bridges:
                args.snap_target = args.snap_auto    # 자동선정 좌표로 단일 처리

        scenes = sorted(str(x) for x in _Path(args.snap_insar).glob("*.zip"))
        if not scenes:
            p.error(f"SLC(.zip) 를 찾지 못함: {args.snap_insar}")
        try:
            if args.snap_bridges:
                bridges = _json.loads(_Path(args.snap_bridges).read_text(encoding="utf-8"))
                results = run_batch(scenes, bridges, out_dir, dem=args.snap_dem,
                                    gpt=args.snap_gpt)
                print("=" * 56)
                print(f"  SNAP(Windows) 배치 완료 — 교량 {len(results)}개")
                print("=" * 56)
                for r in results:
                    tag = f"N={r.n_points} {r.burst}" if r.track_h5 else f"실패: {r.error}"
                    print(f"  · {r.name:<20} {tag}")
                    if r.track_h5:
                        print(f"      → {r.track_h5}")
                print("=" * 56)
            else:
                if not args.snap_target:
                    p.error("--snap-insar 는 --snap-target LAT,LON 또는 --snap-bridges JSON 이 필요합니다")
                try:
                    lat, lon = (float(v) for v in args.snap_target.split(","))
                except ValueError:
                    p.error("--snap-target 형식은 LAT,LON 입니다 (예: 37.3219,127.1083)")
                out_h5 = args.out if args.out.endswith(".h5") else str(_Path(out_dir) / "track_snap.h5")
                res = snap_run(scenes, lat, lon, out_dir=out_dir, out_h5=out_h5,
                               dem=args.snap_dem, gpt=args.snap_gpt,
                               era5_master=args.snap_era5_master)
                print("=" * 56)
                print("  SNAP(Windows 네이티브) InSAR → Track H5 완료")
                if res.weather is not None and hasattr(res.weather, "selected_master"):
                    print(f"  ⑤ERA5 master: {res.weather.selected_master} · "
                          f"악천후 소거 {getattr(res.weather, 'n_excluded', 0)}장")
                print("=" * 56)
                _cov = (f"교량 포함(가장자리서 {res.burst.distance_km:.1f}km 안쪽)"
                        if res.burst.contained
                        else f"⚠️ 커버리지 밖 ~{res.burst.distance_km:.1f}km — 다른 프레임/궤도 권장")
                print(f"  기준영상   : {res.reference}   burst {res.burst.subswath}#{res.burst.burst_index}"
                      f" ({_cov})")
                print(f"  간섭도쌍   : {sum(pp.ok for pp in res.pairs)}/{len(res.pairs)} 성공")
                print(f"  측정점     : N={res.n_points}")
                print(f"  Track H5   : {res.track_h5}")
                print("-" * 56)
                print(f"  다음: python -m inframon --import-track-h5 {res.track_h5} --out data/project.h5")
                print(f"        python -m inframon --custom-pinn {lat},{lon} --out data/project.h5")
                print("=" * 56)
        except SnapError as exc:
            p.error(str(exc))
        return

    if args.snap_fuse:
        from .insar.snap_backend import fuse_snap_asc_desc
        try:
            _asc, _desc = args.snap_fuse.split(",")
        except ValueError:
            p.error("--snap-fuse 형식은 ASC_H5,DESC_H5 입니다")
        r = fuse_snap_asc_desc(_asc.strip(), _desc.strip(),
                               args.out if args.out.endswith(".h5") else None)
        print("=" * 56)
        print(f"  ⑦ asc+desc 연직분해 — {r['mode']}")
        print("=" * 56)
        if r["mode"] == "fused":
            print(f"  연직 Track : {r['out']}  (N={r['n_points']}, {r['n_epochs']}시점)")
            print(f"  연직 범위  : {r['vertical_mm_range'][0]:.1f} ~ {r['vertical_mm_range'][1]:.1f} mm")
        else:
            print(f"  단일 폴백  : {r['reason']}")
            print(f"  사용 Track : {r['out']}")
        print("=" * 56)
        return

    if args.export_bim:
        from .insar.bim_export import export_insar_for_bim
        try:
            _h5, _pref = args.export_bim.split(",")
        except ValueError:
            p.error("--export-bim 형식은 H5,OUT_PREFIX 입니다")
        r = export_insar_for_bim(_h5.strip(), _pref.strip(), ifc_crs=args.bim_crs,
                                 incidence_deg=args.bim_incidence,
                                 fram_project_h5=args.bim_fram)
        print("=" * 56)
        print("  InSAR → BIM/IFC 오버레이 내보내기")
        print("=" * 56)
        print(f"  점 {r['n_points']}개 · CRS {r['ifc_crs']}")
        print(f"  값(UI 토글): {', '.join(r['values'])}")
        print(f"  GeoJSON: {r['geojson']}")
        print(f"  CSV    : {r['csv']}")
        print("=" * 56)
        return

    if args.gnss_validate:
        from .gnss_ngl import validate_insar_vs_gnss
        try:
            r = validate_insar_vs_gnss(args.gnss_validate, incidence_deg=args.gnss_incidence,
                                       heading_deg=args.gnss_heading, max_km=args.gnss_km)
        except (ValueError, FileNotFoundError, OSError) as exc:
            p.error(str(exc))
        print(r.summary())
        return

    if args.fem_crosscheck:
        from .fem_crosscheck import crosscheck_project
        try:
            r = crosscheck_project(args.fem_crosscheck, boundary=args.fem_boundary)
        except (ValueError, FileNotFoundError, OSError) as exc:
            p.error(str(exc))
        print(r.summary())
        return

    if args.validate:
        from .validation import load_reference_csv, validate_project
        try:
            _proj, _ref = args.validate.split(",")
        except ValueError:
            p.error("--validate 형식은 PROJECT_H5,REFERENCE_CSV 입니다")
        ref = load_reference_csv(_ref.strip(), kind=args.validate_kind,
                                 vertical=args.validate_vertical)
        r = validate_project(_proj.strip(), ref, max_dist_m=args.validate_dist,
                             tolerance_mm=args.validate_tol,
                             project_to_los=args.validate_vertical)
        print("=" * 56)
        print("  현장 검증 (InSAR/PINN vs 계측·FEM 기준)")
        print("=" * 56)
        print("  " + r.summary())
        print("=" * 56)
        return

    if args.pipeline:
        from .pipeline_bridge import run_bridge_pipeline
        try:
            _lat, _lon = (float(v) for v in args.pipeline.split(","))
        except ValueError:
            p.error("--pipeline 형식은 LAT,LON 입니다 (예: 37.3219,127.1083)")
        rep = run_bridge_pipeline(_lat, _lon, mode=args.pipeline_mode,
                                  earthdata_token=args.earthdata_token, do_adi=args.pipeline_adi)
        print(rep.summary())
        return

    if args.custom_pinn:
        from .custom_pinn import run_custom_pinn

        try:
            lat, lon = (float(v) for v in args.custom_pinn.split(","))
        except ValueError:
            p.error("--custom-pinn 형식은 LAT,LON 입니다 (예: 37.3634,127.1090)")
        ex_key = args.traffic_ex_key or os.environ.get("EX_API_KEY")
        bridge_csv = args.bridge_csv
        if not bridge_csv:                       # CLI 편의: data/ 에서 표준데이터 CSV 자동탐색
            from .public_data import default_bridge_csv
            bridge_csv = default_bridge_csv()
        try:
            summary = run_custom_pinn(args.out, lat, lon, bridge_csv=bridge_csv,
                                      traffic_ex_key=ex_key)
        except (ValueError, FileNotFoundError) as exc:
            p.error(str(exc))
        _coll = summary['collected']
        print("=" * 56)
        print("  교량 맞춤형 PINN 완료")
        print("=" * 56)
        print(f"  교량       : {summary['bridge_name'] or '-'} ({summary['bridge_type']}/{summary['material']})")
        print(f"  스팬       : {summary['span_m']} m  · 제원출처 {_coll['profile_source']}")
        if _coll.get('bridge_csv'):
            print(f"  표준데이터 : {_coll['bridge_csv']}")
        print(f"  종별등급   : {_coll.get('bridge_grade', '-')}  · 지형 {_coll.get('terrain', '-')}")
        print(f"  상태·노후  : 안전점검 {_coll.get('inspect_grade', '-')}  · 준공 {_coll.get('build_year', '-')}")
        print(f"  온도       : {_coll['temperature']}")
        print(f"  교통량     : {_coll['traffic']}")
        print(f"  최대 CRI   : {summary['cri_global_max']:.3f}  경보 {summary['warning_level']}")
        print("=" * 56)
        return

    if args.doctor is not None:
        from .doctor import format_report, run_doctor

        rep = run_doctor(args.doctor or None)
        print(format_report(rep))
        sys.exit(0 if rep.core_ok else 1)

    if args.inspect_data or args.ingest_data:
        inv = inspect_insar_data(args.inspect_data or args.ingest_data)
        print("=" * 56)
        print("  InSAR 데이터 인벤토리")
        print("=" * 56)
        print(f"  루트            : {inv.root}")
        print(f"  SLC zip         : {inv.slc_zip_count}")
        print(f"  SLC 날짜 범위   : {inv.slc_first_date or '-'} ~ {inv.slc_last_date or '-'}")
        print(f"  SLC 총 용량     : {inv.slc_total_gb:.2f} GB")
        print(f"  Orbit EOF       : {inv.orbit_count}")
        print(f"  DEM 파일        : {len(inv.dem_files)}")
        print(f"  ROI/KMZ         : {', '.join(inv.roi_files) or '-'}")
        if inv.declared_slc_count is not None:
            print(f"  문서상 SLC      : {inv.declared_slc_count}")
        print(f"  Master          : {inv.selected_master or '-'}")
        print(f"  ERA5 Master     : {inv.selected_master_era5 or '-'}")
        print(f"  Bperp 통과/제외 : {inv.bperp_pass_count or 0}/{inv.bperp_exclude_count or 0}")
        print(f"  제외 날짜 수    : {len(inv.exclude_dates)}")
        print(f"  필수 누락       : {', '.join(inv.missing_required) or '없음'}")
        manifest = build_scene_manifest(inv.root)
        print(f"  실제 사용 장면  : {manifest['usable_count']} / {manifest['source_slc_count']}")
        print(f"  Bperp stale     : {len(manifest['stale_bperp_dates'])}")
        print(f"  Bperp 누락      : {len(manifest['missing_bperp_dates'])}")
        print(f"  현재 제외 장면  : {len(manifest['excluded_present_dates'])}")
        if inv.warnings:
            print("  경고")
            for warning in inv.warnings:
                print(f"    - {warning}")
        if args.ingest_data:
            write_inventory(args.out, inv)
            print(f"  기록된 파일     : {args.out}")
        print("=" * 56)
        return

    if args.insar_process:
        from .insar import processing

        if args.insar_process == "real":
            print("=" * 56)
            print("  InSAR 처리(F) — real plan (Linux/WSL 에서 순서대로 실행)")
            print("=" * 56)
            for line in processing.plan_real(args.recipe, args.work, project_h5=args.out):
                print("  " + line)
            print("=" * 56)
            return
        # demo
        try:
            fram = processing.run_demo(args.recipe, args.out)
        except (FileNotFoundError, ValueError) as exc:
            p.error(str(exc))
        print("=" * 56)
        print("  InSAR 처리(F) — demo 완료 (합성 시계열 → /insar → PINN → FRAM)")
        print("=" * 56)
        print(f"  레시피         : {args.recipe}")
        print(f"  결과 파일      : {args.out}")
        print(f"  측정점/시점    : N={fram.n_points}, M={fram.n_dates}")
        print(f"  최대 CRI       : {fram.cri_global_max:.3f}")
        print(f"  경보 등급      : {fram.warning.level}")
        print("=" * 56)
        return

    if args.make_sarvey_config:
        from .insar.sarvey_config import write_sarvey_bundle

        try:
            paths = write_sarvey_bundle(args.make_sarvey_config)
        except FileNotFoundError as exc:
            p.error(str(exc))
        print("=" * 56)
        print("  SARvey 번들 생성 완료")
        print("=" * 56)
        print(f"  처리 매니페스트 : {paths['manifest']}  (ISCE2/MiaplPy 스택 생성용)")
        print(f"  SARvey config   : {paths['config']}  (시계열 추정용)")
        print("=" * 56)
        return

    if args.check_track:
        from .insar.track_preflight import preflight_track_h5

        rep = preflight_track_h5(args.check_track)
        print("=" * 56)
        print("  Track HDF5 투입 사전검증 (preflight)")
        print("=" * 56)
        print(f"  파일            : {rep.path}")
        print(f"  측정점/시점     : N={rep.n_points} / M={rep.n_dates}")
        print(f"  취득일 범위     : {rep.date_first or '-'} ~ {rep.date_last or '-'}")
        if rep.coherence_min is not None:
            print(f"  coherence       : {rep.coherence_min:.3f} ~ {rep.coherence_max:.3f}")
        if rep.los_finite_frac is not None:
            print(f"  LOS 유한 비율   : {rep.los_finite_frac * 100:.1f}%")
        print(f"  고도(z)/CRS     : {'있음' if rep.has_height else '없음'} / {rep.crs or '-'}"
              + ("  (경위도로 보임)" if rep.looks_geographic else ""))
        if rep.errors:
            print("  ❌ 차단 오류")
            for e in rep.errors:
                print(f"    - {e}")
        for w in rep.warnings:
            print(f"  ⚠️  {w}")
        print(f"  판정            : {'✅ 투입 가능' if rep.is_ready else '❌ 투입 불가'}")
        print("=" * 56)
        sys.exit(0 if rep.is_ready else 1)

    if args.insar_progress:
        import time

        from .insar.progress import render_board, scan_progress

        recipe = args.recipe or args.insar_progress  # work 안에 레시피가 있을 수도
        title = ""
        try:
            from .insar.sarvey_config import RecipeBundle
            rb = RecipeBundle(recipe)
            if rb.target is not None:
                title = rb.target.name_ko or rb.target.name
        except Exception:  # noqa: BLE001
            recipe = None
        while True:
            from datetime import datetime
            prog = scan_progress(args.insar_progress, recipe)
            board = render_board(prog, title=title, now=datetime.now().strftime("%H:%M:%S"))
            if args.watch:
                print("\033[2J\033[H" + board, flush=True)   # 화면 지우고 재출력
                if prog["overall"] >= 1.0:
                    print("  ✅ 전 단계 완료 — track.h5 준비됨.")
                    break
                try:
                    time.sleep(max(1, args.watch))
                except KeyboardInterrupt:
                    break
            else:
                print(board)
                break
        return

    if args.insar_conditions:
        from .insar.bridge_conditions import conditions_report
        from .insar.bridge_profile import profile_for
        from .insar.sarvey_config import RecipeBundle

        b = RecipeBundle(args.insar_conditions)
        if b.target is None:
            p.error(f"bridge_target.json 이 없습니다: {args.insar_conditions} (교량 타깃을 먼저 저장)")
        prof = profile_for(b.target)
        rep = conditions_report(b.target, b.track, b.criteria, prof)
        _ICON = {"pass": "✅", "warn": "⚠️ ", "fail": "❌", "unknown": "❔"}
        print("=" * 64)
        print(f"  교량 InSAR 신뢰성 조건 — {b.target.name_ko or b.target.name}"
              f" ({prof.bridge_class_ko}·{prof.scale})")
        print("=" * 64)
        cat_ko = {"geometry": "기하", "temporal": "시간샘플링", "scatterer": "산란체",
                  "processing": "처리", "atmosphere": "대기"}
        last_cat = None
        for c in rep["conditions"]:
            if c["category"] != last_cat:
                print(f"\n[{cat_ko.get(c['category'], c['category'])}]")
                last_cat = c["category"]
            print(f"  {_ICON[c['status']]} {c['id']} {c['title']} — {c['detail']}")
            if c["status"] in ("warn", "fail"):
                print(f"       → {c['fix']}")
        cnt = rep["counts"]
        print("\n" + "-" * 64)
        print(f"  집계: ✅{cnt['pass']} ⚠️{cnt['warn']} ❌{cnt['fail']} ❔{cnt['unknown']}"
              f"  (조건 {rep['n_conditions']}개)")
        print(f"  게이트: {'✅ 진행 가능' if rep['ready'] else '❌ 차단(blocker)'}")
        print("=" * 64)
        sys.exit(0 if rep["ready"] else 1)

    if args.export_csv:
        from .export import export_csv

        srs = getattr(args, "srs", "wgs84")
        to_crs = "EPSG:5179" if srs == "5179" else "EPSG:4326"
        try:
            summ = export_csv(args.out, args.export_csv, bridge_id=args.bridge_id, to_crs=to_crs)
        except FileNotFoundError:
            p.error(f"project.h5 가 없습니다: {args.out} (먼저 --demo 등으로 생성하세요)")
        print("=" * 56)
        print("  KAIA 변위 CSV 내보내기 완료")
        print("=" * 56)
        print(f"  입력 project.h5 : {args.out}")
        print(f"  결과 CSV        : {summ['csv']}")
        print(f"  행/측점/시점    : {summ['rows']} 행 (N={summ['n_points']} × M={summ['n_dates']})")
        print(f"  포함 산출물     : 연직={'O' if summ['has_vertical'] else 'X'} · "
              f"PINN(EI/α)={'O' if summ['has_pinn'] else 'X'} · CRI={'O' if summ['has_fram'] else 'X'}")
        print("=" * 56)
        return

    if args.export_vlm:
        from .vlm_package import export_vlm_package

        srs = getattr(args, "srs", "wgs84")
        to_crs = "EPSG:5179" if srs == "5179" else "EPSG:4326"
        try:
            r = export_vlm_package(args.out, args.export_vlm, bridge_id=args.bridge_id,
                                   to_crs=to_crs, with_figures=not args.no_figures,
                                   zip_it=args.zip)
        except FileNotFoundError:
            p.error(f"project.h5 가 없습니다: {args.out} (먼저 --demo 등으로 생성하세요)")
        ch = r["channels"]
        print("=" * 56)
        print("  VLM 입력 패키지 내보내기 완료")
        print("=" * 56)
        print(f"  입력 project.h5 : {args.out}")
        print(f"  패키지 폴더     : {r['dir']}")
        if r["zip"]:
            print(f"  ZIP            : {r['zip']}")
        print(f"  파일            : {', '.join(r['files'][:4])}"
              + (f" (+figures {len(r['figures'])})" if r["figures"] else ""))
        print(f"  변위 CSV        : {r['rows']} 행 (N={r['n_points']} × M={r['n_dates']})")
        print(f"  채널            : 연직={'O' if ch['vertical_fused'] else 'X'} · "
              f"PINN={'O' if ch['pinn'] else 'X'} · CRI(참고)={'O' if ch['fram_cri'] else 'X'}")
        print(f"  지식그래프      : {r['kg_nodes']} 노드 · {r['kg_edges']} 엣지 (knowledge_graph.json)")
        print("=" * 56)
        return

    if args.export_kg:
        from .kg import export_kg

        srs = getattr(args, "srs", "wgs84")
        to_crs = "EPSG:5179" if srs == "5179" else "EPSG:4326"
        try:
            r = export_kg(args.out, args.export_kg, bridge_id=args.bridge_id, to_crs=to_crs)
        except FileNotFoundError:
            p.error(f"project.h5 가 없습니다: {args.out} (먼저 --demo 등으로 생성하세요)")
        print("=" * 56)
        print("  지식그래프(KG) 내보내기 완료")
        print("=" * 56)
        print(f"  입력 project.h5 : {args.out}")
        print(f"  그래프          : {r['graph']}")
        print(f"  노드/엣지       : {r['n_nodes']} 노드 · {r['n_edges']} 엣지")
        print(f"  사이드카        : {', '.join(Path(f).name for f in r['files'][1:])}")
        print("=" * 56)
        return

    if args.vlm_eval:
        from .vlm import available_backends, run_vlm_assessment

        try:
            a = run_vlm_assessment(args.vlm_eval, backend=args.vlm_backend)
        except FileNotFoundError as exc:
            p.error(str(exc))
        except NotImplementedError as exc:
            p.error(str(exc))
        print("=" * 56)
        print("  VLM 평가(백엔드) 완료")
        print("=" * 56)
        print(f"  패키지          : {args.vlm_eval}")
        print(f"  백엔드          : {a['backend']} (사용가능: {available_backends()})")
        print(f"  코드판정 여부   : {a['is_code_judgment']} · 판정: {a['verdict']}")
        print(f"  소견 {len(a['findings'])}건 → assessment.json")
        print(f"  ⚠️ {a['disclaimer']}")
        print("=" * 56)
        return

    if args.insar_tools:
        from .insar.toolchain import check_toolchain, format_report

        print(format_report(check_toolchain()))
        return

    if args.download_slc:
        from .insar.slc_download import (
            SlcAuthError,
            SlcRecipeError,
            download_recipe_slc,
        )

        try:
            r = download_recipe_slc(
                args.download_slc, args.slc_out,
                username=args.earthdata_user, password=args.earthdata_pass,
                token=args.earthdata_token, limit=args.slc_limit)
        except SlcAuthError as exc:
            p.error(str(exc))
        except SlcRecipeError as exc:
            p.error(str(exc))
        print("=" * 56)
        print("  실 SLC 자동 다운로드 완료")
        print("=" * 56)
        print(f"  레시피          : {args.download_slc}")
        print(f"  인증            : {r.auth}")
        print(f"  선별 장면       : {r.selected}/{r.requested} (SLC·VV·중복제거)  ~ {r.gigabytes:.0f} GB")
        print(f"  받음/스킵       : {r.downloaded} 다운로드 · {r.skipped_existing} 기존 스킵")
        print(f"  저장            : {r.out_dir}")
        if r.missing:
            print(f"  ⚠️ 누락 granule : {len(r.missing)}개 (ASF 검색 실패)")
        print("=" * 56)
        return

    if args.fuse_tracks:
        from .insar.track_fusion import fuse_tracks, fusion_report, write_fused_track_h5
        from .insar.track_reader import read_track_h5

        if len(args.fuse_tracks) < 2:
            p.error("--fuse-tracks 에는 트랙 H5 가 2개 이상 필요합니다.")
        tracks = [read_track_h5(h) for h in args.fuse_tracks]
        result = fuse_tracks(tracks)
        write_fused_track_h5(result, args.out)
        rep = fusion_report(result)
        print("=" * 56)
        print("  4-Track CS 융합 완료 (방법 합의 + 트랙 간 검증)")
        print("=" * 56)
        print(f"  입력 트랙       : {len(tracks)}개")
        print(f"  결과 파일       : {args.out}")
        print(f"  융합 점/시점    : N={result.los_mm.shape[0]}, M={result.los_mm.shape[1]}")
        print(f"  트랙 일치도(MAD): 중앙 {rep['agreement_mm_median']:.2f}mm / "
              f"p90 {rep['agreement_mm_p90']:.2f}mm")
        print(f"  신뢰(>0.6) 비율 : {rep['confident_frac'] * 100:.0f}%")
        print(f"  점당 평균 트랙  : {rep['mean_tracks_per_point']:.2f}")
        print("=" * 56)
        print(f"  다음: python -m inframon --import-track-h5 {args.out} --out data/project.h5")
        return

    if args.import_track_h5:
        from .contracts.io import ProjectStore

        # 레시피의 교량 선형(OSM geometry)이 있으면 넘겨 **곡선 데크 호길이 station**을
        # 폴리라인 투영으로 정확히 산출(없으면 점군 주곡선으로 폴백).
        geometry = None
        tgt_path = Path(f"{args.recipe}/bridge_target.json")
        if tgt_path.exists():
            try:
                from .insar.recipe import load_bridge_target
                geometry = load_bridge_target(str(tgt_path)).geometry or None
            except Exception as exc:  # noqa: BLE001 — geometry 없어도 진행(주곡선 폴백)
                print(f"  (경고) 레시피 geometry 로드 실패 → 주곡선 폴백: {str(exc)[:60]}")
        with ProjectStore(args.out, mode="a") as store:
            insar = import_track_h5(store, args.import_track_h5, geometry_latlon=geometry,
                                    apply_corrections=args.insar_corrections,
                                    ref_min_coherence=args.ref_min_coherence,
                                    dem_geotiff=args.insar_dem,
                                    thermal_correction=args.insar_thermal,
                                    temperature_csv=args.insar_temp_csv,
                                    fetch_temperature=args.insar_fetch_temp)
        print("=" * 56)
        print("  Track HDF5 → /insar 변환 완료")
        print("=" * 56)
        print(f"  입력 파일       : {args.import_track_h5}")
        print(f"  결과 파일       : {args.out}")
        print(f"  측정점/시점     : N={insar.n_points}, M={insar.n_dates}")
        print(f"  데크 station    : {'폴리라인 투영(곡선 대응)' if geometry else '주곡선 추정(레시피 geometry 없음)'}")
        print(f"  고도(z) 출처    : {'DEM 샘플(--insar-dem)' if args.insar_dem else 'Track height 또는 0'}")
        print(f"  정확도 보정     : {'적용(기준점+고도상관) → /insar/velocity_mm_yr' if args.insar_corrections else '없음'}")
        if args.insar_thermal:
            print(f"  열팽창 보정     : {'CSV ' + args.insar_temp_csv if args.insar_temp_csv else ('ERA5 fetch' if args.insar_fetch_temp else '온도원 없음')}")
        print("=" * 56)
        return

    if args.fit_reference_range:
        import glob as _glob
        import json as _json

        from .fram.reference_range import (
            default_reference_range,
            fit_reference_range_from_projects,
        )
        spec = args.fit_reference_range
        paths = ([q for tok in spec.split(",") for q in _glob.glob(tok.strip())]
                 if ("*" in spec or "?" in spec or "," in spec) else _glob.glob(spec) or [spec])
        paths = sorted(set(paths))
        if not paths:
            p.error(f"--fit-reference-range: 대상 project.h5 를 못 찾음: {spec}")
        excl = default_reference_range() if args.reference_clean else None
        try:
            ref = fit_reference_range_from_projects(paths, exclude_out_of_range=excl)
        except ValueError as exc:
            p.error(str(exc))
        Path(args.reference_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.reference_out).write_text(
            _json.dumps(ref.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print("=" * 56)
        print("  CRI 정상범위(reference range) 적합 완료 — 현장 건강 코호트")
        print("=" * 56)
        print(f"  건강 교량 수     : {ref.regime.get('n_bridges')} (표본 {ref.n})")
        print(f"  median·MAD       : {ref.median:.3f} · {ref.mad:.3f}")
        print(f"  정상경계 p97.5/p99: {ref.p97_5:.3f} / {ref.p99:.3f}")
        print(f"  위험경계         : {ref.abnormal_high:.3f}")
        print(f"  관측규모(regime) : 노이즈 {ref.regime.get('noise_mm')}mm · "
              f"기간 {ref.regime.get('span_days')}일")
        print(f"  저장             : {args.reference_out}")
        print("=" * 56)
        print("  적용: custom_pinn(reference_range=<이 dict>) 또는 cfg.fram_reference_range 에 로드.")
        return

    if not args.demo:
        p.error("Phase 0 에서는 --demo 만 지원합니다.")

    try:
        fram = run_pipeline(args.out, cfg)
    except (NotImplementedError, ValueError, FileNotFoundError) as exc:
        p.error(str(exc))

    non_stub = {n: m for n, m in cfg.engines.items() if m != "stub"}
    print("=" * 56)
    print("  통합 인프라 모니터링 — 파이프라인 완료 (CV→InSAR→PINN→FRAM)")
    print("=" * 56)
    print(f"  결과 파일      : {args.out}")
    print(f"  엔진 구현      : {'전부 stub' if not non_stub else non_stub}")
    print(f"  측정점/시점    : N={fram.n_points}, M={fram.n_dates}")
    print(f"  최대 CRI       : {fram.cri_global_max:.3f}")
    print(f"  경보 등급      : {fram.warning.level}")
    if fram.warning.lead_time_days is not None:
        print(f"  예상 리드타임  : {fram.warning.lead_time_days:.0f} 일")
    print(f"  위험 부재      : {', '.join(fram.warning.critical_members) or '없음'}")
    print("=" * 56)


if __name__ == "__main__":
    main()
