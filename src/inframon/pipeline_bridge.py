"""**표준 교량 InSAR·PINN 파이프라인 오케스트레이터** — 정규 순서를 코드로 고정.

사용자 정의 순서(①→⑫)를 하나의 진입점으로 codify 한다. 경량 단계(교량선정·ROI·
트랙조회·기상·교량메타)는 실제 실행하고, 중량 단계(SLC 다운로드·SNAP 처리·PS/DS·PINN·
FRAM)는 mode='plan' 이면 계획만, mode='full' 이면 실제 실행한다. 각 단계는 구현도
(done/partial/stub)를 함께 보고해 "어디까지 됐는지"가 결과에 그대로 드러난다.

정규 순서:
  ① 교량 선정(OSM)          ② SLC/트랙 조회(ASF)        ③ ROI 도심지 가중(5→2km)
  ④ 최적 프레임 선정         ⑤ ERA5 강수·습도·온도→SLC 필터·master  ⑥ 궤도·DEM·AUX
  ⑦ 상승·하강 연직분해       ⑧ SARvey/SNAP InSAR 처리    ⑨ InSAR+PINN PS/DS(교량 인근·shift)
  ⑩ trend·coherence·부재     ⑪ 교량 종별·종류·폭·해상/내륙/산지    ⑫ 교량맞춤 PINN→FRAM
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StageResult:
    step: str                 # "①교량선정" 등
    status: str               # done | partial | stub | skip | planned | error
    detail: str = ""
    output: dict = field(default_factory=dict)


@dataclass
class PipelineReport:
    lat: float
    lon: float
    stages: list[StageResult] = field(default_factory=list)
    context: dict = field(default_factory=dict)

    def add(self, r: StageResult) -> None:
        self.stages.append(r)

    def summary(self) -> str:
        mark = {"done": "✅", "partial": "◐", "stub": "○", "planned": "▷",
                "skip": "–", "error": "✗"}
        lines = ["=" * 60, "  표준 교량 InSAR·PINN 파이프라인", "=" * 60]
        for s in self.stages:
            lines.append(f"  {mark.get(s.status, '?')} {s.step:<22} {s.detail}")
        lines.append("=" * 60)
        return "\n".join(lines)


def run_bridge_pipeline(
    lat: float, lon: float, *, out_dir: str | Path = "data/pipeline",
    mode: str = "plan", roi_sizes=(1.0, 2.0, 3.0, 5.0, 7.0, 10.0),
    earthdata_token: str | None = None, snap_count: int = 8, do_adi: bool = False,
) -> PipelineReport:
    """정규 순서로 교량 파이프라인 실행/계획. mode: 'plan'(경량만)|'full'(전체 실행).

    do_adi=True 면 ⑨ PS/DS 를 코히런스 1차 대신 **진폭분산 ADI**(쌍별 진폭 ~20분 추가)로.
    """
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    rep = PipelineReport(lat=lat, lon=lon)
    ctx = rep.context

    # ① 교량 선정 (OSM)
    try:
        from .insar.osm_bridge import confirm_bridge
        b = confirm_bridge(lat, lon)
        if b:
            ctx["bridge"] = {"name": b.name, "osm": b.osm_url, "length_m": round(b.length_m),
                             "tags": b.tags, "geometry": b.geometry}
            rep.add(StageResult("①교량선정", "done",
                                f"{b.name or b.osm_id} · {round(b.length_m)}m"))
        else:
            rep.add(StageResult("①교량선정", "partial", "OSM 교량 미확인(좌표만 사용)"))
    except Exception as e:  # noqa: BLE001
        rep.add(StageResult("①교량선정", "error", str(e)[:80]))

    # ③ ROI 도심지 가중 (② SLC 조회보다 먼저: 조회 AOI 로 씀)
    try:
        from .insar.roi_selection import select_roi
        roi = select_roi(lat, lon, sizes_km=roi_sizes)
        ctx["roi"] = roi.as_dict(); ctx["roi_wkt"] = roi.wkt(); ctx["roi_bbox"] = roi.bbox
        rep.add(StageResult("③ROI도심지가중", "done",
                            f"{roi.size_km:.0f}km · 건물 {roi.n_buildings} · {roi.density_per_km2:.0f}/km²"))
    except Exception as e:  # noqa: BLE001
        rep.add(StageResult("③ROI도심지가중", "error", str(e)[:80]))

    # ②④ SLC/트랙 조회 → 최적 프레임 선정
    try:
        from .insar.snap_acquire import search_frames
        cands = search_frames(lat, lon, start="2024-01-01", end="2025-07-01")
        top = cands[0] if cands else None
        ctx["frames"] = [c.label() for c in cands[:4]]
        if top:
            ctx["frame"] = {"label": top.label(), "n_scenes": top.n_scenes,
                            "centrality_km": round(top.centrality_km, 1)}
            rep.add(StageResult("②④SLC·트랙·프레임", "done",
                                f"{top.label()} · {top.n_scenes}장 · 중심성 {top.centrality_km:+.1f}km"))
        else:
            rep.add(StageResult("②④SLC·트랙·프레임", "partial", "교량 커버 트랙 없음"))
    except Exception as e:  # noqa: BLE001
        rep.add(StageResult("②④SLC·트랙·프레임", "error", str(e)[:80]))

    # ⑤ ERA5 강수·습도·온도 → master 선정 + 악천후 씬 소거 (SNAP 연동됨)
    rep.add(StageResult("⑤ERA5필터·master", "done",
                        "era5_master: 강수·습도·온도 대기안정도×baseline 로 master 선정 + "
                        "악천후 씬 소거 → SNAP run(era5_master=True) 연동. full 시 실행."))

    # ⑥ 궤도·DEM·AUX — SNAP 자동
    rep.add(StageResult("⑥궤도·DEM·AUX", "done", "SNAP 자동(궤도·SRTM DEM), 2024 IPF AUX 불필요"))

    # ⑦ asc+desc 연직분해 (SNAP 연동됨)
    rep.add(StageResult("⑦asc+desc연직분해", "done",
                        "fuse_snap_asc_desc: 상승·하강 SNAP Track → 연직 U·수평 H 분해 "
                        "(하강 부족/기하 특이 시 단일 폴백). 정자교는 하강 2장 → 단일."))

    # ⑪ 교량 종별(1/2/3종)·종류(PSC box/라멘)·폭·지형(산지/평지/해상)
    try:
        from .insar.bridge_meta import build_bridge_meta
        from .insar.bridge_profile import classify_bridge, water_context_for
        tags = ctx.get("bridge", {}).get("tags", {})
        length = ctx.get("bridge", {}).get("length_m")
        cls = classify_bridge(tags, length)
        water = water_context_for(cls, length)
        meta = build_bridge_meta(lat, lon, tags, cls, length, water)
        ctx["bridge_meta"] = meta.as_dict()
        wtxt = f"{meta.width_m}m" if meta.width_m else "폭미상"
        rep.add(StageResult("⑪교량메타", "done",
                            f"{meta.grade}·{meta.structure_ko}·{wtxt}·경간~{meta.max_span_m}m·{meta.terrain}"))
    except Exception as e:  # noqa: BLE001
        rep.add(StageResult("⑪교량메타", "error", str(e)[:70]))

    # ⑧⑨⑫ 중량 단계 — plan 이면 계획, full 이면 실행
    heavy = [
        ("⑧InSAR처리(SNAP)", "snap_backend.run / --snap-auto"),
        ("⑨PS/DS(교량30m)", "build_bridge_track_ps_ds (ADI PS/DS, 데크 30m)"),
        ("⑫PINN→FRAM", "--custom-pinn (형식별 PINN + FRAM CRI)"),
    ]
    if mode == "full":
        _run_heavy(rep, ctx, lat, lon, out, earthdata_token, snap_count, do_adi)
    else:
        for step, how in heavy:
            rep.add(StageResult(step, "planned", f"mode=full 시 실행: {how}"))

    return rep


def _run_heavy(rep, ctx, lat, lon, out, token, snap_count, do_adi=False):
    """중량 단계 실제 실행(mode='full') — SNAP 처리→PS/DS→PINN. 실패는 단계별 보고."""
    from .insar.snap_acquire import acquire
    from .insar.snap_backend import (amplitude_pairs, build_bridge_track_ps_ds,
                                     platform_heading, scene_date)
    from .insar.snap_backend import run as snap_run
    try:
        acq = acquire(lat, lon, str(out), count=snap_count, start="2024-01-01",
                      end="2025-07-01", token=token)
        ctx["slc_dir"] = acq.slc_dir
        res = snap_run([str(x) for x in Path(acq.slc_dir).glob("*.zip")], lat, lon,
                       out_dir=str(out), out_h5=str(out / "track.h5"),
                       era5_master=True)          # ⑤ ERA5 master·씬 소거 적용
        if res.weather is not None and hasattr(res.weather, "selected_master"):
            rep.add(StageResult("⑤ERA5master(실행)", "done",
                                f"master {res.weather.selected_master} · "
                                f"악천후 소거 {getattr(res.weather, 'n_excluded', 0)}장"))
        _bl = getattr(res, "rejected_slaves", [])
        _blt = f" · baseline/도플러 사전제거 {len(_bl)}장" if _bl else ""
        rep.add(StageResult("⑧InSAR처리(SNAP)", "done",
                            f"{res.reference} · 쌍 {sum(p.ok for p in res.pairs)}/"
                            f"{len(res.pairs)}{_blt}"))
        ctx["snap"] = res.as_dict()
    except Exception as e:  # noqa: BLE001
        rep.add(StageResult("⑧InSAR처리(SNAP)", "error", str(e)[:100]))
        return

    # heading(단일 궤도 기록용) — 기준 SLC 에서
    ref_scene = next((str(s) for s in Path(acq.slc_dir).glob("*.zip")
                      if scene_date(str(s)) == res.reference), None)
    hd = platform_heading(ref_scene, res.burst.subswath) if ref_scene else None

    # ⑨ 교량 데크 30m PS/DS
    geometry = ctx.get("bridge", {}).get("geometry")
    deck_h5 = str(out / "track_deck.h5")
    if geometry:
        try:
            amps = None
            if do_adi:                              # 진폭쌍 → ADI(~20분 추가)
                amps = amplitude_pairs([str(x) for x in Path(ctx["slc_dir"]).glob("*.zip")],
                                       lat, lon, out, reference=res.reference, burst=res.burst)
            r9 = build_bridge_track_ps_ds(res.pairs, res.reference, deck_h5,
                                          geometry_latlon=geometry, buffer_m=30.0,
                                          coh_min=0.35, heading=hd, amp_pairs=amps,
                                          apply_reference=True, roi_bbox=ctx.get("roi_bbox"))
            ctx["ps_ds"] = r9
            _rf = r9.get("reference", {})
            _rft = (f" · 기준점 coh {_rf['coherence']:.3f}"
                    f"{'✓0.98' if _rf.get('meets_098') else '⚠<0.98'}") if _rf.get("applied") else ""
            _rej = r9.get("rejected_slaves", [])
            _rjt = f" · 튀는 slave {len(_rej)}개 제거" if _rej else ""
            rep.add(StageResult("⑨PS/DS(교량30m)", "done",
                                f"{r9['n_points']}점(PS {r9['n_ps']}/DS {r9['n_ds']}) · "
                                f"데크≤{r9['buffer_m']:.0f}m · {r9['class_method']}{_rft}{_rjt}"))
        except Exception as e:  # noqa: BLE001
            rep.add(StageResult("⑨PS/DS(교량30m)", "error", str(e)[:90]))
            deck_h5 = res.track_h5
    else:
        rep.add(StageResult("⑨PS/DS(교량30m)", "partial", "교량 geometry 없음 → 반경 track 사용"))
        deck_h5 = res.track_h5

    # ⑫ import → 교량맞춤 PINN → FRAM
    try:
        from .contracts.io import ProjectStore
        from .custom_pinn import run_custom_pinn
        from .insar.track_reader import import_track_h5
        proj = str(out / "project.h5")
        with ProjectStore(proj, mode="a") as store:
            import_track_h5(store, deck_h5)
        summ = run_custom_pinn(proj, lat, lon)
        ctx["pinn"] = {"cri_max": summ["cri_global_max"], "warning": summ["warning_level"],
                       "project": proj}
        rep.add(StageResult("⑫PINN→FRAM", "done",
                            f"CRI {summ['cri_global_max']:.3f} · 경보 {summ['warning_level']} · {proj}"))
    except Exception as e:  # noqa: BLE001
        rep.add(StageResult("⑫PINN→FRAM", "error", str(e)[:100]))
