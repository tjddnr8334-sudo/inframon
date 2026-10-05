"""⑥ 광역 모니터링 탭 — 지자체 전체 교량의 InSAR 감시 결과(엔진: bridge-insar-monitor).

보여주는 것:
  - 교량 알람(신규 경보·등급 상승·확인 필요)과 **처리 오류 알람**(자료 지연·다운로드·정합·구역 실패·저장공간)
  - 지도(등급 색) · 시군/등급 필터 목록
  - 교량별: 대장 정보, 허용변위 대비 비율(``life.limits`` 기준: 침하 25 mm, 각변위 1/500), 궤도별 판정,
    변위 시계열(LOS / 수직 / 수직-열신축보정, 모두 주변 지반 대비), 주기별 판정 이력
  - ▶ project.h5 로 내보내기 → ②PINN·③FRAM·④잔존수명 탭에서 이어서 해석
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from ..region import judge, results

_COL = {"정상": "#2e8b57", "관심": "#b9a400", "주의": "#d18a00", "경고": "#c8372d", "판정 불가": "#8b97a3", "미처리": "#c9d1d9"}


def _region_configs(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "configs").glob("*/region.json"))


def tab_region(data_root: str) -> None:
    from ..region.cycle import load_region
    repo = Path(__file__).resolve().parents[3]
    cfgs = _region_configs(repo)
    st.subheader("⑥ 광역 모니터링 — 지자체 전체 교량 InSAR 감시")
    st.caption("Sentinel-1 새 영상이 나올 때마다(약 12일) 기준 영상 격자로 정합(보간)하고, 모든 교량의 변위를 "
               "주변 지반 대비로 다시 계산해 허용변위와 비교합니다. 엔진: bridge-insar-monitor (CLI).")
    opts = [str(p.relative_to(repo)) for p in cfgs]
    choice = st.selectbox("지역 설정 (configs/<지역>/region.json)", opts + ["결과 폴더 직접 입력"], key="region_cfg")
    if choice == "결과 폴더 직접 입력" or not opts:
        root = Path(st.text_input("엔진 결과 폴더 (state.json 이 있는 곳)", key="region_root"))
        cfg = None
    else:
        cfg = load_region(repo / choice); root = Path(results.local_path(cfg["results_dir"]))
    if not (root / "state.json").exists():
        st.info(f"결과가 아직 없습니다: `{root}` — 엔진에서 `python -m bim update --config ...` 를 한 번 실행하세요.")
        return
    state = results.load_state(root); rows = results.bridge_rows(state)
    al = results.load_alerts(root); he = results.load_health(root); cnt = results.counts(rows)
    st.markdown(f"**{state.get('title', '')}** · 마지막 판정 {state.get('made') or '-'} · 교량 {len(rows):,}개")
    cols = st.columns(6)
    for c, k in zip(cols, ("경고", "주의", "관심", "정상", "판정 불가", "미처리")):
        c.metric(k, f"{cnt.get(k, 0):,}")

    a, b = st.columns(2)
    with a:
        st.markdown(f"#### 🚨 교량 알람 {len(al.get('alerts', []))}건")
        if al.get("alerts"):
            st.dataframe([dict(구분=x["kind"], 등급=x["status"], 교량=x["name"], 시군=x["city"], 종별=x.get("cls"),
                               형식=x.get("type"), 허용변위대비=f"{round(100 * (x.get('ratio_now') or 0))} %",
                               예측10년=f"{round(100 * (x.get('ratio_proj') or 0))} %", 관리기관=x.get("org"),
                               연락처=x.get("tel")) for x in al["alerts"]], use_container_width=True, hide_index=True)
        else:
            st.success("이번 주기에 확인이 필요한 교량이 없습니다.")
    with b:
        ev = he.get("events", [])
        st.markdown(f"#### ⚠️ 처리 오류 알람 {len(ev)}건")
        st.caption(f"점검 {he.get('checked', '-')}")
        if ev:
            st.dataframe([dict(수준=e["level"], 분류=e["kind"], 궤도=e.get("track", ""), 내용=e["msg"]) for e in ev],
                         use_container_width=True, hide_index=True)
        else:
            st.success("처리 오류가 없습니다.")

    st.markdown("#### 🗺️ 교량 지도·목록")
    f1, f2, f3 = st.columns(3)
    city = f1.selectbox("시·군", ["전체"] + sorted({r["city"] for r in rows if r["city"]}), key="region_city")
    lev = f2.selectbox("등급", ["전체", "경고", "주의", "관심", "정상", "판정 불가", "미처리"], key="region_level")
    q = f3.text_input("교량명", key="region_q")
    sel = [r for r in rows if (city == "전체" or r["city"] == city) and (lev == "전체" or r["status"] == lev)
           and (not q or q in r["name"])]
    sel.sort(key=lambda r: (-(r["level"] if r["level"] is not None else -2), -(r["ratio_now"] or 0)))
    try:
        import folium
        from streamlit_folium import st_folium
        if sel:
            m = folium.Map(location=[sum(r["lat"] for r in sel) / len(sel), sum(r["lon"] for r in sel) / len(sel)],
                           zoom_start=10 if city != "전체" else 8, tiles="OpenStreetMap")
            for r in sel[:3000]:
                folium.CircleMarker([r["lat"], r["lon"]], radius=7 if (r["level"] or 0) >= 2 else 4, color="#ffffff", weight=1,
                                    fill=True, fill_color=_COL[r["status"]], fill_opacity=0.9,
                                    tooltip=f"{r['name']} ({r['status']})").add_to(m)
            st_folium(m, height=420, use_container_width=True, returned_objects=[])
    except ImportError:
        st.caption("지도는 `pip install -e .[dashboard]` (folium, streamlit-folium) 설치 시 표시됩니다.")
    st.caption(f"{len(sel):,}개 (위험도 높은 순)")
    names = [f"{r['name']} · {r['city']} · {r['status']}" for r in sel[:2000]]
    if not names:
        return
    pick = st.selectbox("교량 선택", names, key="region_pick")
    row = sel[names.index(pick)]
    full = next(b_ for b_ in state["bridges"] if b_["id"] == row["id"])
    _bridge_detail(root, cfg, row, full, data_root)


def inframon_proj_years() -> int:
    return 10


def _bridge_detail(root: Path, cfg, row: dict, full: dict, data_root: str) -> None:
    r = full.get("r") or {}
    st.markdown(f"##### {row['name']} — {row['status']}{' · 확인 필요(자료 부족)' if row['review'] else ''}")
    st.caption(f"{row['city']} · {row.get('cls') or '종별 미상'} · {row.get('type') or '형식 미상'} · 연장 {row.get('length_m')} m · "
               f"{row.get('year') or '?'}년 준공 · 최근 안전점검 {row.get('inspect') or '-'} · 관리 {full.get('org') or '-'} {full.get('tel') or ''}")
    if not r:
        st.info("아직 처리되지 않은 교량입니다(해당 구역 정합 대기)."); return
    if r.get("lv", -1) < 0:
        st.warning(f"판정 불가: {r.get('why', '')}"); return
    inf = judge.ratios(r.get("dn"), r.get("bn"), d_proj_mm=r.get("d10"), beta_proj=r.get("b10"))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("누적 수직변위 (지반 대비)", f"{r.get('dn')} mm", help=f"관측 {r.get('tob')}년")
    c2.metric("허용변위 대비", f"{round(100 * (r.get('rn') or 0))} %",
              help=f"life.limits 기준: 침하 {inf['settlement_mm']:.0f} mm, 각변위 1/{1 / inf['angular_distortion']:.0f} "
                   f"(경간 {r.get('sp')} m 가정)")
    c3.metric(f"{inframon_proj_years()}년 예측", f"{round(100 * (r.get('r10') or 0))} %",
              help="현재 변위속도가 이어진다고 가정. 등급은 한 단계까지만 올린다.")
    c4.metric("교량 위 PS / 신뢰도", f"{r.get('nps')} / {r.get('conf') or '-'}")
    if r.get("nt"):
        st.caption(r["nt"])
    tr = full.get("tracks") or {}
    if tr:
        st.dataframe([dict(궤도=k, 판정=results.status(v.get("level")), 교량위PS=v.get("n_ps"), 교대부대체=v.get("proxy"),
                           허용변위대비=None if v.get("r_now") is None else f"{round(100 * v['r_now'])} %") for k, v in tr.items()],
                     use_container_width=True, hide_index=True)
        if full.get("agree") is False:
            st.warning("궤도 간 판정이 다릅니다 — 현장 확인을 권장합니다.")
    ser = results.series(root, row["id"])
    kind = st.radio("시계열", list(results.KINDS), format_func=results.KINDS.get, horizontal=True, key="region_kind")
    data = {}
    for trk, kinds in ser.items():
        for d, v in kinds.get(kind, []):
            data.setdefault(d, {})[trk] = v
    if data:
        import pandas as pd
        df = pd.DataFrame.from_dict(data, orient="index").sort_index()
        df.index = pd.to_datetime(df.index, format="%Y%m%d")
        st.line_chart(df, y_label="mm (주변 지반 대비)")
        st.caption("점 하나가 Sentinel-1 촬영 한 번입니다. 새 영상이 정합될 때마다 자동으로 이어집니다.")
    else:
        st.caption("이 종류의 시계열이 아직 없습니다(엔진 재판정 후 표시).")
    hist = results.judgement_history(root, row["id"])
    if len(hist) > 1:
        with st.expander(f"주기별 판정 이력 {len(hist)}회"):
            st.dataframe(hist, use_container_width=True, hide_index=True)
    out = Path(data_root) / "region" / (cfg["title"] if cfg else "region") / row["id"]
    if st.button("▶ 이 교량을 project.h5 로 내보내기 (②PINN · ③FRAM · ④잔존수명)", key="btn_region_export"):
        from ..region.export import export_bridge
        npz = results.local_path(r.get("npz"))
        if not npz or not Path(npz).exists():
            st.error("엔진 StaMPS 점 파일(npz)을 찾지 못했습니다 — 엔진 결과를 다시 만드세요(`bim report`).")
        else:
            res = export_bridge(full, npz, out)
            if res["ok"]:
                st.session_state["region_project"] = res["project"]
                st.success(f"{res['project']} — 측정점 {res['n_points']}개 · 시점 {res['n_dates']}개. "
                           "사이드바 project.h5 경로가 이 파일로 바뀝니다(②~④ 탭).")
            else:
                st.error(res["reason"])
