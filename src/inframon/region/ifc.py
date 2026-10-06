"""광역 감시 교량별 IFC — 대장 제원으로 세운 프록시 부재(상판 S1·교각 P#·코핑·교대 A1/A2) + InSAR 판정 속성.

부재 형상은 ``bim.proxy_model.bridge_elements`` (표준데이터 연장·폭·높이, 경간수 = 연장 / 형식별 대표 경간),
파일 쓰기는 ``bim.ifc_write.write_elements`` 를 그대로 쓴다. 여기서는 속성 세트만 덧붙인다:

  IfcSite        Pset_BridgeRegistry      교량명·시군·종별·형식·연장·폭·높이·준공·안전점검·관리기관·좌표
  상판 S1         Pset_InSAR_Monitoring    판정·허용변위 대비(현재·하한·상한·10년)·누적변위(범위)·각변위·
                                           한계값(life.limits)·교량위 PS·신뢰도·확인 필요·불확실성(온도·자료 공백)·궤도
  교각·교대       Pset_InSAR_Support       가장 가까운 지점부의 변위속도·누적변위·유의 여부

정확한 BIM 이 있으면 그쪽이 낫다 — 이 IFC 는 실도면 없이 **위치·형식·결과를 한 파일로 묶는** 대체물이며,
Description 과 Pset 에 그 사실(proxy_from_registry)을 남긴다.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..life.limits import DEFAULTS


def _georef(row: dict):
    """대장 시점·종점(WGS84) → EPSG:5179 MapConversion(원점=교량 중심, x축=교축). pyproj 없으면 None."""
    try:
        from pyproj import Transformer
    except ImportError:
        return None
    from ..bim.georef import MapConversion
    geo = row.get("geo") or []
    if not geo or not isinstance(geo[0][0], (list, tuple)):
        return None
    (lo0, la0), (lo1, la1) = geo[0][0], geo[0][-1]
    tr = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    e0, n0 = tr.transform(lo0, la0)
    e1, n1 = tr.transform(lo1, la1)
    de, dn = e1 - e0, n1 - n0
    norm = (de * de + dn * dn) ** 0.5
    a, o = (de / norm, dn / norm) if norm > 0.5 else (1.0, 0.0)
    return MapConversion(eastings=(e0 + e1) / 2, northings=(n0 + n1) / 2, x_axis_abscissa=a, x_axis_ordinate=o,
                         target_crs="EPSG:5179", source="registry_start_end")


def _v(x):
    """IFC 속성값: None 은 빼고, numpy/str 은 파이썬 기본형으로."""
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return float(x) if isinstance(x, float) else int(x)
    return str(x)


def bridge_ifc(row: dict, out_path, *, title: str = "") -> dict:
    """``state.json`` 교량 행 하나 → IFC4 파일. 반환: 요약."""
    import ifcopenshell
    import ifcopenshell.api

    from ..bim.ifc_write import write_elements
    from ..bim.proxy_model import bridge_elements
    r = row.get("r") or {}
    L = float(row.get("len") or 0) or 20.0
    W = float(row.get("w") or r.get("w") or 10.0)
    span = min(float(r.get("sp") or 25.0), L)          # 대표 경간은 연장을 넘지 않는다
    n_sp = max(1, round(L / span))
    els = bridge_elements(length_m=L, width_m=W, n_spans=n_sp, clearance_m=float(row.get("h") or 5.0), name=row["id"])
    mc = _georef(row)
    out = Path(out_path)
    write_elements(els, out, map_conversion=mc, project_name=f"{title} 광역 InSAR 감시",
                   site_name=row.get("n") or row["id"],
                   description="proxy_from_registry — 전국교량표준데이터 제원으로 세운 프록시 부재(실도면 아님)")
    f = ifcopenshell.open(str(out))
    site = (f.by_type("IfcSite") or f.by_type("IfcProject"))[0]

    def pset(product, name, props):
        ps = ifcopenshell.api.run("pset.add_pset", f, product=product, name=name)
        ifcopenshell.api.run("pset.edit_pset", f, pset=ps, properties={k: _v(v) for k, v in props.items() if _v(v) is not None})

    pset(site, "Pset_BridgeRegistry", {
        "교량명": row.get("n"), "시군구": row.get("c"), "시설물종별": row.get("cls"), "상부구조형식": row.get("t"),
        "연장_m": L, "폭_m": W, "교량높이_m": row.get("h"), "준공연도": row.get("yr"), "최종안전점검결과": row.get("gr"),
        "최종안전점검일자": row.get("ins"), "관리기관": row.get("org"), "관리기관전화번호": row.get("tel"),
        "위도": row.get("lat"), "경도": row.get("lon"), "경간수_가정": n_sp, "경간_가정_m": span,
        "출처": "국토교통부 전국교량표준데이터(15081953)", "형상": "proxy_from_registry"})
    st = "미처리" if not r else ("판정 불가" if r.get("lv", -1) < 0 else ["정상", "관심", "주의", "경고"][r["lv"]])
    g = r.get("gaps") or {}
    mon = {"판정": st, "판정불가사유": r.get("why"), "허용변위대비_현재": r.get("rn"), "허용변위대비_하한": r.get("rlo"),
           "허용변위대비_상한": r.get("rhi"), "허용변위대비_10년예측": r.get("r10"), "누적수직변위_mm": r.get("dn"),
           "누적수직변위_하한_mm": r.get("dlo"), "누적수직변위_상한_mm": r.get("dhi"), "각변위_rad": r.get("bn"),
           "허용총침하_mm": DEFAULTS["settlement_mm"], "허용각변위_rad": DEFAULTS["angular_distortion"],
           "기준": "inframon life.limits (침하 25 mm, 각변위 1/500)", "관측기간_년": r.get("tob"),
           "교량위PS": r.get("nps"), "노드확보율": r.get("cov"), "신뢰도": r.get("conf"), "확인필요": bool(r.get("rv")) if r else None,
           "오차범위_등급경계초과": bool(r.get("unc")) if r else None, "온도보정영향_mm": r.get("tmm"),
           "사용영상수": g.get("n_used"), "예상영상수": g.get("n_expected"), "최장공백_일": g.get("max_gap_days"),
           "마지막영상": g.get("last_date"), "마지막영상경과_일": g.get("stale_days"), "자료공백경고": g.get("flag"),
           "궤도": r.get("trk"), "기준면": "교량 주변 지반(30–300 m) 대비, 첫 촬영일 이후"}
    zones = [z for z in (r.get("zones") or []) if z and "s" in z]              # 교축 위치가 있는 지점부
    ends = {z["end"]: z for z in (r.get("zones") or []) if z and "end" in z}   # 교대부 대체 판정(시점·종점)
    for e in f.by_type("IfcElement"):
        nm = e.Name or ""
        if nm == "S1":
            pset(e, "Pset_InSAR_Monitoring", mon)
        elif ends and nm in ("A1", "A2") and ({"A1": "시점", "A2": "종점"}[nm] in ends):
            z = ends[{"A1": "시점", "A2": "종점"}[nm]]
            pset(e, "Pset_InSAR_Support", {"위치": f"교대부 {z['end']} 10~200 m 지반(대체 판정)", "변위속도_mm_yr": z.get("v"),
                                           "유의": z.get("sig"), "측정점수": z.get("n")})
        elif zones and (nm.startswith("P") or nm.startswith("A")):
            # 부재 중심의 교축 위치(로컬 x) → 가장 가까운 지점부 결과
            el = next((x for x in els if x.name == nm), None)
            if el is None:
                continue
            xc = (el.bbox_min[0] + el.bbox_max[0]) / 2
            z = min(zones, key=lambda q: abs(q["s"] - xc))
            pset(e, "Pset_InSAR_Support", {"지점위치_s_m": z["s"], "변위속도_mm_yr": z.get("v"), "누적변위_mm": z.get("D"),
                                           "유의": z.get("sig"), "속도표준오차_mm_yr": z.get("sv"), "온도보정영향_mm": z.get("dT")})
    f.write(str(out))
    return dict(path=str(out), elements=len(els), spans=n_sp, georef=mc is not None, status=st)


def region_ifc(root, out_dir, *, only_judged: bool = True, ids=None, title: str = "") -> dict:
    """결과 폴더(state.json)의 교량들 → ``out_dir/<시군>/<교량명>_<id>.ifc`` + index.json."""
    from .results import load_state
    out_dir = Path(out_dir)
    done, fail = [], []
    for row in load_state(root).get("bridges", []):
        if ids and row["id"] not in ids:
            continue
        if only_judged and not (row.get("r") and row["r"].get("lv", -1) >= 0):
            continue
        safe = "".join(ch for ch in (row.get("n") or row["id"]) if ch not in '\\/:*?"<>|').strip() or row["id"]
        p = out_dir / (row.get("c") or "기타") / f"{safe}_{row['id']}.ifc"
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            done.append(dict(id=row["id"], name=row.get("n"), city=row.get("c"), **bridge_ifc(row, p, title=title)))
        except Exception as exc:  # noqa: BLE001 — 한 교량 실패가 전체를 멈추지 않게, 사유는 남긴다
            fail.append(dict(id=row["id"], name=row.get("n"), reason=str(exc)[:200]))
    (out_dir / "index.json").write_text(json.dumps(dict(made=len(done), failed=fail, bridges=done), ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    return dict(made=len(done), failed=len(fail), out=str(out_dir))
