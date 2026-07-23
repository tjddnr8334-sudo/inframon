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
y = y - np.median(y)                     # 데크 중심선 기준(절대 120~140m 는 읽을 이유가 없다)
M = cri.shape[1]
MEM = {0: "deck", 1: "pier", 2: "abutment"}
markers = {0: "o", 1: "s", 2: "^"}

# 교량은 500m × 20m 라 종횡비가 25:1 이다. 1:1(`set_aspect("equal")`)로 그리면 점이
# 실오라기 같은 띠에 뭉치고 나머지 축은 전부 빈 공간이 된다(예전 그림이 그랬다).
# 종단면도처럼 **연직을 과장**하되 그 사실을 축 라벨에 밝힌다 — 숨기면 점 간격을 오해한다.
_EXAG = 4.0
_XPAD, _YPAD = np.ptp(x) * 0.02, max(np.ptp(y) * 0.35, 2.0)


def _style(a, title: str):
    a.set_aspect(_EXAG)
    a.set_xlim(x.min() - _XPAD, x.max() + _XPAD)
    a.set_ylim(y.min() - _YPAD, y.max() + _YPAD)
    a.set_xlabel("along span (m)")
    a.set_ylabel(f"offset from\ncentreline (m)\n[×{_EXAG:g} exaggerated]", fontsize=8)
    a.set_title(title, fontsize=11)
    a.grid(alpha=.25, linewidth=.5)


# ── static: 최종 시점 CRI on 데크 ──
fig, a = plt.subplots(figsize=(12, 2.9))
for m, mk in markers.items():
    sel = member == m
    if sel.any():
        a.scatter(x[sel], y[sel], c=cri[sel, -1], cmap="RdYlGn_r", vmin=0, vmax=1,
                  marker=mk, s=55, edgecolor="k", linewidth=.3, label=MEM[m])
_style(a, "inframon — monitoring points ON a bridge deck (CRI) · SYNTHETIC demo")
# 범례를 축 밖(bbox_to_anchor)에 두면 tight_layout 과 충돌해 그림이 한쪽으로 쏠린다.
# 데크 위아래 여백이 충분하므로 축 안에 둔다.
a.legend(loc="upper right", fontsize=8, ncol=3, framealpha=.85)
sm = plt.cm.ScalarMappable(cmap="RdYlGn_r", norm=plt.Normalize(0, 1))
fig.colorbar(sm, ax=a, label="CRI (0→1)", shrink=.95, pad=.012)
fig.text(0.5, 0.02, "illustrative synthetic data (not real InSAR) — deck·pier·abutment points",
         ha="center", fontsize=8, color="gray")
fig.tight_layout(rect=(0, 0.06, 1, 1))
fig.savefig(f"{OUT}/demo_bridge.png", dpi=120); plt.close(fig)
print(f"wrote {OUT}/demo_bridge.png")

# ── GIF: CRI over time on deck ──
frames = list(range(0, M, max(1, M // 24)))
fig, a = plt.subplots(figsize=(12, 2.9))
scs = {}
for m, mk in markers.items():
    sel = member == m
    if sel.any():
        scs[m] = (a.scatter(x[sel], y[sel], c=cri[sel, 0], cmap="RdYlGn_r", vmin=0, vmax=1,
                            marker=mk, s=55, edgecolor="k", linewidth=.3), sel)
# 제목은 프레임마다 바뀌지만, tight_layout 시점에 비어 있으면 자리를 안 잡아 잘린다.
# 대표 문자열을 먼저 넣어 높이를 확보한다.
_style(a, f"inframon — bridge-deck CRI · epoch {M}/{M} (synthetic)")
fig.colorbar(plt.cm.ScalarMappable(cmap="RdYlGn_r", norm=plt.Normalize(0, 1)), ax=a,
             label="CRI (0→1)", shrink=.95, pad=.012)
ttl = a.title
fig.text(0.5, 0.015, "SYNTHETIC demo — points along a bridge deck (not real InSAR)",
         ha="center", fontsize=8, color="gray")
fig.tight_layout(rect=(0, 0.04, 1, 1))


def _upd(k):
    for m, (sc, sel) in scs.items():
        sc.set_array(cri[sel, k])
    ttl.set_text(f"inframon — bridge-deck CRI · epoch {k+1}/{M} (synthetic)")
    return [sc for sc, _ in scs.values()]


FuncAnimation(fig, _upd, frames=frames).save(f"{OUT}/demo_bridge.gif", writer=PillowWriter(fps=4))
plt.close(fig)
print(f"wrote {OUT}/demo_bridge.gif ({len(frames)} frames)")
