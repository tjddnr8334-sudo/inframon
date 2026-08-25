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
    ifc: str | Path | None = None, bim_elements: str | Path | None = None,
    registry: str | Path | None = None, bridge_id: str | None = None,
    twin_value: str = "cri",
) -> PipelineReport:
    """정규 순서로 교량 파이프라인 실행/계획. mode: 'plan'(경량만)|'full'(전체 실행).

    do_adi=True 면 ⑨ PS/DS 를 코히런스 1차 대신 **진폭분산 ADI**(쌍별 진폭 ~20분 추가)로.

    ⑬ 디지털트윈: `ifc`(또는 `bim_elements`)를 주면 부재 GlobalId 로 결합해 glTF·3D Tiles
    를 낸다. **없어도 진행**한다 — 점군 트윈만 만들고 결합은 생략(임의 교량은 IFC 가
    없는 게 정상이며, 여기서 멈추면 체인이 끊긴다).
    ⑭ BMAP: 산출 project.h5 를 `registry`(기본 <out>/bridge_registry.json)에 등록해
    `--serve-api` 로 바로 서빙 가능한 상태로 만든다.
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
        # ①에서 확인된 교량 길이보다 ROI 한 변이 크도록(교량이 ROI 안, off-deck reference 확보)
        _blen = (ctx.get("bridge") or {}).get("length_m")
        roi = select_roi(lat, lon, sizes_km=roi_sizes, bridge_length_m=_blen)
        ctx["roi"] = roi.as_dict(); ctx["roi_wkt"] = roi.wkt(); ctx["roi_bbox"] = roi.bbox
        _blen_txt = (f" · 교량 {round(_blen)}m<ROI {'✅' if roi.larger_than_bridge else '⚠️'}"
                     if _blen else "")
        rep.add(StageResult("③ROI도심지가중", "done",
                            f"{roi.size_km:.0f}km · 건물 {roi.n_buildings} · "
                            f"{roi.density_per_km2:.0f}/km²{_blen_txt}"))
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
                        "(반대 궤도 장면 부족·기하 특이 시 단일 궤도 폴백)."))

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

    # ⑧⑨⑫⑬⑭ 중량 단계 — plan 이면 계획, full 이면 실행
    _twin_how = ("export_insar_gltf + write_3dtiles_tileset"
                 + (" (IFC 부재 GlobalId 결합)" if (ifc or bim_elements)
                    else " — IFC 미지정: 점군 트윈만(결합 생략)"))
    heavy = [
        ("⑧InSAR처리(SNAP)", "snap_backend.run / --snap-auto"),
        ("⑨PS/DS(교량30m)", "build_bridge_track_ps_ds (ADI PS/DS, 데크 30m)"),
        ("⑫PINN→FRAM", "--custom-pinn (형식별 PINN + FRAM CRI)"),
        ("⑬IFC디지털트윈", _twin_how),
        ("⑭BMAP등록", "bridge_registry.json 등록 → --serve-api 서빙"),
    ]
    if mode == "full":
        _run_heavy(rep, ctx, lat, lon, out, earthdata_token, snap_count, do_adi,
                   ifc=ifc, bim_elements=bim_elements, registry=registry,
                   bridge_id=bridge_id, twin_value=twin_value)
    else:
        for step, how in heavy:
            rep.add(StageResult(step, "planned", f"mode=full 시 실행: {how}"))

    return rep


def _twin_and_register(rep, ctx, lat, lon, out, *, ifc, bim_elements, registry,
                       bridge_id, twin_value):
    """⑬ 디지털트윈(glTF·3D Tiles) + ⑭ BMAP 레지스트리 등록.

    IFC 가 없으면 결합만 건너뛰고 **점군 트윈은 그대로 만든다** — 임의 교량에서 IFC 는
    있는 쪽이 예외라, 없다고 체인을 끊으면 목표(트윈→BMAP)에 도달할 수 없다.
    """
    proj = (ctx.get("pinn") or {}).get("project")
    if not proj or not Path(proj).exists():
        rep.add(StageResult("⑬IFC디지털트윈", "skip", "project.h5 없음(⑫ 실패) → 트윈 생략"))
        rep.add(StageResult("⑭BMAP등록", "skip", "project.h5 없음 → 등록 생략"))
        return

    # ⑬ 트윈 — 부재 결합(있으면) → glTF → 3D Tiles
    glb = out / "twin.glb"
    try:
        from .insar.gltf_export import export_insar_gltf, write_3dtiles_tileset
        guids = element_z = None
        bound = "IFC 미지정 → 점군 트윈(GlobalId 결합 없음)"
        src = ifc or bim_elements
        if src:
            try:
                from .insar.gltf_export import guid_map_from_alignment
                guids, ginfo = guid_map_from_alignment(proj, str(src))
                element_z = ginfo.get("element_z")
                n_bound = int(sum(1 for g in guids if g)) if guids is not None else 0
                bound = f"부재 결합 {n_bound}/{len(guids)}점 (GlobalId)"
            except Exception as e:  # noqa: BLE001 — 결합 실패해도 트윈은 만든다
                bound = f"부재 결합 실패({str(e)[:50]}) → 점군 트윈으로 진행"
        r = export_insar_gltf(proj, glb, value=twin_value, fram_project=proj,
                              element_guids=guids, element_z=element_z,
                              z_source="element" if element_z is not None else "flat")
        t = write_3dtiles_tileset(glb)
        ctx["twin"] = {"glb": str(glb), "tileset": t.get("out") or str(out / "tileset.json"),
                       "meta": str(glb) + ".meta.json", "bound": bound,
                       "n_points": r.get("n_points") if isinstance(r, dict) else None}
        rep.add(StageResult("⑬IFC디지털트윈", "done",
                            f"{bound} · {glb.name}+3D Tiles"))
    except Exception as e:  # noqa: BLE001
        rep.add(StageResult("⑬IFC디지털트윈", "error", str(e)[:100]))

    # ⑭ BMAP 레지스트리 등록 — 있으면 갱신, 없으면 추가(멱등)
    try:
        import json as _json
        reg_path = Path(registry) if registry else (out / "bridge_registry.json")
        data = {"bridges": []}
        if reg_path.exists():
            try:
                data = _json.loads(reg_path.read_text(encoding="utf-8")) or {"bridges": []}
            except ValueError:
                data = {"bridges": []}
        data.setdefault("bridges", [])
        name = (ctx.get("bridge") or {}).get("name") or f"bridge_{lat:.4f}_{lon:.4f}"
        bid = bridge_id or f"INFRAMON-{lat:.4f}_{lon:.4f}"
        entry = {"bridge_id": bid, "name": name, "project_h5": str(Path(proj).resolve()),
                 "wgs84_center": [round(lat, 6), round(lon, 6)]}
        if ctx.get("twin"):
            entry["twin_gltf"] = ctx["twin"]["glb"]
            entry["twin_tileset"] = ctx["twin"]["tileset"]
        data["bridges"] = [b for b in data["bridges"] if b.get("bridge_id") != bid] + [entry]
        reg_path.parent.mkdir(parents=True, exist_ok=True)
        reg_path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        ctx["registry"] = {"path": str(reg_path), "bridge_id": bid,
                           "n_bridges": len(data["bridges"])}
        rep.add(StageResult("⑭BMAP등록", "done",
                            f"{bid} · 교량 {len(data['bridges'])}건 · {reg_path.name} "
                            f"→ --serve-api --registry {reg_path}"))
    except Exception as e:  # noqa: BLE001
        rep.add(StageResult("⑭BMAP등록", "error", str(e)[:100]))


def _run_heavy(rep, ctx, lat, lon, out, token, snap_count, do_adi=False, *,
               ifc=None, bim_elements=None, registry=None, bridge_id=None,
               twin_value="cri"):
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
        # 뒤 단계를 조용히 빠뜨리면 "왜 트윈이 없지?" 가 된다 — 사유와 함께 명시 보고.
        for _s in ("⑨PS/DS(교량30m)", "⑫PINN→FRAM", "⑬IFC디지털트윈", "⑭BMAP등록"):
            rep.add(StageResult(_s, "skip", "⑧ InSAR 처리 실패 → 선행 산출물 없음"))
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
        _geom = ctx.get("bridge", {}).get("geometry")   # ①에서 확인된 교량 선형(곡선 station용)
        with ProjectStore(proj, mode="a") as store:
            import_track_h5(store, deck_h5, geometry_latlon=_geom)
        summ = run_custom_pinn(proj, lat, lon)
        ctx["pinn"] = {"cri_max": summ["cri_global_max"], "warning": summ["warning_level"],
                       "project": proj}
        rep.add(StageResult("⑫PINN→FRAM", "done",
                            f"CRI {summ['cri_global_max']:.3f} · 경보 {summ['warning_level']} · {proj}"))
    except Exception as e:  # noqa: BLE001
        rep.add(StageResult("⑫PINN→FRAM", "error", str(e)[:100]))

    # ⑬⑭ 디지털트윈 → BMAP 등록 (목표 체인의 마지막 두 고리)
    _twin_and_register(rep, ctx, lat, lon, out, ifc=ifc, bim_elements=bim_elements,
                       registry=registry, bridge_id=bridge_id, twin_value=twin_value)
