#!/usr/bin/env python3
"""README 그림 — **InSAR 점을 교량 위에 올린 결과**(정자교, Sentinel-1 실데이터).

"BIM 에 InSAR 를 올렸다"가 실제로 무엇을 뜻하는지 한 장으로 보인다. 지오코딩 산출물을
그대로 두면 점은 (a) DEM 지면 표고에 깔리고 (b) δh/tanθ 만큼 밀려 있다. 둘 다 되돌려야
점이 데크 위에 앉는다.

  ① 평면   — 차도 중심선 + 양측 보도(OSM), 쉬프트 보정 전/후. 점이 **남측 보도선을 따라**
              늘어선다 — 매끈한 노면이 아니라 보도·난간이 산란체라는 뜻이다.
  ② 종단면 — 이 그림의 핵심. DEM 지면에 깔린 점(회색)과 데크 레벨로 올린 점(색).
              교각·교대는 표준데이터 실측 제원(연장 108m·5경간)으로 세운 프록시.
  ③ 3D     — 같은 것을 트윈에서 본 모습. 부재 위에 점이 앉는다.

폭은 추정하지 않는다. OSM 양측 보도 중심선 간격(23.5 m)이 구조물 폭의 하한이고,
보도 폭 절반씩을 더해 데크 폭으로 쓴다 — 근거가 그림에 함께 표시된다.

    python scripts/make_ondeck_figure.py

산출: docs/img/ondeck_jeongjagyo.png
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from inframon.bim.proxy_model import bridge_elements                    # noqa: E402
from inframon.insar.chainage import _signed_offset, _use_korean_font    # noqa: E402
from inframon.insar.deck_geometry import project_to_polyline            # noqa: E402
from inframon.insar.deck_shift import for_bridge                        # noqa: E402
from inframon.insar.geolocation import apply_correction                 # noqa: E402
from inframon.insar.osm_bridge import _overpass_query                   # noqa: E402

PROJ = ROOT / "data/jeongjagyo_real.h5"
DECK = ROOT / "data/jeongjagyo_f120/deck_polyline.json"   # 없으면 OSM 에서 받아 여기 캐시
OUT = ROOT / "docs/img/ondeck_jeongjagyo.png"

OSM_ROAD_WAY = 51473197            # 정자교 차도
OSM_LAT, OSM_LON = 37.36854, 127.10900

# 전국교량표준데이터(data.go.kr 15081953) 정자교 — OSM way 51473197 중심에서 8m
LENGTH_M, N_SPANS, MAX_SPAN_M = 108.0, 5, 27.0
FOOTWAY_HALF_M = 1.5   # 보도 중심선 → 구조물 외측. 보도 폭 3 m 가정
CLEARANCE_M = 6.0      # 탄천 위 형하고 — 표준데이터에 교량높이가 없어 가정(그림에 명시)
HEADING_DEG = -13.249       # SARvey 트랙의 −0.2312 는 라디안 — 도로 환산(track_reader.normalize_heading_deg)

C_DECK, C_PIER, C_ABUT = "#4a6b8a", "#8a8f96", "#7a8288"


def fetch_deck() -> dict:
    """OSM 에서 차도 중심선 + 양측 보도를 받는다 — 데크 폭의 근거가 보도 간격이다."""
    q = (f"[out:json][timeout:30];"
         f"way(around:120,{OSM_LAT},{OSM_LON})[\"bridge\"];out geom tags;")
    els = _overpass_query(q)["elements"]
    out: dict = {"road": None, "footways": []}
    for el in els:
        t = el.get("tags", {})
        g = [(x["lat"], x["lon"]) for x in el.get("geometry", []) if "lat" in x]
        if len(g) < 2:
            continue
        rec = {"osm_way": el["id"], "name": t.get("name"), "highway": t.get("highway"),
               "lanes": t.get("lanes"), "geometry": [list(p) for p in g]}
        if t.get("highway") == "footway":
            out["footways"].append(rec)
        elif el["id"] == OSM_ROAD_WAY:
            out["road"] = rec
    if out["road"] is None or len(out["footways"]) < 2:
        raise SystemExit(f"OSM 에서 정자교(way {OSM_ROAD_WAY}) 차도+보도 2개를 찾지 못했습니다.")
    P = np.asarray(out["road"]["geometry"], float)
    lat0 = float(P[:, 0].mean())
    xy = np.column_stack([P[:, 1] * np.cos(np.radians(lat0)), P[:, 0]]) * 111_320.0
    out["road"]["length_m"] = float(np.sum(np.linalg.norm(np.diff(xy, axis=0), axis=1)))
    offs = []
    for fw in out["footways"]:
        F = np.asarray(fw["geometry"], float)
        fw["offset_m"] = float(np.mean(F[:, 0] - lat0) * 111_320.0)   # 위도차(부호 규약 다름)
        offs.append(fw["offset_m"])
    out["structure_width_m"] = float(max(offs) - min(offs))
    out["note"] = ("차도 중심선 + 양측 보도. 보도 간격이 구조물 실폭의 하한 — "
                   "PS 산란체는 노면이 아니라 보도·난간에 생긴다.")
    DECK.parent.mkdir(parents=True, exist_ok=True)
    DECK.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def load() -> dict:
    if not PROJ.exists():
        raise SystemExit(
            f"실데이터가 없습니다: {PROJ}\n"
            "  정자교 SARvey 산출물(2,661점 × 201에폭)이 필요합니다 — "
            "docs/실데이터_런북.md 참조.")
    deck = json.loads(DECK.read_text(encoding="utf-8")) if DECK.exists() else fetch_deck()
    road = deck["road"]
    with h5py.File(PROJ, "r") as h:
        g = h["insar"]
        xyz = np.asarray(g["xyz"][()], float)
        return {"geom": [tuple(p) for p in road["geometry"]],
                "deck_len": float(road["length_m"]),
                "footways": deck["footways"],
                "width_m": float(deck["structure_width_m"]) + 2 * FOOTWAY_HALF_M,
                "lonlat": xyz[:, :2], "dem_z": xyz[:, 2],
                "vel": np.asarray(g["velocity_mm_yr"][()], float),
                "inc": np.asarray(g["incidence_deg"][()], float)}


def _box(ax, lo, hi, color, alpha):
    """3D 축에 직육면체 하나 — 부재 프록시를 그린다."""
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [[v[0], v[1], v[2], v[3]], [v[4], v[5], v[6], v[7]],
             [v[0], v[1], v[5], v[4]], [v[2], v[3], v[7], v[6]],
             [v[1], v[2], v[6], v[5]], [v[0], v[3], v[7], v[4]]]
    ax.add_collection3d(Poly3DCollection(faces, facecolor=color, edgecolor="#2c3e50",
                                         linewidths=.45, alpha=alpha))


def main() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    d = load()
    geom, width = d["geom"], d["width_m"]
    half = width / 2
    inc_med = float(np.median(d["inc"]))

    # ── 쉬프트: 데크 방위 × LOS 기하 → 종/횡 성분과 관측가능성
    gsh = for_bridge(geom, heading_deg=HEADING_DEG, incidence_deg=inc_med,
                     dh_m=CLEARANCE_M, width_m=width)
    corr = apply_correction(d["lonlat"], np.full(len(d["lonlat"]), CLEARANCE_M),
                            d["inc"], HEADING_DEG, crs_is_lonlat=True, set_height=False)
    ll1 = np.asarray(corr["xyz"], float)[:, :2]

    st0, of0 = project_to_polyline(d["lonlat"], geom)
    st1, of1 = project_to_polyline(ll1, geom)
    of0 = _signed_offset(d["lonlat"], geom, of0)   # 좌/우 부호 — 폭 방향 분포가 보인다
    of1 = _signed_offset(ll1, geom, of1)
    a1 = np.abs(of1)
    buf = float(gsh.buffer_m)                      # 반폭 + 화소 절반(위치 불확실성)
    in_span = (st1 >= -2.0) & (st1 <= d["deck_len"] + 2.0)
    on = (a1 <= half) & in_span                    # 데크 폭 안 — 확실히 교량 위
    assoc = (a1 <= buf) & in_span & ~on            # 버퍼 안 — 화소 불확실성 고려
    both = on | assoc
    near = a1 <= 60.0

    ground = float(np.median(d["dem_z"][near])) if near.any() else float(np.median(d["dem_z"]))
    els = bridge_elements(length_m=LENGTH_M, width_m=width, n_spans=N_SPANS,
                          clearance_m=CLEARANCE_M, name="정자교")
    deck_el = next(e for e in els if e.member == "deck")
    deck_top = ground + deck_el.bbox_max[2]
    # 보도 오프셋도 점과 **같은 투영·부호 규약**으로 구한다. JSON 의 offset_m 은 위도차라
    # 부호가 반대여서 그대로 쓰면 보도선이 점 반대쪽에 그려진다.
    fw_off = []
    for f in d["footways"]:
        fg = np.asarray(f["geometry"], float)
        fll = np.column_stack([fg[:, 1], fg[:, 0]])          # [lat,lon] → [lon,lat]
        _, fo = project_to_polyline(fll, geom)
        fw_off.append(float(np.median(_signed_offset(fll, geom, fo))))

    _use_korean_font(plt)
    fig = plt.figure(figsize=(17.2, 4.9))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.15, 1.0], wspace=.27)
    vlim = float(np.nanpercentile(np.abs(d["vel"][near]), 95)) or 1.0
    cmap, norm = "RdYlBu_r", plt.Normalize(-vlim, vlim)

    # ① 평면 — 쉬프트 보정 전/후, 보도선 대비
    ax = fig.add_subplot(gs[0])
    ax.axhspan(-buf, buf, color="#7f8c9a", alpha=.08, zorder=0)
    ax.axhspan(-half, half, color="#7f8c9a", alpha=.20, zorder=0)
    ax.plot([0, d["deck_len"]], [0, 0], color="#34495e", lw=4.0, zorder=2,
            label="차도 중심선 (OSM)")
    for i, y in enumerate(sorted(fw_off)):
        ax.plot([0, d["deck_len"]], [y, y], color="#16a085", lw=1.6, ls="-",
                zorder=2, label="양측 보도 (OSM)" if i == 0 else None)
    for y in (-buf, buf):
        ax.axhline(y, color="#7f8c9a", lw=.8, ls=":", zorder=1)
    m = near
    ax.scatter(st0[m], of0[m], s=14, c="#b7bec5", marker="x", lw=.85,
               label=f"보정 전 (n={int(m.sum())})", zorder=3)
    sc = ax.scatter(st1[m], of1[m], s=28, c=d["vel"][m], cmap=cmap, norm=norm,
                    ec="#2c3e50", lw=.35, zorder=4, label="보정 후")
    ax.scatter(st1[assoc], of1[assoc], s=95, facecolors="none", ec="#5d6d7e",
               lw=1.3, ls="--", zorder=5, label=f"버퍼 ±{buf:.0f} m ({int(assoc.sum())})")
    ax.scatter(st1[on], of1[on], s=118, facecolors="none", ec="#111",
               lw=1.7, zorder=6, label=f"데크 ±{half:.0f} m ({int(on.sum())})")
    ax.set_xlim(-18, d["deck_len"] + 18)
    ax.set_ylim(-45, 45)
    ax.set_xlabel("교축 거리 [m]")
    ax.set_ylabel("교축 직각 거리 [m]")
    ax.set_title(f"① 평면 — 쉬프트 {np.mean(corr['shift_m']):.1f} m 되돌림 "
                 f"(종축 {gsh.along_m:+.1f} · 횡축 {gsh.cross_m:+.1f} m)\n"
                 f"점이 남측 보도선을 따라 늘어선다 — 노면이 아닌 보도·난간이 산란체",
                 fontsize=9.5)
    ax.legend(fontsize=7, loc="lower right", framealpha=.93, ncol=2)
    ax.grid(alpha=.25, lw=.5)

    # ② 종단면 — 이 그림의 핵심
    ax = fig.add_subplot(gs[1])
    ax.axhspan(ground - 10, ground, color="#c9baa0", alpha=.5, zorder=0)
    ax.axhline(ground, color="#8a7a5c", lw=1.2, zorder=1)
    ax.text(-16, ground - 5.2, f"지면 DEM\n{ground:.0f} m", fontsize=8.5, color="#6b5d45")
    for e in els:
        x0, _, z0 = e.bbox_min
        x1, _, z1 = e.bbox_max
        col = {"deck": C_DECK, "pier": C_PIER, "abutment": C_ABUT}[e.member]
        ax.add_patch(Rectangle((x0 + LENGTH_M / 2, ground + z0), x1 - x0, z1 - z0,
                               fc=col, ec="#2c3e50", lw=.7,
                               alpha=.92 if e.member == "deck" else .72, zorder=2))
    ax.scatter(st1[near], d["dem_z"][near], s=18, c="#aeb6bd", marker="v",
               label="보정 전 — DEM 지면 표고에 깔림", zorder=3)
    ax.scatter(st1[assoc], np.full(int(assoc.sum()), deck_top), s=78, c=d["vel"][assoc],
               cmap=cmap, norm=norm, ec="#5d6d7e", lw=1.2, zorder=5,
               label=f"버퍼 결합 ({int(assoc.sum())}점)")
    ax.scatter(st1[on], np.full(int(on.sum()), deck_top), s=122, c=d["vel"][on],
               cmap=cmap, norm=norm, ec="#111", lw=1.2, zorder=6,
               label=f"데크 위 {deck_top:.0f} m ({int(on.sum())}점)")
    ax.annotate("", xy=(LENGTH_M * .70, deck_top - 1.4),
                xytext=(LENGTH_M * .70, ground + 1.0),
                arrowprops=dict(arrowstyle="-|>", color="#c0392b", lw=2.0), zorder=7)
    ax.text(LENGTH_M * .73, (ground + deck_top) / 2,
            f"+{deck_top - ground:.1f} m\n형하고 + 형고", fontsize=8.5,
            color="#c0392b", va="center")
    ax.set_xlim(-18, LENGTH_M + 18)
    ax.set_ylim(ground - 10, deck_top + 12)
    ax.set_xlabel("교축 거리 [m]")
    ax.set_ylabel("표고 [m]")
    ax.set_title(f"② 종단면 — 점을 데크 레벨로 올린다\n"
                 f"프록시 부재: 연장 {LENGTH_M:.0f} m · {N_SPANS}경간 "
                 f"(전국교량표준데이터 실측)", fontsize=9.5)
    ax.legend(fontsize=7, loc="upper left", framealpha=.93)
    ax.grid(alpha=.25, lw=.5)

    # ③ 3D — 트윈에서 본 모습
    ax = fig.add_subplot(gs[2], projection="3d")
    for e in els:
        lo = (e.bbox_min[0] + LENGTH_M / 2, e.bbox_min[1], ground + e.bbox_min[2])
        hi = (e.bbox_max[0] + LENGTH_M / 2, e.bbox_max[1], ground + e.bbox_max[2])
        col = {"deck": C_DECK, "pier": C_PIER, "abutment": C_ABUT}[e.member]
        _box(ax, lo, hi, col, .5 if e.member == "deck" else .38)
    for x, y in zip(st1[both], of1[both]):        # 데크면까지 수직 지시선
        ax.plot([x, x], [y, y], [deck_top, deck_top + 1.2], color="#34495e",
                lw=.7, zorder=9)
    ax.scatter(st1[both], of1[both], np.full(int(both.sum()), deck_top + 1.2),
               s=np.where(on[both], 100, 58), c=d["vel"][both], cmap=cmap, norm=norm,
               ec="#111", lw=.85, depthshade=False, zorder=10)
    ax.set_xlim(-6, LENGTH_M + 6)
    ax.set_ylim(-half - 4, half + 4)
    ax.set_zlim(ground - 1, deck_top + 6)
    ax.set_box_aspect((3.0, 1.0, 0.66))
    ax.view_init(elev=25, azim=-64)
    ax.set_xlabel("교축 [m]", fontsize=8, labelpad=1)
    ax.set_ylabel("횡축 [m]", fontsize=8, labelpad=-6)
    ax.set_zlabel("표고 [m]", fontsize=8, labelpad=-6)
    ax.tick_params(labelsize=6.2, pad=-2)
    ax.set_zticks([int(ground), int(deck_top)])
    ax.set_yticks([-int(half), 0, int(half)])
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.set_alpha(.06)
    ax.grid(False)
    ax.set_title(f"③ 3D 트윈 — 부재 위에 점이 앉는다\n"
                 f"{int(both.sum())}점이 GlobalId 로 부재에 결합", fontsize=9.5)

    cb = fig.colorbar(sc, ax=fig.axes, fraction=.012, pad=.055)
    cb.set_label("LOS 변위속도 [mm/yr]", fontsize=9)
    fig.suptitle("정자교 (성남 분당 · OSM way 51473197) — Sentinel-1 실데이터를 교량 위에 올린다   ·   "
                 "2,661점 × 201에폭 (2017-02 ~ 2025-12, SARvey)", fontsize=11.5, y=1.04)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=145, bbox_inches="tight", facecolor="white")

    print(f"저장: {OUT}")
    print(f"  데크 폭 {width:.1f} m (OSM 보도 간격 {width - 2 * FOOTWAY_HALF_M:.1f} m "
          f"+ 보도 반폭 ×2)")
    print(f"  전체 {len(st1)}점 · 60 m 안 {int(near.sum())}점 · "
          f"데크 ±{half:.1f} m {int(on.sum())}점 · 버퍼 ±{buf:.1f} m {int(assoc.sum())}점 "
          f"→ 결합 {int(both.sum())}점")
    print(f"  지면 {ground:.1f} m → 데크 상단 {deck_top:.1f} m (+{deck_top - ground:.1f} m)")
    print(f"  쉬프트 {np.mean(corr['shift_m']):.2f} m "
          f"(종축 {gsh.along_m:+.2f} · 횡축 {gsh.cross_m:+.2f}) · "
          f"관측가능성 {gsh.observability:.3f}")


if __name__ == "__main__":
    main()
