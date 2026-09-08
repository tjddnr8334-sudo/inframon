#!/usr/bin/env python3
"""데모 디지털 트윈 — **IFC 생성 → 부재 결합 → 3D 트윈**을 한 번에.

저장소에 "IFC 까지 붙은 트윈"의 실물이 없었다. 코드와 테스트는 있는데 열어 볼 파일이
없으니 확인할 방법도 없었다. 여기서 정자교(README 예시) 기준으로 통째로 만든다:

  1. 표준데이터 실측 제원 → 프록시 부재            (proxy_model)
  2. 부재 → **IFC4 파일** + IfcMapConversion       (ifc_write)
  3. 그 IFC 를 **되읽어** 부재 AABB·GlobalId 확보   (ifc_io)  ← 왕복 검증
  4. InSAR 점을 데크 레벨로 올려 부재에 결합        (gltf_export)
  5. .glb + 자립형 웹뷰어 + 3D Tiles
  6. 그 점으로 **PINN 가상센싱 + FRAM CRI** 를 돌려 트윈 위에 얹는다 — CRI 채널 트윈,
     가상센싱 전체 변위장 그림, LOS 시계열, 결과 문서(docs/twin/결과.md)

3번이 요점이다. 부재 테이블에서 바로 트윈을 만들면 IFC 는 장식이 된다. IFC 를 다시
읽어 그 결과로 결합해야 "IFC 로 결합했다"가 참이 된다.

    python scripts/make_demo_twin.py

산출: docs/twin/  (jeongjagyo_proxy.ifc · twin.glb · twin.viewer.html · tileset.json
                  · twin_cri.glb · twin_cri.viewer.html · twin_project.h5 · 결과.md · *.png)
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from inframon.bim.georef import MapConversion                          # noqa: E402
from inframon.bim.ifc_io import read_elements, read_map_conversion     # noqa: E402
from inframon.bim.ifc_write import write_elements                      # noqa: E402
from inframon.bim.proxy_model import bridge_elements                   # noqa: E402
from inframon.insar.gltf_export import (export_insar_gltf,             # noqa: E402
                                        guid_map_from_alignment,
                                        write_3dtiles_tileset, write_web_viewer)

OUT = ROOT / "docs/twin"
PROJ = ROOT / "data/jeongjagyo_real.h5"

# 전국교량표준데이터(data.go.kr 15081953) 정자교 — OSM way 51473197 중심에서 8 m
NAME = "정자교"
LAT, LON = 37.36854, 127.10900
LENGTH_M, N_SPANS = 108.0, 5
WIDTH_M = 26.5           # OSM 양측 보도 간격 23.5 m + 보도 반폭 ×2
CLEARANCE_M = 6.0        # 탄천 위 형하고(표준데이터에 교량높이 없음 — 가정)
GROUND_M = 37.2          # DEM 지면 표고
DECK_AZ_DEG = 0.4        # OSM 차도 방위(동서 방향)
CRS = "EPSG:5186"
# SARvey 트랙의 HEADING −0.2312 는 라디안이다(−13.25°). 읽기 경로는 track_reader 가
# 정규화하지만 여기서는 트랙 attrs 를 직접 쓰므로 같은 함수로 맞춘다.
DECK_SEL_M = 30.0        # 데크 중심선 ±30 m — 감사표 ②와 같은 기준
BIN_M = 10.0             # 교축 구간집계 폭(5~10 m)


def _deck_geom():
    """OSM 차도 중심선 [(lat,lon),...]. make_ondeck_figure 가 캐시한 파일을 재사용한다."""
    cache = ROOT / "data/jeongjagyo_f120/deck_polyline.json"
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        return [tuple(p) for p in d["road"]["geometry"]]
    from inframon.insar.osm_bridge import _overpass_query
    el = _overpass_query("[out:json][timeout:30];way(51473197);out geom;")["elements"][0]
    return [(g["lat"], g["lon"]) for g in el["geometry"]]


def build_ifc() -> tuple[Path, dict]:
    """① 실측 제원 → 프록시 부재  ② → IFC4 + 지오참조."""
    els = bridge_elements(length_m=LENGTH_M, width_m=WIDTH_M, n_spans=N_SPANS,
                          clearance_m=CLEARANCE_M, name=NAME)
    from pyproj import Transformer
    e0, n0 = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(LON, LAT)
    az = math.radians(DECK_AZ_DEG)
    mc = MapConversion(eastings=e0, northings=n0, orthogonal_height=GROUND_M,
                       x_axis_abscissa=math.cos(az), x_axis_ordinate=math.sin(az),
                       target_crs=CRS, source="proxy_placement",
                       fit={"note": "표준데이터 제원 + OSM 방위로 배치 — 측량 정합 아님"})
    ifc = OUT / "jeongjagyo_proxy.ifc"
    info = write_elements(els, ifc, map_conversion=mc, project_name=f"{NAME} 프록시 교량",
                          site_name=f"{NAME} (성남 분당)")
    return ifc, info


def roundtrip(ifc: Path) -> tuple[list, MapConversion, Path]:
    """③ IFC 를 **되읽는다** — 이게 있어야 'IFC 로 결합했다'가 참이 된다."""
    els = read_elements(ifc)
    mc = read_map_conversion(ifc)
    if mc is None:
        raise SystemExit("되읽은 IFC 에 IfcMapConversion 이 없습니다 — 배치를 알 수 없습니다.")
    ej = OUT / "jeongjagyo_elements.json"
    ej.write_text(json.dumps({
        "schema": "inframon.bim.elements/1",
        "source": f"read back from {ifc.name}",
        "elements": [{"guid": e.guid, "name": e.name, "ifc_type": e.ifc_type,
                      "member": e.member, "bbox_min": list(e.bbox_min),
                      "bbox_max": list(e.bbox_max), "extra": e.extra} for e in els],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return els, mc, ej


def prepare_points(geom_latlon) -> Path:
    """④ 실 InSAR 점을 트윈용 프로젝트로 — **쉬프트 보정 후** 데크 ±30 m 안만.

    지오코딩은 DEM(지면)을 쓰므로 데크 위 산란체는 δh/tanθ 만큼 밀려 있다. 보정 없이
    반경으로 자르면 데크 위 점이 밖으로, 밖 점이 안으로 들어온다. 보정을 먼저 하고
    데크 중심선 기준 ±30 m(감사표 ② 기준)로 고른다.
    """
    from inframon.insar.chainage import _signed_offset
    from inframon.insar.deck_geometry import project_to_polyline
    from inframon.insar.geolocation import apply_correction
    from inframon.insar.track_reader import normalize_heading_deg

    if not PROJ.exists():
        raise SystemExit(
            f"실데이터가 없습니다: {PROJ}\n"
            "  정자교 SARvey 산출물(2,661점 × 201에폭)이 필요합니다 — docs/실데이터_런북.md")
    with h5py.File(PROJ, "r") as h:
        g = h["insar"]
        xyz = np.asarray(g["xyz"][()], float)
        vel = np.asarray(g["velocity_mm_yr"][()], float)
        inc = np.asarray(g["incidence_deg"][()], float)
        keys = [k for k in g.keys() if k != "xyz"]
        data = {k: g[k][()] for k in keys}
        attrs = dict(g.attrs)
    ts = json.loads(attrs.get("track_source", "{}"))
    heading = normalize_heading_deg(float(ts.get("attrs", {}).get("HEADING", 0.0) or 0.0))
    corr = apply_correction(xyz[:, :2], np.full(len(xyz), CLEARANCE_M), inc, heading,
                            crs_is_lonlat=True, set_height=False)
    ll = np.asarray(corr["xyz"], float)[:, :2]
    st, of = project_to_polyline(ll, geom_latlon)
    of = _signed_offset(ll, geom_latlon, of)
    m = (np.abs(of) <= DECK_SEL_M) & (st >= -5.0) & (st <= LENGTH_M + 5.0)
    if m.sum() < 3:
        raise SystemExit(f"데크 ±{DECK_SEL_M:.0f} m 안 점이 {int(m.sum())}개뿐입니다.")
    xyz_c = xyz.copy()
    xyz_c[:, :2] = ll                                 # 보정 좌표를 싣는다
    out = OUT / "_twin_points.h5"
    with h5py.File(out, "w") as o:
        for grp in ("cv", "pinn", "fram"):            # ProjectStore 규약(GROUPS)
            o.create_group(grp)
        gi = o.create_group("insar")
        gi.create_dataset("xyz", data=xyz_c[m])
        for k, v in data.items():
            a = np.asarray(v)
            gi.create_dataset(k, data=(a[m] if a.shape[:1] == (len(m),) else a))
        for k, v in attrs.items():
            gi.attrs[k] = v
        # 계약 meta 의 n_points 는 부분집합 크기로 — 안 고치면 PINN 요약이 2661 로 나온다
        meta = json.loads(attrs.get("meta", "{}"))
        meta["n_points"] = int(m.sum())
        gi.attrs["meta"] = json.dumps(meta, ensure_ascii=False)
        gi.attrs["geolocation_correction"] = json.dumps({
            "applied": True, "dh_m": CLEARANCE_M, "heading_deg": heading,
            "mean_shift_m": float(np.mean(corr["shift_m"])),
            "selection": f"데크 중심선 ±{DECK_SEL_M:.0f} m · 교축 −5~{LENGTH_M + 5:.0f} m"},
            ensure_ascii=False)
    n_on = int(((np.abs(of) <= WIDTH_M / 2) & m).sum())
    print(f"  쉬프트 {np.mean(corr['shift_m']):.2f} m 보정(heading {heading:.2f}°) → "
          f"데크 ±{DECK_SEL_M:.0f} m 안 {int(m.sum())} / {len(xyz)} 점 "
          f"(데크 폭 안 {n_on}) · 속도 {np.nanmin(vel[m]):+.2f}~{np.nanmax(vel[m]):+.2f} mm/yr")
    return out


def chainage_stations(proj: Path, els) -> tuple[list[dict], object]:
    """트윈 점 → PS 재선별(완화+노이즈 제거) → 교축 BIN_M 구간집계 → 축 위 스테이션."""
    from inframon.insar.chainage import build_profile, stations_for_twin

    geom = _deck_geom()
    prof = build_profile(proj, geom, bin_m=BIN_M, mode="relaxed", denoise=True,
                         correct_shift=False,             # 좌표는 이미 보정돼 있다
                         bridge_width_m=WIDTH_M, max_offset_m=20.0)
    deck = next(e for e in els if e.member == "deck")
    z_top = GROUND_M + deck.bbox_max[2]
    return stations_for_twin(prof, geom, z_m=z_top), prof


def derive_pinn_fram(proj: Path, ej: Path, mc, guids, ginfo) -> None:
    """트윈 점으로 PINN(가상센싱) + FRAM(CRI) 을 돌리고 결과를 트윈 위에 얹는다.

    프로젝트는 트윈이 실제로 쓴 12점이다 — 다른 산출물의 값을 가져다 붙이지 않는다.
    """
    from inframon.custom_pinn import run_custom_pinn
    from inframon.structure import BridgeProfile

    # 제원 출처를 사실대로 — 연장·경간수는 파트너 실측 CSV(bridges_specs.csv)에서 좌표
    # 8 m 매칭으로 찾은 정자교 기록이고, 폭은 OSM 보도 간격이다. 'manual' 로 적으면 감사가
    # "실 제원 확인 필요"로 낮춘다 — 확인된 제원이므로 그 출처를 그대로 쓴다.
    prof = BridgeProfile(name=NAME, bridge_type="girder", material="concrete",
                         length_m=LENGTH_M, width_m=WIDTH_M,
                         source="specs_csv:bridges_load.csv, bridges_specs.csv",
                         extra={"n_spans": N_SPANS, "clearance_m": CLEARANCE_M,
                                "match_dist_m": 8.0,
                                "note": "파트너 실측 CSV 정자교(좌표 8 m 매칭) + OSM 보도 간격 폭"})
    summ = run_custom_pinn(proj, LAT, LON, bridge_name=NAME, bridge_profile=prof)
    print(f"      PINN·FRAM: CRI max {summ['cri_global_max']:.3f} · 경보 {summ['warning_level']}"
          f" · 점 {summ['n_points']} × {summ['n_dates']}시점")

    # CRI 채널 트윈 — 같은 결합·같은 고도로
    r = export_insar_gltf(proj, OUT / "twin_cri.glb", value="cri", fram_project=proj,
                          element_guids=guids, element_z=ginfo["element_z"],
                          z_source="deck", element_z_datum=GROUND_M)
    write_web_viewer(OUT / "twin_cri.glb", elements_json=ej, map_conversion=mc, ifc_crs=CRS,
                     stations=json.loads((OUT / "twin_stations.json").read_text(encoding="utf-8"))["stations"],
                     bin_m=BIN_M)
    write_3dtiles_tileset(OUT / "twin_cri.glb")
    print(f"      CRI 트윈: 점 {r['n_points']} · 결합 {r['bound']} · twin_cri.viewer.html")

    figs = _twin_figures(proj)
    figs.append(_chainage_figure(proj))
    figs.append(_brief_figure(proj))
    _write_results_md(proj, summ, figs)
    for f in figs + [OUT / "결과.md", proj]:
        print(f"      {Path(f).relative_to(ROOT)}  {Path(f).stat().st_size:,} B")


def _chainage_figure(proj: Path) -> Path:
    """PS 재선별(ADI 0.25→0.40 완화 + γ≥0.60 + 노이즈 제거) → 교축 10 m 구간집계 → 지점별
    PINN 비교. scripts/make_chainage_figure.py 를 같은 인자로 호출한다."""
    import subprocess

    out = OUT / "twin_chainage.png"
    subprocess.run([sys.executable, str(ROOT / "scripts/make_chainage_figure.py"), str(proj),
                    "--bridge", NAME, "--lat", str(LAT), "--lon", str(LON),
                    "--height", str(CLEARANCE_M), "--width", str(WIDTH_M), "--bin", "10",
                    "--offset", "20", "--out", str(out)], check=True,
                   env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    return out


def _brief_figure(proj: Path) -> Path:
    """건기연 브리프(2026-08-26) 2쪽 형식 — QC 는 속도 95% CI, 교대 기준점, (b) 는 잔차고도
    없음을 명시. scripts/make_brief_figure.py."""
    import subprocess

    out = OUT / "twin_brief.png"
    subprocess.run([sys.executable, str(ROOT / "scripts/make_brief_figure.py"), str(proj),
                    "--bridge", NAME, "--lat", str(LAT), "--lon", str(LON),
                    "--height", str(CLEARANCE_M), "--width", str(WIDTH_M), "--bin", "10",
                    "--offset", "20", "--meta",
                    f"준공 1993 · 연장 {LENGTH_M:.0f} m · 폭 {WIDTH_M} m · {N_SPANS}경간 (표준데이터+OSM)",
                    "--out", str(out)], check=True,
                   env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    return out


def _twin_figures(proj: Path) -> list[Path]:
    """InSAR 시계열 · PINN 가상센싱 변위장 · 성분 분해 · CRI — 트윈 점 기준 그림."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from inframon.insar.chainage import _use_korean_font

    _use_korean_font(plt)
    out = []
    with h5py.File(proj, "r") as f:
        ins, pn, fr = f["insar"], f["pinn"], f["fram"]
        dates = np.asarray(ins["dates"][()], float)
        labels = [x.decode() if isinstance(x, bytes) else str(x) for x in ins["date_labels"][()]]
        los = np.asarray(ins["los"][()], float)
        vel = np.asarray(ins["velocity_mm_yr"][()], float)
        st = np.asarray(ins["deck_station"][()], float)
        vx = np.asarray(pn["vsens_x"][()], float)
        vtot = np.asarray(pn["vsens_total"][()], float)
        vdef = np.asarray(pn["vsens_deflection"][()], float)
        vth = np.asarray(pn["vsens_thermal"][()], float)
        vse = np.asarray(pn["vsens_settle"][()], float)
        van = np.asarray(pn["vsens_anomaly"][()], float)
        cri = np.asarray(fr["CRI"][()], float)
        nf = np.asarray(pn["natural_freq"][()], float)
    yrs = dates / 365.25
    t0, t1 = labels[0], labels[-1]

    # ── 그림 A: InSAR 관측 — 12점 LOS 시계열 + 속도
    fig, ax = plt.subplots(1, 2, figsize=(13, 4), gridspec_kw={"width_ratios": [2.2, 1]})
    order = np.argsort(st)
    cm = plt.get_cmap("viridis")
    for k, i in enumerate(order):
        ax[0].plot(yrs, los[i], lw=.9, color=cm(k / max(len(order) - 1, 1)),
                   label=f"#{i} st {st[i]:.0f} m")
    ax[0].set_xlabel(f"경과 [년]  ({t0} ~ {t1})")
    ax[0].set_ylabel("LOS 변위 [mm]")
    ax[0].set_title(f"InSAR 관측 — 트윈 점 {len(los)}개 × {len(dates)}시점 (SARvey)")
    ax[0].grid(alpha=.3)
    ax[0].legend(fontsize=7, ncol=2, loc="upper left")
    sc = ax[1].scatter(st, vel, c=vel, cmap="RdYlBu_r", ec="k", s=60,
                       vmin=-np.abs(vel).max(), vmax=np.abs(vel).max())
    ax[1].axhline(0, color="#7f8c8d", lw=.8, ls="--")
    ax[1].set_xlabel("교축 거리 [m]")
    ax[1].set_ylabel("LOS 속도 [mm/yr]")
    ax[1].set_title("점별 변위 속도")
    ax[1].grid(alpha=.3)
    fig.colorbar(sc, ax=ax[1], fraction=.05).set_label("mm/yr")
    fig.tight_layout()
    pa = OUT / "twin_insar.png"
    fig.savefig(pa, dpi=130, facecolor="white")
    plt.close(fig)
    out.append(pa)

    # ── 그림 B: PINN 가상센싱 — 전체 변위장(교축 × 시간) + 성분 분해 + 최종 시점 프로파일
    xm = vx * LENGTH_M if vx.max() <= 1.0 + 1e-6 else vx
    fig = plt.figure(figsize=(14, 8))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.15, 1], hspace=.38, wspace=.25)
    ax = fig.add_subplot(gs[0, :])
    im = ax.imshow(vtot.T, aspect="auto", origin="lower", cmap="magma",
                   extent=[xm.min(), xm.max(), yrs.min(), yrs.max()])
    ax.set_xlabel("교축 거리 [m]")
    ax.set_ylabel("경과 [년]")
    ax.set_title(f"PINN 가상센싱 — 상부거더 전체 변위량 |u| [mm]  "
                 f"(가상센서 {len(xm)}점 × {len(dates)}시점, 관측점 {len(los)}개로 학습)")
    fig.colorbar(im, ax=ax, fraction=.025, pad=.01).set_label("mm")
    for s_ in st:
        ax.axvline(s_, color="w", lw=.6, alpha=.5)
    ax.text(.01, .96, "흰 선 = InSAR 관측점 위치", transform=ax.transAxes, color="w",
            fontsize=8, va="top")

    ax = fig.add_subplot(gs[1, 0])
    j = -1
    ax.plot(xm, vdef[:, j], label="처짐(하중)", lw=1.6)
    ax.plot(xm, vth[:, j], label="열팽창", lw=1.6)
    ax.plot(xm, vse[:, j], label="침하", lw=1.6)
    ax.plot(xm, van[:, j], label="이상", lw=1.6)
    ax.plot(xm, vtot[:, j], label="합(전체 변위량)", color="k", lw=2.2)
    ax.set_xlabel("교축 거리 [m]")
    ax.set_ylabel("[mm]")
    ax.set_title(f"성분 분해 — 마지막 시점 {t1}")
    ax.grid(alpha=.3)
    ax.legend(fontsize=8, ncol=3)

    ax = fig.add_subplot(gs[1, 1])
    cmax = cri.max(axis=1)
    sc = ax.scatter(st, cmax, c=cmax, cmap="RdYlGn_r", vmin=0, vmax=1, ec="k", s=70)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("교축 거리 [m]")
    ax.set_ylabel("CRI (최대)")
    ax.set_title(f"FRAM 위험도 — 점별 CRI 최대  ·  f₁ = {nf[0]:.2f} Hz")
    ax.grid(alpha=.3)
    fig.colorbar(sc, ax=ax, fraction=.05).set_label("CRI")
    pb = OUT / "twin_pinn.png"
    fig.savefig(pb, dpi=130, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    out.append(pb)
    return out


def _write_results_md(proj: Path, summ: dict, figs: list[Path]) -> None:
    with h5py.File(proj, "r") as f:
        ins, pn, fr = f["insar"], f["pinn"], f["fram"]
        n, m = ins["los"].shape
        labels = [x.decode() if isinstance(x, bytes) else str(x) for x in ins["date_labels"][()]]
        vel = np.asarray(ins["velocity_mm_yr"][()], float)
        vtot = np.asarray(pn["vsens_total"][()], float)
        strain = np.asarray(pn["strain"][()], float)
        stress = np.asarray(pn["stress"][()], float)
        th = np.asarray(pn["comp_thermal"][()], float)
        nf = np.asarray(pn["natural_freq"][()], float)
        cri = np.asarray(fr["CRI"][()], float)
        inputs = json.loads(pn.attrs.get("inputs", "{}"))
        geo = json.loads(ins.attrs.get("geolocation_correction", "{}"))
        span_yr = float(np.asarray(ins["dates"][()], float)[-1]) / 365.25
    md = f"""# 트윈에서 도출한 InSAR · PINN · CRI — 정자교

트윈이 실제로 쓴 **같은 12점**으로 돌린 결과다. 다른 산출물의 값을 가져다 붙이지 않았다.
다시 만들기: `python scripts/make_demo_twin.py` (6단계). 프로젝트: `twin_project.h5`.

## 입력

| | 값 |
|---|---|
| 관측 | SARvey PS/DS · {n}점 × {m}시점 · {labels[0]} ~ {labels[-1]} |
| 선택 | 쉬프트 {geo.get('mean_shift_m', 0):.2f} m 보정(heading {geo.get('heading_deg', 0):.2f}°) 후 {geo.get('selection', '')} |
| 제원 | 연장 {LENGTH_M:.0f} m · {N_SPANS}경간 · 폭 {WIDTH_M} m · 형하고 {CLEARANCE_M} m (표준데이터 + OSM) |
| PINN 제원 출처 | {inputs.get('profile_source', '?')} |

## InSAR (관측)

![InSAR](twin_insar.png)

| | 값 |
|---|---|
| LOS 속도 | {vel.min():+.2f} ~ {vel.max():+.2f} mm/yr (중앙 {np.median(vel):+.2f}) |
| 관측 기간 | {m}시점 · {span_yr:.1f}년 |

## PINN (가상센싱)

![PINN](twin_pinn.png)

| | 값 | 물리 기준 |
|---|---|---|
| 전체 변위량 |u| 최대 | {np.nanmax(vtot):.2f} mm (가상센서 {vtot.shape[0]}점) | — |
| 열 성분 최대 | {np.nanmax(np.abs(th)):.2f} mm | 0 이면 분리 실패 |
| 변형률 최대 | {np.nanmax(np.abs(strain)):.2e} | 파괴 3e−3 |
| 응력 최대 | {np.nanmax(np.abs(stress)) / 1e6:.3f} MPa | 콘크리트 30~50 |
| f₁ | {nf[0]:.2f} Hz (f₁ × 구조경간 {LENGTH_M / N_SPANS:.1f} m = {nf[0] * LENGTH_M / N_SPANS:.0f}) | 10~600 (감사표 ⑥ 기준) |
| EI | {'식별' if inputs.get('EI_identified') else '설계 제원(기하 EI) 기반 — InSAR 는 상대 변위라 절대 강성 식별 불가'} | ⓘ |

## PS 재선별 → 교축 등간격 프로파일 → 지점별 PINN

![chainage](twin_chainage.png)

| 단계 | 기준 | 결과 |
|---|---|---|
| ① 엄격 | ADI ≤ 0.25 (이 트랙은 ADI 없음 → γ_temp ≥ 0.80 대체) | 1점 · 커버리지 0 % |
| ② 완화+재선별 | ADI ≤ 0.40 ∧ γ_temp ≥ 0.60 → 노이즈 점 제거 | 9 → 8점 |
| ③ 구간집계 | 교축 10 m 등간격 · 중앙값 ± SE | 12구간 중 3구간에 대표값 · 커버리지 25 % |
| ④ 지점별 PINN | PS 구간 대표 누적 vs PINN 가상센서 누적 | 3구간 RMS 2.9 mm · **PS 없는 0~30 m 는 PINN 이 요동** |

**각 지점에 PS 가 있어야 그 지점의 가상센싱을 말할 수 있다** — ④가 그것을 보여준다.
색띠 게이트(커버리지 70 %·σ_b/σ_w 1.5)는 차단이므로 3D 데크에 프로파일을 칠하지 않는다.

## 건기연 브리프 형식 (KICT_2PP, 2026-08-26)

![brief](twin_brief.png)

| 브리프 항목 | 적용 | 결과 |
|---|---|---|
| QC: 속도 불확실도 95% CI ≤ 1.0 mm/yr ∧ γ ≥ 0.4 | ✅ | 유효 8/12 (201시점이라 CI 반폭 0.2 mm/yr) |
| 기준점: 교대 위 안정점 0 mm | 🟡 | 양끝 8 m 구역에 점 없음 → 미적용(사유 기록) |
| (b) DEM 대비 상대고도(잔차고도) | ❌ | SARvey 트랙에 B⊥ 없음. **청양교(SNAP star)는 됨** — `docs/img/brief_chyg.png` (b): 교면 위 11점 vs 밖 37점 차이 **+12.5 ± 6.4 m (z=1.96)**, 형하고 10 m 와 부합 |
| (c) 95% CI 가 0 포함 → 변형 없음 | ✅ | **0/8 — 8점 모두 +0.5~+1.4 mm/yr 로 0 을 벗어남** |
| (d) 중앙값 시계열 · 추세 | ✅ | 추세 +0.74 mm/yr (LOS, 위성 방향 접근) |

정자교 유효 PS 는 관측기간 8.8년 동안 **일관된 양(+)의 LOS 속도**를 보인다. 남측 보도·난간
산란체라는 점, 열·계절 성분이 완전히 제거되지 않았을 가능성, 잔차고도 미확인을 감안해
"구조 변형"으로 단정하지 않는다 — 다만 브리프의 내곡교(추세 0.00)와는 다른 양상이다.

비교: 청양교(`docs/img/brief_chyg.png`)는 25시점/4.8년이라 CI 반폭 중앙 2.5 mm/yr 로
브리프 기준을 1점만 통과한다. 브리프의 각동교·내곡교는 119~148장이었다 — **시점 수가
결정적**이며, 청양교는 확보된 SLC 34장 전부를 쓰는 재처리가 다음 단계다.

## FRAM (위험도)

| | 값 |
|---|---|
| CRI 최대 | **{summ['cri_global_max']:.3f}** |
| 경보 | **{summ['warning_level']}** ({summ.get('warning_basis', '')}) |
| 점별 CRI 최대 범위 | {cri.max(axis=1).min():.3f} ~ {cri.max(axis=1).max():.3f} |

3D 로 보기: `twin_cri.viewer.html` (CRI 채널) · `twin.viewer.html` (속도 채널)

## 읽을 때 주의

- 12점은 데크 남측 보도·난간 위 산란체로 해석된다(1화소급 잔여 오프셋은 배제 못 함).
- 관측점은 교축 28~53 m 에 11점, 94 m 에 1점이다. **그 밖(0~28 m · 55~90 m · 95~108 m)의
  가상센싱 값은 외삽**이라 관측 구간보다 불확실하다. 그림 위 흰 선이 관측점 위치다.
- EI·f₁ 은 관측이 아니라 설계 제원 기반이다. InSAR 가 주는 것은 변위 속도·이상·위험도다.
- CRI 는 FRAM 의 상대 지표다. 이 값만으로 구조 안전을 판정하지 않는다.
"""
    (OUT / "결과.md").write_text(md, encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"[1/6] 실측 제원 → 프록시 부재 → IFC4  ({LENGTH_M:.0f} m · {N_SPANS}경간)")
    ifc, info = build_ifc()
    print(f"      {ifc.relative_to(ROOT)} · 부재 {info['n_elements']} · "
          f"지오참조 {info['target_crs']}")

    print("[2/6] IFC 되읽기 — 왕복 검증")
    els, mc, ej = roundtrip(ifc)
    geom_n = sum(1 for e in els if e.extra.get("bbox_source") == "geometry")
    print(f"      부재 {len(els)} · 형상 AABB {geom_n} · "
          f"배치 E={mc.eastings:.1f} N={mc.northings:.1f} 회전 {mc.rotation_deg:.2f}°")

    print("[3/6] InSAR 점 준비")
    pts = prepare_points(_deck_geom())

    print("[4/6] 점 → 부재 결합(GlobalId) + 데크 레벨")
    guids, ginfo = guid_map_from_alignment(pts, ej, map_conversion=mc, ifc_crs=CRS,
                                           max_dist_m=30.0)
    r = export_insar_gltf(pts, OUT / "twin.glb", value="velocity",
                          element_guids=guids, element_z=ginfo["element_z"],
                          z_source="deck", element_z_datum=GROUND_M)
    g = r["georef"]
    print(f"      점 {r['n_points']} · 결합 {r['bound']} · "
          f"고도 {g['z_source']} → {g.get('deck_z_median_m')} m")

    print("[5/6] 웹뷰어 + 3D Tiles")
    # 교축 1D 투영 → 10 m 구간집계 스테이션 — 데크 중심선 위에 놓는다(투영이 횡방향
    # 오프셋을 버린다). 원 점은 보도·난간 쪽으로 밀려 있어도 대표값의 자리는 축 위다.
    stations, prof = chainage_stations(pts, els)
    v = write_web_viewer(OUT / "twin.glb", elements_json=ej, map_conversion=mc,
                         ifc_crs=CRS, stations=stations, bin_m=BIN_M)
    (OUT / "twin_stations.json").write_text(json.dumps({
        "bridge": NAME, "bin_m": BIN_M, "selection": prof.meta["selection"],
        "n_selected": int(prof.selected.sum()), "coverage": prof.coverage(),
        "publishable": prof.is_publishable()[0], "why": prof.is_publishable()[1],
        "stations": stations}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"      교축 {BIN_M:g} m 구간집계: {prof.meta['selection']} → 선별 "
          f"{int(prof.selected.sum())} · 대표값 {int((prof.bin_n >= 2).sum())}/{prof.bin_n.size} 구간 "
          f"· 커버리지 {prof.coverage() * 100:.0f}%")
    t = write_3dtiles_tileset(OUT / "twin.glb")
    for p in (ifc, ej, OUT / "twin.glb", Path(v["viewer"]), Path(t["tileset"])):
        print(f"      {Path(p).relative_to(ROOT)}  {Path(p).stat().st_size:,} B")
    print(f"      뷰어: 부재 박스 {v['n_boxes']} · "
          f"{'오프라인 가능(three.js 동봉)' if v['offline'] else '인터넷 필요(CDN)'}")

    print("[6/6] PINN 가상센싱 + FRAM CRI → 트윈 위에")
    proj = OUT / "twin_project.h5"
    pts.replace(proj)                                # 트윈 프로젝트로 남긴다(12점 × 201시점)
    derive_pinn_fram(proj, ej, mc, guids, ginfo)
    print(f"\n브라우저로 열기: {(OUT / 'twin.viewer.html')}  ·  CRI: {(OUT / 'twin_cri.viewer.html')}")


if __name__ == "__main__":
    main()
