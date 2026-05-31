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
    p.add_argument("--doctor", nargs="?", const="", default=None, metavar="PATH",
                   help="환경·데이터 준비도 진단 후 종료. PATH 가 폴더면 인벤토리, .h5 면 preflight 포함")
    p.add_argument(
        "--engine",
        action="append",
        default=[],
        metavar="NAME=MODE",
        help="엔진 구현 선택 (예: --engine insar=real). 반복 지정 가능. 기본 전부 stub.",
    )
    p.add_argument("--insar-source", default=None, help="insar=real 이 소비할 Track 결과 H5")
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
    cfg.resume = args.resume
    cfg.force_stages = tuple(args.force_stage)
    try:
        cfg.validate()
    except ValueError as exc:
        p.error(str(exc))

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
