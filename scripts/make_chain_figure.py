#!/usr/bin/env python3
"""좌표 하나 → OSM 데크선 → PS 선별 → 트윈 결합, **네 칸으로 한 번에**.

트윈만 보여 주면 "그 점들이 어디서 나왔는지" 가 빠진다. 이 그림은 그 앞 단계를 같이 놓는다.

  ⓐ OSM 데크선 위에 트랙 전체(2만 점)를 얹는다 — 고르기 **전**
  ⓑ 쉬프트를 되돌리고 데크 ±30 m 만 남긴다 — 무엇을, 왜 골랐는지
  ⓒ 종단면 — 고른 점을 슬래브 높이로 올린다
  ⓓ 3D 트윈 — IFC 부재에 GlobalId 로 묶인다

쉬프트가 왜 필요한가
--------------------
지오코딩 산출물의 점은 (1) DEM 지면 표고에 깔려 있고 (2) 형하고 때문에 δh/tanθ 만큼
레인지 방향으로 밀려 있다. 되돌리지 않으면 교면 점이 데크선 옆 강물에 찍힌다 —
파이프라인이 ④ 단계에서 하는 그 보정을 여기서 눈으로 보인다(같은 코드를 부른다).

    python scripts/make_chain_figure.py --bridges 암사대교 성수대교
    python scripts/make_chain_figure.py            # docs/bridges/hangang16.json 전부

산출: docs/bridges/<교량>/chain.png
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle

from inframon.insar.chainage import _signed_offset, _use_korean_font
from inframon.insar.deck_geometry import project_to_polyline

from inframon.insar.track_reader import normalize_heading_deg

from make_twin_ps_figure import (MEMBER_COLOR, MEMBER_KO, MEMBER_ORDER,  # noqa: E402
                                 _draw_elements, panel3d, read_twin)

GRAY, RED, BLUE, NAVY = "#8A8F96", "#C03028", "#1F6FB2", "#123A5E"
DECK_SEL_M = 30.0


def read_track(b: dict, *, radius_m: float = 2500.0) -> dict | None:
    """트랙 원시 점 → 쉬프트 보정 전/후 좌표와 데크 선별 마스크.

    쉬프트 크기는 **산출물에 적힌 값**(`sources.points` 의 "쉬프트 N m")을 쓴다.
    파이프라인은 ④ 점 선택에서 그때의 형하고로 보정하는데, 그 뒤 ⑤ 잔차고도가
    형하고를 덮어쓴다(암사대교 27 m → 8.5 m). 지금 값으로 다시 계산하면 그때와 다른
    쉬프트가 나와 선별 점 수가 어긋난다 — 그러니 기록된 값을 그대로 되돌린다.
    부호(어느 쪽으로 밀렸는지)는 두 방향을 다 해 보고 기록된 점 수에 맞는 쪽을 쓴다.
    """
    tp = Path(b.get("track") or "")
    geom = b.get("geometry") or []
    if not tp.exists() or len(geom) < 2:
        return None
    with h5py.File(tp, "r") as f:
        ll_all = np.asarray(f["pixel_lonlat"][()], float)
        heading = normalize_heading_deg(float(f.attrs.get("HEADING", 0.0) or 0.0))
        los_all = np.asarray(f["los_mm"][()], float)
        days = np.asarray(f["epochs"][()], float)

    lat0 = float(np.mean([p[0] for p in geom]))
    k = math.cos(math.radians(lat0))
    near = (np.hypot((ll_all[:, 0] - b["lon"]) * k, ll_all[:, 1] - b["lat"]) * 111_320
            <= radius_m)
    ll, los = ll_all[near], los_all[near]

    m = re.search(r"쉬프트\s*([0-9.]+)\s*m", (b.get("sources") or {}).get("points", ""))
    shift_m = float(m.group(1)) if m else 0.0
    m2 = re.search(r"안\s*(\d+)\s*/", (b.get("sources") or {}).get("points", ""))
    n_rec = int(m2.group(1)) if m2 else None

    look = math.radians(heading + 90.0)               # 우측 관측 — 레인지 방향
    dx, dy = math.sin(look) * shift_m, math.cos(look) * shift_m
    best = None
    for sgn in (1.0, -1.0):
        ll1 = ll + np.array([sgn * dx / (111_320 * k), sgn * dy / 111_320])
        st, of = project_to_polyline(ll1, geom)
        of = _signed_offset(ll1, geom, of)
        sel = (np.abs(of) <= DECK_SEL_M) & (st >= -5) & (st <= (b.get("length_m") or 0) + 5)
        score = (abs(int(sel.sum()) - n_rec) if n_rec else -int(sel.sum()))
        if best is None or score < best[0]:
            best = (score, ll1, st, of, sel)
    _, ll1, st, of, sel = best

    t = (days - days.min()) / 365.25
    A = np.vstack([np.ones_like(t), t]).T
    c, *_ = np.linalg.lstsq(A, los.T, rcond=None)
    return {"raw": ll, "corr": ll1, "st": st, "of": of, "sel": sel,
            "vel": np.asarray(c[1], float), "heading": heading,
            "n_all": int(len(ll_all)), "n_near": int(near.sum()),
            "n_sel": int(sel.sum()), "n_rec": n_rec, "shift_m": shift_m}


def _zoom(ax, G: np.ndarray, pad: float = 320.0) -> None:
    """데크선 둘레만 — 트랙 전체(반경 2.5 km)를 다 보이면 교량이 점이 된다."""
    ax.set_xlim(G[:, 0].min() - pad, G[:, 0].max() + pad)
    ax.set_ylim(G[:, 1].min() - pad, G[:, 1].max() + pad)


def _to_m(ll: np.ndarray, lat0: float, lon0: float) -> np.ndarray:
    k = math.cos(math.radians(lat0))
    return np.stack([(ll[:, 0] - lon0) * 111_320 * k, (ll[:, 1] - lat0) * 110_540], 1)


def figure(name: str, b: dict, tr: dict, tw: dict | None, out: Path) -> None:
    geom = np.asarray(b["geometry"], float)
    lat0, lon0 = float(geom[:, 0].mean()), float(geom[:, 1].mean())
    G = _to_m(geom[:, ::-1], lat0, lon0)
    raw, cor = _to_m(tr["raw"], lat0, lon0), _to_m(tr["corr"], lat0, lon0)
    sel = tr["sel"]
    vmax = float(np.nanpercentile(np.abs(tr["vel"][sel]), 95)) if sel.any() else 3.0
    vmax = max(vmax, 0.5)

    fig = plt.figure(figsize=(17.2, 9.4))
    gs = fig.add_gridspec(2, 2, left=0.055, right=0.985, top=0.855, bottom=0.075,
                          hspace=0.34, wspace=0.20)

    # ⓐ 고르기 전 — 트랙 전체 + OSM 데크선
    a = fig.add_subplot(gs[0, 0])
    a.plot(raw[:, 0], raw[:, 1], "x", ms=2.6, color="#C3CBD3", mew=0.6,
           label=f"트랙 원시 점 {tr['n_all']:,}점 중 이 창 안 (쉬프트 전)")
    a.plot(G[:, 0], G[:, 1], "-", lw=2.6, color=NAVY, label="OSM 데크선(교량 중심선)")
    a.set_aspect("equal", adjustable="datalim")
    a.set_title("ⓐ 고르기 전 — 트랙 전체를 OSM 데크선 위에 얹는다\n"
                "점이 데크선 옆으로 치우쳐 있다(지오코딩 쉬프트)", fontsize=11, pad=6)
    a.set_xlabel("동 [m]", fontsize=9); a.set_ylabel("북 [m]", fontsize=9)
    a.legend(fontsize=8.4, framealpha=.92); a.grid(alpha=.22); a.tick_params(labelsize=8)
    _zoom(a, G)

    # ⓑ 되돌리고 고른다
    bx = fig.add_subplot(gs[0, 1])
    bx.plot(raw[~sel][:, 0], raw[~sel][:, 1], "x", ms=2.2, color="#DCE2E8", mew=0.5)
    bx.plot(G[:, 0], G[:, 1], "-", lw=2.6, color=NAVY, zorder=3)
    for s in (-DECK_SEL_M, DECK_SEL_M):                 # ±30 m 선별 띠
        n = np.array([-(G[-1] - G[0])[1], (G[-1] - G[0])[0]])
        n = n / (np.hypot(*n) or 1.0)
        bx.plot(G[:, 0] + n[0] * s, G[:, 1] + n[1] * s, "--", lw=1.0, color=BLUE,
                zorder=2)
    sc = bx.scatter(cor[sel][:, 0], cor[sel][:, 1], c=tr["vel"][sel], cmap="RdYlBu_r",
                    vmin=-vmax, vmax=vmax, s=16, ec="#111", lw=.35, zorder=5)
    bx.set_aspect("equal", adjustable="datalim")
    bx.set_title(f"ⓑ 쉬프트 {tr['shift_m']:.0f} m 되돌리고 데크 ±{DECK_SEL_M:.0f} m 만\n"
                 f"{tr['n_all']:,}점 → {tr['n_sel']}점 (heading {tr['heading']:.1f}°)",
                 fontsize=11, pad=6)
    bx.set_xlabel("동 [m]", fontsize=9)
    bx.legend(handles=[plt.Line2D([], [], ls="--", color=BLUE, label="데크 ±30 m 선별 띠"),
                       plt.Line2D([], [], ls="", marker="x", color="#C3CBD3",
                                  label="버린 점")],
              fontsize=8.4, framealpha=.92)
    bx.grid(alpha=.22); bx.tick_params(labelsize=8); _zoom(bx, G)
    cb = fig.colorbar(sc, ax=bx, fraction=0.035, pad=0.015)
    cb.set_label("LOS 변위속도 [mm/yr]", fontsize=9)

    # ⓒ 종단면 · ⓓ 3D — 트윈에서 그대로
    c = fig.add_subplot(gs[1, 0])
    if tw:
        _draw_elements(c, tw["elements"], axis="elev")
        c.scatter(tw["u"], tw["h"], c=tw["val"], cmap="RdYlBu_r",
                  vmin=tw["vmin"], vmax=tw["vmax"], s=22, ec="#111", lw=.45, zorder=5)
        c.set_title("ⓒ 종단면 — 고른 점을 슬래브 높이로 올린다\n"
                    "DEM 지면에 깔려 있던 점이 교면에 앉는다", fontsize=11, pad=6)
        c.set_xlabel("교축 거리 [m]", fontsize=9); c.set_ylabel("표고 [m]", fontsize=9)
        c.grid(alpha=.22); c.tick_params(labelsize=8)
    else:
        c.axis("off")

    d = fig.add_subplot(gs[1, 1], projection="3d")
    if tw:
        panel3d(d, tw, s=20, leaders=False)
        n_mem = {}
        for e in tw["elements"]:
            n_mem[e["member"]] = n_mem.get(e["member"], 0) + 1
        d.set_title("ⓓ 3D 트윈 — IFC 부재에 GlobalId 로 묶는다\n"
                    f"PS {tw['n_points']}점 중 {tw['n_bound']}점 결합", fontsize=11,
                    pad=0, y=1.03)
        d.legend(handles=[Patch(color=MEMBER_COLOR[k], alpha=.5, label=MEMBER_KO[k])
                          for k in MEMBER_ORDER if k in n_mem],
                 fontsize=7.4, loc="lower left", bbox_to_anchor=(-0.04, -0.04),
                 ncol=2, framealpha=.9)
        d.tick_params(labelsize=6.0, pad=-3)
    else:
        d.set_axis_off()

    fig.suptitle(f"{name} — 좌표 하나에서 트윈 위 측점까지, 중간을 빼지 않고",
                 fontsize=15, fontweight="bold", y=0.965)
    fig.text(0.008, 0.012,
             "ⓐ→ⓑ 선별은 파이프라인 ④단계와 **같은 코드**(geolocation.apply_correction + "
             "deck_geometry.project_to_polyline). ⓒ→ⓓ 는 트윈 산출물을 그대로 읽는다 — "
             "발표용으로 다시 그리지 않는다.", fontsize=8.6, color=GRAY)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=135)
    plt.close(fig)
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--list", default="docs/bridges/hangang16.json")
    ap.add_argument("--bridges", nargs="*", default=None)
    a = ap.parse_args()
    _use_korean_font(plt)

    root = Path(a.root)
    names = a.bridges or [x["name"] for x in
                          json.loads(Path(a.list).read_text(encoding="utf-8"))]
    ok = 0
    for nm in names:
        bj = root / nm / "bridge.json"
        if not bj.exists():
            print(f"{nm}: bridge.json 없음")
            continue
        b = json.loads(bj.read_text(encoding="utf-8"))
        tr = read_track(b)
        if tr is None or tr["n_sel"] == 0:
            print(f"{nm}: 트랙이 없거나 선별 점 0 — 건너뜀")
            continue
        figure(nm, b, tr, read_twin(root / nm), root / nm / "chain.png")
        ok += 1
    print(f"\n{ok}/{len(names)}개소")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
