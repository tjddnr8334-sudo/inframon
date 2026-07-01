"""합성 데모(--demo)로 '교량 데크 위 모니터링 점' 미리보기 생성 (illustrative).

실 InSAR 는 교량 데크에 자연 PS/DS 가 희소해 점이 안 잡히는 경우가 많다. 이 미리보기는
inframon 이 산정한 **교량 데크 격자 점**에 CRI 를 입힌 개념 예시(합성)다 — 실측 아님.
"""
from __future__ import annotations

import os

import h5py
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402

SRC = "data/_demo_preview.h5"
OUT = "docs/img"
os.makedirs(OUT, exist_ok=True)

with h5py.File(SRC, "r") as f:
    xyz = f["/insar/xyz"][()]
    member = f["/insar/member"][()]
    cri = f["/fram/CRI"][()]
x, y = xyz[:, 0], xyz[:, 1]
M = cri.shape[1]
MEM = {0: "deck", 1: "pier", 2: "abutment"}
markers = {0: "o", 1: "s", 2: "^"}

# ── static: 최종 시점 CRI on 데크 ──
fig, a = plt.subplots(figsize=(11, 3.2))
for m, mk in markers.items():
    sel = member == m
    if sel.any():
        a.scatter(x[sel], y[sel], c=cri[sel, -1], cmap="RdYlGn_r", vmin=0, vmax=1,
                  marker=mk, s=60, edgecolor="k", linewidth=.3, label=MEM[m])
a.set_aspect("equal"); a.set_xlabel("along span (m)"); a.set_ylabel("width (m)")
a.set_title("inframon — monitoring points ON a bridge deck (CRI) · SYNTHETIC demo")
a.legend(loc="upper right", fontsize=8, ncol=3)
sm = plt.cm.ScalarMappable(cmap="RdYlGn_r", norm=plt.Normalize(0, 1))
fig.colorbar(sm, ax=a, label="CRI (0→1)", shrink=.8)
fig.text(0.5, 0.005, "illustrative synthetic data (not real InSAR) — deck·pier·abutment points",
         ha="center", fontsize=8, color="gray")
fig.tight_layout(); fig.savefig(f"{OUT}/demo_bridge.png", dpi=120); plt.close(fig)
print(f"wrote {OUT}/demo_bridge.png")

# ── GIF: CRI over time on deck ──
frames = list(range(0, M, max(1, M // 24)))
fig, a = plt.subplots(figsize=(11, 3.4))
scs = {}
for m, mk in markers.items():
    sel = member == m
    if sel.any():
        scs[m] = (a.scatter(x[sel], y[sel], c=cri[sel, 0], cmap="RdYlGn_r", vmin=0, vmax=1,
                            marker=mk, s=60, edgecolor="k", linewidth=.3), sel)
a.set_aspect("equal"); a.set_xlabel("along span (m)"); a.set_ylabel("width (m)")
fig.colorbar(plt.cm.ScalarMappable(cmap="RdYlGn_r", norm=plt.Normalize(0, 1)), ax=a,
             label="CRI (0→1)", shrink=.8)
ttl = a.set_title("")
fig.text(0.5, 0.005, "SYNTHETIC demo — points along a bridge deck (not real InSAR)",
         ha="center", fontsize=8, color="gray")


def _upd(k):
    for m, (sc, sel) in scs.items():
        sc.set_array(cri[sel, k])
    ttl.set_text(f"inframon — bridge-deck CRI · epoch {k+1}/{M} (synthetic)")
    return [sc for sc, _ in scs.values()]


FuncAnimation(fig, _upd, frames=frames).save(f"{OUT}/demo_bridge.gif", writer=PillowWriter(fps=4))
plt.close(fig)
print(f"wrote {OUT}/demo_bridge.gif ({len(frames)} frames)")
