"""내장 대시보드 (문서 5.8 / 7.2) — 단계별 탭 구조 (InSAR → PINN → FRAM).

실행:  pip install -e .[dashboard]
       streamlit run src/inframon/dashboard/app.py

데이터 흐름의 시작점인 InSAR(실측 변위)를 첫 탭·관문으로 두고,
PINN(물리 해석) → FRAM(공명 위험 CRI) 순으로 파이프라인을 따라간다.
FRAM 탭은 기능 공명 다이어그램(4기능 변동 레이더 + R_ij 결합)을 포함한다.
"""

from __future__ import annotations

import json
import os
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
_CONFIG_FILE = Path.home() / ".inframon" / "config.json"


def _config_load() -> dict:
    try:
        return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _config_save(**kv) -> None:
    """설정을 ~/.inframon/config.json 에 병합 저장(재시작 후에도 유지)."""
    cfg = _config_load(); cfg.update({k: v for k, v in kv.items() if v is not None})
    try:
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def data_root() -> str:
    """모든 저장(project.h5·레시피·SLC·결과)의 루트 폴더.

    우선순위: 세션 설정 → 저장된 config → 환경변수 INFRAMON_DATA_ROOT →
    (frozen exe: exe 옆 data/) → 작업폴더 data/. 사용자가 F:\\inframon 등으로 지정 가능.
    """
    for v in (st.session_state.get("data_root"),
              _config_load().get("data_root"),
              os.environ.get("INFRAMON_DATA_ROOT")):
        if v and str(v).strip():
            return str(v).strip()
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).parent / "data")
    return "data"


def default_project_path() -> str:
    """기본 project.h5 경로 = <데이터 루트>/project.h5.

    루트가 없으면 만들고, frozen(.exe) 최초 실행이면 번들 데모를 시드해 바로 보이게 한다.
    """
    root = Path(data_root())
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return DEFAULT_H5
    target = root / "project.h5"
    if not target.exists() and getattr(sys, "frozen", False):
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


def _combined_bridge_search(query: str, csv_path: str | None,
                            limit: int = 12) -> tuple[list[dict], str | None]:
    """교량명 검색을 **CSV(전국교량표준데이터) + OSM(Nominatim)** 양쪽에서 → (후보, OSM오류).

    각 후보에 source('CSV'|'OSM')·좌표·데크 지오메트리. 지도 마커·타깃 저장에 공용.
    """
    from inframon.public_data import search_bridges_by_name
    hits: list[dict] = []
    seen = set()
    if csv_path:
        for h in search_bridges_by_name(csv_path, query, limit=limit):
            seen.add((round(h["lat"], 4), round(h["lon"], 4)))
            hits.append({**h, "source": "CSV"})
    osm_err = None
    try:                                    # OSM 이름검색(네트워크) — 실패해도 CSV 결과 유지
        from inframon.insar.osm_bridge import find_bridges_by_name
        for b in find_bridges_by_name(query, limit=limit):
            g = [[p[0], p[1]] for p in b.geometry]
            lat, lon = g[0][0], g[0][1]
            if (round(lat, 4), round(lon, 4)) in seen:      # CSV 와 근접 중복 제외
                continue
            hits.append({"name": b.name, "lat": lat, "lon": lon, "geometry": g,
                         "structure": b.tags.get("bridge:structure") or b.tags.get("osm_feature"),
                         "length_m": b.length_m or None, "width_m": None, "grade": None,
                         "osm_id": b.osm_id, "osm_type": b.osm_type, "source": "OSM",
                         "bridge_confirmed": b.tags.get("bridge_confirmed")})
    except Exception as exc:  # noqa: BLE001
        osm_err = f"{type(exc).__name__}: {exc}"
    return hits, osm_err


def _save_target_from_csv(hit: dict) -> str:
    """교량명 검색 결과(hit) → bridge_target.json(레시피)로 저장. 시종점 좌표로 데크 지오메트리."""
    geom = hit.get("geometry") or [[hit["lat"], hit["lon"]]]
    lats = [p[0] for p in geom]; lons = [p[1] for p in geom]
    mlat, Mlat, mlon, Mlon = min(lats), max(lats), min(lons), max(lons)
    mrg = 0.0018    # ~200m AOI 여유
    tgt = {
        "name": hit.get("name"), "name_ko": hit.get("name"),
        "selected_lat": hit["lat"], "selected_lon": hit["lon"],
        "osm_type": str(hit.get("osm_type") or "data_go_kr"),
        "osm_id": int(hit.get("osm_id") or 0),          # CSV 는 osm_id 없음 → 0(정수 필수)
        "bbox": [mlon - mrg, mlat - mrg, Mlon + mrg, Mlat + mrg],
        "aoi_buffer_m": 200.0,
        "bridge_bbox": [mlon, mlat, Mlon, Mlat],
        "length_m": hit.get("length_m"), "distance_m": None,
        # tags 값은 문자열만(None 제외) — BridgeTarget 검증
        "tags": {k: str(v) for k, v in (("structure", hit.get("structure")),
                                        ("grade", hit.get("grade")),
                                        ("source", hit.get("source"))) if v is not None},
        "geometry": geom, "confirmed": True,
        "source": "전국교량표준데이터(교량명 검색)",
    }
    out = Path(_recipe_dir()); out.mkdir(parents=True, exist_ok=True)
    p = out / "bridge_target.json"
    p.write_text(json.dumps(tgt, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def _recipe_dir() -> str:
    """현재 교량 프로젝트의 레시피 폴더 = <데이터 루트>/insar_recipe (상단에서 설정).

    교량마다 분리해 여러 개를 관리하려면 recipe_dir 세션값으로 개별 지정 가능.
    """
    ss = (st.session_state.get("recipe_dir") or "").strip()
    return ss or str(Path(data_root()) / "insar_recipe")


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

    # 🔎 사이드바 교량명 검색 결과(CSV+OSM) — 지도에 마커로 표기
    hits = st.session_state.get("search_hits", [])
    sel_i = st.session_state.get("search_sel", 0)

    click = st.session_state.get("bridge_click")
    if hits:                                        # 검색결과 있으면 선택 후보로 지도 중심
        _h = hits[sel_i] if sel_i < len(hits) else hits[0]
        center, zoom = [_h["lat"], _h["lon"]], 15
    elif click:
        center, zoom = [click["lat"], click["lng"]], 16
    elif existing:
        center, zoom = [existing.selected_lat, existing.selected_lon], 16
    else:
        center, zoom = [36.5, 127.8], 7  # 한국 전역

    m = folium.Map(location=center, zoom_start=zoom, tiles="OpenStreetMap")
    for j, h in enumerate(hits):                    # CSV=파랑, OSM=초록, 선택=별
        col = "blue" if h["source"] == "CSV" else "green"
        ic = "star" if j == sel_i else "info-sign"
        folium.Marker(
            [h["lat"], h["lon"]], icon=folium.Icon(color=col, icon=ic),
            tooltip=f"[{h['source']}] {h['name']}",
            popup=folium.Popup(f"<b>{h['name']}</b><br>{h['source']} · "
                               f"{h.get('structure') or '-'}<br>"
                               f"{h['lat']:.5f}, {h['lon']:.5f}", max_width=250)).add_to(m)
        if len(h.get("geometry") or []) >= 2:       # 데크 폴리라인
            folium.PolyLine([[p[0], p[1]] for p in h["geometry"]], color=col, weight=4).add_to(m)
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

    if hits:
        st.caption("🔵 CSV · 🟢 OSM 마커를 **클릭하면 그 교량으로 바로 설정**됩니다.")
    state = st_folium(m, height=420, key="bridge_map")

    # 검색결과 마커 클릭 → 해당 교량 직접 선택(타깃 저장)
    obj = state.get("last_object_clicked") if state else None
    if obj and hits:
        okey = (round(obj["lat"], 6), round(obj["lng"], 6))
        if st.session_state.get("_last_obj_click") != okey:
            st.session_state["_last_obj_click"] = okey
            best = min(hits, key=lambda h: (h["lat"] - obj["lat"]) ** 2
                       + (h["lon"] - obj["lng"]) ** 2)
            d = ((best["lat"] - obj["lat"]) ** 2 + (best["lon"] - obj["lng"]) ** 2) ** 0.5
            if d < 0.002:                       # ~200m 이내 마커와 매칭
                _save_target_from_csv(best)     # recipe_dir 위젯 기설정 → 별도 세팅 불필요
                st.success(f"지도에서 선택 → {best['name']} ({best['source']})")
                st.rerun()
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

    both_dir = st.checkbox("🔀 상승(ASC)+하강(DESC) 둘 다 선택 (각 1트랙 · 연직분해용)",
                           value=False, key="both_dir",
                           help="켜면 상승 최다 트랙과 하강 최다 트랙을 각각 골라 둘 다 저장 "
                                "(track_selection_asc/desc.json) — Asc+Desc 연직변위 분해에 사용.")
    st.caption(f"대상: **{target.name}** · bbox={tuple(round(v, 4) for v in target.bbox)} · "
               f"편파 {pol} · 궤도 " + ("ASC+DESC 둘 다" if both_dir else (orbit or '자동(최다)')))
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
                st.session_state["slc_scenes"] = scenes
                st.session_state["slc_ntotal"] = len(scenes)
            except Exception as exc:  # noqa: BLE001
                st.session_state.pop("slc_scenes", None)
                st.error(f"검색 실패: {exc}")

    scenes = st.session_state.get("slc_scenes")
    if scenes is None:
        return
    n_total = st.session_state.get("slc_ntotal", len(scenes))

    # 양방향: ASC 최다 + DESC 최다 각각 / 단방향: 기존 최다(또는 criteria 방향)
    if both_dir:
        a_best, a_chosen, groups = select_track(scenes, orbit_direction="ASCENDING")
        d_best, d_chosen, _ = select_track(scenes, orbit_direction="DESCENDING")
        _, _, groups = select_track(scenes)          # 표시는 전체 그룹
        picks = [(a_best, a_chosen, "ASC"), (d_best, d_chosen, "DESC")]
        pick_keys = {p[0].key for p in picks if p[0]}
    else:
        best, chosen, groups = select_track(scenes, orbit_direction=orbit)
        picks = [(best, chosen, best.flight_direction[:4] if best else "")]
        pick_keys = {best.key} if best else set()

    st.write(f"총 **{n_total}** 장면({pol}) · 트랙 후보 **{len(groups)}** 개")
    if not any(p[0] for p in picks):
        st.warning("조건에 맞는 트랙이 없습니다"
                   + (" (상승·하강 중 하나가 없을 수 있음)" if both_dir else "") + ".")
        return

    rows = [{"선택": "✅" if g.key in pick_keys else "", "방향": g.flight_direction,
             "path": g.path, "frame": g.frame, "장면수": g.n_scenes,
             "시작": g.first_date, "끝": g.last_date} for g in groups]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    for grp, chs, tag in picks:
        if grp:
            st.success(f"[{tag}] **{grp.flight_direction} path {grp.path}/frame {grp.frame}** "
                       f"— {grp.n_scenes}장 ({grp.first_date}~{grp.last_date})")
        else:
            st.warning(f"[{tag}] 해당 방향 트랙 없음")

    # 데이터 가용성 자동 판정 (asc/desc 장면·시간겹침 → 처리 모드 추천)
    from inframon.insar.availability import assess_availability
    adv = assess_availability(groups)
    _mode = {"asc+desc": "🟢 상승+하강 **연직분해 가능**", "union": "🟡 **UNION만**(점↑, 연직분해 불가)",
             "single": "🟠 **단일 LOS**", "accumulate": "🔴 **데이터 부족**(누적 대기)",
             "none": "⚫ 데이터 없음"}
    a, d = adv["ascending"], adv["descending"]
    st.info(f"**데이터 가용성** → {_mode.get(adv['mode'], adv['mode'])}\n\n"
            f"- 상승: {a['n_scenes'] if a else 0}장 · 하강: {d['n_scenes'] if d else 0}장 · "
            f"시간겹침 {adv['overlap_days']}일\n- {adv['reason']}")

    if st.button("💾 트랙 선별 저장 (레시피)", key="btn_save_track"):
        saved = []
        if both_dir:
            for grp, chs, tag in picks:
                if not grp:
                    continue
                sel = TrackSelection.from_selection(grp, chs, polarization=pol)
                out = save_track_selection(
                    f"{_recipe_dir()}/track_selection_{tag.lower()}.json", sel)
                saved.append((tag, out, sel.n_scenes))
            # 기본 track_selection.json = 장면 많은 쪽(하위호환)
            primary = max((p for p in picks if p[0]), key=lambda p: p[0].n_scenes)
            save_track_selection(f"{_recipe_dir()}/track_selection.json",
                                 TrackSelection.from_selection(primary[0], primary[1], polarization=pol))
            st.success("저장됨 → " + " · ".join(f"track_selection_{t.lower()}.json({n}장)"
                                                for t, _, n in saved)
                       + f"  · 기본 track_selection.json = {primary[2]}({primary[0].n_scenes}장)")
        else:
            grp, chs, _ = picks[0]
            sel = TrackSelection.from_selection(grp, chs, polarization=pol)
            out = save_track_selection(f"{_recipe_dir()}/track_selection.json", sel)
            saved.append(("", out, sel.n_scenes))
            st.success(f"저장됨 → `{out}` (SARvey 처리 대상 {sel.n_scenes}장)")

    # 다운로드 대상 장면: 선택 트랙(들)의 장면 — 양방향이면 asc+desc 합집합
    chosen = []
    _seen = set()
    for grp, chs, _tag in picks:
        if not grp:
            continue
        for s in chs:
            key = (s.date, grp.flight_direction)
            if key not in _seen:
                _seen.add(key); chosen.append(s)

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


def support_zone_section(path: str, times) -> None:
    """지지부(교각·교대) ZONE — 매끈한 데크 대신 지지부 근처 점으로 침하·변위 감시."""
    from inframon.insar.support_zone import support_velocity, support_zone
    los, xyz = read(path, "/insar/los", "/insar/xyz")
    st.caption("매끈한 데크는 InSAR 점이 없으므로, **교각·교대(거친 콘크리트)** 근처 점으로 침하·변위를 감시하고 "
               "데크는 PINN 으로 추론한다. 교량 선형(OSM)이 필요.")
    tp = f"{_recipe_dir()}/bridge_target.json"
    nodes = None
    if Path(tp).exists():
        try:
            from inframon.insar.recipe import load_bridge_target
            g = load_bridge_target(tp).geometry
            if g and len(g) >= 2:
                nodes = [(la, lo) for la, lo in g]
        except Exception:  # noqa: BLE001
            pass
    if nodes is None:
        st.info("🗺️ 교량 타깃(선형 geometry)을 먼저 저장하세요 — 지지부 위치 산정에 필요.")
        return
    c1, c2 = st.columns(2)
    n_piers = c1.number_input("교각 수(추정)", 0, 20, 3, 1, key="sz_piers")
    buf = c2.number_input("지지부 buffer (m)", 5, 100, 30, 5, key="sz_buf")
    r = support_zone(xyz[:, :2], nodes, n_piers=int(n_piers), buffer_m=float(buf))
    days = np.array([(t - times[0]).days for t in times], dtype=float)
    v = support_velocity(los, days, r["mask"])
    m1, m2, m3 = st.columns(3)
    m1.metric("지지부 점 수", r["n_support_points"])
    m2.metric("평균 LOS 속도", "—" if v["n"] == 0 else f"{v['mean_mm_yr']:+.2f} mm/yr")
    m3.metric("속도 범위", "—" if v["n"] == 0 else f"{v['min_mm_yr']:+.1f}~{v['max_mm_yr']:+.1f}")
    st.dataframe(pd.DataFrame([{"지지부": f"{s['kind']}", "lat": round(s['lat'], 5),
                                "lon": round(s['lon'], 5), f"{int(buf)}m내 점": s['n'],
                                "최근접(m)": round(s['nearest_m'], 1)} for s in r["supports"]]),
                 hide_index=True, use_container_width=True)
    lon_c, lat_c = xyz[:, 0], xyz[:, 1]
    if r["n_support_points"] > 0 and float(np.abs(lon_c).max()) <= 180:
        try:
            import folium
            from streamlit_folium import st_folium
            mp = folium.Map(location=[float(np.mean([p[0] for p in r["positions"]])),
                                      float(np.mean([p[1] for p in r["positions"]]))],
                            zoom_start=17, tiles="OpenStreetMap")
            for plat, plon, kind in r["positions"]:      # 지지부 위치 마커
                folium.Marker([plat, plon], tooltip=kind,
                              icon=folium.Icon(color="black", icon="tower", prefix="fa")).add_to(mp)
            idx = np.where(r["mask"])[0]
            tv = np.array([(t - times[0]).days / 365.25 for t in times])
            vel = np.linalg.lstsq(np.vstack([tv, np.ones_like(tv)]).T, los[idx].T, rcond=None)[0][0]
            vmax = float(np.percentile(np.abs(vel), 95)) or 1.0
            for j, i in enumerate(idx):
                x = float(np.clip(vel[j] / vmax, -1, 1))
                col = (f"#ff{int(255*(1+x)):02x}{int(255*(1+x)):02x}" if x < 0
                       else f"#{int(255*(1-x)):02x}{int(255*(1-x)):02x}ff")
                folium.CircleMarker([float(lat_c[i]), float(lon_c[i])], radius=5, color=col,
                                    fill=True, fill_color=col, fill_opacity=.9, weight=1,
                                    tooltip=f"{vel[j]:+.2f} mm/yr").add_to(mp)
            st.markdown("**지지부 ZONE 점** (🗼=교대/교각 위치, 원=근처 점 속도)")
            st_folium(mp, height=420, key="sz_map")
        except ImportError:
            pass
    st.caption("데크 직접 점은 CR/고해상도 SAR 영역. 지지부 점으로 침하·부등변위 추세를 보고, "
               "데크 거동은 PINN 경계추론.")


def accuracy_section(path: str, times) -> None:
    """InSAR 정확도 보정 — 기준점 정합 + 온도회귀(열팽창 분리) → 순 변형속도."""
    from inframon.insar.atmo import (
        height_correlated_correction, reference_correction, select_reference_point,
        temporal_decompose,
    )
    los, xyz, coh = read(path, "/insar/los", "/insar/xyz", "/insar/coherence")
    # 시간 결맞음(temporal coherence) 우선 — 없으면 공간 coherence 폴백
    ref_coh = coh
    try:
        _tc = read(path, "/insar/temporal_coherence")
        if _tc is not None and np.size(_tc) == los.shape[0]:
            ref_coh = _tc
    except (KeyError, ValueError, OSError):
        pass
    N, M = los.shape
    days = np.array([(t - times[0]).days for t in times], dtype=float)
    st.caption("기준점 대비 상대변위 + 온도회귀로 열팽창 분리 → **순 변형속도(mm/yr)**. "
               "InSAR 절대·계절 편향을 줄입니다.")
    # 기준점: 시간 결맞음 ≥ 0.98 인 초안정 PS (도심 고밀도에서 확보)
    min_coh = st.slider("기준점 최소 시간결맞음", 0.80, 0.999, 0.98, 0.01, key="ref_min_coh",
                        help="reference point 는 이 값 이상의 초안정 PS 여야 상대변위가 신뢰됩니다.")
    rp = select_reference_point(los, ref_coh, min_coh=float(min_coh))
    auto = rp["index"]
    if rp["meets_threshold"]:
        st.success(f"기준점 #{auto} · 결맞음 {rp['coherence']:.3f} ≥ {min_coh:.2f} "
                   f"(초안정 후보 {rp['n_candidates']}개)")
    else:
        st.warning(f"⚠️ 결맞음 ≥ {min_coh:.2f} 기준점 없음 — 최고 {rp['coherence']:.3f} 사용. "
                   "**ROI 를 도심(고밀도) 쪽으로 넓혀** 초안정 기준점을 확보하세요.")
    ref = st.number_input(f"기준점 index (0.98 자동추천 #{auto})", 0, N - 1, int(auto), key="ref_idx")
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
    default_name = Path(default_project_path()).name          # 기본 = project.h5
    idx = names.index(default_name) if default_name in names else 0
    pick = st.selectbox("교량(프로젝트) 선택", names, index=idx, key="portfolio_pick")
    st.caption("선택하면 아래 경로가 그 교량으로 바뀝니다. (여러 교량은 data/ 에 project_*.h5 로 저장)")
    return next(r["_path"] for r in rows if r["파일"] == pick)


def asc_desc_section() -> None:
    """상승+하강 궤도 결합 — ①UNION(점 증가) ②연직/EW 분해(fuse_asc_desc)."""
    import math

    from inframon.insar.fusion import FusionError, fuse_asc_desc
    from inframon.insar.track_reader import read_track_h5
    st.caption("**상승(ascending) + 하강(descending)** 두 궤도를 결합: "
               "**① UNION** = 두 궤도 점 합쳐 감시점 증가 · **② 연직분해** = 정합쌍을 연직 U·종축 H 로. "
               "(연직분해는 두 Track H5 에 입사각·heading 필요)")
    c1, c2 = st.columns(2)
    asc_p = c1.text_input("상승궤도 Track H5", "data/sarvey_permissive.h5", key="ad_asc")
    desc_p = c2.text_input("하강궤도 Track H5", "data/sarvey_desc.h5", key="ad_desc")
    if not st.button("🔀 상승+하강 결합 (union + 연직분해)", key="btn_ad"):
        return
    if not (Path(asc_p).exists() and Path(desc_p).exists()):
        st.error("두 Track H5 경로가 모두 존재해야 합니다 (하강궤도도 다운→코레지→SARvey 로 준비).")
        return
    asc, desc = read_track_h5(asc_p), read_track_h5(desc_p)
    na, nd = asc.los.shape[0], desc.los.shape[0]

    # ① UNION — 점 증가
    st.markdown("**① UNION — 감시점 증가**")
    u1, u2, u3 = st.columns(3)
    u1.metric("상승 점", na); u2.metric("하강 점", nd)
    u3.metric("UNION 합계", na + nd, delta=f"+{nd} vs 상승단독")
    st.caption(f"두 궤도는 서로 다른 시선(LOS)이라 각각 독립 관측점 → 합치면 **{na}+{nd}={na+nd}점**으로 감시 밀도↑.")

    # ② 연직/EW 분해
    st.markdown("**② 연직/EW 분해 (fuse_asc_desc)**")
    for td in (asc, desc):                    # heading 라디안이면 도(°)로
        if td.heading is not None and abs(td.heading) < 7:
            td.heading = math.degrees(td.heading)
    try:
        res = fuse_asc_desc(asc, desc)
    except FusionError as e:
        st.warning(f"연직분해 불가: {e} — UNION(①)만 사용하세요.")
        return
    U = res.vertical
    lon, lat = res.track.lonlat[:, 0], res.track.lonlat[:, 1]
    vU = U[:, -1] - U[:, 0]
    f1, f2 = st.columns(2)
    f1.metric("정합쌍(연직 산출점)", U.shape[0])
    f2.metric("연직변위 범위(말−초)", f"{float(np.nanmin(vU)):+.1f}~{float(np.nanmax(vU)):+.1f} mm")
    st.caption(f"heading: 상승 {asc.heading:.1f}° · 하강 {desc.heading:.1f}° → 2×2 역산으로 연직 U·종축 H 분리.")
    if float(np.abs(lon).max()) <= 180:
        try:
            import folium
            from streamlit_folium import st_folium
            m = folium.Map(location=[float(np.nanmean(lat)), float(np.nanmean(lon))],
                           zoom_start=16, tiles="OpenStreetMap")
            vmax = float(np.nanpercentile(np.abs(vU), 95)) or 1.0
            for i in range(len(lon)):
                x = float(np.clip(vU[i] / vmax, -1, 1))
                col = (f"#ff{int(255*(1+x)):02x}{int(255*(1+x)):02x}" if x < 0
                       else f"#{int(255*(1-x)):02x}{int(255*(1-x)):02x}ff")
                folium.CircleMarker([float(lat[i]), float(lon[i])], radius=4, weight=0, color=col,
                                    fill=True, fill_color=col, fill_opacity=0.85,
                                    tooltip=f"연직 {vU[i]:+.1f} mm").add_to(m)
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
    st.text_input("🌉 교량 프로젝트 (레시피 폴더)", str(Path(data_root()) / "insar_recipe"),
                  key="recipe_dir",
                  help="저장 루트 아래 교량별 폴더. 예: <루트>/한강대교 · <루트>/마포대교 — "
                       "여러 교량을 덮어쓰지 않고 따로 보관합니다.")

    _has_target = (Path(_recipe_dir()) / "bridge_target.json").exists()
    with st.expander("🗺️ 교량 타깃 지정 (지도 + OSM 확인)  · A·B 단계",
                     expanded=not _has_target):     # 미지정이면 펼쳐서 먼저 안내
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
            # 인제스트 정확도 보정(--insar-corrections)이 저장한 /insar/velocity_mm_yr 를 우선 사용
            # (기준점 정합·고도상관 보정 반영). 없으면 원 LOS 에서 즉석 선형회귀로 폴백.
            stored_vel = read(path, "/insar/velocity_mm_yr")
            if stored_vel is not None and len(stored_vel) == len(lon_c):
                vel = np.asarray(stored_vel, dtype=float)
                vel_src = "인제스트 보정 속도(/insar/velocity_mm_yr)"
            else:
                tv = np.array([(t - times[0]).days / 365.25 for t in times])   # 연 단위
                vel = np.linalg.lstsq(np.vstack([tv, np.ones_like(tv)]).T, los.T, rcond=None)[0][0]
                vel_src = "즉석 선형회귀(원 LOS)"
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
                       f"속도 출처: **{vel_src}**. "
                       f"지도 점 클릭 → 오른쪽에 그 점의 변위 시계열. (미클릭 시 위 '측정점 index' 점)")

    with st.expander("🎯 InSAR 정확도 보정 (기준점 · 온도회귀 · 대기보정)", expanded=False):
        accuracy_section(path, times)

    with st.expander("🏗️ 지지부 ZONE 모니터링 (교각·교대 · 데크 대안)", expanded=False):
        support_zone_section(path, times)


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
        # 식별 EI 가 물리 클립 천장(1e14)에 닿으면 평균이 그 위로 나올 수 있어 max 여유(1e15)+클램프
        _ei_def = float(np.mean(EI)) if EI is not None else 5e9
        EI_v = vc1.number_input("EI [N·m²]", 1e6, 1e15, min(max(_ei_def, 1e6), 1e15),
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
    basis_ko = {"calibrated_probability": "보정 붕괴확률",
                "reference_range": "정상범위(건강 인구 대비)"}.get(basis, "원시 CRI")
    getattr(st, banner_fn)(f"{emoji} 경보 등급: **{level}**  ·  근거: {basis_ko}")

    # 🩺 CRI 정상범위(reference range) 판독 — 의료 검사수치처럼 건강 인구 대비 위치 표시
    ref = data.get("reference_range")
    obs = data.get("observation")
    if obs and not obs.get("sufficient", True):
        st.warning(f"🕒 **잠정 판정** — {obs.get('note') or '관측 기간이 짧아 계절/노이즈 분리가 불충분합니다.'} "
                   f"(관측 {obs.get('span_days', 0):.0f}일)")
    if ref:
        bc = ref.get("band_counts", {})
        _be = {"정상": "🟢", "주의": "🟡", "경고": "🟠", "위험": "🔴"}
        badges = " · ".join(f"{_be.get(b, '⚪')} {b} {bc.get(b, 0)}"
                            for b in ("정상", "주의", "경고", "위험"))
        st.markdown(f"🩺 **건강 인구 대비 판독** — {badges}  "
                    f"(정상범위 밖 **{ref.get('n_out_of_range', 0)}점**)")
        rp1, rp2, rp3 = st.columns(3)
        rp1.metric("최악점 백분위", f"{ref.get('worst_percentile', 0):.0f} %",
                   help="건강 교량 인구에서 이 교량 최악점의 상대 위치(100%=가장 높음).")
        rp2.metric("최악점 robust-z", f"{ref.get('worst_robust_z', 0):+.2f} σ",
                   help="건강 인구 중앙값 대비 로버스트 표준편차 단위 이탈(의료 검사수치의 z).")
        rp3.metric("정상범위 밖 점수", f"{ref.get('n_out_of_range', 0)}")
        if ref.get("regime_mismatch"):
            st.caption(f"⚠️ 관측조건 부적합 비교 주의: {ref['regime_mismatch']} "
                       "— 정상범위는 비슷한 노이즈·기간에서 학습됨.")
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
def tab_psi(start: date) -> None:
    """④ PSI 방법론 비교 — PS(ADI)·SBAS/DS(소baseline)·QPS(하이브리드) 데크 결과."""
    st.subheader("④ PSI 방법론 비교 — PS · SBAS(DS) · QPS")
    st.caption("같은 교량 데크를 세 시계열 방법론으로 처리한 결과. **PS**=진폭분산 ADI<0.25 "
               "점같은 영구산란체(단일마스터) · **SBAS/DS**=소baseline 네트워크 역산(분포산란체) · "
               "**QPS**=PS∪DS 하이브리드.")
    default_h5 = str(Path(data_root()) / "jeongjagyo_psi_compare.h5")
    if not Path(default_h5).exists():
        default_h5 = "data/jeongjagyo_psi_compare.h5"
    h5 = st.text_input("PSI 비교 H5", default_h5, key="psi_h5")
    if not Path(h5).exists():
        st.info("PSI 비교 H5 가 없습니다. `psi_pipeline.build_psi_comparison_h5` 로 생성하세요 "
                "(소baseline 간섭도 + 진폭 tif).")
        return

    with h5py.File(h5, "r") as f:
        at = {k: f.attrs[k] for k in f.attrs}
        lonlat = f["pixel_lonlat"][()]
        ts = f["ts_sbas_mm"][()]; epochs = [str(e) for e in f["epochs"][()]]
        vel = f["velocity_mm_yr"][()]; adi = f["adi"][()]
        coh = f["sbas_coherence"][()]; qps = f["qps_class"][()]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("데크 점", int(at.get("n_points", len(qps))))
    c2.metric("PS (ADI<0.25)", int(at.get("n_ps", 0)))
    c3.metric("DS (SBAS γ)", int(at.get("n_ds", 0)))
    c4.metric("QPS (PS∪DS)", int(at.get("n_qps", 0)))
    st.caption(f"SBAS 네트워크: rank {int(at.get('network_rank', 0))} · "
               f"{'완전연결 ✅' if at.get('network_connected') else '⚠️ 미연결'} · "
               f"epoch당 최소 {int(at.get('min_pairs_per_epoch', 0))}쌍 · "
               f"임계 ADI<{at.get('ps_adi_max', 0.25):.2f}·γ≥{at.get('ds_coh_min', 0.7):.2f}")
    if int(at.get("n_ps", 0)) == 0:
        st.info("ℹ️ **PS 0점** — 교량 데크(도로면)는 점같은 영구산란체가 없어 PS 로는 안 잡힙니다. "
                "데크에는 **SBAS/DS** 가 적합(분포산란체). PS 는 건물·구조물(강볼트·신축이음)에서 잡힙니다.")

    import numpy as np
    st.markdown("**SBAS 누적 변위 시계열 (DS/QPS 점)**")
    sel = qps > 0
    if sel.any():
        base = datetime(start.year, start.month, start.day)
        dts = [datetime.strptime(e, "%Y%m%d") for e in epochs]
        df = pd.DataFrame({"중앙값": np.median(ts[sel], axis=0),
                           "평균": ts[sel].mean(axis=0)}, index=dts)
        st.line_chart(df, height=240)
        st.caption(f"LOS 누적 변위(mm) · 속도 중앙값 {np.median(vel[sel]):+.2f} mm/yr")
    else:
        st.warning("QPS 점이 없습니다.")

    st.markdown("**산란체 분류 (ADI vs SBAS 위상결맞음)**")
    lbl = np.where(qps == 2, "PS", np.where(qps == 1, "DS", "제외"))
    sc = pd.DataFrame({"ADI(진폭분산)": adi, "SBAS γ(위상결맞음)": coh, "분류": lbl,
                       "lon": lonlat[:, 0], "lat": lonlat[:, 1], "속도mm/yr": vel})
    st.scatter_chart(sc, x="ADI(진폭분산)", y="SBAS γ(위상결맞음)", color="분류", height=300)
    st.caption("PS 는 ADI<0.25(왼쪽), DS 는 γ≥0.7(위쪽·PS 아님). 데크는 ADI 가 높아 대부분 DS.")

    with st.expander("점별 표 (좌표·속도·분류)"):
        st.dataframe(sc.round(3), hide_index=True, use_container_width=True)

    # 🗺️ 변위 색 오버레이 지도 (BIM/IFC 얹기 전 웹지도 미리보기, 값 토글)
    st.markdown("**🗺️ 변위 색 오버레이 지도** — 값 선택 시 그 값으로 점을 색칠(BIM/IFC 오버레이 프리뷰)")
    try:
        import folium
        from streamlit_folium import st_folium
        from inframon.insar.bim_export import _hex_colors, _VALUE_SPECS
    except ImportError:
        st.info("지도에는 folium·streamlit-folium 이 필요합니다: `pip install -e .[dashboard]`")
        return

    # 값 계산: LOS 속도·연직(입사각 39° 가정)·누적 변위
    cum = (ts[:, -1] - ts[:, 0]) if ts.ndim == 2 else np.full(len(vel), np.nan)
    vert = vel / np.cos(np.radians(39.0))
    valmap = {"LOS 속도(mm/yr)": ("los_velocity_mm_yr", vel),
              "연직 속도(mm/yr)": ("vertical_velocity_mm_yr", vert),
              "누적 변위(mm)": ("cumulative_mm", cum)}
    # CRI(FRAM 위험도) 색 옵션 — FRAM project.h5(/fram/CRI) 주면 점별 최근접 매핑
    fram_h5 = st.text_input("FRAM project.h5 (CRI 색, 선택)", "data/jeongjagyo_fram.h5",
                            key="bim_fram", help="/fram/CRI 를 InSAR 점에 최근접 매핑해 위험도로 색칠.")
    if fram_h5 and Path(fram_h5).exists():
        try:
            from inframon.insar.bim_export import map_cri_to_points
            cri_arr = map_cri_to_points(lonlat, fram_h5, reduce="max")
            if np.isfinite(cri_arr).any():
                valmap["CRI(FRAM 위험도)"] = ("cri", cri_arr)
        except (ValueError, OSError) as exc:
            st.caption(f"CRI 매핑 불가: {exc}")
    vlabel = st.radio("색 기준 값", list(valmap), horizontal=True, key="bim_val")
    vkey, varr = valmap[vlabel]
    cmap_name, kind, _ = _VALUE_SPECS[vkey]
    cols, vmin, vmax = _hex_colors(varr, cmap_name, kind)

    lat0 = float(np.median(lonlat[:, 1])); lon0 = float(np.median(lonlat[:, 0]))
    m = folium.Map(location=[lat0, lon0], zoom_start=16, tiles="OpenStreetMap")
    for i in range(len(varr)):
        c = cols[i]
        folium.CircleMarker(
            [float(lonlat[i, 1]), float(lonlat[i, 0])], radius=5, color=c, weight=1,
            fill=True, fill_color=c, fill_opacity=0.85,
            tooltip=f"{lbl[i]} · {vlabel} {varr[i]:+.2f}").add_to(m)
    st_folium(m, height=430, key="bim_map", returned_objects=[])
    if kind == "unit":      # CRI: 초록=안전 / 빨강=위험
        st.caption(f"🎨 {vlabel} · 범위 [{vmin:.1f}, {vmax:.1f}] · RdYlGn_r"
                   "(🟢초록=안전, 🔴빨강=위험) · FRAM 위험도. "
                   "IFC 지오레퍼런싱 오면 이 좌표를 IFC 로컬로 정합해 부재 색칠.")
    else:                   # 변위/속도: 발산맵
        st.caption(f"🎨 {vlabel} · 범위 [{vmin:+.1f}, {vmax:+.1f}] · 발산맵 RdBu"
                   "(🔵파랑=+상승/접근, 🔴빨강=−침하/이격) · **LOS 기준**. "
                   "IFC 지오레퍼런싱(IfcMapConversion) 오면 이 좌표를 IFC 로컬로 정합해 부재 색칠.")


def main() -> None:
    st.set_page_config(page_title="inframon — 인프라 모니터링", page_icon="🌉", layout="wide")
    # 섹션(라디오)을 오가도 위젯 값 유지: 렌더링 안 되는 섹션의 keyed 위젯은 Streamlit 이
    # session_state 에서 GC 하므로, 매 rerun 마다 키를 재확정해 기본값 리셋을 막는다.
    # 단, 버튼(btn_*·prof_save)은 session_state 로 값 설정이 금지되므로 제외한다.
    for _k in list(st.session_state.keys()):
        if _k.startswith("btn_") or _k in ("prof_save",) or _k.startswith("FormSubmitter"):
            continue
        st.session_state[_k] = st.session_state[_k]
    st.title("🌉 inframon — 통합 인프라 모니터링")
    st.caption("InSAR(변위) → PINN(구조해석) → FRAM(공명 위험 CRI)  ·  위성 SAR 기반 교량 안전 모니터링")

    # 📁 저장 폴더(데이터 루트) — project·레시피·SLC·결과가 모두 여기에 저장된다.
    st.sidebar.markdown("### 📁 저장 폴더")
    _cur_root = data_root()
    _root_in = st.sidebar.text_input(
        "데이터 루트", _cur_root, key="data_root_input",
        help="위성 SLC·레시피·project.h5·결과가 저장되는 위치. 예: F:\\inframon "
             "(대용량 SLC는 외장드라이브 권장).")
    if _root_in.strip() and _root_in.strip() != _cur_root:
        st.session_state["data_root"] = _root_in.strip()
        _config_save(data_root=_root_in.strip())        # 재시작 후에도 유지
        try:
            Path(_root_in.strip()).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            st.sidebar.error(f"폴더 생성 실패: {exc}")
        st.rerun()
    try:
        _free_gb = shutil.disk_usage(data_root()).free / 1e9
        st.sidebar.caption(f"저장 위치: `{data_root()}` · 여유 {_free_gb:.0f} GB")
    except OSError:
        st.sidebar.caption(f"저장 위치: `{data_root()}`")

    # 📍 현재 교량(위치) — 레시피가 있으면 이름·좌표 표시, 없으면 지정 안내
    st.sidebar.markdown("### 📍 현재 교량")
    _tgt = Path(_recipe_dir()) / "bridge_target.json"
    try:
        _t = json.loads(_tgt.read_text(encoding="utf-8")) if _tgt.exists() else None
    except (OSError, ValueError):
        _t = None
    if _t and _t.get("selected_lat") is not None:
        st.sidebar.success(f"**{_t.get('name') or '교량'}**  \n"
                           f"{_t['selected_lat']:.5f}, {_t['selected_lon']:.5f}")
    else:
        st.sidebar.caption("교량 미지정 — 아래에서 이름으로 검색하거나 ① InSAR 탭 지도로 지정.")

    # 🔎 교량명 검색 — CSV(전국교량표준데이터) + OSM 양쪽 → 지도(① InSAR)에 마커로 표기
    with st.sidebar.expander("🔎 교량명 검색 (CSV+OSM)", expanded=not (_t and _t.get("selected_lat"))):
        from inframon.public_data import find_bridge_csv
        _csv = find_bridge_csv(data_root())
        if not _csv:
            st.caption("⚠️ 전국교량표준데이터 CSV 없음 — OSM 만 검색됩니다. "
                       f"CSV 는 data.go.kr/15081953 에서 받아 `{data_root()}` 에 두세요.")
        _q = st.text_input("교량명 (예: 한강대교, 정자교)", key="bridge_q")
        if _q and _q.strip():
            # 같은 질의는 재검색 안 함(OSM 네트워크 절약)
            if st.session_state.get("_last_q") != _q.strip():
                with st.spinner("CSV + OSM 검색 중…"):
                    _h, _e = _combined_bridge_search(_q.strip(), _csv)
                    st.session_state["search_hits"] = _h
                    st.session_state["search_osm_err"] = _e
                    st.session_state["_last_q"] = _q.strip()
            hits = st.session_state.get("search_hits", [])
            _oerr = st.session_state.get("search_osm_err")
            if _oerr:
                st.warning(f"OSM 조회 실패(재시도 권장): {_oerr}")
            if not hits:
                st.info("일치하는 교량이 없습니다 (CSV·OSM).")
            else:
                n_csv = sum(1 for h in hits if h["source"] == "CSV")
                n_osm = sum(1 for h in hits if h["source"] == "OSM")
                st.caption(f"🔵 CSV {n_csv} · 🟢 OSM {n_osm} — 아래 선택, ① InSAR 지도에 표기됨")

                def _lbl(i):
                    h = hits[i]
                    ic = "🔵" if h["source"] == "CSV" else "🟢"
                    # OSM 태그로 교량 확정(✓) vs 이름만 일치(도로 가능성, ~)
                    bc = h.get("bridge_confirmed")
                    mark = " ✓교량" if bc == "yes" else (" ~이름" if bc == "name_only" else "")
                    return (f"{ic} {h['name']}{mark} · {h.get('structure') or '-'} "
                            f"{('%.0fm' % h['length_m']) if h.get('length_m') else '?'} "
                            f"({h['lat']:.3f},{h['lon']:.3f})")
                _i = st.radio(f"결과 {len(hits)}건", range(len(hits)), format_func=_lbl,
                              key="bridge_hit")
                st.session_state["search_sel"] = _i        # 지도 강조용
                if st.button("📍 이 교량으로 설정", use_container_width=True, key="btn_set_bridge"):
                    _save_target_from_csv(hits[_i])
                    st.session_state["recipe_dir"] = _recipe_dir()
                    st.success(f"설정됨 → {hits[_i]['name']} ({hits[_i]['source']})")
                    st.rerun()
        else:
            st.session_state.pop("search_hits", None)

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

    # 섹션 선택 — st.tabs 는 rerun 시 첫 탭으로 리셋되므로, session_state 에 유지되는
    # 라디오(key='active_tab')로 대체. 위젯 조작으로 rerun 돼도 현재 섹션이 유지된다.
    _SECTIONS = ["① InSAR", "② PINN", "③ FRAM", "④ PSI 방법론"]
    active = st.radio("섹션", _SECTIONS, key="active_tab", horizontal=True,
                      label_visibility="collapsed")
    st.divider()

    if active == _SECTIONS[0]:
        tab_insar(path, start)
    elif active == _SECTIONS[1]:
        tab_pinn(path, start) if Path(path).exists() else \
            st.info("project.h5 없음 — 사이드바에서 데모 데이터를 먼저 생성하세요.")
    elif active == _SECTIONS[2]:
        tab_fram(path, start) if Path(path).exists() else \
            st.info("project.h5 없음 — 사이드바에서 데모 데이터를 먼저 생성하세요.")
    elif active == _SECTIONS[3]:
        tab_psi(start)


if __name__ == "__main__":
    main()
