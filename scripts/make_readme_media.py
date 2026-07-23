"""README용 결과 그림 + 데모 GIF 생성 (실제 project.h5 산출로).

사용: python scripts/make_readme_media.py [PROJECT_H5]
"""
from __future__ import annotations

import json
import os

import h5py
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# 한글 폰트 — 없으면 교량명이 □□□ 로 깨진다(matplotlib 기본 DejaVu Sans 엔 한글 없음).
# dashboard/report.py 와 같은 방식. 못 찾으면 라벨을 로마자로 폴백한다(아래 _label).
import matplotlib.font_manager as _fm  # noqa: E402
_HANGUL_OK = False
_installed = {f.name for f in _fm.fontManager.ttflist}
for _cand in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "맑은 고딕"):
    if _cand in _installed:
        plt.rcParams["font.family"] = _cand
        plt.rcParams["axes.unicode_minus"] = False
        _HANGUL_OK = True
        break


def _label(text: str, roman: str) -> str:
    """한글 폰트가 있으면 원문, 없으면 로마자 — 깨진 네모를 내보내지 않는다."""
    return text if _HANGUL_OK else roman
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402

OUT = "docs/img"
os.makedirs(OUT, exist_ok=True)

import sys  # noqa: E402

PROJECT = sys.argv[1] if len(sys.argv) > 1 else "data/project.h5"
print("project =", PROJECT)

with h5py.File(PROJECT, "r") as f:
    los = f["/insar/los"][()]
    xyz = f["/insar/xyz"][()]
    cri = f["/fram/CRI"][()]
    cal = f["/fram/calibrated_risk"][()] if "/fram/calibrated_risk" in f else None
    ct = f["/pinn/comp_thermal"][()] if "/pinn/comp_thermal" in f else None
    cl = f["/pinn/comp_load"][()] if "/pinn/comp_load" in f else None
    cs = f["/pinn/comp_settle"][()] if "/pinn/comp_settle" in f else None
    fm = json.loads(f["/fram"].attrs.get("meta", "{}"))
lon, lat = xyz[:, 0], xyz[:, 1]
N, M = los.shape
level = fm.get("warning", {}).get("level", "-")
# 그림은 영문 라벨이고 matplotlib 기본 폰트(DejaVu Sans)엔 한글이 없어 □□ 로 깨진다.
# 경보 등급만 영문으로 옮긴다(폰트를 바꾸면 나머지 축 라벨과 스타일이 흔들린다).
level = {"정상": "normal", "주의": "caution", "경고": "warning", "위험": "danger"}.get(level, level)

# OSM 베이스맵용: 경위도(4326) → 웹메르카토르(3857)
import contextily as ctx
from pyproj import Transformer
_tf = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
mx, my = _tf.transform(lon, lat)
_padx = (mx.max() - mx.min()) * 0.18 + 60
_pady = (my.max() - my.min()) * 0.18 + 60


def _basemap(ax, extent=None):
    """축(3857 좌표)에 OSM 타일을 깐다. 실패(오프라인)해도 무시.

    `extent=(x0,x1,y0,y1)` 를 주면 그 범위로 — 안 주면 점군 전체. 예전엔 전체 범위를
    하드코딩해서 호출 측이 확대해도 무시됐다(확대 패널이 전경과 똑같이 나오던 원인).
    """
    if extent is None:
        ax.set_xlim(mx.min() - _padx, mx.max() + _padx)
        ax.set_ylim(my.min() - _pady, my.max() + _pady)
    else:
        ax.set_xlim(extent[0], extent[1])
        ax.set_ylim(extent[2], extent[3])
    try:
        ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik, crs="EPSG:3857",
                        attribution_size=5)
    except Exception as e:  # noqa: BLE001
        print("basemap 실패(오프라인?):", e)
    ax.set_xticks([]); ax.set_yticks([])

# ── overview 2x2 ──
fig, ax = plt.subplots(2, 2, figsize=(11, 8))
fig.suptitle(f"inframon — bridge InSAR monitoring (SARvey→PINN→FRAM)   N={N} pts, M={M} epochs, "
             f"alert: {level}", fontsize=12, fontweight="bold")
s0 = ax[0, 0].scatter(lon, lat, c=los[:, -1], cmap="RdBu_r", s=6)
ax[0, 0].set_title("InSAR LOS displacement (last epoch, mm)"); fig.colorbar(s0, ax=ax[0, 0])
vel = np.polyfit(np.arange(M), los.T, 1)[0]
s1 = ax[0, 1].scatter(lon, lat, c=vel, cmap="RdBu_r", s=6)
ax[0, 1].set_title("LOS velocity (mm/epoch)"); fig.colorbar(s1, ax=ax[0, 1])
ax[1, 0].plot(cri.max(axis=0), color="crimson"); ax[1, 0].axhline(0.85, ls="--", c="r", alpha=.5)
ax[1, 0].axhline(0.6, ls="--", c="orange", alpha=.5)
ax[1, 0].set_title("FRAM global max CRI over time"); ax[1, 0].set_xlabel("epoch"); ax[1, 0].grid(alpha=.3)
if ct is not None:
    p = int(np.argmax(np.abs(los[:, -1])))
    ax[1, 1].plot(ct[p], label="thermal"); ax[1, 1].plot(cl[p], label="load")
    if cs is not None:
        ax[1, 1].plot(cs[p], label="settle")
    ax[1, 1].set_title(f"PINN decomposition (pt #{p})"); ax[1, 1].legend(fontsize=8)
for a in ax.ravel():
    a.tick_params(labelsize=8)
fig.tight_layout()
fig.savefig(f"{OUT}/overview.png", dpi=110); plt.close(fig)
print(f"wrote {OUT}/overview.png")

# ── velocity map (OSM 베이스맵 위) ──
# 예전 그림은 1.5km 일대를 한 장에 담아 "정자교가 어디냐"는 질문에 답을 못 했다.
# 두 패널로 나눈다: 좌=전경(교량 위치 표시), 우=교량 확대(데크 위 점이 몇 개인지).
BRIDGE = sys.argv[2] if len(sys.argv) > 2 else None      # {"name","geometry":[[lat,lon],...]}
bg = None
if BRIDGE:
    with open(BRIDGE, encoding="utf-8") as fh:
        bg = json.load(fh)

if bg is None:
    fig, a = plt.subplots(figsize=(7, 6))
    sc = a.scatter(mx, my, c=vel, cmap="RdBu_r", s=14, edgecolor="k", linewidth=.2, zorder=3)
    _basemap(a)
    a.set_title("inframon — LOS velocity on OpenStreetMap")
    fig.colorbar(sc, ax=a, label="LOS velocity (mm/epoch)", shrink=.8)
else:
    blat = np.array([p[0] for p in bg["geometry"]], dtype=float)
    blon = np.array([p[1] for p in bg["geometry"]], dtype=float)
    bx, by = _tf.transform(blon, blat)
    name = _label(bg.get("name") or "bridge", bg.get("name_roman") or "Jeongja Br.")

    # 데크 근접 점 — 교량 선분까지의 거리로 판정(단순 최근접 노드가 아니라 선분 거리).
    def _seg_dist(px, py, ax_, ay_, bx_, by_):
        vx, vy = bx_ - ax_, by_ - ay_
        L2 = vx * vx + vy * vy
        t = np.clip(((px - ax_) * vx + (py - ay_) * vy) / (L2 + 1e-12), 0, 1)
        return np.hypot(px - (ax_ + t * vx), py - (ay_ + t * vy))

    d = np.full(mx.shape, np.inf)
    for i in range(len(bx) - 1):
        d = np.minimum(d, _seg_dist(mx, my, bx[i], by[i], bx[i + 1], by[i + 1]))
    DECK_M = 50.0                     # 데크 폭 + 정합 여유
    on_deck = d <= DECK_M
    span_m = float(np.sum(np.hypot(np.diff(bx), np.diff(by))))

    fig, axs = plt.subplots(1, 2, figsize=(13, 6))
    # 좌: 전경 — 점군 전체 + 교량 위치
    a = axs[0]
    sc = a.scatter(mx, my, c=vel, cmap="RdBu_r", s=10, edgecolor="k", linewidth=.15, zorder=3)
    a.plot(bx, by, "-", color="lime", lw=5, zorder=4, solid_capstyle="round")
    a.plot(bx, by, "-", color="black", lw=1.5, zorder=5)
    a.annotate(f"{name}\n({span_m:.0f} m)", xy=(bx.mean(), by.mean()),
               xytext=(28, 46), textcoords="offset points", fontsize=11, fontweight="bold",
               color="black", zorder=6,
               bbox=dict(boxstyle="round,pad=0.3", fc="lime", ec="black", alpha=.9),
               arrowprops=dict(arrowstyle="->", lw=2, color="black"))
    _basemap(a)
    a.set_title(f"All {N:,} PS/DS points — bridge marked in green")
    fig.colorbar(sc, ax=a, label="LOS velocity (mm/epoch)", shrink=.75)

    # 우: 교량 확대 — 데크 위에 점이 실제로 몇 개인가
    a = axs[1]
    pad = max(span_m * 0.9, 250.0)
    x0, x1 = bx.min() - pad, bx.max() + pad
    y0, y1 = by.min() - pad, by.max() + pad
    box = (mx >= x0) & (mx <= x1) & (my >= y0) & (my <= y1)
    a.scatter(mx[box & ~on_deck], my[box & ~on_deck], c="0.45", s=16,
              edgecolor="k", linewidth=.2, zorder=3, label=f"off deck ({int((box & ~on_deck).sum())})")
    if on_deck.any():
        a.scatter(mx[on_deck], my[on_deck], c=vel[on_deck], cmap="RdBu_r", s=90, marker="o",
                  edgecolor="lime", linewidth=2.0, zorder=5,
                  label=f"within {DECK_M:.0f} m of deck ({int(on_deck.sum())})")
    a.plot(bx, by, "-", color="lime", lw=5, zorder=4, solid_capstyle="round")
    a.plot(bx, by, "-", color="black", lw=1.5, zorder=6)
    _basemap(a, extent=(x0, x1, y0, y1))
    a.set_title(f"Zoom on {name} — {int(on_deck.sum())} of {N:,} points lie on the deck")
    a.legend(loc="lower left", fontsize=9, framealpha=.9)

    for a in axs:
        a.set_xticks([]); a.set_yticks([])
    fig.suptitle(f"inframon — LOS velocity on OpenStreetMap · {name} (OSM way {bg.get('osm_id','?')})",
                 fontsize=13, fontweight="bold")
    print(f"  데크 {DECK_M:.0f}m 이내 점: {int(on_deck.sum())} / {N}  (지간 {span_m:.0f}m)")

fig.tight_layout(); fig.savefig(f"{OUT}/velocity_map.png", dpi=120); plt.close(fig)
print(f"wrote {OUT}/velocity_map.png")

# ── demo GIF: FRAM 붕괴확률(또는 CRI) 시공간 지도 ──
risk = cal if cal is not None else cri
metric = "collapse prob. (isotonic)" if cal is not None else "CRI"
frames = list(range(0, M, max(1, M // 30)))
fig, a = plt.subplots(figsize=(6.8, 6.0))
sc = a.scatter(mx, my, c=risk[:, 0], cmap="RdYlGn_r", vmin=0, vmax=1, s=16,
               edgecolor="k", linewidth=.2, zorder=3)
_basemap(a)                                    # OSM 타일 (어디가 위험인지 지도로)
fig.colorbar(sc, ax=a, label=f"FRAM {metric} · 0=safe→1=high", shrink=.8)
ttl = a.set_title("")
fig.text(0.5, 0.015, "risk per InSAR point on OpenStreetMap · FRAM output (not a validated diagnosis)",
         ha="center", fontsize=8, color="gray")


def _update(k):
    sc.set_array(risk[:, k])
    ttl.set_text(f"inframon — FRAM {metric} · epoch {k+1}/{M} (Jeongja Br.)")
    return sc, ttl


anim = FuncAnimation(fig, _update, frames=frames, blit=False)
anim.save(f"{OUT}/demo.gif", writer=PillowWriter(fps=4))
plt.close(fig)
print(f"wrote {OUT}/demo.gif ({len(frames)} frames)")
