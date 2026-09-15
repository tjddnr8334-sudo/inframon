#!/usr/bin/env python3
"""KICT 브리핑용 그림 — 한강 교량 여러 개를 **한 장에** 놓고 본다.

교량 하나짜리 그림(`brief.png`·`velocity_map.png`)은 이미 있다. 여러 교량을 나란히
보여 주는 그림이 없어서, 발표에서 "같은 프로그램이 교량을 갈아 끼워도 그대로 돈다"를
말로만 해야 했다. 이 스크립트가 그 한 장을 만든다.

  ① `seoul3_map.png`      — 한강 일대 OSM 위 3 교량 + PS/DS 점(LOS 속도 색) · 확대 3단
  ② `seoul3_summary.png`  — 교량별 LOS 시계열 중앙값·속도 분포 비교

    python scripts/make_seoul_figures.py docs/bridges/성수대교 docs/bridges/한강대교 ...

산출: docs/img/ 아래. 입력은 `bridge_run.py` 가 만든 교량 폴더(bridge.json·project.h5).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from inframon.insar.chainage import _use_korean_font

OUT = ROOT / "docs" / "img"
ACCENT = "#1F6FB2"


def load(folder: Path) -> dict:
    """교량 폴더 하나 → 그림에 필요한 것만."""
    meta = json.loads((folder / "bridge.json").read_text(encoding="utf-8"))
    d: dict = {"name": meta["name"], "meta": meta, "folder": folder}
    p5 = folder / "project.h5"
    if p5.exists():
        with h5py.File(p5, "r") as f:
            d["xyz"] = f["insar/xyz"][()] if "insar/xyz" in f else None
            d["los"] = f["insar/los"][()] if "insar/los" in f else None
            d["vel"] = (f["insar/velocity_mm_yr"][()]
                        if "insar/velocity_mm_yr" in f else None)
            d["dates"] = ([s.decode() for s in f["insar/date_labels"][()]]
                          if "insar/date_labels" in f else [])
            d["cri"] = f["fram/CRI"][()] if "fram/CRI" in f else None
    return d


def _to3857(lon, lat):
    from pyproj import Transformer
    return Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(lon, lat)


# OSM 공용 타일(Mapnik)은 스크립트 UA 를 403 으로 막는다 — 오류를 내지 않고 "Access
# blocked" 글자 타일을 돌려주므로 그림이 조용히 못 쓰게 된다(실제로 그렇게 나왔다).
# 위성영상이 교량·하천을 보여 주기도 더 낫다.
_BASEMAPS = ("Esri.WorldImagery", "Esri.WorldGrayCanvas")


def _basemap(ax, which: int = 0):
    import contextily as ctx
    prov = ctx.providers
    for part in _BASEMAPS[which].split("."):
        prov = prov[part]
    try:
        ctx.add_basemap(ax, source=prov, crs="EPSG:3857", attribution_size=4)
    except Exception as e:                       # noqa: BLE001 — 오프라인이면 점만 남는다
        print("  basemap 실패(오프라인?):", e)
    ax.set_xticks([]); ax.set_yticks([])


def _fit(ax, x0, x1, y0, y1):
    """축의 **물리 종횡비**에 맞춰 지도 범위를 넓힌다.

    웹메르카토르는 x·y 축척이 같아서 범위를 따로 잡으면 지도가 찌그러진다. 그렇다고
    `set_aspect("equal")` 만 걸면 가로로 긴 패널에 정사각 지도가 가운데만 그려지고
    양옆이 흰 띠로 남는다. 좁은 쪽을 넓혀 패널을 꽉 채우면 둘 다 없다.
    """
    fig = ax.figure
    bb = ax.get_position()
    ar = (bb.width * fig.get_figwidth()) / (bb.height * fig.get_figheight())
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hx, hy = max((x1 - x0) / 2, 1.0), max((y1 - y0) / 2, 1.0)
    if hx / hy < ar:
        hx = hy * ar
    else:
        hy = hx / ar
    ax.set_xlim(cx - hx, cx + hx); ax.set_ylim(cy - hy, cy + hy)


def _sym(v):
    a = np.abs(np.asarray(v, float)); a = a[np.isfinite(a)]
    m = float(np.percentile(a, 95)) if a.size else 1.0
    return -max(m, 1e-9), max(m, 1e-9)


def map_figure(bridges: list[dict], out_png: Path) -> None:
    """위: 한강 일대 전경 · 아래: 교량별 확대. 같은 색범위를 공유해 서로 비교된다."""
    vels = np.concatenate([b["vel"] for b in bridges if b.get("vel") is not None])
    vmin, vmax = _sym(vels)

    # 가로로 길쭉하게 — 발표 슬라이드의 표 아래에 들어가야 한다(가용 높이 ~4인치).
    fig = plt.figure(figsize=(15.6, 5.0))
    gs = fig.add_gridspec(2, len(bridges), height_ratios=[1.05, 1.0], hspace=0.16, wspace=0.06)
    top = fig.add_subplot(gs[0, :])

    allx, ally = [], []
    for b in bridges:
        if b.get("xyz") is None:
            continue
        x, y = _to3857(b["xyz"][:, 0], b["xyz"][:, 1])
        b["mx"], b["my"] = np.asarray(x), np.asarray(y)
        allx.append(b["mx"]); ally.append(b["my"])
    AX = np.concatenate(allx); AY = np.concatenate(ally)
    padx = (AX.max() - AX.min()) * 0.10 + 400
    pady = (AY.max() - AY.min()) * 0.55 + 400
    _fit(top, AX.min() - padx, AX.max() + padx, AY.min() - pady, AY.max() + pady)

    for b in bridges:
        geom = b["meta"].get("geometry") or []
        if geom:
            gx, gy = _to3857([p[1] for p in geom], [p[0] for p in geom])
            b["gx"], b["gy"] = np.asarray(gx), np.asarray(gy)
            top.plot(b["gx"], b["gy"], "-", color="#00D26A", lw=5, zorder=4,
                     solid_capstyle="round")
            top.plot(b["gx"], b["gy"], "-", color="black", lw=1.1, zorder=5)
        sc = top.scatter(b["mx"], b["my"], c=b["vel"], cmap="RdBu_r", s=13,
                         vmin=vmin, vmax=vmax, edgecolor="k", linewidth=.2, zorder=6)
        top.annotate(f"{b['name']}  ({b['meta'].get('length_m','?')} m)",
                     (np.median(b["mx"]), np.max(b["my"])),
                     textcoords="offset points", xytext=(0, 12), ha="center",
                     fontsize=11, fontweight="bold", color="#10263D", zorder=7,
                     bbox={"boxstyle": "round,pad=0.25", "fc": "white",
                           "ec": "#10263D", "lw": .8, "alpha": .9})
    _basemap(top)
    n_pts = sum(len(b["mx"]) for b in bridges)
    n_ep = max(len(b.get("dates") or []) for b in bridges)
    top.set_title(f"한강 교량 3개소 — Sentinel-1 ASC path127 · {n_ep}시점 · "
                  f"교면 결합 PS/DS {n_pts}점 (색: LOS 변위속도)", fontsize=13, pad=8)
    cb = fig.colorbar(sc, ax=top, shrink=.85, pad=.01)
    cb.set_label("LOS 변위속도 [mm/yr]", fontsize=9); cb.ax.tick_params(labelsize=8)

    for i, b in enumerate(bridges):
        a = fig.add_subplot(gs[1, i])
        cx, cy = np.median(b["mx"]), np.median(b["my"])
        half = max(np.ptp(b.get("gx", b["mx"])), np.ptp(b.get("gy", b["my"])), 300) * 0.85 + 150
        _fit(a, cx - half, cx + half, cy - half, cy + half)
        if "gx" in b:
            a.plot(b["gx"], b["gy"], "-", color="#00D26A", lw=6, zorder=4, solid_capstyle="round")
            a.plot(b["gx"], b["gy"], "-", color="black", lw=1.1, zorder=5)
        a.scatter(b["mx"], b["my"], c=b["vel"], cmap="RdBu_r", s=52, vmin=vmin, vmax=vmax,
                  edgecolor="k", linewidth=.35, zorder=6)
        _basemap(a, which=0)
        m = b["meta"]
        a.set_title(f"{b['name']} — 교면 ±30 m {len(b['mx'])}점 · "
                    f"{m.get('bridge_type','?')} · {m.get('n_spans','?')}경간",
                    fontsize=10.5, pad=5)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=140, bbox_inches="tight"); plt.close(fig)
    print("wrote", out_png)


def summary_figure(bridges: list[dict], out_png: Path) -> None:
    """(a) 교량별 LOS 시계열 중앙값 · (b) 속도 분포 · (c) CRI 추이 — 나란히 비교."""
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.4))
    colors = ["#1F6FB2", "#E8703A", "#3E8E5A", "#8A5AC4"]

    a = axes[0]
    for b, c in zip(bridges, colors):
        los, dates = b.get("los"), b.get("dates")
        if los is None or not dates:
            continue
        t = np.array([int(d[:4]) + (int(d[4:6]) - 1) / 12 + int(d[6:]) / 365 for d in dates])
        a.plot(t, np.median(los, axis=0), "-", color=c, lw=1.6, label=b["name"])
    a.axhline(0, color="k", lw=.6, ls=":")
    a.set_xlabel("연도"); a.set_ylabel("LOS 변위 [mm]")
    a.set_title("(a) 교면 점 LOS 변위 중앙값 시계열", fontsize=11)
    a.legend(fontsize=9); a.grid(alpha=.25)

    a = axes[1]
    data = [b["vel"] for b in bridges if b.get("vel") is not None]
    names = [b["name"] for b in bridges if b.get("vel") is not None]
    bp = a.boxplot(data, tick_labels=names, patch_artist=True, widths=.55)
    for patch, c in zip(bp["boxes"], colors):
        patch.set_facecolor(c); patch.set_alpha(.35)
    for med in bp["medians"]:
        med.set_color("k")
    a.axhspan(-0.5, 0.5, color="#2E7D32", alpha=.12)
    a.axhline(0, color="k", lw=.6, ls=":")
    a.set_ylabel("LOS 변위속도 [mm/yr]")
    a.set_title("(b) 교면 점 속도 분포 (녹색 = 참고범위 ±0.5 mm/yr)", fontsize=11)
    a.grid(alpha=.25, axis="y")

    a = axes[2]
    for b, c in zip(bridges, colors):
        cri, dates = b.get("cri"), b.get("dates")
        if cri is None or not dates:
            continue
        t = np.array([int(d[:4]) + (int(d[4:6]) - 1) / 12 + int(d[6:]) / 365 for d in dates])
        a.plot(t, np.max(cri, axis=0), "-", color=c, lw=1.6, label=b["name"])
    for y, lab, c in [(0.3, "주의", "#C8A415"), (0.6, "경고", "#E8703A"), (0.85, "위험", "#C03028")]:
        a.axhline(y, color=c, lw=.9, ls="--")
        a.annotate(lab, (a.get_xlim()[0], y), fontsize=8, color=c, va="bottom")
    a.set_ylim(0, 1); a.set_xlabel("연도"); a.set_ylabel("CRI (공진위험지수)")
    a.set_title("(c) CRI 추이 — 참고용 내부 물리지표(시방서 판정 아님)", fontsize=11)
    a.legend(fontsize=9); a.grid(alpha=.25)

    fig.suptitle("서울 한강 3개 교량 — 같은 파이프라인·같은 기준으로 비교",
                 fontsize=14, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_png, dpi=125); plt.close(fig)
    print("wrote", out_png)


def main() -> int:
    _use_korean_font(plt)
    folders = [Path(a) for a in sys.argv[1:]]
    if not folders:
        print(__doc__)
        return 2
    bridges = [load(f) for f in folders]
    usable = [b for b in bridges if b.get("xyz") is not None and len(b["xyz"])]
    if not usable:
        print("✗ project.h5 에 insar/xyz 가 있는 교량이 없습니다")
        return 1
    map_figure(usable, OUT / "seoul3_map.png")
    summary_figure(usable, OUT / "seoul3_summary.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
