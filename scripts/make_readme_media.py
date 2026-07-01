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

# ── velocity map ──
fig, a = plt.subplots(figsize=(7, 5))
sc = a.scatter(lon, lat, c=vel, cmap="RdBu_r", s=10)
a.set_title("inframon — LOS velocity map (bridge PS/DS points)")
a.set_xlabel("lon"); a.set_ylabel("lat"); fig.colorbar(sc, label="mm/epoch")
fig.tight_layout(); fig.savefig(f"{OUT}/velocity_map.png", dpi=110); plt.close(fig)
print(f"wrote {OUT}/velocity_map.png")

# ── demo GIF: risk(CRI or calibrated) over time ──
risk = cal if cal is not None else cri
frames = list(range(0, M, max(1, M // 30)))
fig, a = plt.subplots(figsize=(6, 5))
sc = a.scatter(lon, lat, c=risk[:, 0], cmap="RdYlGn_r", vmin=0, vmax=1, s=12)
cb = fig.colorbar(sc, label="collapse prob." if cal is not None else "CRI")
a.set_xlabel("lon"); a.set_ylabel("lat")
ttl = a.set_title("")


def _update(k):
    sc.set_array(risk[:, k])
    ttl.set_text(f"inframon risk map — epoch {k+1}/{M}")
    return sc, ttl


anim = FuncAnimation(fig, _update, frames=frames, blit=False)
anim.save(f"{OUT}/demo.gif", writer=PillowWriter(fps=4))
plt.close(fig)
print(f"wrote {OUT}/demo.gif ({len(frames)} frames)")
