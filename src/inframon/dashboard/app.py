"""내장 대시보드 (문서 5.8 / 7.2) — 단계별 탭 구조 (InSAR → PINN → FRAM).

실행:  pip install -e .[dashboard]
       streamlit run src/inframon/dashboard/app.py

데이터 흐름의 시작점인 InSAR(실측 변위)를 첫 탭·관문으로 두고,
PINN(물리 해석) → FRAM(공명 위험 CRI) 순으로 파이프라인을 따라간다.
육각형 FRAM 다이어그램은 Phase 4 에서 확장.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from inframon.contracts.schema import MEMBER_TYPES
from inframon.dashboard.data import fram_panel_data, has_group, read_meta
from inframon.dashboard.data import read_arrays as read

DEFAULT_H5 = "data/project.h5"
LEVEL_STYLE = {  # 경보 등급 → (스트림릿 배너 함수명, 이모지)
    "정상": ("success", "🟢"),
    "주의": ("info", "🔵"),
    "경고": ("warning", "🟠"),
    "위험": ("error", "🔴"),
}


# ───────────────────────────── 데이터 접근 ─────────────────────────────
# has_group / read(=read_arrays) / read_meta 는 streamlit 비의존 계층(dashboard.data)에서 가져온다.


def to_datetimes(dates: np.ndarray, labels, start: date) -> list[datetime]:
    """시점 축을 달력 날짜로 변환한다.

    - date_labels(실제 취득일 YYYYMMDD)가 있으면 그것을 쓴다.
    - 없으면(데모) 기준 시작일 + epoch days 오프셋으로 합성한다.
    """
    if labels is not None:
        return [datetime.strptime(str(s), "%Y%m%d") for s in labels]
    base = datetime(start.year, start.month, start.day)
    return [base + timedelta(days=float(d)) for d in dates]


def member_names(member: np.ndarray) -> list[str]:
    return [MEMBER_TYPES[int(i)] if 0 <= int(i) < len(MEMBER_TYPES) else f"?{i}" for i in member]


def run_demo(path: str, n_points: int, n_dates: int, engines: dict | None = None) -> None:
    """UI 안에서 파이프라인(CV→InSAR→PINN→FRAM)을 재실행해 project.h5 를 새로 만든다."""
    from inframon.config import PipelineConfig, _default_engines

    from inframon.orchestrator.pipeline import run_pipeline

    cfg = PipelineConfig(n_points=n_points, n_dates=n_dates,
                         engines=engines or _default_engines())
    run_pipeline(path, cfg)


def project_times(path: str, start: date):
    """현재 project.h5 의 /insar 시점 축(달력 날짜)을 반환. 없으면 None."""
    if not has_group(path, "insar"):
        return None
    dates = read(path, "/insar/dates")
    labels = read(path, "/insar/date_labels")
    return to_datetimes(dates, labels, start)


def bridge_target_section() -> None:
    """A·B 단계 — 한국 지도에서 위치를 고르고 OSM 으로 교량을 확인해 레시피로 저장."""
    try:
        import folium
        from streamlit_folium import st_folium
    except ImportError:
        st.info("지도 기능에는 folium·streamlit-folium 이 필요합니다: `pip install -e .[dashboard]`")
        return

    recipe_path = st.text_input("레시피 저장 경로", "data/insar_recipe/bridge_target.json",
                               key="recipe_path")
    existing = None
    if Path(recipe_path).exists():
        try:
            from inframon.insar.recipe import load_bridge_target
            existing = load_bridge_target(recipe_path)
        except Exception:  # noqa: BLE001
            existing = None

    click = st.session_state.get("bridge_click")
    if click:
        center, zoom = [click["lat"], click["lng"]], 16
    elif existing:
        center, zoom = [existing.selected_lat, existing.selected_lon], 16
    else:
        center, zoom = [36.5, 127.8], 7  # 한국 전역

    m = folium.Map(location=center, zoom_start=zoom, tiles="OpenStreetMap")
    if click:
        folium.Marker([click["lat"], click["lng"]], tooltip="선택 지점",
                      icon=folium.Icon(color="red")).add_to(m)
    if existing:
        mn_lon, mn_lat, mx_lon, mx_lat = existing.bbox
        folium.Rectangle([[mn_lat, mn_lon], [mx_lat, mx_lon]], color="blue", weight=3,
                         tooltip=f"저장된 타깃: {existing.name}").add_to(m)

    state = st_folium(m, height=420, key="bridge_map")
    if state and state.get("last_clicked"):
        st.session_state["bridge_click"] = state["last_clicked"]
        click = state["last_clicked"]

    radius = st.slider("OSM 탐색 반경 (m)", 50, 1000, 200, 50, key="osm_radius")
    if click:
        st.caption(f"선택 지점: **{click['lat']:.5f}, {click['lng']:.5f}** — 지도를 클릭해 바꿀 수 있어요.")
    else:
        st.caption("지도를 클릭해 교량 위치를 지정하세요.")

    if st.button("🔎 이 위치에서 교량 확인 (OSM)", disabled=not click, key="btn_osm"):
        from inframon.insar.osm_bridge import find_bridges_near
        with st.spinner("OSM(Overpass) 조회 중…"):
            try:
                st.session_state["bridge_candidates"] = find_bridges_near(
                    click["lat"], click["lng"], radius
                )
            except Exception as exc:  # noqa: BLE001
                st.session_state["bridge_candidates"] = []
                st.error(f"OSM 조회 실패: {exc}")

    cands = st.session_state.get("bridge_candidates", [])
    if cands:
        labels = [
            f"{b.name} · {b.distance_m:.0f}m 거리 · 길이 {b.length_m:.0f}m · {b.osm_type}/{b.osm_id}"
            for b in cands
        ]
        i = st.selectbox("확인된 교량", range(len(cands)),
                        format_func=lambda i: labels[i], key="cand_sel")
        sel = cands[i]
        st.success(f"✅ 교량 확인: **{sel.name}** "
                   f"({sel.tags.get('bridge', 'bridge')}) — [{sel.osm_url}]({sel.osm_url})")
        if click and st.button("💾 타깃으로 저장 (레시피)", key="btn_save_target"):
            from inframon.insar.recipe import BridgeTarget, save_bridge_target
            tgt = BridgeTarget.from_bridge(sel, click["lat"], click["lng"])
            save_bridge_target(recipe_path, tgt)
            st.success(f"저장됨 → `{recipe_path}` (SLC 검색 영역 bbox 포함)")
            st.json(tgt.model_dump())
    elif click:
        st.caption("‘교량 확인’을 눌러 이 위치가 교량인지 OSM 에서 조회하세요.")


def selection_criteria_section() -> None:
    """C·D 단계 선별 기준 — 공간/시간 baseline·VV·트랙 선택을 레시피로 저장(C/D 구현 시 적용)."""
    from inframon.insar.recipe import (
        SelectionCriteria,
        load_selection_criteria,
        save_selection_criteria,
    )

    crit_path = st.text_input("선별 기준 저장 경로", "data/insar_recipe/selection_criteria.json",
                             key="crit_path")
    cur = SelectionCriteria()
    if Path(crit_path).exists():
        try:
            cur = load_selection_criteria(crit_path)
        except Exception:  # noqa: BLE001
            pass

    c1, c2 = st.columns(2)
    perp = c1.number_input("공간(수직) baseline 상한 [m]", 1.0, 5000.0,
                          float(cur.perp_baseline_max_m), 10.0, key="perp_max")
    use_temporal = c2.checkbox("시간 baseline 상한 사용",
                              value=cur.temporal_baseline_max_days is not None, key="use_temporal")
    temporal = c2.number_input("시간 baseline 상한 [일]", 1.0, 3650.0,
                              float(cur.temporal_baseline_max_days or 60.0), 6.0,
                              disabled=not use_temporal, key="temporal_max")
    c3, c4 = st.columns(2)
    pol = c3.selectbox("편파", ["VV", "VH", "HH", "HV"],
                      index=["VV", "VH", "HH", "HV"].index(cur.polarization), key="pol")
    orbit = c4.selectbox("궤도 방향", ["자동(최다)", "ASCENDING", "DESCENDING"],
                        index=0 if cur.orbit_direction is None
                        else ["자동(최다)", "ASCENDING", "DESCENDING"].index(cur.orbit_direction),
                        key="orbit_dir")
    most = st.checkbox("track/frame 중 취득 최다 조합 선택", value=cur.prefer_most_data_track,
                      key="most_data")

    st.caption(f"→ 공간 baseline **≤ {perp:.0f} m** 인 페어만 사용"
               + (f", 시간 baseline ≤ {temporal:.0f} 일" if use_temporal else "")
               + f", **{pol}** 편파"
               + (", 최다 트랙 자동 선택" if most else ""))

    if st.button("💾 선별 기준 저장", key="btn_save_crit"):
        crit = SelectionCriteria(
            perp_baseline_max_m=float(perp),
            temporal_baseline_max_days=float(temporal) if use_temporal else None,
            polarization=pol,
            orbit_direction=None if orbit == "자동(최다)" else orbit,
            prefer_most_data_track=most,
        )
        save_selection_criteria(crit_path, crit)
        st.success(f"저장됨 → `{crit_path}`")
        st.json(crit.model_dump())


def slc_search_section() -> None:
    """C·D 단계 — 교량 타깃 bbox 로 ASF Sentinel-1 SLC 검색 후 취득 최다 트랙 선별."""
    try:
        import asf_search  # noqa: F401
    except ImportError:
        st.info("SLC 검색에는 asf_search 가 필요합니다: `pip install -e .[search]`")
        return

    from inframon.insar.recipe import (
        TrackSelection,
        load_bridge_target,
        load_selection_criteria,
        save_track_selection,
    )
    from inframon.insar.slc_search import search_slc, select_track

    target_path = st.text_input("교량 타깃 레시피", "data/insar_recipe/bridge_target.json",
                               key="slc_target_path")
    if not Path(target_path).exists():
        st.info("먼저 **🗺️ 교량 타깃 지정**에서 교량을 저장하세요(검색 영역 bbox 필요).")
        return
    target = load_bridge_target(target_path)

    crit_path = "data/insar_recipe/selection_criteria.json"
    pol, orbit = "VV", None
    if Path(crit_path).exists():
        try:
            crit = load_selection_criteria(crit_path)
            pol, orbit = crit.polarization, crit.orbit_direction
        except Exception:  # noqa: BLE001
            pass

    st.caption(f"대상: **{target.name}** · bbox={tuple(round(v, 4) for v in target.bbox)} · "
               f"편파 {pol} · 궤도 {orbit or '자동(최다)'}")
    c1, c2 = st.columns(2)
    use_range = c1.checkbox("취득 기간 제한", value=False, key="slc_use_range")
    start_d = c1.date_input("시작", value=date(2018, 1, 1), key="slc_start", disabled=not use_range)
    end_d = c2.date_input("끝", value=date(2024, 12, 31), key="slc_end", disabled=not use_range)

    if st.button("🛰️ ASF SLC 검색", key="btn_slc"):
        with st.spinner("ASF 검색 중…(네트워크)"):
            try:
                scenes = search_slc(
                    target.bbox,
                    start=start_d.isoformat() if use_range else None,
                    end=end_d.isoformat() if use_range else None,
                    polarization=pol,
                )
                best, chosen, groups = select_track(scenes, orbit_direction=orbit)
                st.session_state["slc_result"] = (best, chosen, groups, len(scenes))
            except Exception as exc:  # noqa: BLE001
                st.session_state.pop("slc_result", None)
                st.error(f"검색 실패: {exc}")

    result = st.session_state.get("slc_result")
    if not result:
        return
    best, chosen, groups, n_total = result
    st.write(f"총 **{n_total}** 장면({pol}) · 트랙 후보 **{len(groups)}** 개")
    if not best:
        st.warning("조건에 맞는 트랙이 없습니다.")
        return

    rows = [{"선택": "✅" if g.key == best.key else "", "방향": g.flight_direction,
             "path": g.path, "frame": g.frame, "장면수": g.n_scenes,
             "시작": g.first_date, "끝": g.last_date} for g in groups]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.success(f"선택 트랙: **{best.flight_direction} path {best.path}/frame {best.frame}** "
               f"— {best.n_scenes}장 ({best.first_date}~{best.last_date})")
    st.caption("취득일: " + ", ".join(s.date for s in chosen))

    if st.button("💾 트랙 선별 저장 (레시피)", key="btn_save_track"):
        sel = TrackSelection.from_selection(best, chosen, polarization=pol)
        out = save_track_selection("data/insar_recipe/track_selection.json", sel)
        st.success(f"저장됨 → `{out}` (SARvey 처리 대상 {sel.n_scenes}장)")
        st.json(sel.model_dump())


def era5_master_section() -> None:
    """E 단계 — 선별 트랙 취득일의 ERA5(강수·습도)로 SARvey master 선정."""
    from inframon.insar.recipe import (
        load_bridge_target,
        load_track_selection,
        save_master_selection,
    )

    target_path = "data/insar_recipe/bridge_target.json"
    track_path = "data/insar_recipe/track_selection.json"
    if not Path(target_path).exists() or not Path(track_path).exists():
        st.info("먼저 **교량 타깃**과 **🛰️ SLC 트랙 선별**을 저장하세요(위치·취득일 필요).")
        return
    target = load_bridge_target(target_path)
    track = load_track_selection(track_path)

    st.caption(f"대상: **{target.name}** @ {target.selected_lat:.4f}, {target.selected_lon:.4f} · "
               f"트랙 {track.flight_direction} path{track.path}/frame{track.frame} · "
               f"{track.n_scenes}장 ({track.first_date}~{track.last_date})")
    st.caption("종합: combined = baseline 기대 coherence(rho) × 건조도(강수·습도). 최대가 master.")
    use_baseline = st.checkbox("수직 baseline 포함 (ASF 조회, 네트워크)", value=True, key="era5_perp")

    if st.button("🌧️ master 선정 (baseline × 강수·습도)", key="btn_era5"):
        from inframon.insar.era5_master import select_master
        perp = None
        with st.spinner("ERA5(Open-Meteo) + baseline(ASF) 조회 중…"):
            if use_baseline:
                try:
                    from inframon.insar.slc_search import perpendicular_baselines
                    perp = perpendicular_baselines(track.scene_names) or None
                except Exception as exc:  # noqa: BLE001
                    st.warning(f"수직 baseline 조회 실패 → 시간 baseline 만 사용: {exc}")
            try:
                st.session_state["era5_master"] = select_master(
                    target.selected_lat, target.selected_lon,
                    track.scene_dates, track.scene_names, perp_baselines=perp,
                )
            except Exception as exc:  # noqa: BLE001
                st.session_state.pop("era5_master", None)
                st.error(f"master 선정 실패: {exc}")

    sel = st.session_state.get("era5_master")
    if not sel:
        return
    rows = [{"선택": "⭐" if w.date == sel.selected_master else "", "취득일": w.date,
             "강수(mm)": round(w.precip_mm, 2), "습도(%)": round(w.humidity_pct, 1),
             "rho(baseline)": round(w.rho, 3), "건조도": round(w.dry_score, 3),
             "combined": round(w.combined, 3)} for w in sel.scenes]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.success(f"선정 master: **{sel.selected_master}** "
               f"(baseline {'포함' if sel.used_baseline else '시간만'} × 건조도, "
               f"combined {max(w.combined for w in sel.scenes):.3f})")

    if st.button("💾 master 저장 (레시피)", key="btn_save_master"):
        out = save_master_selection("data/insar_recipe/master_selection_era5.json", sel)
        st.success(f"저장됨 → `{out}` (inventory.py 호환: selected_master={sel.selected_master})")
        st.json(sel.model_dump())


def sarvey_bundle_section() -> None:
    """F 준비 — 레시피 4종을 SARvey 처리 번들(매니페스트 + config)로 묶는다."""
    from inframon.insar.sarvey_config import write_sarvey_bundle

    recipe_dir = st.text_input("레시피 폴더", "data/insar_recipe", key="bundle_dir")
    st.caption("교량 타깃·트랙 선별이 있어야 하며, master(ERA5)가 있으면 reference_date 로 들어갑니다.")

    if st.button("🧩 SARvey 번들 생성", key="btn_bundle"):
        try:
            paths = write_sarvey_bundle(recipe_dir)
        except Exception as exc:  # noqa: BLE001
            st.error(f"생성 실패: {exc}")
            return
        st.success(f"생성됨 → `{paths['manifest']}` · `{paths['config']}`")
        import json as _json
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**processing_manifest.json** (상류 스택 생성)")
            st.json(_json.loads(Path(paths["manifest"]).read_text(encoding="utf-8")))
        with c2:
            st.markdown("**sarvey_config.json** (시계열 추정)")
            st.json(_json.loads(Path(paths["config"]).read_text(encoding="utf-8")))


def insar_process_section(path: str) -> None:
    """F 처리 실행 — demo(합성 시계열 → /insar→PINN→FRAM) 또는 real plan 보기."""
    from inframon.insar import processing

    recipe_dir = st.text_input("레시피 폴더", "data/insar_recipe", key="proc_recipe")
    have = Path(recipe_dir, "bridge_target.json").exists() and \
        Path(recipe_dir, "track_selection.json").exists()
    if not have:
        st.info("교량 타깃 + 트랙 선별 레시피가 필요합니다(위 섹션에서 저장).")
        return

    n_points = st.number_input("측정점 수 N (demo 합성)", 10, 5000, 200, 10, key="proc_n")
    cc1, cc2 = st.columns(2)
    pinn_real = cc1.checkbox("PINN 실구현 (PyTorch+PDE+FEM, 느림)", value=False, key="proc_pinn_real")
    fram_real = cc2.checkbox("FRAM 고도화 (점별 공명+절대보정)", value=True, key="proc_fram_real")
    c1, c2 = st.columns(2)
    if c1.button("▶ F demo 실행 (합성 → /insar→PINN→FRAM)", use_container_width=True, key="btn_fdemo"):
        pm = "real" if pinn_real else "stub"
        fm = "real" if fram_real else "stub"
        with st.spinner(f"합성 시계열 → PINN({pm}) → FRAM({fm})…" +
                        (" (PINN 학습 중, 수십 초)" if pinn_real else "")):
            try:
                fram = processing.run_demo(recipe_dir, path, n_points=int(n_points),
                                           pinn_mode=pm, fram_mode=fm)
            except Exception as exc:  # noqa: BLE001
                st.error(f"실패: {exc}")
                return
        st.success(f"완료 — project.h5 갱신: N={fram.n_points}, M={fram.n_dates}, "
                   f"최대 CRI {fram.cri_global_max:.3f}, 경보 {fram.warning.level}")
        st.rerun()

    if c2.button("real 처리 plan 보기 (Linux/WSL)", use_container_width=True, key="btn_fplan"):
        st.session_state["f_plan"] = processing.plan_real(recipe_dir, "data/insar_work",
                                                          project_h5=path)
    if st.session_state.get("f_plan"):
        st.caption("real 모드는 ISCE2/MiaplPy/SARvey 가 있는 Linux/WSL 에서 순서대로 실행:")
        st.code("\n".join(st.session_state["f_plan"]), language="bash")


# ───────────────────────────── ① InSAR 탭 ─────────────────────────────
def tab_insar(path: str, start: date) -> None:
    st.subheader("① InSAR — 변위 시계열 추출 (데이터 관문)")

    with st.expander("🗺️ 교량 타깃 지정 (지도 + OSM 확인)  · A·B 단계", expanded=False):
        bridge_target_section()

    with st.expander("⚙️ SLC 선별 기준 (baseline · 편파 · 트랙)  · C·D 단계", expanded=False):
        selection_criteria_section()

    with st.expander("🛰️ SLC 검색 · 트랙 선별 (ASF)  · C·D 단계", expanded=False):
        slc_search_section()

    with st.expander("🌧️ ERA5 master 선정 (강수·습도)  · E 단계", expanded=False):
        era5_master_section()

    with st.expander("🧩 SARvey 번들 생성 (레시피 → config)  · F 준비", expanded=False):
        sarvey_bundle_section()

    with st.expander("▶ InSAR 처리 실행 (F)  · demo=합성 end-to-end / real=WSL plan", expanded=False):
        insar_process_section(path)

    # 실데이터 인벤토리 점검
    with st.expander("🛰️ 실데이터 인벤토리 점검 (SLC/궤도/DEM)"):
        root = st.text_input("InSAR 데이터 루트", "", key="insar_root",
                             placeholder="예: D:/insar_data/jeongjagyo")
        if st.button("인벤토리 점검", key="btn_inspect") and root:
            from inframon.insar.inventory import build_scene_manifest, inspect_insar_data
            try:
                inv = inspect_insar_data(root)
                man = build_scene_manifest(root)
                a, b, c = st.columns(3)
                a.metric("SLC zip", inv.slc_zip_count)
                b.metric("SLC 용량", f"{inv.slc_total_gb:.2f} GB")
                c.metric("사용 가능 장면", f"{man['usable_count']}/{man['source_slc_count']}")
                st.write(f"날짜 범위: **{inv.slc_first_date or '-'} ~ {inv.slc_last_date or '-'}** · "
                         f"Master: **{inv.selected_master or '-'}** · Orbit {inv.orbit_count} · DEM {len(inv.dem_files)}")
                if inv.missing_required:
                    st.error("필수 누락: " + ", ".join(inv.missing_required))
                for w in inv.warnings:
                    st.warning(w)
            except Exception as exc:  # noqa: BLE001 — UI에 그대로 노출
                st.error(f"점검 실패: {exc}")

    # Track HDF5 가져오기
    with st.expander("📥 Track HDF5 → /insar 계약으로 가져오기"):
        track = st.text_input("Track 결과 HDF5 경로", "", key="track_path",
                             placeholder="예: data/track_b_results.h5")
        if st.button("가져오기", key="btn_import") and track:
            from inframon.contracts.io import ProjectStore
            from inframon.insar.track_reader import import_track_h5
            try:
                with ProjectStore(path, mode="a") as store:
                    meta = import_track_h5(store, track)
                st.success(f"가져오기 완료 — N={meta.n_points}, M={meta.n_dates} → {path}")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"가져오기 실패: {exc}")

    if not has_group(path, "insar"):
        st.info("아직 InSAR 데이터가 없습니다. 사이드바 **데모 데이터 생성** 또는 위 **Track HDF5 가져오기**를 쓰세요.")
        return

    # 변위 시각화
    los, lon, xyz, member, coh = read(
        path, "/insar/los", "/insar/longitudinal", "/insar/xyz", "/insar/member", "/insar/coherence"
    )
    times = project_times(path, start)
    N, M = los.shape

    a, b, c, d = st.columns(4)
    a.metric("측정점 N", N)
    b.metric("시점 M", M)
    c.metric("평균 coherence", f"{float(np.mean(coh)):.3f}")
    d.metric("기간", f"{times[0]:%y-%m} ~ {times[-1]:%y-%m}")

    k = st.slider("시점 (t)", 0, M - 1, M - 1, key="insar_t")
    st.caption(f"선택 시점: **{times[k]:%Y-%m-%d}**")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**LOS 변위 맵 (선택 시점)**")
        st.scatter_chart({"x": xyz[:, 0], "y": xyz[:, 1], "LOS": los[:, k]},
                         x="x", y="y", color="LOS")
    with c2:
        st.markdown("**측정점별 변위 시계열**")
        pt = st.number_input("측정점 index", 0, N - 1, 0, key="insar_pt")
        st.caption(f"측정점 {pt} coherence: **{float(coh[pt]):.3f}**")
        df = pd.DataFrame({"날짜": times, "LOS": los[pt], "종방향": lon[pt]}).set_index("날짜")
        st.line_chart(df)

    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**부재 구성**")
        names = member_names(member)
        counts = pd.Series(names).value_counts()
        st.bar_chart(counts)
    with c4:
        st.markdown("**coherence 분포**")
        hist, edges = np.histogram(coh, bins=20, range=(0, 1))
        st.bar_chart(pd.DataFrame({"coherence": edges[:-1], "n": hist}).set_index("coherence"))


# ───────────────────────────── ② PINN 탭 ──────────────────────────────
def tab_pinn(path: str, start: date) -> None:
    st.subheader("② PINN — 물리 성분분해 · 구조응답 · 변동 V")
    if not has_group(path, "pinn"):
        st.info("PINN 결과가 없습니다. 사이드바 **데모 데이터 생성**으로 전체 파이프라인을 돌리세요.")
        return

    meta = read_meta(path, "pinn")
    func_names = meta.get("func_names", ["thermal", "load", "bearing", "foundation"])
    Vfs = read(path, "/pinn/V_func_series")  # [n_func, M]
    times = project_times(path, start)

    st.markdown("**기능별 변동 V 시계열** (FRAM 공명 입력)")
    st.line_chart(pd.DataFrame(Vfs.T, index=pd.Index(times, name="날짜"), columns=func_names))

    ct, cl, cs, ca = read(
        path, "/pinn/comp_thermal", "/pinn/comp_load", "/pinn/comp_settle", "/pinn/comp_anomaly"
    )
    N = ct.shape[0]
    pt = st.number_input("측정점 index", 0, N - 1, 0, key="pinn_pt")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**변위 성분분해 (선택 측정점)**")
        df = pd.DataFrame(
            {"날짜": times, "열팽창": ct[pt], "하중": cl[pt], "침하": cs[pt], "이상": ca[pt]}
        ).set_index("날짜")
        st.line_chart(df)
    with c2:
        st.markdown("**구조응답 (선택 측정점)**")
        strain, stress, defl = read(path, "/pinn/strain", "/pinn/stress", "/pinn/deflection")
        df = pd.DataFrame(
            {"날짜": times, "변형률": strain[pt], "처짐": defl[pt]}
        ).set_index("날짜")
        st.line_chart(df)

    EI, alpha, nat = read(path, "/pinn/EI", "/pinn/alpha", "/pinn/natural_freq")
    a, b, c = st.columns(3)
    a.metric("평균 EI", f"{float(np.mean(EI)):.2e}")
    b.metric("평균 α", f"{float(np.mean(alpha)):.2e}")
    c.metric("고유진동수", ", ".join(f"{x:.1f}" for x in np.atleast_1d(nat)) + " Hz")


# ───────────────────────────── ③ FRAM 탭 ──────────────────────────────
def tab_fram(path: str, start: date) -> None:
    st.subheader("③ FRAM — 공명 위험 지수 CRI · 경보")
    if not has_group(path, "fram"):
        st.info("FRAM 결과가 없습니다. 사이드바 **데모 데이터 생성**으로 전체 파이프라인을 돌리세요.")
        return

    data = fram_panel_data(path)
    cri, xyz = data["cri"], data["xyz"]
    net, cal = data["network_resonance"], data["calibrated_risk"]
    times = project_times(path, start)
    warning = data["warning"]
    N, M = cri.shape

    level = warning.get("level", "정상")
    banner_fn, emoji = LEVEL_STYLE.get(level, ("info", "⚪"))
    members = warning.get("critical_members", [])
    lead = warning.get("lead_time_days")
    getattr(st, banner_fn)(f"{emoji} 경보 등급: **{level}**")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("최대 CRI", f"{cri.max():.3f}")
    c2.metric("예상 리드타임", "—" if lead is None else f"{lead:.0f} 일")
    c3.metric("위험 부재 수", len(members))
    # 보정 캘리브레이터가 있으면 절대 붕괴확률을, 없으면 격자 크기를 보여준다.
    if data["calibrated_max"] is not None:
        c4.metric("최대 붕괴확률(보정)", f"{data['calibrated_max'] * 100:.1f}%")
    else:
        c4.metric("측정점 × 시점", f"{N} × {M}")
    if members:
        st.caption("위험 부재: " + ", ".join(members))

    k = st.slider("시점 (t)", 0, M - 1, M - 1, key="fram_t")
    st.caption(f"선택 시점: **{times[k]:%Y-%m-%d}**")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**측정점별 CRI (선택 시점)**")
        st.scatter_chart({"x": xyz[:, 0], "y": xyz[:, 1], "CRI": cri[:, k]},
                         x="x", y="y", color="CRI")
    with col2:
        st.markdown("**전역 최대 CRI 시계열**")
        st.line_chart(pd.DataFrame({"CRI_max": cri.max(axis=0)}, index=pd.Index(times, name="날짜")))

    st.markdown("**CRI 히트맵 (측정점 × 시점)**")
    st.image((cri / (cri.max() + 1e-9) * 255).astype(np.uint8),
             caption="밝을수록 위험", use_container_width=True)

    # ── 함수망 공명(N-K) S(t): FRAM real 이 있을 때만 ──
    if net is not None:
        st.markdown("**함수망 공명 강도 S(t)** (기능 결합 네트워크 스펙트럼 — 창발적 다기능 공명)")
        st.line_chart(pd.DataFrame({"S(t)": np.asarray(net)}, index=pd.Index(times, name="날짜")))

    # ── isotonic 보정 붕괴확률: 캘리브레이터를 적용했을 때만 ──
    if cal is not None:
        cal = np.asarray(cal)
        st.markdown("**보정 붕괴확률** (isotonic 캘리브 — CRI 를 절대 확률로 매핑)")
        cc1, cc2 = st.columns(2)
        with cc1:
            st.caption(f"선택 시점 **{times[k]:%Y-%m-%d}** · 측정점별 붕괴확률")
            st.scatter_chart({"x": xyz[:, 0], "y": xyz[:, 1], "P": cal[:, k]},
                             x="x", y="y", color="P")
        with cc2:
            st.caption("전역 최대 붕괴확률 시계열")
            st.line_chart(pd.DataFrame({"P_max": cal.max(axis=0)},
                                       index=pd.Index(times, name="날짜")))


# ───────────────────────────────── main ───────────────────────────────
def main() -> None:
    st.set_page_config(page_title="인프라 모니터링", layout="wide")
    st.title("통합 인프라 모니터링 — InSAR → PINN → FRAM")

    path = st.sidebar.text_input("project.h5 경로", DEFAULT_H5)
    with st.sidebar.expander("⚙️ 데모 데이터 생성", expanded=not Path(path).exists()):
        n_points = st.number_input("측정점 수 N", 2, 2000, 200, 10)
        n_dates = st.number_input("취득 시점 수 M", 2, 240, 36, 1)
        st.caption("엔진별 real 구현 선택 (체크=real, 해제=stub):")
        cv_r = st.checkbox("CV real (영상→ROI/축선)", value=False, key="eng_cv")
        pinn_r = st.checkbox("PINN real (PDE+FEM, 느림)", value=False, key="eng_pinn")
        fram_r = st.checkbox("FRAM 고도화", value=False, key="eng_fram")
        if st.button("▶ 데모 파이프라인 실행", use_container_width=True):
            engines = {"cv": "real" if cv_r else "stub", "insar": "stub",
                       "pinn": "real" if pinn_r else "stub", "fram": "real" if fram_r else "stub"}
            with st.spinner("CV→InSAR→PINN→FRAM 실행 중…"):
                run_demo(path, int(n_points), int(n_dates), engines)
            st.success("완료 — project.h5 갱신됨")
            st.rerun()
    start = st.sidebar.date_input("기준 시작일 (날짜축용)", value=date(2023, 1, 1))

    tabs = st.tabs(["① InSAR", "② PINN", "③ FRAM"])
    with tabs[0]:
        tab_insar(path, start)
    with tabs[1]:
        if Path(path).exists():
            tab_pinn(path, start)
        else:
            st.info("project.h5 없음 — 사이드바에서 데모 데이터를 먼저 생성하세요.")
    with tabs[2]:
        if Path(path).exists():
            tab_fram(path, start)
        else:
            st.info("project.h5 없음 — 사이드바에서 데모 데이터를 먼저 생성하세요.")


if __name__ == "__main__":
    main()
