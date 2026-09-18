#!/usr/bin/env python3
"""교량 변위 속도와 FRAM 공명 위험 지수를 **3D 로** 그린다 — 산출물에서 직접.

발표자료에 쓸 그림이지만 숫자를 손으로 옮기지 않는다. `project.h5` 의
`insar/xyz` · `insar/velocity_mm_yr` · `fram/CRI` 를 그대로 읽어 같은 기하 위에
색만 바꿔 두 장을 낸다. 같은 점, 같은 시점(視點) — 그래야 "속도는 잠잠한데
공명 지수는 높다" 같은 말이 그림으로 확인된다.

부재 윤곽(데크·주탑·교각)은 `<교량>_elements.json` 의 bbox 에서 가져온다. 점만
띄워 놓으면 그게 교량 위인지 아닌지 알 수 없기 때문이다.

    python scripts/make_value_3d.py --bridges 올림픽대교 가양대교

산출: docs/img/value/3D_변위속도_<교량>.png · 3D_FRAM_CRI_<교량>.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from kaia_theme import MPL, use_mpl_style

# 발표자료(KAIA 톤)와 같은 서체·색을 쓴다 — 한 장에 붙였을 때 따로 놀지 않게.
use_mpl_style()

ROOT = Path(__file__).resolve().parent.parent
NAVY = MPL["ink"]
DIM = MPL["gray"]

# 부재별 윤곽 색 — 트윈 뷰어와 같은 계열로 맞춘다.
MEMBER_COLOR = {
    "deck": "#9AA7B4", "girder": "#9AA7B4", "slab": "#9AA7B4",
    "pier": "#7E8B98", "pylon": "#5C6B7A", "cable": "#C3CDD6",
    "abutment": "#7E8B98", "arch": "#5C6B7A", "truss": "#8C99A6",
}


def deck_top(folder: Path, name: str) -> float:
    """IFC 부재에서 교면 높이를 읽는다 — 점을 띄울 z 가 필요하다."""
    ej = folder / f"{name}_elements.json"
    if not ej.exists():
        return 0.0
    try:
        els = json.loads(ej.read_text(encoding="utf-8")).get("elements", [])
    except (OSError, json.JSONDecodeError):
        return 0.0
    z = [e["bbox_max"][2] for e in els
         if e.get("member") in ("deck", "girder", "slab") and e.get("bbox_max")]
    return float(np.median(z)) if z else 0.0


def load(folder: Path):
    """project.h5 → **로컬 교축 좌표** · 속도 · CRI. 없는 건 None 으로 돌려준다.

    `insar/xyz` 는 경위도(z=0)라 IFC 부재(교량 중앙이 원점인 미터 좌표)와 그대로는
    겹치지 않는다. 데크 방위로 회전시켜 교축·교축직각 미터로 바꾸고, 높이는 IFC
    교면 상면을 쓴다 — 점 자체에는 고도가 없으므로 지어내지 않고 교면에 붙인다.
    """
    import h5py

    p5 = folder / "project.h5"
    if not p5.exists():
        return None
    with h5py.File(p5, "r") as f:
        if "insar/xyz" not in f:
            return None
        xyz = np.asarray(f["insar/xyz"][()], float)
        st = (np.asarray(f["insar/deck_station"][()], float)
              if "insar/deck_station" in f else None)
        vel = (np.asarray(f["insar/velocity_mm_yr"][()], float)
               if "insar/velocity_mm_yr" in f else None)
        cri = (np.asarray(f["fram/CRI"][()], float)
               if "fram/CRI" in f else None)
        # CRI 는 (점, 시점) 이다. 한 장에 그리려면 시간축을 줄여야 하는데, 최대값을
        # 쓰면 잡음 한 번에 전부 붉어진다 — 시간 중앙값을 쓰고 그렇게 적는다.
        if cri is not None and cri.ndim == 2:
            cri = np.nanmedian(cri, axis=1)
        mem = ([s.decode() if isinstance(s, bytes) else str(s)
                for s in f["insar/member"][()]]
               if "insar/member" in f else None)

    lon, lat = xyz[:, 0], xyz[:, 1]
    lat0, lon0 = float(np.mean(lat)), float(np.mean(lon))
    e = (lon - lon0) * 111320.0 * np.cos(np.radians(lat0))
    n = (lat - lat0) * 110540.0
    # 교축 방향은 bridge.json 의 방위각으로 잡으려 했으나 부호·기준(북기준/동기준)이
    # 파일마다 달라 교축직각 폭이 수백 m 로 벌어졌다. 점 구름 자체의 주축(PCA)을 쓴다 —
    # 교량 위 점들은 길이 방향으로 늘어서 있으므로 제1주성분이 곧 교축이다.
    P = np.column_stack([e, n])
    P = P - P.mean(axis=0)
    _u, _s, vt = np.linalg.svd(P, full_matrices=False)
    ax1 = vt[0]
    th = float(np.arctan2(ax1[1], ax1[0]))
    along = e * np.cos(th) + n * np.sin(th)
    perp = -e * np.sin(th) + n * np.cos(th)
    # 회전 방향이 맞는지는 측점과 맞춰 본다 — 반대로 돌았으면 뒤집는다.
    if (st is not None and st.size == along.size and np.std(st) > 1e-6
            and np.corrcoef(along, st)[0, 1] < 0):
        along, perp = -along, -perp
    z = deck_top(folder, folder.name)
    local = np.column_stack([along, perp, np.full_like(along, z)])
    return local, vel, cri, mem


def outline(ax, folder: Path, name: str):
    """IFC 부재 bbox 를 옅은 상자로 깔아 교량 형태를 보이게 한다. 범위를 돌려준다."""
    ej = folder / f"{name}_elements.json"
    if not ej.exists():
        return None
    try:
        els = json.loads(ej.read_text(encoding="utf-8")).get("elements", [])
    except (OSError, json.JSONDecodeError):
        return None
    seen = []
    for e in els:
        lo, hi = e.get("bbox_min"), e.get("bbox_max")
        if not lo or not hi:
            continue
        c = MEMBER_COLOR.get(e.get("member", ""), "#B7C1CB")
        # 면 하나(윗면)만 그린다 — 전부 그리면 점이 안 보인다.
        x0, y0, z1 = lo[0], lo[1], hi[2]
        x1, y1 = hi[0], hi[1]
        if not np.isfinite([x0, y0, x1, y1, z1]).all():
            continue
        ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0],
                [z1] * 5, color=c, lw=0.5, alpha=0.45, zorder=1)
        seen.append((lo, hi))
    if not seen:
        return None
    lo = np.min([s[0] for s in seen], axis=0)
    hi = np.max([s[1] for s in seen], axis=0)
    return lo, hi


def panel(fig, ax, xyz, val, cmap, vlo, vhi, label):
    """점을 찍고 색막대를 붙인다. 색막대는 자리를 직접 잡는다 — ax 에서 자리를
    빼앗게 두면 3D 축이 쪼그라들어 교량이 실처럼 가늘어진다."""
    s = ax.scatter(xyz[:, 0], xyz[:, 1], xyz[:, 2], c=val, cmap=cmap,
                   vmin=vlo, vmax=vhi, s=30, depthshade=False,
                   edgecolors=MPL["slate"], linewidths=0.35, zorder=5)
    cax = fig.add_axes((0.935, 0.20, 0.017, 0.56))
    cb = fig.colorbar(s, cax=cax)
    cb.set_label(label, fontsize=10, color=NAVY)
    cb.ax.tick_params(labelsize=9)
    return s


def frame(ax, xyz, ext=None):
    """교량은 가늘고 길다 — 축을 그대로 두면 실 한 가닥이 된다. 종횡비를 눌러 준다."""
    lo = xyz.min(axis=0)
    hi = xyz.max(axis=0)
    if ext is not None:
        lo = np.minimum(lo, ext[0])
        hi = np.maximum(hi, ext[1])
    pad = np.maximum((hi - lo) * 0.06, 2.0)
    lo, hi = lo - pad, hi + pad
    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(lo[1], hi[1])
    ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect((1.0, 0.40, 0.36))
    ax.set_xlabel("교축 [m]", fontsize=9.5, labelpad=-2)
    ax.set_ylabel("교축직각 [m]", fontsize=9.5, labelpad=-2)
    ax.set_zlabel("높이 [m]", fontsize=9.5, labelpad=-2)
    ax.tick_params(labelsize=8)
    ax.view_init(elev=24, azim=-58)
    ax.grid(False)
    for a in (ax.xaxis, ax.yaxis, ax.zaxis):
        a.pane.set_facecolor("#FCFDFE")
        a.pane.set_edgecolor(MPL["rule"])


def render(folder: Path, out_dir: Path) -> list[Path]:
    got = load(folder)
    if got is None:
        print("skip(자료 없음)", folder.name)
        return []
    xyz, vel, cri, _mem = got
    name = folder.name
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []

    if vel is not None and vel.size:
        lim = float(np.nanpercentile(np.abs(vel), 96)) or 1.0
        fig = plt.figure(figsize=(9.6, 4.25))
        ax = fig.add_subplot(111, projection="3d")
        ext = outline(ax, folder, name)
        panel(fig, ax, xyz, vel, "RdYlBu", -lim, lim, "LOS 속도 [mm/년]")
        frame(ax, xyz, ext)
        ax.set_title(f"{name} — 교량 변위 속도 3D\n"
                     f"점 {vel.size}개 · 중앙값 {np.median(vel):+.1f} mm/년 · "
                     f"범위 {vel.min():+.1f} ~ {vel.max():+.1f}",
                     fontsize=12.5, color=NAVY, pad=2)
        fig.text(0.008, 0.008,
                 "※ 위성 시선(LOS) 방향 속도다. 붉은색이 가라앉는 쪽, 푸른색이 솟는 쪽.\n"
                 "   옅은 회색 윤곽은 같은 좌표계의 IFC 부재 상면이다.",
                 fontsize=8.6, color=DIM)
        ax.set_position((-0.045, -0.085, 0.985, 0.935))
        p = out_dir / f"3D_변위속도_{name}.png"
        fig.savefig(p, dpi=160)
        plt.close(fig)
        made.append(p)
        print("wrote", p)

    if cri is not None and cri.size:
        fig = plt.figure(figsize=(9.6, 4.25))
        ax = fig.add_subplot(111, projection="3d")
        ext = outline(ax, folder, name)
        n = min(len(cri), len(xyz))
        # 0~1 로 고정하면 값이 0.1 대인 교량은 전부 같은 연노랑이 된다. 자료에 맞춰
        # 위끝을 잡되, 얼마로 잡았는지 그림 아래에 적는다.
        vmax = max(0.25, float(np.nanpercentile(cri, 98)))
        panel(fig, ax, xyz[:n], cri[:n], "YlOrRd", 0.0, vmax, "공명 위험 지수 CRI")
        frame(ax, xyz[:n], ext)
        hot = int(np.sum(cri >= 0.8))
        ax.set_title(f"{name} — FRAM 공명 위험 지수(CRI) 3D\n"
                     f"점별 시간중앙값 {np.median(cri):.3f} · 최대 {cri.max():.3f} · "
                     f"0.8 이상 {hot}개 점",
                     fontsize=12.5, color=NAVY, pad=2)
        fig.text(0.008, 0.008,
                 "※ CRI 는 이웃한 점들이 같은 주기로 함께 움직이는 정도(공명)를 0~1 로 "
                 "나타낸다. 값 자체가 손상이 아니라 '같이 움직이는 구간'을 짚어 주는 지표다."
                 f"\n   색 위끝은 자료에 맞춰 {vmax:.2f} 로 잡았다(0~1 고정 아님).",
                 fontsize=8.6, color=DIM)
        ax.set_position((-0.045, -0.085, 0.985, 0.935))
        p = out_dir / f"3D_FRAM_CRI_{name}.png"
        fig.savefig(p, dpi=160)
        plt.close(fig)
        made.append(p)
        print("wrote", p)
    return made


def _boxes(folder: Path, name: str):
    ej = folder / f"{name}_elements.json"
    if not ej.exists():
        return []
    try:
        els = json.loads(ej.read_text(encoding="utf-8")).get("elements", [])
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for e in els:
        lo, hi = e.get("bbox_min"), e.get("bbox_max")
        if lo and hi and np.isfinite(lo).all() and np.isfinite(hi).all():
            out.append((np.asarray(lo, float), np.asarray(hi, float),
                        e.get("member", ""), e.get("ifc_type", "")))
    return out


def _flat(ax, boxes, ia, ib, pts=None, val=None):
    """bbox 를 두 축으로 눌러 그린다 — 평면(x,y) 과 입면(x,z) 이 같은 코드로 나온다."""
    from matplotlib.patches import Rectangle

    for lo, hi, mem, _ty in boxes:
        c = MEMBER_COLOR.get(mem, "#B7C1CB")
        w, h = hi[ia] - lo[ia], hi[ib] - lo[ib]
        ax.add_patch(Rectangle((lo[ia], lo[ib]), max(w, 0.8), max(h, 0.8),
                               facecolor=c, edgecolor=MPL["slate"], lw=0.35,
                               alpha=0.85, zorder=2))
    if pts is not None:
        ax.scatter(pts[:, ia], pts[:, ib], c=val, cmap="RdYlBu", s=13,
                   edgecolors=MPL["slate"], linewidths=0.25, zorder=5)
    ax.autoscale_view()
    ax.set_aspect("equal", adjustable="datalim")
    ax.tick_params(labelsize=8)
    ax.grid(alpha=.2)


def plan_elevation(folder: Path, out_dir: Path) -> Path | None:
    """IFC 부재에서 **평면 · 입면 · 3D** 를 한 장에 — 도면처럼 보이게.

    chain.png 은 파이프라인 설명용이라 발표 한 칸에 넣으면 글씨가 안 보인다.
    이 그림은 IFC 부재 bbox 만으로 평면/입면을 그려 '모델이 섰다'는 것만 보인다.
    """
    name = folder.name
    boxes = _boxes(folder, name)
    if not boxes:
        return None
    got = load(folder)
    pts = val = None
    if got is not None:
        pts, vel, _cri, _mem = got
        if vel is not None and vel.size == len(pts):
            val = vel

    fig = plt.figure(figsize=(12.6, 4.5))
    gs = fig.add_gridspec(2, 2, width_ratios=(1.62, 1.0), hspace=0.42,
                          wspace=0.16, left=0.055, right=0.985,
                          top=0.855, bottom=0.155)
    ax1 = fig.add_subplot(gs[0, 0])
    _flat(ax1, boxes, 0, 1, pts, val)
    ax1.set_title("평면 — 교축 × 교축직각", fontsize=10.5, color=NAVY, pad=4)
    ax1.set_ylabel("교축직각 [m]", fontsize=9)

    ax2 = fig.add_subplot(gs[1, 0], sharex=ax1)
    _flat(ax2, boxes, 0, 2,
          None if pts is None else pts, None if val is None else val)
    ax2.set_title("입면 — 교축 × 높이", fontsize=10.5, color=NAVY, pad=4)
    ax2.set_xlabel("교축 [m]", fontsize=9)
    ax2.set_ylabel("높이 [m]", fontsize=9)

    ax3 = fig.add_subplot(gs[:, 1], projection="3d")
    ext = outline(ax3, folder, name)
    if pts is not None and val is not None:
        lim = float(np.nanpercentile(np.abs(val), 96)) or 1.0
        ax3.scatter(pts[:, 0], pts[:, 1], pts[:, 2], c=val, cmap="RdYlBu",
                    vmin=-lim, vmax=lim, s=16, depthshade=False,
                    edgecolors=MPL["slate"], linewidths=0.25, zorder=5)
        frame(ax3, pts, ext)
    elif ext is not None:
        frame(ax3, np.vstack([ext[0], ext[1]]), ext)
    ax3.set_title("3D 트윈 — PS 점이 부재에 결합된다", fontsize=10.5, color=NAVY,
                  pad=0)

    n_mem = len({m for _l, _h, m, _t in boxes if m})
    fig.suptitle(f"{name} — IFC 부재 {len(boxes):,}개로 세운 평면 · 입면 · 3D "
                 f"(부재 종류 {n_mem}가지)",
                 fontsize=13.5, fontweight="bold", color=NAVY, y=0.965)
    fig.text(0.006, 0.012,
             "※ 준공도면이 아니라 IFC proxy 다 — 공공데이터·OSM 제원으로 세운 기하이며, "
             "위성 점을 부재에 붙이기 위한 그릇이다. 점 색은 LOS 속도.",
             fontsize=8.6, color=DIM)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"IFC_평면입면_{name}.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print("wrote", p)
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", nargs="*", default=[],
                    help="평면·입면·3D 한 장을 낼 교량")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--bridges", nargs="*", default=["올림픽대교"])
    ap.add_argument("--out-dir", default="docs/img/value")
    a = ap.parse_args()
    for nm in a.bridges:
        render(Path(a.root) / nm, Path(a.out_dir))
    for nm in a.plan:
        plan_elevation(Path(a.root) / nm, Path(a.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
