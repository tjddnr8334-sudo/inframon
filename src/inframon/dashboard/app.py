"""내장 대시보드 (문서 5.8 / 7.2) — 단계별 탭 구조 (InSAR → PINN → FRAM).

실행:  pip install -e .[dashboard]
       streamlit run src/inframon/dashboard/app.py

데이터 흐름의 시작점인 InSAR(실측 변위)를 첫 탭·관문으로 두고,
PINN(물리 해석) → FRAM(공명 위험 CRI) 순으로 파이프라인을 따라간다.
FRAM 탭은 기능 공명 다이어그램(4기능 변동 레이더 + R_ij 결합)을 포함한다.
"""

from __future__ import annotations

import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import streamlit as st

from inframon.contracts.schema import MEMBER_TYPES
from inframon.dashboard.data import (
    fram_function_diagram,
    fram_panel_data,
    has_group,
    read_meta,
)
from inframon.dashboard.data import read_arrays as read

DEFAULT_H5 = "data/project.h5"


def default_project_path() -> str:
    """기본 project.h5 경로.

    소스 실행: 작업폴더의 `data/project.h5`.
    frozen(.exe): exe 옆 `data/project.h5`(쓰기 가능). 없으면 번들에 동봉한 데모를
    한 번 복사해 시드 → 더블클릭 첫 실행에서 바로 채워진 대시보드가 보인다.
    """
    if not getattr(sys, "frozen", False):
        return DEFAULT_H5
    data_dir = Path(sys.executable).parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "project.h5"
    if not target.exists():
        seed = Path(getattr(sys, "_MEIPASS", "")) / "data" / "project.h5"
        if seed.exists():
            try:
                shutil.copyfile(seed, target)
            except OSError:
                pass
    return str(target)


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
        def _s(x):  # S8(bytes)·np.bytes_·str 모두 'YYYYMMDD' 문자열로
            return x.decode() if isinstance(x, (bytes, np.bytes_)) else str(x)
        return [datetime.strptime(_s(s).strip(), "%Y%m%d") for s in labels]
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


def _recipe_dir() -> str:
    """현재 교량 프로젝트의 레시피 폴더(상단에서 설정). 교량마다 분리해 여러 개를 관리."""
    return (st.session_state.get("recipe_dir") or "data/insar_recipe").strip() or "data/insar_recipe"


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

    recipe_path = st.text_input("레시피 저장 경로", f"{_recipe_dir()}/bridge_target.json",
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
                         tooltip=f"점 추출 AOI: {existing.name} (buffer {existing.aoi_buffer_m:.0f}m)").add_to(m)
        if existing.bridge_bbox:
            bn_lon, bn_lat, bx_lon, bx_lat = existing.bridge_bbox
            folium.Rectangle([[bn_lat, bn_lon], [bx_lat, bx_lon]], color="orange", weight=2,
                             tooltip="교량 자체 extent").add_to(m)

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
        aoi_buffer = st.slider("🎯 주변 point 추출 반경 (buffer, m)", 50, 1000, 200, 50,
                               key="aoi_buffer",
                               help="교량 둘레로 이만큼 확장한 영역에서 점을 뽑습니다. 교량 자체(주황)는 "
                                    "얇은 선이라, 주변 점을 얻으려면 buffer(파랑 AOI)가 필요합니다.")
        if click and st.button("💾 타깃으로 저장 (레시피)", key="btn_save_target"):
            from inframon.insar.recipe import BridgeTarget, save_bridge_target
            tgt = BridgeTarget.from_bridge(sel, click["lat"], click["lng"], aoi_buffer_m=float(aoi_buffer))
            save_bridge_target(recipe_path, tgt)
            mn_lon, mn_lat, mx_lon, mx_lat = tgt.bbox
            w_m = (mx_lon - mn_lon) * 111320 * 0.79
            h_m = (mx_lat - mn_lat) * 111320
            st.success(f"저장됨 → `{recipe_path}` · 점 추출 AOI ≈ {w_m:.0f}×{h_m:.0f} m "
                       f"(buffer {aoi_buffer}m). 지도 다시 그리면 파랑=AOI·주황=교량.")
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

    crit_path = st.text_input("선별 기준 저장 경로", f"{_recipe_dir()}/selection_criteria.json",
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


def _win2wsl(p: str) -> str:
    """Windows 경로 → WSL(/mnt/<drive>/...) 경로."""
    p = str(Path(p).resolve())
    if len(p) > 1 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:].replace("\\", "/")
    return p.replace("\\", "/")


def wsl_status(target_dir: str = "~") -> dict:
    """WSL2 가용성·도구·Earthdata 인증 + 다운로드 폴더 여유 용량(GB)을 점검."""
    import re
    import subprocess
    out = {"wsl": False, "asf": False, "netrc": False, "free_gb": None, "detail": ""}
    try:
        r = subprocess.run(
            ["wsl", "-e", "bash", "-lc",
             "echo WSL_OK; python3 -c 'import asf_search' 2>/dev/null && echo ASF_OK; "
             "grep -q urs.earthdata ~/.netrc 2>/dev/null && echo NETRC_OK; "
             f"mkdir -p {target_dir} 2>/dev/null; "
             f"echo FREEGB:$(df -BG --output=avail {target_dir} 2>/dev/null | tail -1 | tr -dc '0-9')"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        s = r.stdout or ""
        out["wsl"] = "WSL_OK" in s
        out["asf"] = "ASF_OK" in s
        out["netrc"] = "NETRC_OK" in s
        mfree = re.search(r"FREEGB:(\d+)", s)
        out["free_gb"] = int(mfree.group(1)) if mfree and mfree.group(1) else None
        out["detail"] = s.strip() or (r.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001 (wsl 미설치 등)
        out["detail"] = str(exc)
    return out


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

    target_path = st.text_input("교량 타깃 레시피", f"{_recipe_dir()}/bridge_target.json",
                               key="slc_target_path")
    if not Path(target_path).exists():
        st.info("먼저 **🗺️ 교량 타깃 지정**에서 교량을 저장하세요(검색 영역 bbox 필요).")
        return
    target = load_bridge_target(target_path)

    crit_path = f"{_recipe_dir()}/selection_criteria.json"
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
        out = save_track_selection(f"{_recipe_dir()}/track_selection.json", sel)
        st.success(f"저장됨 → `{out}` (SARvey 처리 대상 {sel.n_scenes}장)")
        st.json(sel.model_dump())

    # ── ⬇️ 실제 SLC 다운로드 (선택 트랙 장면) — WSL 우선 ──
    st.markdown("**⬇️ 선택 트랙 SLC 다운로드** (실제 Sentinel-1 SLC, GB급)")
    st.caption("실 SAR 처리(ISCE2→MiaplPy→SARvey)는 WSL2 에서 하므로, SLC 도 WSL 에 받는 것이 정석입니다. "
               "먼저 WSL 가용성을 확인합니다.")
    if st.button("WSL 환경 확인", key="btn_wsl_check"):
        with st.spinner("WSL2 점검 중…"):
            st.session_state["wsl_status"] = wsl_status()
    wsl = st.session_state.get("wsl_status")
    if wsl is None:
        st.info("‘WSL 환경 확인’을 눌러 WSL2·asf_search·Earthdata 인증 준비도를 점검하세요.")
        return
    free = wsl.get("free_gb")
    st.write(f"WSL2 {'✅' if wsl['wsl'] else '❌'} · asf_search {'✅' if wsl['asf'] else '❌'} · "
             f"Earthdata(~/.netrc) {'✅' if wsl['netrc'] else '❌'} · "
             f"여유 용량 {'❓' if free is None else f'{free} GB'}")

    n_av = len(chosen)

    def _scene_lbl(i):
        s = chosen[i]
        b = getattr(s, "perpendicular_baseline", None)
        return f"{i:02d} · {s.date}" + (f" · B⊥{b:+.0f}m" if b is not None else "")

    sel_mode = st.radio("장면 선택 방식", ["앞에서 N장", "직접 선택", "기간으로"],
                        horizontal=True, key="slc_sel_mode")
    if sel_mode == "앞에서 N장":
        cap = st.number_input("장면 수", 1, n_av, min(20, n_av), 1, key="slc_cap")
        sel_idx = list(range(int(cap)))
    elif sel_mode == "직접 선택":
        sel_idx = sorted(st.multiselect(
            "다운로드할 장면 (날짜 · 수직baseline)", list(range(n_av)),
            default=list(range(min(20, n_av))), format_func=_scene_lbl, key="slc_pick"))
    else:  # 기간으로
        from datetime import date as _date

        def _pd(s):
            return _date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        cc1, cc2 = st.columns(2)
        d0 = cc1.date_input("시작", _pd(chosen[0].date), key="slc_d0")
        d1 = cc2.date_input("끝", _pd(chosen[-1].date), key="slc_d1")
        s0, s1 = d0.strftime("%Y%m%d"), d1.strftime("%Y%m%d")
        sel_idx = [i for i, s in enumerate(chosen) if s0 <= s.date <= s1]

    sel_scenes = [chosen[i] for i in sel_idx]
    urls = [s.url for s in sel_scenes if getattr(s, "url", None)]
    est_gb = float(len(sel_scenes)) * 4.0             # S1 IW SLC ≈ 4GB/장(보수적)
    need_gb = est_gb + 5.0                            # 다운로드 + 여유 5GB
    st.caption(f"선택 **{len(sel_scenes)}장** · 예상 ~{est_gb:.0f} GB · 여유분 포함 필요 ~{need_gb:.0f} GB")

    # ── 용량 게이트: 여유 < 필요 면 다운로드 차단 ──
    enough = (free is None) or (free >= need_gb)
    if free is None:
        st.warning("여유 용량을 확인 못 했습니다(‘WSL 환경 확인’ 재실행). 다운로드는 진행 가능하나 용량 주의.")
    elif enough:
        st.success(f"용량 충분: 여유 {free} GB ≥ 필요 ~{need_gb:.0f} GB → 다운로드 가능")
    else:
        st.error(f"⛔ 용량 부족: 여유 {free} GB < 필요 ~{need_gb:.0f} GB. "
                 f"장면 수를 {max(1, int((free-5)//4))}장 이하로 줄이거나 공간을 확보하세요.")
    st.caption("※ 다운로드 후 ISCE2 코레지·MiaplPy 처리엔 SLC 대비 추가 공간(대략 2~3배)이 더 필요합니다.")

    if wsl["wsl"]:
        # ── WSL 경로: WSL 의 asf_search + ~/.netrc 로 WSL 폴더에 다운로드(처리 위치) ──
        wsl_dir = st.text_input("WSL 다운로드 폴더", "~/insar_dl/SLC", key="wsl_dl_dir")
        ready = wsl["asf"] and wsl["netrc"] and enough and bool(urls)
        if not (wsl["asf"] and wsl["netrc"]):
            st.warning("WSL 에 asf_search 또는 Earthdata ~/.netrc 가 없습니다. "
                       "`python3 -m pip install --user asf_search` + ~/.netrc(urs.earthdata) 설정 필요.")
        if not urls:
            st.info("다운로드할 장면을 선택하세요.")
        if st.button(f"⬇️ WSL 로 {len(sel_scenes)}장 다운로드", key="btn_wsl_dl", type="primary", disabled=not ready):
            import subprocess
            urls_win = Path("data/_dl_urls.txt"); urls_win.parent.mkdir(parents=True, exist_ok=True)
            urls_win.write_text("\n".join(urls), encoding="utf-8")
            dl_py = _win2wsl(str(Path("scripts/wsl_sarvey/dl_urls.py")))
            urls_wsl = _win2wsl(str(urls_win))
            cmd = ["wsl", "-e", "bash", "-lc",
                   f"python3 {dl_py} --urls {urls_wsl} --out {wsl_dir}"]
            if not urls:
                st.error("다운로드 URL이 없습니다(검색·선별 먼저).")
            else:
                with st.spinner(f"WSL 에서 {len(urls)}장 다운로드 중… (~{est_gb:.0f}GB, 시간 소요)"):
                    try:
                        r = subprocess.run(cmd, capture_output=True, text=True,
                                           encoding="utf-8", errors="replace", timeout=7200)
                        out = (r.stdout or "") + (r.stderr or "")
                        tail = out.strip().splitlines()[-8:]
                        if "DONE" in (r.stdout or ""):
                            st.success(f"완료 → WSL `{wsl_dir}`")
                        elif r.returncode != 0:
                            st.warning(f"WSL 종료코드 {r.returncode} — 로그 확인:")
                        else:
                            st.warning("종료(완료 표식 없음) — 로그 확인:")
                        st.code("\n".join(tail) or "(출력 없음)")
                        st.caption("다음(WSL): `scripts/wsl_sarvey/20_stack_isce.sh` (ISCE2) → MiaplPy → SARvey.")
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"WSL 다운로드 실패: {exc}")
    else:
        st.warning("WSL2 가 없어 실 SAR 처리(ISCE2/SARvey)는 불가합니다. 임시로 Windows 에 받을 수는 있으나 "
                   "처리하려면 결국 WSL2 가 필요합니다.")
        with st.expander("Windows 로 받기(폴백, 토큰 필요)"):
            dl_dir = st.text_input("다운로드 폴더", "data/slc", key="slc_dl_dir")
            token = st.text_input("Earthdata 토큰", type="password", key="edl_token")
            if st.button(f"⬇️ Windows 로 {len(sel_scenes)}장 다운로드", key="btn_slc_dl_win",
                         disabled=not (enough and urls)):
                import os

                import asf_search as asf
                Path(dl_dir).mkdir(parents=True, exist_ok=True)
                with st.spinner(f"{len(urls)}장 다운로드 중…"):
                    try:
                        sess = asf.ASFSession()
                        if token.strip():
                            sess = sess.auth_with_token(token.strip())
                        asf.download_urls(urls, path=dl_dir, session=sess, processes=2)
                        st.success(f"완료 → `{dl_dir}` "
                                   f"(zip {len([f for f in os.listdir(dl_dir) if f.endswith('.zip')])}개)")
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"실패: {exc} (토큰/EULA 확인)")


def era5_master_section() -> None:
    """E 단계 — 선별 트랙 취득일의 ERA5(강수·습도·온도)로 SARvey master 선정.

    강수·습도·온도가 낮은(수증기 적은) 날을 선호하며, 과도한 값은 임계로 소거할 수 있다.
    """
    from inframon.insar.recipe import (
        load_bridge_target,
        load_track_selection,
        save_master_selection,
    )

    target_path = f"{_recipe_dir()}/bridge_target.json"
    track_path = f"{_recipe_dir()}/track_selection.json"
    if not Path(target_path).exists() or not Path(track_path).exists():
        st.info("먼저 **교량 타깃**과 **🛰️ SLC 트랙 선별**을 저장하세요(위치·취득일 필요).")
        return
    target = load_bridge_target(target_path)
    track = load_track_selection(track_path)

    st.caption(f"대상: **{target.name}** @ {target.selected_lat:.4f}, {target.selected_lon:.4f} · "
               f"트랙 {track.flight_direction} path{track.path}/frame{track.frame} · "
               f"{track.n_scenes}장 ({track.first_date}~{track.last_date})")
    st.caption("종합: combined = baseline 기대 coherence(rho) × 대기 안정도(강수·습도·온도). "
               "강수·습도·온도가 낮을수록(수증기 적음) 좋다. 최대가 master.")
    use_baseline = st.checkbox("수직 baseline 포함 (ASF 조회, 네트워크)", value=True, key="era5_perp")

    with st.expander("🚫 과도한 강수·습도·온도 장면 소거 (임계 초과 시 master 후보 제외)"):
        use_excl = st.checkbox("소거 사용", value=False, key="era5_excl_on")
        e1, e2, e3, e4 = st.columns(4)
        precip_max = e1.number_input("강수 상한 (mm)", 0.0, 500.0, 10.0, 1.0,
                                     key="era5_pmax", disabled=not use_excl)
        hum_max = e2.number_input("습도 상한 (%)", 0.0, 100.0, 90.0, 1.0,
                                  key="era5_hmax", disabled=not use_excl)
        tmax = e3.number_input("기온 상한 (°C)", -50.0, 60.0, 30.0, 1.0,
                               key="era5_tmax", disabled=not use_excl)
        tmin = e4.number_input("기온 하한 (°C)", -50.0, 60.0, -20.0, 1.0,
                               key="era5_tmin", disabled=not use_excl)

    if st.button("🌧️ master 선정 (baseline × 강수·습도·온도)", key="btn_era5"):
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
                    precip_max_mm=precip_max if use_excl else None,
                    humidity_max_pct=hum_max if use_excl else None,
                    temp_max_c=tmax if use_excl else None,
                    temp_min_c=tmin if use_excl else None,
                )
            except Exception as exc:  # noqa: BLE001
                st.session_state.pop("era5_master", None)
                st.error(f"master 선정 실패: {exc}")

    sel = st.session_state.get("era5_master")
    if not sel:
        return
    rows = [{"선택": "⭐" if w.date == sel.selected_master else ("🚫" if w.excluded else ""),
             "취득일": w.date, "강수(mm)": round(w.precip_mm, 2),
             "습도(%)": round(w.humidity_pct, 1),
             "기온(°C)": (round(w.temp_c, 1) if w.temp_c is not None else None),
             "rho(baseline)": round(w.rho, 3), "대기안정도": round(w.dry_score, 3),
             "combined": round(w.combined, 3),
             "소거사유": w.exclude_reason} for w in sel.scenes]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if sel.n_excluded:
        st.caption(f"🚫 과도한 강수/습도/온도로 **{sel.n_excluded}개** 장면 소거됨.")
    st.success(f"선정 master: **{sel.selected_master}** "
               f"(baseline {'포함' if sel.used_baseline else '시간만'} × 대기 안정도, "
               f"combined {max(w.combined for w in sel.scenes):.3f})")

    if st.button("💾 master 저장 (레시피)", key="btn_save_master"):
        out = save_master_selection(f"{_recipe_dir()}/master_selection_era5.json", sel)
        st.success(f"저장됨 → `{out}` (inventory.py 호환: selected_master={sel.selected_master})")
        st.json(sel.model_dump())


def accuracy_section(path: str, times) -> None:
    """InSAR 정확도 보정 — 기준점 정합 + 온도회귀(열팽창 분리) → 순 변형속도."""
    from inframon.insar.atmo import (
        height_correlated_correction, most_stable_index, reference_correction, temporal_decompose,
    )
    los, xyz, coh = read(path, "/insar/los", "/insar/xyz", "/insar/coherence")
    N, M = los.shape
    days = np.array([(t - times[0]).days for t in times], dtype=float)
    st.caption("기준점 대비 상대변위 + 온도회귀로 열팽창 분리 → **순 변형속도(mm/yr)**. "
               "InSAR 절대·계절 편향을 줄입니다.")
    auto = most_stable_index(los, coh)
    ref = st.number_input(f"기준점 index (안정점 자동추천 #{auto})", 0, N - 1, int(auto), key="ref_idx")
    use_T = st.checkbox("🌡️ 온도 회귀로 열팽창 분리", value=True, key="acc_T")
    use_tropo = st.checkbox("🌫️ 고도상관 대기보정 (GACOS 대안)", value=False, key="acc_tropo")
    if not st.button("🎯 정확도 보정 실행", key="btn_accuracy"):
        st.caption("기준점·온도회귀·대기보정 옵션을 정하고 '실행'을 누르세요 (온도는 실행 시 수집).")
        return
    losr = reference_correction(los, ref)
    T = None
    if use_T:
        lat = lon = None
        tp = f"{_recipe_dir()}/bridge_target.json"
        if Path(tp).exists():
            try:
                from inframon.insar.recipe import load_bridge_target
                _t = load_bridge_target(tp); lat, lon = _t.selected_lat, _t.selected_lon
            except Exception:  # noqa: BLE001
                pass
        if lat is None and float(np.abs(xyz[:, 0]).max()) <= 180:
            lon, lat = float(np.median(xyz[:, 0])), float(np.median(xyz[:, 1]))
        if lat is not None:
            from inframon.insar.era5_master import fetch_temperature
            try:
                T = np.array(fetch_temperature(lat, lon, [t.strftime("%Y%m%d") for t in times]))
            except Exception as e:  # noqa: BLE001
                st.warning(f"온도 수집 실패(회귀 생략): {e}")

    # 고도상관 대기보정(성층 대류권) — /insar/height 있을 때만
    if use_tropo:
        try:
            hgt = read(path, "/insar/height")
            if hgt is not None and np.ptp(hgt) > 1.0:
                losr = height_correlated_correction(losr, hgt)["corrected"]
                st.caption("고도-위상 선형상관 제거 적용됨.")
            else:
                st.caption("고도 정보 없음/평탄 — 대기보정 skip (Track H5 에 height 필요).")
        except Exception:  # noqa: BLE001
            st.caption("고도 정보 없음 — 대기보정 skip.")

    dec = temporal_decompose(losr, days, T)
    vel = dec["velocity_mm_yr"]
    st.success(f"순 변형속도: 평균 {vel.mean():+.2f} · 범위 {vel.min():+.1f}~{vel.max():+.1f} mm/yr "
               f"· 잔차 {dec['resid_std'].mean():.2f}mm"
               + (f" · 열계수 {np.mean(dec['thermal_coef']):.2f} mm/°C" if dec["used_temperature"] else ""))
    lon_c, lat_c = xyz[:, 0], xyz[:, 1]
    if float(np.abs(lon_c).max()) <= 180:
        try:
            import folium
            from streamlit_folium import st_folium
            m = folium.Map(location=[float(lat_c.mean()), float(lon_c.mean())], zoom_start=16,
                           tiles="OpenStreetMap")
            vmax = float(np.percentile(np.abs(vel), 95)) or 1.0
            for i in range(len(lon_c)):
                x = float(np.clip(vel[i] / vmax, -1, 1))
                col = (f"#ff{int(255*(1+x)):02x}{int(255*(1+x)):02x}" if x < 0
                       else f"#{int(255*(1-x)):02x}{int(255*(1-x)):02x}ff")
                folium.CircleMarker([float(lat_c[i]), float(lon_c[i])], radius=3, weight=0, color=col,
                                    fill=True, fill_color=col, fill_opacity=0.85,
                                    tooltip=f"{vel[i]:+.2f} mm/yr").add_to(m)
            st.markdown("**순 변형속도 지도** (열팽창 분리 후, 🔴침하·🔵융기)")
            st_folium(m, height=420, key="acc_map")
        except ImportError:
            pass


def portfolio_section():
    """여러 교량 project.h5 를 목록·상태(CRI/경보)로 보여주고 선택 → 경로 반환."""
    import glob
    import json as _json
    files = sorted(set(glob.glob("data/*.h5") + glob.glob("data/projects/*.h5")))
    rows = []
    for fp in files:
        try:
            with h5py.File(fp, "r") as f:
                if "/fram" not in f:
                    continue
                fm = _json.loads(f["/fram"].attrs.get("meta", "{}"))
                im = _json.loads(f["/insar"].attrs.get("meta", "{}")) if "/insar" in f else {}
                rows.append({"파일": Path(fp).name,
                             "점": im.get("n_points"), "시점": im.get("n_dates"),
                             "최대CRI": round(float(fm.get("cri_global_max", 0)), 3),
                             "경보": fm.get("warning", {}).get("level", "—"), "_path": fp})
        except Exception:  # noqa: BLE001
            continue
    if not rows:
        st.caption("등록된 교량 프로젝트 없음 (data/*.h5 중 /fram 포함).")
        return None
    st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if k != "_path"} for r in rows]),
                 hide_index=True, use_container_width=True)
    names = [r["파일"] for r in rows]
    pick = st.selectbox("교량(프로젝트) 선택", names, key="portfolio_pick")
    st.caption("선택하면 아래 경로가 그 교량으로 바뀝니다. (여러 교량은 data/ 에 project_*.h5 로 저장)")
    return next(r["_path"] for r in rows if r["파일"] == pick)


def asc_desc_section() -> None:
    """Asc+Desc 두 Track H5 → 연직(U)·종축(H) 분해 (fuse_asc_desc). LOS 모호성 해소."""
    from inframon.insar.fusion import FusionError, fuse_asc_desc
    from inframon.insar.track_reader import read_track_h5
    st.caption("서로 다른 궤도(오름·내림) LOS 2개를 합쳐 **연직 변위 U**(교량 처짐의 핵심)와 종축 H 로 분해합니다. "
               "두 Track H5 모두 **입사각(incidence)** 이 있어야 합니다.")
    c1, c2 = st.columns(2)
    asc_p = c1.text_input("Ascending Track H5", "data/sarvey_track.h5", key="ad_asc")
    desc_p = c2.text_input("Descending Track H5", "data/sarvey_track_desc.h5", key="ad_desc")
    out_p = st.text_input("융합 결과 저장", "data/fused_vertical.h5", key="ad_out")
    if st.button("🔀 Asc+Desc 연직분해", key="btn_ad"):
        if not (Path(asc_p).exists() and Path(desc_p).exists()):
            st.error("두 Track H5 경로가 모두 존재해야 합니다. (descending 트랙도 다운→코레지→SARvey 로 준비)")
            return
        try:
            asc, desc = read_track_h5(asc_p), read_track_h5(desc_p)
            res = fuse_asc_desc(asc, desc)
        except FusionError as e:
            st.error(f"융합 불가: {e}")
            st.caption("→ 두 Track H5 에 incidence/heading 이 필요합니다(SARvey export 시 geometry 포함). "
                       "없으면 단일 궤도 LOS 로 진행하세요.")
            return
        except Exception as e:  # noqa: BLE001
            st.error(f"실패: {e}"); return
        U = res.vertical
        import numpy as _np
        tv = res.track.date_labels
        try:
            days = _np.array([int(str(int(d)) ) for d in tv])  # noqa
        except Exception:  # noqa: BLE001
            days = _np.arange(U.shape[1])
        # 점별 연직 속도(mm/yr) 근사
        st.success(f"연직분해 완료 — 정합점 {U.shape[0]}개 × 공통 {U.shape[1]}시점")
        lon = res.track.lonlat[:, 0]; lat = res.track.lonlat[:, 1]
        vU = U[:, -1] - U[:, 0]
        st.caption(f"연직변위(말−초) 범위 {float(_np.nanmin(vU)):.1f} ~ {float(_np.nanmax(vU)):.1f} mm")
        try:
            import folium
            from streamlit_folium import st_folium
            m = folium.Map(location=[float(_np.nanmean(lat)), float(_np.nanmean(lon))], zoom_start=16,
                           tiles="OpenStreetMap")
            vmax = float(_np.nanpercentile(_np.abs(vU), 95)) or 1.0
            for i in range(len(lon)):
                x = float(_np.clip(vU[i] / vmax, -1, 1))
                col = (f"#ff{int(255*(1+x)):02x}{int(255*(1+x)):02x}" if x < 0
                       else f"#{int(255*(1-x)):02x}{int(255*(1-x)):02x}ff")
                folium.CircleMarker([float(lat[i]), float(lon[i])], radius=3, weight=0, color=col,
                                    fill=True, fill_color=col, fill_opacity=0.85,
                                    tooltip=f"연직 {vU[i]:.1f} mm").add_to(m)
            st.markdown("**연직 변위 U 지도** (🔴침하·🔵융기)")
            st_folium(m, height=420, key="ad_map")
        except ImportError:
            pass


def aux_data_section() -> None:
    """F 준비 — ISCE2/SARvey 보조데이터(궤도·DEM·AUX_CAL) 준비. 실 처리는 WSL2."""
    import subprocess
    st.caption("ISCE2 코레지에 필요: **궤도(POEORB)·DEM·AUX_CAL**. SLC 처럼 WSL 에 준비합니다. "
               "(ERA5 온도·강수·습도는 🌧️ master 섹션 + PINN 🌡️ 열팽창에서 처리)")
    if st.button("WSL 환경 확인", key="btn_wsl_aux"):
        with st.spinner("WSL 점검 중…"):
            st.session_state["wsl_status"] = wsl_status()
    wsl = st.session_state.get("wsl_status")
    if not wsl:
        st.info("‘WSL 환경 확인’을 눌러 WSL2 준비도·여유 용량을 점검하세요.")
        return
    st.write(f"WSL2 {'✅' if wsl['wsl'] else '❌'} · 여유 {wsl.get('free_gb', '?')} GB")
    if not wsl["wsl"]:
        st.warning("WSL2 가 없어 ISCE2 보조데이터 준비 불가.")
        return

    slc_dir = st.text_input("SLC 폴더(궤도 날짜 기준)", "~/insar_dl/SLC", key="aux_slc")
    orbit_dir, dem_dir = "~/insar_dl/orbits", "~/insar_dl/DEM"

    def _run(title, cmd):
        with st.spinner(f"{title} 실행 중… (WSL)"):
            try:
                r = subprocess.run(["wsl", "-e", "bash", "-lc", cmd], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=3600)
                out = (r.stdout or "") + (r.stderr or "")
                (st.success if r.returncode == 0 else st.warning)(f"{title} 종료코드 {r.returncode}")
                st.code("\n".join(out.strip().splitlines()[-10:]) or "(출력 없음)")
            except Exception as exc:  # noqa: BLE001
                st.error(f"{title} 실패: {exc}")

    conda = "source ~/miniforge3/etc/profile.d/conda.sh; conda activate isce2_mintpy;"
    c1, c2, c3 = st.columns(3)
    if c1.button("🛰️ 궤도(EOF) 받기", key="btn_orbit"):
        _run("궤도", f"{conda} mkdir -p {orbit_dir}; "
                    f"eof --search-path {slc_dir} --save-dir {orbit_dir} 2>&1 | tail -4; "
                    f"echo \"궤도 $(ls {orbit_dir}/*.EOF 2>/dev/null | wc -l)개\"")
    tgt_path = f"{_recipe_dir()}/bridge_target.json"
    if c2.button("🏔️ DEM(Copernicus) 받기", key="btn_dem"):
        if not Path(tgt_path).exists():
            st.error("🗺️ 교량 타깃(AOI bbox) 을 먼저 저장하세요.")
        else:
            from inframon.insar.recipe import load_bridge_target
            b = load_bridge_target(tgt_path).bbox    # (min_lon,min_lat,max_lon,max_lat)
            _run("DEM", f"{conda} mkdir -p {dem_dir}; cd {dem_dir}; "
                        f"sardem --bbox {b[0]} {b[1]} {b[2]} {b[3]} --data COP --output dem.wgs84 2>&1 | tail -4; "
                        f"(gdal2isce_xml.py -i dem.wgs84 >/dev/null 2>&1 && echo ISCE_XML_OK) "
                        f"|| echo 'ISCE xml 별도 필요(재사용 권장)'; ls -la dem.wgs84* 2>/dev/null")
    if c3.button("📡 AUX_CAL 확인", key="btn_aux"):
        _run("AUX_CAL", "n=$(ls ~/insar_data/aux_cal 2>/dev/null | wc -l); "
                        "echo \"기존 AUX_CAL $n개 (공개 ESA 보정파일)\"; "
                        "[ $n -gt 0 ] && echo '재사용: stackSentinel -a ~/insar_data/aux_cal' "
                        "|| echo 'ESA 에서 S1 AUX_CAL 다운 필요'")
    st.caption("준비된 궤도·DEM·AUX_CAL 로 WSL 에서 stackSentinel 코레지 → MiaplPy → SARvey 로 이어집니다.")


# coherence 임계 프리셋 — 교량에 PS/DS 점이 적을 때 완화 (native SARvey 1.3 키)
SARVEY_PRESETS = {
    "엄격 (점 적고 깨끗)":  {"coherence_p1": 0.9, "point_median_coherence": 0.7,
                            "arc_unwrapping_coherence": 0.7, "coherence_p2": 0.8},
    "균형 (권장)":         {"coherence_p1": 0.8, "point_median_coherence": 0.5,
                            "arc_unwrapping_coherence": 0.6, "coherence_p2": 0.7},
    "관대 (점 많이 — 점 부족 시)": {"coherence_p1": 0.7, "point_median_coherence": 0.4,
                            "arc_unwrapping_coherence": 0.5, "coherence_p2": 0.6},
}


def sarvey_bundle_section() -> None:
    """F 준비 — 레시피 4종을 SARvey 처리 번들(매니페스트 + config)로 묶는다."""
    import json as _json

    from inframon.insar.sarvey_config import write_sarvey_bundle

    recipe_dir = st.text_input("레시피 폴더", _recipe_dir(), key="bundle_dir")
    st.caption("교량 타깃·트랙 선별이 있어야 하며, master(ERA5)가 있으면 reference_date 로 들어갑니다.")

    # ── coherence 프리셋: 원하는 교량에 점이 안 나올 때 완화 ──
    preset_name = st.radio(
        "coherence 프리셋 (점 부족하면 '관대'로)", list(SARVEY_PRESETS), index=1,
        horizontal=True, key="sarvey_preset",
        help="교량에 PS/DS 점이 안 생기면 임계를 낮춰 점 수를 늘립니다(노이즈↑). 콘크리트/난간 등 "
             "강반사체 많은 교량은 '엄격', 매끈하거나 작은 교량은 '관대'.")
    pv = SARVEY_PRESETS[preset_name]
    pc1, pc2, pc3, pc4 = st.columns(4)
    pc1.metric("coherence_p1 (PS)", pv["coherence_p1"])
    pc2.metric("coherence_p2 (DS)", pv["coherence_p2"])
    pc3.metric("point_median", pv["point_median_coherence"])
    pc4.metric("arc_unwrap", pv["arc_unwrapping_coherence"])

    if st.button("🧩 SARvey 번들 생성 (프리셋 적용)", key="btn_bundle"):
        try:
            paths = write_sarvey_bundle(recipe_dir)
        except Exception as exc:  # noqa: BLE001
            st.error(f"생성 실패: {exc}")
            return
        # 생성된 config 에 프리셋 임계 주입
        cfg_path = Path(paths["config"])
        cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg.setdefault("consistency_check", {})["coherence_p1"] = pv["coherence_p1"]
        cfg["consistency_check"]["point_median_coherence"] = pv["point_median_coherence"]
        cfg["consistency_check"]["arc_unwrapping_coherence_threshold"] = pv["arc_unwrapping_coherence"]
        cfg.setdefault("filtering", {})["coherence_p2"] = pv["coherence_p2"]
        cfg["_preset"] = preset_name
        cfg_path.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        st.success(f"생성됨 (프리셋 **{preset_name}**) → `{paths['manifest']}` · `{paths['config']}`")

        # WSL 의 실제 SARvey config.json 에 바로 적용할 명령(native 키)
        sed = ("sed -i '"
               f"s/coherence_p1: [0-9.]*/coherence_p1: {pv['coherence_p1']}/; "
               f"s/coherence_p2: [0-9.]*/coherence_p2: {pv['coherence_p2']}/; "
               f"s/point_median_coherence: [0-9.]*/point_median_coherence: {pv['point_median_coherence']}/; "
               f"s/arc_unwrapping_coherence: [0-9.]*/arc_unwrapping_coherence: {pv['arc_unwrapping_coherence']}/"
               "' config.json")
        st.markdown("**WSL 에서 실제 SARvey `config.json` 에 프리셋 적용** (`sarvey -f config.json -g` 후):")
        st.code(sed, language="bash")
        st.caption("점이 여전히 부족하면 '관대' 프리셋 + (MiaplPy) phase-linking/densification 강화, "
                   "그래도 없으면 asc+desc 결합 → 근본은 코너리플렉터/고해상도 SAR.")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**processing_manifest.json** (상류 스택)")
            st.json(_json.loads(Path(paths["manifest"]).read_text(encoding="utf-8")))
        with c2:
            st.markdown("**sarvey_config.json** (프리셋 반영)")
            st.json(cfg)


def insar_process_section(path: str) -> None:
    """F 처리 실행 — demo(합성 시계열 → /insar→PINN→FRAM) 또는 real plan 보기."""
    from inframon.insar import processing

    recipe_dir = st.text_input("레시피 폴더", _recipe_dir(), key="proc_recipe")
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

    # 교량 프로젝트(레시피 폴더) — 교량마다 다른 폴더를 쓰면 여러 교량을 따로 관리한다.
    # 아래 A~F 단계의 모든 레시피 경로가 이 폴더를 기준으로 자동 구성된다.
    st.text_input("🌉 교량 프로젝트 (레시피 폴더)", "data/insar_recipe", key="recipe_dir",
                  help="교량마다 다른 폴더명을 쓰세요. 예: data/insar_recipe/한강대교 · "
                       "data/insar_recipe/마포대교 — 여러 교량을 덮어쓰지 않고 따로 보관합니다.")

    with st.expander("🗺️ 교량 타깃 지정 (지도 + OSM 확인)  · A·B 단계", expanded=False):
        bridge_target_section()

    with st.expander("⚙️ SLC 선별 기준 (baseline · 편파 · 트랙)  · C·D 단계", expanded=False):
        selection_criteria_section()

    with st.expander("🛰️ SLC 검색 · 트랙 선별 (ASF)  · C·D 단계", expanded=False):
        slc_search_section()

    with st.expander("🌧️ ERA5 master 선정 (강수·습도)  · E 단계", expanded=False):
        era5_master_section()

    with st.expander("🌐 보조 데이터 준비 (궤도·DEM·AUX_CAL)  · F 준비", expanded=False):
        aux_data_section()

    with st.expander("🔀 Asc+Desc 연직변위 분해 (수직/종축)", expanded=False):
        asc_desc_section()

    with st.expander("🧩 SARvey 번들 생성 (레시피 → config)  · F 준비", expanded=False):
        sarvey_bundle_section()

    with st.expander("▶ InSAR 처리 실행 (F)  · demo=합성 end-to-end / real=WSL plan", expanded=False):
        insar_process_section(path)

    # 실데이터 인벤토리 점검
    with st.expander("🛰️ 실데이터 인벤토리 점검 (SLC/궤도/DEM)"):
        root = st.text_input("InSAR 데이터 루트", "", key="insar_root",
                             placeholder="예: D:/insar_data/<교량명>")
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

    # ── 🛰️ LOS 속도 점군 지도 (SARvey PS/DS 점, 클릭 → 해당 점 시계열) ──
    lon_c, lat_c = xyz[:, 0], xyz[:, 1]
    if float(np.abs(lon_c).max()) <= 180 and float(np.abs(lat_c).max()) <= 90:
        try:
            import folium
            from streamlit_folium import st_folium
        except ImportError:
            folium = None
        if folium is not None:
            st.markdown("**🛰️ LOS 속도 점군 지도** — SARvey PS/DS 점, **점 클릭 → 해당 점 변위 시계열**")
            tv = np.array([(t - times[0]).days / 365.25 for t in times])   # 연 단위
            vel = np.linalg.lstsq(np.vstack([tv, np.ones_like(tv)]).T, los.T, rcond=None)[0][0]
            vmax = float(np.percentile(np.abs(vel), 95)) or 1.0

            def _vcol(v):  # 🔴침하(음)·⚪0·🔵융기(양)
                x = float(np.clip(v / vmax, -1, 1))
                if x < 0:
                    return f"#ff{int(255*(1+x)):02x}{int(255*(1+x)):02x}"
                return f"#{int(255*(1-x)):02x}{int(255*(1-x)):02x}ff"

            mc1, mc2 = st.columns([3, 2])
            with mc1:
                vmap = folium.Map(location=[float(lat_c.mean()), float(lon_c.mean())],
                                  zoom_start=16, tiles="OpenStreetMap")
                for i in range(len(lon_c)):
                    col = _vcol(vel[i])
                    folium.CircleMarker([float(lat_c[i]), float(lon_c[i])], radius=3, weight=0,
                                        color=col, fill=True, fill_color=col, fill_opacity=0.85,
                                        tooltip=f"#{i} · {vel[i]:.1f} mm/yr · coh {coh[i]:.2f}").add_to(vmap)
                md = st_folium(vmap, height=430, key="insar_vel_map",
                               returned_objects=["last_object_clicked"])
            with mc2:
                click = (md or {}).get("last_object_clicked")
                if click:
                    sel = int(np.argmin((lat_c - click["lat"]) ** 2 + (lon_c - click["lng"]) ** 2))
                else:
                    sel = int(np.clip(st.session_state.get("insar_pt", 0), 0, N - 1))
                st.caption(f"선택 점 **#{sel}** · 속도 **{vel[sel]:.2f} mm/yr** · coh {float(coh[sel]):.2f}")
                st.line_chart(pd.DataFrame({"날짜": times, "LOS": los[sel],
                                            "종방향": lon[sel]}).set_index("날짜"))
            st.caption(f"색 = LOS 속도(mm/yr): 🔴침하(음)·⚪0·🔵융기(양), 범위 ±{vmax:.1f}. "
                       f"지도 점 클릭 → 오른쪽에 그 점의 변위 시계열. (미클릭 시 위 '측정점 index' 점)")

    with st.expander("🎯 InSAR 정확도 보정 (기준점 · 온도회귀 · 대기보정)", expanded=False):
        accuracy_section(path, times)


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
    b.metric("평균 α (열팽창계수)", f"{float(np.mean(alpha)):.2e} /°C")
    c.metric("고유진동수", ", ".join(f"{x:.1f}" for x in np.atleast_1d(nat)) + " Hz")

    # ── 🔬 구조 검증 (SAP2000/MIDAS 없이 해석해로 PINN/FEM 교차검증) ──
    with st.expander("🔬 구조 검증 (해석해 vs FEM · EI 복원)", expanded=False):
        st.caption("상용 SW 없이: 단순지지 보 **고유진동수 닫힌해**와 내부 FEM 비교 + EI 복원 검증. "
                   "(무료 대안: OpenSees·PyNite·anastruct 로도 동일 비교 가능)")
        vc1, vc2, vc3 = st.columns(3)
        EI_v = vc1.number_input("EI [N·m²]", 1e6, 1e14, float(np.mean(EI)) if EI is not None else 5e9,
                                format="%.2e", key="val_EI")
        rhoA_v = vc2.number_input("ρA [kg/m]", 1e2, 1e6, 1.0e4, format="%.1e", key="val_rhoA")
        L_v = vc3.number_input("스팬 L [m]", 5.0, 5000.0, 110.0, 1.0, key="val_L")
        if st.button("🔬 검증 실행", key="btn_validate"):
            from inframon.pinn.benchmark import ei_recovery_benchmark, run_fem_benchmark
            r = run_fem_benchmark(EI_v, rhoA_v, L_v, n_modes=3)
            er = ei_recovery_benchmark(EI_v, rhoA_v, L_v)
            st.dataframe(pd.DataFrame({
                "모드": [1, 2, 3], "해석해(Hz)": [round(x, 4) for x in r["analytic_hz"]],
                "FEM(Hz)": [round(x, 4) for x in r["fem_hz"]],
                "오차(%)": [round(x, 3) for x in r["err_pct"]]}), hide_index=True)
            ok_f = r["max_err_pct"] < 5.0
            ok_e = er["err_pct"] < 5.0
            st.success(f"{'✅' if ok_f else '⚠️'} FEM 모달 최대오차 {r['max_err_pct']:.2f}% "
                       f"· {'✅' if ok_e else '⚠️'} EI 복원오차 {er['err_pct']:.2f}% "
                       f"(EI {er['EI_recovered']:.2e} vs {er['EI_true']:.2e})")
            st.caption("두 오차 모두 <5% 면 PINN 구조 코어(모달·EI 식별)가 물리적으로 타당. "
                       "실교량 검증은 상시진동시험(AVT) 실측 진동수와 비교하세요.")

    # ── 🌉 교량 특화: 제원 + 외생(온도 열팽창 · 교통량 하중) → 맞춤형 PINN ──
    st.markdown("**🌉 교량 특화** — 제원(형식·재료·스팬) + 온도(열팽창 α·L·ΔT) + 교통량(하중 변조)")
    thermal_bridge_section(path, times)


def thermal_bridge_section(path: str, times) -> None:
    """교량 제원 + 온도·교통량(CSV/Excel) → cfg 로 PINN real 재실행."""
    from inframon.structure import BRIDGE_TYPES, MATERIAL_E, BridgeProfile
    labels = [t.strftime("%Y%m%d") for t in times]
    lat = lon = None
    tgt_path = f"{_recipe_dir()}/bridge_target.json"
    if Path(tgt_path).exists():
        try:
            from inframon.insar.recipe import load_bridge_target
            _t = load_bridge_target(tgt_path); lat, lon = _t.selected_lat, _t.selected_lon
        except Exception:  # noqa: BLE001
            pass
    if lat is None:
        xyz = read(path, "/insar/xyz")
        if float(np.abs(xyz[:, 0]).max()) <= 180:
            lon, lat = float(np.median(xyz[:, 0])), float(np.median(xyz[:, 1]))

    # ── 교량 제원 (폼 + CSV/Excel) ──
    prof_path = f"{_recipe_dir()}/bridge_profile.json"
    prof = BridgeProfile()
    if Path(prof_path).exists():
        try:
            prof = BridgeProfile.model_validate_json(Path(prof_path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    bnds = ["simply_supported", "continuous", "fixed"]
    with st.expander("🏗️ 교량 제원 입력 / CSV·Excel 불러오기", expanded=False):
        up = st.file_uploader("제원 파일(선택) — 열 `key,value` 또는 명명열", type=["csv", "xlsx", "xls"],
                              key="prof_up")
        if up is not None:
            try:
                df = (pd.read_excel(up) if up.name.lower().endswith(("xlsx", "xls"))
                      else pd.read_csv(up))
                if df.shape[1] >= 2 and str(df.columns[0]).lower() in ("key", "항목", "field", "name"):
                    kv = dict(zip(df.iloc[:, 0].astype(str), df.iloc[:, 1]))
                else:
                    kv = {str(c): df[c].iloc[0] for c in df.columns}

                def _g(*names):
                    for n in names:
                        if n in kv and pd.notna(kv[n]):
                            return kv[n]
                    return None
                upd = {"bridge_type": _g("bridge_type", "형식"), "material": _g("material", "재료"),
                       "length_m": _g("length_m", "경간", "스팬", "length", "연장"),
                       "section_depth_m": _g("section_depth_m", "단면높이", "형고"),
                       "load_per_len": _g("load_per_len", "하중")}
                prof = prof.model_copy(update={k: v for k, v in upd.items() if v is not None})
                st.success(f"제원 불러옴: {up.name}")
            except Exception as e:  # noqa: BLE001
                st.error(f"제원 파일 파싱 실패: {e}")
        c1, c2, c3 = st.columns(3)
        bt = c1.selectbox("형식", list(BRIDGE_TYPES),
                          index=list(BRIDGE_TYPES).index(prof.bridge_type) if prof.bridge_type in BRIDGE_TYPES else 0,
                          key="prof_bt")
        mats = list(MATERIAL_E)
        mat = c2.selectbox("재료", mats, index=mats.index(prof.material) if prof.material in mats else 0,
                           key="prof_mat")
        bnd = c3.selectbox("경계조건", bnds, index=bnds.index(prof.boundary) if prof.boundary in bnds else 0,
                           key="prof_bnd")
        c4, c5, c6 = st.columns(3)
        L = c4.number_input("스팬 length_m (0=자동)", 0.0, 5000.0, float(prof.length_m or 0.0), 1.0, key="prof_L")
        dep = c5.number_input("단면높이 m", 0.1, 50.0, float(prof.section_depth_m), 0.1, key="prof_dep")
        q = c6.number_input("분포하중 N/m", 0.0, 1e6, float(prof.load_per_len), 1e3, key="prof_q")
        prof = prof.model_copy(update={"bridge_type": bt, "material": mat, "boundary": bnd,
                                       "length_m": (L or None), "section_depth_m": dep,
                                       "load_per_len": q, "source": "manual"})
        st.caption(f"E={prof.youngs():.2e} Pa · ρA={prof.rho_a():.2e} kg/m")
        if st.button("💾 제원 저장", key="prof_save"):
            Path(prof_path).parent.mkdir(parents=True, exist_ok=True)
            Path(prof_path).write_text(prof.model_dump_json(indent=2), encoding="utf-8")
            st.success(f"저장 → `{prof_path}`")

    # ── 외생: 온도(자동) + 교통량(CSV/Excel) ──
    use_temp = st.checkbox("🌡️ 온도 열팽창 반영 (Open-Meteo ERA5)", value=True, key="use_temp",
                          disabled=lat is None)
    tf = st.file_uploader("🚗 교통량 CSV/Excel (열: date, traffic) — data.go.kr 등", type=["csv", "xlsx", "xls"],
                          key="traffic_up")
    traffic_arr = None
    if tf is not None:
        try:
            tdf = (pd.read_excel(tf) if tf.name.lower().endswith(("xlsx", "xls")) else pd.read_csv(tf))
            dcol = next((c for c in tdf.columns if str(c).lower() in ("date", "날짜", "일자", "ymd")),
                        tdf.columns[0])
            vcol = next((c for c in tdf.columns
                         if str(c).lower() in ("traffic", "교통량", "count", "volume", "aadt")),
                        tdf.columns[-1])
            tmap = {}
            for _, row in tdf.iterrows():
                d = "".join(ch for ch in str(row[dcol]) if ch.isdigit())[:8]
                try:
                    tmap[d] = float(row[vcol])
                except Exception:  # noqa: BLE001
                    pass
            keys = sorted(k for k in tmap if len(k) == 8)
            if keys:
                ki = np.array([int(k) for k in keys]); kv = np.array([tmap[k] for k in keys])
                traffic_arr = [float(kv[int(np.argmin(np.abs(ki - int(x))))]) for x in labels]
                st.success(f"교통량 불러옴: {tf.name} ({len(keys)}행) → {len(labels)}시점 정렬")
                st.line_chart(pd.DataFrame({"교통량": traffic_arr}, index=pd.Index(times, name="날짜")))
        except Exception as e:  # noqa: BLE001
            st.error(f"교통량 파싱 실패: {e}")

    if lat is not None:
        st.caption(f"온도 위치 {lat:.4f}, {lon:.4f} · {len(times)}시점")
    if st.button("🌉 교량특화 PINN 재실행 (제원+온도+교통량)", key="btn_custom_pinn", type="primary"):
        from inframon.config import PipelineConfig
        from inframon.contracts.io import ProjectStore
        from inframon.contracts.schema import InSAROutput
        from inframon.orchestrator.engines import resolve
        cfg = PipelineConfig(); cfg.pinn_epochs = 250
        cfg.bridge_profile = prof.model_dump()
        used = [f"제원({prof.bridge_type}/{prof.material})"]
        if use_temp and lat is not None:
            from inframon.insar.era5_master import fetch_temperature
            with st.spinner("Open-Meteo 온도 수집…"):
                try:
                    temps = fetch_temperature(lat, lon, labels)
                    cfg.pinn_temperature = list(map(float, temps)); used.append("온도")
                    st.line_chart(pd.DataFrame({"기온(°C)": temps}, index=pd.Index(times, name="날짜")))
                except Exception as e:  # noqa: BLE001
                    st.warning(f"온도 수집 실패(생략): {e}")
        if traffic_arr is not None:
            cfg.pinn_traffic = traffic_arr; used.append("교통량")
        with st.spinner("교량특화 PINN real + FRAM 재실행…"):
            with ProjectStore(path, mode="a") as s:
                insar = s.read_meta("insar", InSAROutput)
                pinn = resolve("pinn", "real")(s, insar, cfg)
                fram = resolve("fram", "real")(s, insar, pinn, cfg)
                al = float(np.mean(s.read_array(pinn.alpha_ds)))
                ei = float(np.mean(s.read_array(pinn.EI_ds)))
                nf = np.atleast_1d(s.read_array(pinn.natural_freq_ds))
        st.success(f"완료 · {' + '.join(used)} → α {al:.2e}/°C · EI {ei:.2e} · "
                   f"f₁ {nf[0]:.2f}Hz · CRI {fram.cri_global_max:.3f}")
        st.rerun()


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
    lead_fwd = warning.get("lead_time_forecast_days")
    basis = warning.get("basis", "cri")
    fstates = warning.get("function_states", {})
    basis_ko = "보정 붕괴확률" if basis == "calibrated_probability" else "원시 CRI"
    getattr(st, banner_fn)(f"{emoji} 경보 등급: **{level}**  ·  근거: {basis_ko}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("최대 CRI", f"{cri.max():.3f}")
    # 전방 예측(위험 도달까지) 우선, 없으면 후방 경과
    if lead_fwd is not None:
        c2.metric("위험 도달 예측", f"{lead_fwd:.0f} 일 후")
    else:
        c2.metric("리드타임(경과)", "—" if lead is None else f"{lead:.0f} 일")
    c3.metric("위험 부재 수", len(members))
    if data["calibrated_max"] is not None:
        c4.metric("최대 붕괴확률(보정)", f"{data['calibrated_max'] * 100:.1f}%")
    else:
        c4.metric("측정점 × 시점", f"{N} × {M}")
    if members:
        st.caption("위험 부재: " + ", ".join(members))
    # ① 기능별 상태 (열팽창/하중/받침/침하)
    if fstates:
        _emoji = {"위험": "🔴", "주의": "🟠", "정상": "🟢"}
        st.caption("기능 상태 — " + " · ".join(
            f"{_emoji.get(s, '⚪')} {f}: {s}" for f, s in fstates.items()))

    # 📄 교량별 리포트(PDF)
    rc1, rc2 = st.columns([1, 3])
    if rc1.button("📄 리포트(PDF) 생성", key="btn_report"):
        from inframon.dashboard.report import build_report
        bname = None
        tp = f"{_recipe_dir()}/bridge_target.json"
        if Path(tp).exists():
            try:
                from inframon.insar.recipe import load_bridge_target
                bname = load_bridge_target(tp).name
            except Exception:  # noqa: BLE001
                pass
        try:
            out = build_report(path, "data/report.pdf", bridge_name=bname)
            st.session_state["report_pdf"] = str(out)
            st.success("리포트 생성됨")
        except Exception as e:  # noqa: BLE001
            st.error(f"리포트 실패: {e}")
    if st.session_state.get("report_pdf") and Path(st.session_state["report_pdf"]).exists():
        with open(st.session_state["report_pdf"], "rb") as fh:
            rc2.download_button("⬇️ PDF 다운로드", fh.read(), file_name="inframon_bridge_report.pdf",
                                mime="application/pdf", key="dl_report")

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

    # ── 🗺️ 위험 지점 지도 (실 경위도일 때만) ──
    lon, lat = xyz[:, 0], xyz[:, 1]
    if float(np.abs(lon).max()) <= 180 and float(np.abs(lat).max()) <= 90:
        try:
            import folium
            from streamlit_folium import st_folium
        except ImportError:
            folium = None
        if folium is not None:
            # 붕괴확률(isotonic 보정)이 있으면 그걸로, 없으면 CRI 로 색칠
            use_prob = cal is not None
            val = np.asarray(cal) if use_prob else cri    # [N,M]
            metric, lo, mid, hi = (("붕괴확률 P", 0.3, 0.5, 0.8) if use_prob
                                   else ("CRI", 0.3, 0.6, 0.85))
            from folium.plugins import HeatMap

            val_k = val[:, k]                        # 선택 시점(슬라이더 연동)
            n_hi = int((val_k >= hi).sum()); n_mid = int(((val_k >= mid) & (val_k < hi)).sum())
            st.markdown(f"**🗺️ 위험 히트맵** — {metric} @ {times[k]:%Y-%m-%d} (슬라이더로 시간 이동) · "
                        f"🔴위험 {n_hi} · 🟠경고 {n_mid} / 전체 {len(lon)}점")
            fmap = folium.Map(location=[float(lat.mean()), float(lon.mean())],
                              zoom_start=16, tiles="OpenStreetMap")
            # 선택 시점 값을 가중치로 한 열지도(전 점 사용, 마커 없이 가벼움)
            heat = [[float(lat[i]), float(lon[i]), float(np.clip(val_k[i], 0, 1))]
                    for i in range(len(lon)) if val_k[i] > 0.05]
            HeatMap(heat, radius=14, blur=10, min_opacity=0.3, max_zoom=18,
                    gradient={0.3: "blue", 0.5: "lime", 0.7: "orange", 0.9: "red"}
                    ).add_to(fmap)
            st_folium(fmap, height=460, key="fram_danger_map")
            cap = (f"열강도 = {metric}(선택 시점). 파랑(낮음)→초록→주황→빨강(높음). 전 {len(lon)}점 가중. "
                   f"위험부재 {warning.get('critical_members', [])}. ")
            cap += ("붕괴확률 = isotonic 캘리브(합성 Morandi 라벨) — 슬라이더로 위험 발달을 보세요."
                    if use_prob else "isotonic 캘리브 적용 시 붕괴확률로 표시됩니다.")
            st.caption(cap)

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

    # ── FRAM 기능 공명 다이어그램 (4기능 변동 레이더 + 기능 간 결합 R_ij) ──
    diag = fram_function_diagram(path, k)
    if diag is not None:
        st.markdown(f"**FRAM 기능 공명 다이어그램** (시점 {times[k]:%Y-%m-%d}) — "
                    "4기능 변동 + 기능 간 결합")
        d1, d2 = st.columns(2)
        with d1:
            names, vals = diag["func_names"], diag["variability"]
            try:
                import plotly.graph_objects as go
                fig = go.Figure(go.Scatterpolar(
                    r=list(vals) + [vals[0]], theta=names + [names[0]], fill="toself",
                    line_color="#d62728"))
                fig.update_layout(polar={"radialaxis": {"visible": True, "range": [0, 1]}},
                                  showlegend=False, height=320,
                                  margin={"l": 40, "r": 40, "t": 20, "b": 20})
                st.plotly_chart(fig, use_container_width=True)
            except ImportError:  # plotly 없으면 막대그래프 폴백
                st.bar_chart(pd.DataFrame({"변동 V": vals}, index=pd.Index(names, name="기능")))
        with d2:
            st.caption("기능 간 공명 R_ij (1에 가까울수록 동조 → 공명 위험)")
            st.dataframe(pd.DataFrame(diag["coupling"], index=names, columns=names).round(2),
                         use_container_width=True)


# ──────────────────────────── 상태 헤더 ────────────────────────────
def status_header(path: str) -> None:
    """상단 '한눈에 보기' — 현재 프로젝트의 경보 등급·최대 CRI·규모를 카드로.

    FRAM 결과가 있으면 경보 배너 + 지표, 없으면 안내. 어떤 경우에도 페이지를
    중단시키지 않도록 방어적으로 처리한다(데모 전/손상 파일 등).
    """
    if not Path(path).exists():
        st.info("📂 아직 분석 결과가 없습니다 — 왼쪽 사이드바의 **'데모 데이터 생성'** 으로 시작하세요.")
        return
    try:
        d = fram_panel_data(path)
    except Exception:  # noqa: BLE001 — 헤더는 부가정보, 실패해도 본문 탭은 살아야 함
        st.caption(f"📄 {path}  ·  요약을 읽을 수 없습니다(분석 진행 전일 수 있음).")
        return

    warning = d.get("warning") or {}
    level = warning.get("level", "—")
    banner, emoji = LEVEL_STYLE.get(level, ("info", "⚪"))
    cri = d.get("cri")
    cri_max = d.get("cri_max")
    n_points = "—" if cri is None else f"{cri.shape[0]:,}"
    n_dates = "—" if cri is None else f"{cri.shape[1]:,}"

    getattr(st, banner)(f"{emoji}  현재 경보 등급 : **{level}**"
                        + (f"   ·   {', '.join(warning['critical_members'])}"
                           if warning.get("critical_members") else ""))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("최대 공명위험 CRI", "—" if cri_max is None else f"{cri_max:.3f}")
    c2.metric("측정점 수 N", n_points)
    c3.metric("취득 시점 수 M", n_dates)
    c4.metric("프로젝트", Path(path).name)


# ───────────────────────────────── main ───────────────────────────────
def main() -> None:
    st.set_page_config(page_title="inframon — 인프라 모니터링", page_icon="🌉", layout="wide")
    st.title("🌉 inframon — 통합 인프라 모니터링")
    st.caption("InSAR(변위) → PINN(구조해석) → FRAM(공명 위험 CRI)  ·  위성 SAR 기반 교량 안전 모니터링")

    with st.sidebar.expander("🏙️ 교량 포트폴리오", expanded=False):
        picked = portfolio_section()
    path = st.sidebar.text_input("project.h5 경로", picked or default_project_path())
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

    status_header(path)
    st.divider()

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
