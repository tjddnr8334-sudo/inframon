"""README용 결과 그림 + 데모 GIF 생성 (data/project.h5 실제 산출로)."""
from __future__ import annotations

import json
import os

import h5py
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402

OUT = "docs/img"
os.makedirs(OUT, exist_ok=True)

with h5py.File("data/project.h5", "r") as f:
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

# OSM 베이스맵용: 경위도(4326) → 웹메르카토르(3857)
import contextily as ctx
from pyproj import Transformer
_tf = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
mx, my = _tf.transform(lon, lat)
_padx = (mx.max() - mx.min()) * 0.18 + 60
_pady = (my.max() - my.min()) * 0.18 + 60


def _basemap(ax):
    """현재 축(3857 좌표)에 OSM 타일을 깐다. 실패(오프라인)해도 무시."""
    ax.set_xlim(mx.min() - _padx, mx.max() + _padx)
    ax.set_ylim(my.min() - _pady, my.max() + _pady)
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
fig, a = plt.subplots(figsize=(7, 6))
sc = a.scatter(mx, my, c=vel, cmap="RdBu_r", s=14, edgecolor="k", linewidth=.2, zorder=3)
_basemap(a)
a.set_title("inframon — LOS velocity on OpenStreetMap (Jeongja Br.)")
fig.colorbar(sc, ax=a, label="LOS velocity (mm/epoch)", shrink=.8)
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
