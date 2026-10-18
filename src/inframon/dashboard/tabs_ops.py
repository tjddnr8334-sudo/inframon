"""대시보드 운영 섹션 — ⑦ 법정 근거 · ⑧ SLC 보관소 · ⑨ 교량 상세 내역.

inframon 의 역할(2026-10-16 재정의): InSAR 산출 · 위험도(CRI) · 법정 근거 · SLC 보관을
한 곳에서 보고, 결과를 Pontifex 창고(IFC 결합·B-Maps Add-in)로 넘긴다.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import h5py
import pandas as pd
import streamlit as st

_GRADES = ["미상", "A", "B", "C", "D", "E"]
_KINDS = ["정기안전점검", "정밀안전점검", "정밀안전진단", "성능평가"]
_TYPES = ["girder", "box_girder", "rahmen", "slab", "truss", "arch", "cable_stayed", "suspension"]


def _bridge_meta(folder: Path) -> dict:
    meta: dict = {}
    for name in ("bridge.json", "legal_inputs.json"):
        p = folder / name
        if p.is_file():
            try:
                meta.update(json.loads(p.read_text(encoding="utf-8")))
            except ValueError:
                st.warning(f"{p.name} 을 읽지 못했습니다(JSON 형식 오류).")
    return meta


def _obs(path: str) -> dict:
    """법정 근거표 관측 해석 입력 — 관측일이 있는 산출물에서만."""
    from inframon.pontifex import PontifexError, build_records, observation_summary

    try:
        recs = build_records(path, 0)
    except (PontifexError, OSError, KeyError):
        return {}
    return observation_summary(path, recs)


# ── ⑦ 법정 근거 ────────────────────────────────────────────────────────
def tab_legal(path: str) -> None:
    from inframon.legal import build_legal_table

    st.subheader("법정 근거 — 시설물안전법 · 시행령 · 세부지침")
    st.caption("InSAR 는 법정 안전점검·진단을 대체하지 않는다(법 제2조제5호). 이 표는 종별·점검 기한과 "
               "위성 관측값이 세부지침의 어느 조항과 닿는지를 보여주는 참고 자료다.")
    folder = Path(path).parent
    meta = _bridge_meta(folder)

    with st.expander("교량 제원·점검 이력 입력", expanded=not meta.get("length_m")):
        c1, c2, c3, c4 = st.columns(4)
        length = c1.number_input("연장 (m)", 0.0, 10000.0, float(meta.get("length_m") or 0.0), 1.0)
        max_span = c2.number_input("최대 경간장 (m)", 0.0, 2000.0, float(meta.get("max_span_m") or 0.0), 1.0)
        n_spans = c3.number_input("경간 수", 0, 200, int(meta.get("n_spans") or 0), 1)
        bt = meta.get("bridge_type") or "girder"
        btype = c4.selectbox("상부구조형식", _TYPES, index=_TYPES.index(bt) if bt in _TYPES else 0)
        d1, d2, d3 = st.columns(3)
        g0 = str(meta.get("safety_grade") or "미상")
        grade = d1.selectbox("안전등급 (최근 점검 결과)", _GRADES, index=_GRADES.index(g0) if g0 in _GRADES else 0)
        official = d2.text_input("공식 종별 (공공데이터, 예: 2종)", str(meta.get("facility_class") or ""))
        road = d3.text_input("도로종류 (예: 일반국도)", str(meta.get("road_kind") or ""))
        st.markdown("**최근 실시일** (모르면 비워 두세요 — 기한을 추정하지 않습니다)")
        cols = st.columns(4)
        insp = dict(meta.get("inspections") or {})
        for k, col in zip(_KINDS, cols):
            insp[k] = col.text_input(k, str(insp.get(k) or ""), placeholder="YYYY-MM-DD")
        if st.button("입력 저장 (legal_inputs.json — Pontifex 전송에도 쓰임)"):
            out = {"length_m": length or None, "max_span_m": max_span or None,
                   "n_spans": int(n_spans) or None, "bridge_type": btype,
                   "safety_grade": None if grade == "미상" else grade,
                   "facility_class": official or None, "road_kind": road or None,
                   "inspections": {k: v for k, v in insp.items() if v}}
            (folder / "legal_inputs.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
            st.success(f"저장: {folder / 'legal_inputs.json'}")
            meta.update(out)

    meta_now = {**meta, "length_m": length or None, "max_span_m": max_span or None,
                "n_spans": int(n_spans) or None, "bridge_type": btype,
                "safety_grade": None if grade == "미상" else grade,
                "facility_class": official or None, "road_kind": road or None,
                "inspections": {k: v for k, v in insp.items() if v}}
    obs = _obs(path) if Path(path).exists() else {}
    t = build_legal_table(meta_now, obs)
    fc, sg = t["facility_class"], t["safety_grade"]

    m1, m2, m3 = st.columns(3)
    m1.metric("시설물 종별", fc["label"])
    m2.metric("안전등급", f"{sg['grade']} ({sg['name']})" if sg["grade"] else "미상")
    overdue = [i["kind"] for i in t["inspections"] if i["overdue"]]
    m3.metric("기한 경과 점검", f"{len(overdue)}건", ", ".join(overdue) or None,
              delta_color="inverse" if overdue else "off")
    for r in fc["reasons"]:
        st.write(f"· {r}")
    if fc["mismatch"]:
        st.warning(f"공식 종별 {fc['official']}종 과 제원으로 계산한 {fc['computed']}종 이 다릅니다 — 대장 확인 필요")
    if sg["state"]:
        st.caption(f"안전등급 {sg['grade']}: {sg['state']} — {sg['basis']}")

    st.markdown("#### 법정 점검 기한 (시행령 별표 3)")
    app = {True: "대상", False: "대상 아님", None: "확인 필요"}
    st.dataframe(pd.DataFrame([{
        "점검": i["kind"], "대상": app[i["applicable"]], "주기": i["interval_text"],
        "최근": i["last"] or "", "다음 기한": i["next_due"] or "",
        "경과": "⚠️ 경과" if i["overdue"] else "", "대상 근거": i["why"]} for i in t["inspections"]]),
        hide_index=True, width="stretch")

    st.markdown("#### 위성 관측 ↔ 세부지침 조항")
    if not obs:
        st.caption("관측일(/insar/date_labels)이 있는 산출물이 아니어서 관측값 행은 비어 있습니다.")
    st.dataframe(pd.DataFrame([{"관측 항목": f["item"], "값": f["value"], "관련 조항·기준": f["basis"],
                                "해석": f["reading"],
                                "법정 수치 기준": "있음" if f["has_legal_threshold"] else "없음"}
                               for f in t["findings"]]), hide_index=True, width="stretch")

    with st.expander("근거 법령 · 원격탐사 관련 확인 사항"):
        for law in t["laws"]:
            st.write(f"· {law['name']} — {law['no'] or ''} (시행 {law['effective'] or '-'})"
                     + ("" if law["verified"] else " · 현행 판 미확인"))
        for r in t["remote_sensing"]:
            st.caption(r)
    st.info(t["notice"])
    st.download_button("법정 근거표 JSON", json.dumps(t, ensure_ascii=False, indent=1),
                       file_name="legal_table.json", mime="application/json")


# ── ⑧ SLC 보관소 ───────────────────────────────────────────────────────
def tab_slc() -> None:
    from inframon.insar.slc_store import get_slc_dir, inventory

    st.subheader("SLC 보관소 — 받아 둔 Sentinel-1 장면 현황")
    st.caption("보관 폴더의 .zip 과 조립된 .SAFE 를 궤도별로 센다. 같은 장면이 여러 폴더에 있으면 한 번만 센다. "
               "받다 만 조각(zip 끝 손상·manifest 없음)은 따로 표시한다.")
    default = str(get_slc_dir() or "")
    roots_txt = st.text_area("보관 폴더 (한 줄에 하나)", st.session_state.get("slc_roots", default),
                             height=80, placeholder="E:\\SLC")
    c1, c2 = st.columns([1, 3])
    if c1.button("현황 읽기", type="primary"):
        roots = [r.strip() for r in roots_txt.splitlines() if r.strip()]
        st.session_state["slc_roots"] = roots_txt
        missing = [r for r in roots if not Path(r).is_dir()]
        if missing:
            st.error("없는 폴더: " + ", ".join(missing))
        with st.spinner("장면을 세는 중…"):
            st.session_state["slc_inv"] = inventory([r for r in roots if Path(r).is_dir()])
    c2.caption("CLI: `python -m inframon --slc-inventory E:\\SLC --slc-inventory-csv slc.csv`")
    inv = st.session_state.get("slc_inv")
    if not inv:
        st.info("보관 폴더를 적고 **현황 읽기** 를 누르세요." if not default else
                f"설정된 보관 폴더: {default}")
        return

    m = st.columns(4)
    m[0].metric("장면", inv["n_scenes"])
    m[1].metric("용량", f"{inv['size_gb']} GB")
    m[2].metric("궤도", len(inv["tracks"]))
    m[3].metric("받다 만 조각", inv["incomplete"])
    st.markdown("#### 궤도별")
    st.dataframe(pd.DataFrame([{
        "궤도": t["key"], "장면": t["n_scenes"], "날짜": t["n_dates"], "처음": t["first"],
        "마지막": t["last"], "GB": t["size_gb"], "받다 만": t["incomplete"],
        "24일 넘는 공백": t["gaps_over_24d"],
        "최대 공백": (f"{t['largest_gap']['from']} ~ {t['largest_gap']['to']} ({t['largest_gap']['days']}일)"
                   if t["largest_gap"] else "")} for t in inv["tracks"]]),
        hide_index=True, width="stretch")
    if any(t["track"] is None for t in inv["tracks"]):
        st.caption("S1C·S1D 는 파일명으로 상대궤도를 계산하는 공식 오프셋을 확인하지 못해 'T?' 로 둡니다.")

    df = pd.DataFrame(inv["scenes"])
    if not df.empty:
        df["궤도"] = df.apply(lambda r: f"{r['mission'][:2]}-T{int(r['track']):03d}"
                            if pd.notna(r["track"]) else f"{r['mission']}-T?", axis=1)
        df["날짜"] = pd.to_datetime(df["date"])
        st.markdown("#### 관측 타임라인")
        st.scatter_chart(df, x="날짜", y="궤도", size=20, height=260)
        st.markdown("#### 장면 목록")
        show = df[["scene", "궤도", "date", "format", "size_gb", "complete", "folder", "root"]]
        st.dataframe(show, hide_index=True, width="stretch", height=360)
        st.download_button("장면 목록 CSV", show.to_csv(index=False).encode("utf-8-sig"),
                           file_name="slc_inventory.csv", mime="text/csv")


# ── ⑨ 교량 상세 내역 ───────────────────────────────────────────────────
def _h5_tree(path: str) -> list[dict]:
    rows = []
    with h5py.File(path, "r") as f:
        def visit(name, obj):
            if isinstance(obj, h5py.Dataset):
                rows.append({"경로": "/" + name, "형상": " × ".join(map(str, obj.shape)) or "스칼라",
                             "자료형": str(obj.dtype)})
        f.visititems(visit)
    return rows


def tab_dossier(path: str) -> None:
    from inframon.audit import audit_artifact
    from inframon.pontifex import discover_bridge_folder

    st.subheader("교량 상세 내역")
    p = Path(path)
    if not p.exists():
        st.info("project.h5 가 없습니다 — ⓪ 시작 에서 교량을 처리하세요.")
        return
    folder = p.parent
    meta = _bridge_meta(folder)
    found = discover_bridge_folder(p)

    a = audit_artifact(p)
    mark = {"보고 가능": "✅", "조건부": "🟡", "보고 불가": "❌"}.get(a.verdict, "?")
    c = st.columns(4)
    c[0].metric("교량", meta.get("name") or folder.name)
    c[1].metric("산출물 감사", f"{mark} {a.verdict}")
    c[2].metric("관측 시점", meta.get("n_epochs") or "-")
    c[3].metric("IFC", "있음" if found["ifc"] else "없음")
    for r in a.reasons:
        st.caption(f"· {r}")

    st.markdown("#### 제원")
    keys = [("연장 m", "length_m"), ("폭 m", "width_m"), ("경간 수", "n_spans"), ("최대 경간 m", "max_span_m"),
            ("형식", "bridge_type"), ("재료", "material"), ("형하고 m", "clearance_m"),
            ("위도", "lat"), ("경도", "lon"), ("궤도", "track"), ("기준영상", "master")]
    st.dataframe(pd.DataFrame([{"항목": k, "값": str(meta.get(v, ""))} for k, v in keys if meta.get(v) is not None]),
                 hide_index=True, width="stretch")
    src = meta.get("sources")
    if isinstance(src, dict):
        with st.expander("제원 출처"):
            st.json(src)

    st.markdown("#### 산출물")
    files = sorted(x for x in folder.iterdir() if x.is_file())
    st.dataframe(pd.DataFrame([{"파일": x.name, "MB": round(x.stat().st_size / 1e6, 2),
                                "수정": date.fromtimestamp(x.stat().st_mtime).isoformat()} for x in files]),
                 hide_index=True, width="stretch", height=240)
    with st.expander("project.h5 구조"):
        st.dataframe(pd.DataFrame(_h5_tree(path)), hide_index=True, width="stretch")

    st.markdown("#### Pontifex 창고로 보내기")
    st.caption("CRI 시계열 + 부재 GUID 결과 + 법정 근거표 + IFC 를 한 번에 보낸다. '보고 불가' 감사 결과는 막는다. "
               "창고는 IFC 부재 위에 결과를 올리고, 교량관리번호로 KICT B-Maps 탭에 노출한다.")
    k1, k2, k3, k4 = st.columns([3, 1, 2, 2])
    base = k1.text_input("Pontifex 주소", st.session_state.get("pfx_base", "http://127.0.0.1:38000"))
    bid = k2.number_input("교량 id", 0, 10_000_000, int(meta.get("pontifex_id") or 0), 1)
    bmaps = k3.text_input("B-Maps 교량관리번호", str(meta.get("bmaps_id") or ""))
    token = k4.text_input("토큰", "", type="password")
    b1, b2 = st.columns(2)
    dry = b1.button("미리 보기 (전송 안 함)")
    go = b2.button("전송", type="primary", disabled=not bid)
    if dry or go:
        from inframon.pontifex import PontifexError, push_bundle

        st.session_state["pfx_base"] = base
        try:
            res, extra = push_bundle(path, int(bid), base=base, token=token or None, dry_run=dry,
                                     bmaps_id=bmaps or None)
        except PontifexError as e:
            st.error(str(e))
            return
        st.success(res.describe())
        st.write(f"부재 GUID 레코드 {extra['n_element_records']}건 · 찾은 파일: "
                 + ", ".join(f"{k}={Path(v).name}" for k, v in extra["found"].items() if v))
        if go:
            st.markdown(f"[창고에서 보기]({base}/warehouse/bridge/{int(bid)}/)"
                        + (f" · [B-Maps 탭]({base}/addin/bmaps/tab/{bmaps}/)" if bmaps else ""))
            if extra.get("ifc_result"):
                st.json(extra["ifc_result"])
