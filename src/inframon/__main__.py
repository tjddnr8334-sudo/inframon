"""CLI 진입점.

사용:
  python -m inframon --demo
  python -m inframon --demo --out data/project.h5
"""

from __future__ import annotations

import argparse
import sys

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
    p.add_argument("--export-csv", default=None, metavar="CSV",
                   help="--out 의 project.h5 를 KAIA 변위 CSV(점×시점 롱포맷)로 내보내고 종료. "
                        "--bridge-id 로 교량ID, --srs 로 좌표계 지정.")
    p.add_argument("--bridge-id", default="", metavar="ID",
                   help="--export-csv 의 bridge_id 컬럼값(Bmaps 교량관리번호). 기본 빈값.")
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
    cfg.resume = args.resume
    cfg.force_stages = tuple(args.force_stage)
    try:
        cfg.validate()
    except ValueError as exc:
        p.error(str(exc))

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
