"""CLI 진입점.

사용:
  python -m inframon --demo
  python -m inframon --demo --out data/project.h5
"""

from __future__ import annotations

import argparse
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
    p.add_argument("--custom-pinn", default=None, metavar="LAT,LON",
                   help="--out 의 /insar 위에 교량 맞춤형 PINN 실행 — 위치로 제원(OSM)·온도"
                        "(Open-Meteo) 자동수집 후 형식별 PDE. 키 불필요 경로.")
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

    if args.custom_pinn:
        from .custom_pinn import run_custom_pinn

        try:
            lat, lon = (float(v) for v in args.custom_pinn.split(","))
        except ValueError:
            p.error("--custom-pinn 형식은 LAT,LON 입니다 (예: 37.3634,127.1090)")
        try:
            summary = run_custom_pinn(args.out, lat, lon)
        except (ValueError, FileNotFoundError) as exc:
            p.error(str(exc))
        print("=" * 56)
        print("  교량 맞춤형 PINN 완료")
        print("=" * 56)
        print(f"  교량       : {summary['bridge_name'] or '-'} ({summary['bridge_type']}/{summary['material']})")
        print(f"  스팬       : {summary['span_m']} m  · 제원출처 {summary['collected']['profile_source']}")
        print(f"  온도       : {summary['collected']['temperature']}")
        print(f"  교통량     : {summary['collected']['traffic']}")
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

        with ProjectStore(args.out, mode="a") as store:
            insar = import_track_h5(store, args.import_track_h5)
        print("=" * 56)
        print("  Track HDF5 → /insar 변환 완료")
        print("=" * 56)
        print(f"  입력 파일       : {args.import_track_h5}")
        print(f"  결과 파일       : {args.out}")
        print(f"  측정점/시점     : N={insar.n_points}, M={insar.n_dates}")
        print("=" * 56)
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
