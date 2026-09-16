#!/usr/bin/env python3
"""건기연 보고서 형식 그대로 — GNSS(현장계측) ↔ InSAR 산점도 · R² · 합성 한 장.

참고한 형식(2025 보고서 final/GNSS_vs_InSAR_*.png · 1.tif)을 그대로 따른다.

  ① 점 하나짜리 산점도  x=현장 계측, y=InSAR, 회색 회귀선, 좌상단 굵은 라벨,
                        우하단 R² — 점마다 한 장(QPS01… 대신 우리 점 번호)
  ② 패널 한 줄          위 산점도를 색만 바꿔 가로로 늘어놓은 것
  ③ 합성 한 장          왼쪽 위성영상 위 PS 점(연직속도 색) · 오른쪽 위 시계열
                        (InSAR 여러 점 = 파랑, 현장계측 = 주황) · 오른쪽 아래 산점도 3칸

**이름을 GNSS 라고 붙이지 않는다.** 한강 교량에서 우리가 가진 현장값은 교량마다
다르다(올림픽대교는 레이저 처짐계, 샛강은 GNSS 연직). 그 이름을 그대로 쓴다.

    python scripts/make_qps_style_figure.py --bridge 올림픽대교
    python scripts/make_qps_style_figure.py --bridge 올림픽대교 --no-basemap

산출: docs/img/qps/<교량>_scatter_P##.png · _panels.png · _composite.png
"""

from __future__ import annotations

import argparse
import io as _io
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_trend_agree import load_points, report_series, slope_ci   # noqa: E402

for _f in ("Malgun Gothic", "맑은 고딕", "NanumGothic"):
    if any(_f in f.name for f in font_manager.fontManager.ttflist):
        rcParams["font.family"] = _f
        break
rcParams["axes.unicode_minus"] = False

PALETTE = ["#3B6FD4", "#E03B3B", "#2ECC40", "#F39C12", "#7B1FA2",
           "#00838F", "#C2185B"]
GRAY = "#8A8A8A"
ESRI = ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/"
        "MapServer/tile/{z}/{y}/{x}")


def deg2num(lat: float, lon: float, z: int) -> tuple[float, float]:
    la = np.radians(lat)
    n = 2.0 ** z
    return ((lon + 180.0) / 360.0 * n,
            (1.0 - np.log(np.tan(la) + 1 / np.cos(la)) / np.pi) / 2.0 * n)


def num2deg(x: float, y: float, z: int) -> tuple[float, float]:
    n = 2.0 ** z
    lon = x / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * y / n))))
    return lat, lon


def basemap(lat0: float, lat1: float, lon0: float, lon1: float, z: int = 17):
    """Esri World Imagery 타일을 이어 붙여 배경으로. 실패하면 None."""
    from PIL import Image

    x0, y0 = deg2num(lat1, lon0, z)
    x1, y1 = deg2num(lat0, lon1, z)
    xa, xb = int(np.floor(x0)), int(np.ceil(x1))
    ya, yb = int(np.floor(y0)), int(np.ceil(y1))
    if (xb - xa) * (yb - ya) > 64:                 # 타일이 너무 많으면 줌을 낮춘다
        return basemap(lat0, lat1, lon0, lon1, z - 1) if z > 13 else None
    W, H = (xb - xa) * 256, (yb - ya) * 256
    im = Image.new("RGB", (W, H))
    for i, xt in enumerate(range(xa, xb)):
        for j, yt in enumerate(range(ya, yb)):
            url = ESRI.format(z=z, x=xt, y=yt)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "inframon"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    t = Image.open(_io.BytesIO(r.read())).convert("RGB")
            except Exception:                      # noqa: BLE001
                return None
            im.paste(t, (i * 256, j * 256))
    la_t, lo_l = num2deg(xa, ya, z)
    la_b, lo_r = num2deg(xb, yb, z)
    return np.asarray(im), (lo_l, lo_r, la_b, la_t)


def scatter_panel(ax, x, y, color, label, *, fs=11, big=False):
    """참고 형식 — 파란 점 · 회색 회귀선 · 좌상 라벨 · 우하 R²."""
    ax.scatter(x, y, s=(120 if big else 34), c=color, alpha=.92, edgecolors="none",
               zorder=3)
    r2 = np.nan
    if len(x) >= 3 and np.std(x) > 0:
        k = np.polyfit(x, y, 1)
        xs = np.linspace(min(x), max(x), 50)
        ax.plot(xs, np.polyval(k, xs), "-", lw=(4.0 if big else 1.8), color=GRAY,
                zorder=2)
        r = float(np.corrcoef(x, y)[0, 1])
        r2 = r * r
    ax.text(0.04, 0.96, label, transform=ax.transAxes, ha="left", va="top",
            fontsize=(38 if big else fs + 3), fontweight="bold", color="#111")
    ax.text(0.96, 0.05, f"$R^2 = {r2:.3f}$", transform=ax.transAxes, ha="right",
            va="bottom", fontsize=(38 if big else fs + 2), color="#111")
    ax.grid(alpha=.25, lw=.8)
    ax.tick_params(labelsize=(24 if big else fs - 1))
    for s in ax.spines.values():
        s.set_linewidth(2.4 if big else 1.2)
    return r2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="올림픽대교")
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--auto", default="docs/bridges/hangang_gnss_monthly.json")
    ap.add_argument("--eye", default="docs/bridges/hangang_displacement_2024.json")
    ap.add_argument("--out-dir", default="docs/img/qps")
    ap.add_argument("--top", type=int, default=5, help="산점도로 낼 점 수")
    ap.add_argument("--no-basemap", action="store_true")
    a = ap.parse_args()

    auto = json.loads(Path(a.auto).read_text(encoding="utf-8")) \
        if Path(a.auto).exists() else {}
    eye = json.loads(Path(a.eye).read_text(encoding="utf-8")) \
        if Path(a.eye).exists() else {}

    nm = a.bridge
    rp = report_series(auto, eye, nm)
    pts = load_points(Path(a.root) / nm)
    if rp is None or pts is None:
        print(f"{nm} — 현장 계측 또는 InSAR 자료가 없다")
        return 2
    tr, vr, what, how = rp
    los, ti, st, mem, inc = pts
    lo, hi = float(tr.min()), float(tr.max())
    sel = (ti >= lo - 0.12) & (ti <= hi + 0.12)

    import h5py
    with h5py.File(Path(a.root) / nm / "project.h5", "r") as f:
        xyz = np.asarray(f["insar/xyz"][()], float)
        vel = np.asarray(f["insar/velocity_mm_yr"][()], float)
    cosv = float(np.cos(np.radians(inc)))
    vert = vel / cosv

    # 달 단위로 짝짓기 — 현장계측은 월값이고 위성은 취득일이다.
    def ym(t):
        y = np.floor(t).astype(int)
        return list(zip(y, np.clip(((t - y) * 12).astype(int) + 1, 1, 12)))

    key_r = ym(tr)
    key_i = ym(ti[sel])
    common = sorted(set(key_r) & set(key_i))
    if len(common) < 4:
        print(f"{nm} — 같은 달로 짝지을 게 {len(common)}개뿐이다")
        return 2
    ridx = {k: i for i, k in enumerate(key_r)}
    X = np.vstack([los[:, sel][:, [i for i, k in enumerate(key_i) if k == c]
                                ].mean(axis=1) for c in common]).T
    gv = np.asarray([vr[ridx[c]] for c in common], float)

    # 상관이 큰 점부터 — 참고 형식의 QPS01… 자리에 우리 점을 놓는다.
    Xc = X - X.mean(1, keepdims=True)
    sx = Xc.std(1)
    gc = gv - gv.mean()
    ok = sx > 1e-9
    r = np.full(X.shape[0], np.nan)
    r[ok] = (Xc[ok] @ gc) / (len(common) * sx[ok] * float(gc.std()))
    order = np.argsort(-np.abs(np.nan_to_num(r)))[:a.top]

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    xl = f"{what} [mm]"

    r2s = []
    for k, j in enumerate(order):
        fig, ax = plt.subplots(figsize=(10.2, 11.4))
        r2 = scatter_panel(ax, gv, X[j], PALETTE[k % len(PALETTE)],
                           f"P{j:02d}", big=True)
        r2s.append(r2)
        ax.set_xlabel(xl, fontsize=32)
        ax.set_ylabel("InSAR Displacement [mm]", fontsize=32)
        fig.tight_layout()
        p = out / f"{nm}_scatter_P{j:02d}.png"
        fig.savefig(p, dpi=150)
        plt.close(fig)
        print("wrote", p)

    fig, axes = plt.subplots(1, len(order), figsize=(4.2 * len(order), 4.6))
    axes = np.atleast_1d(axes)
    for k, (ax, j) in enumerate(zip(axes, order)):
        scatter_panel(ax, gv, X[j], PALETTE[k % len(PALETTE)], f"P{j:02d}")
        ax.set_xlabel(xl, fontsize=11)
        if k == 0:
            ax.set_ylabel("InSAR Displacement [mm]", fontsize=11)
    fig.suptitle(f"{nm} — {what} ↔ InSAR ({how} · {len(common)}개월 짝)",
                 fontsize=14, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    p = out / f"{nm}_panels.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print("wrote", p)

    # ── 합성 한 장 ──────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(19.2, 8.6))
    gs = fig.add_gridspec(2, 4, width_ratios=[1.45, 1, 1, 1],
                          height_ratios=[1.15, 1], hspace=0.34, wspace=0.30,
                          left=0.045, right=0.985, top=0.94, bottom=0.085)

    axm = fig.add_subplot(gs[:, 0])
    lat, lon = xyz[:, 1], xyz[:, 0]
    pad = 0.0016
    bb = (lat.min() - pad, lat.max() + pad, lon.min() - pad, lon.max() + pad)
    bm = None if a.no_basemap else basemap(*bb)
    if bm is not None:
        img, ext = bm
        axm.imshow(img, extent=ext, origin="upper")
        axm.set_xlim(bb[2], bb[3])
        axm.set_ylim(bb[0], bb[1])
    v = float(np.nanpercentile(np.abs(vert), 95)) or 1.0
    sc = axm.scatter(lon, lat, c=vert, s=26, cmap="jet_r", vmin=-v, vmax=v,
                     edgecolors="none")
    for j in order[:3]:
        axm.plot(lon[j], lat[j], "s", ms=13, mfc="none", mec="red", mew=2.2)
    cb = fig.colorbar(sc, ax=axm, orientation="horizontal", pad=0.07,
                      fraction=0.045)
    cb.set_label("Vertical Velocity [mm/yr]", fontsize=11)
    axm.set_title(f"{nm} — PS 측점 연직속도", fontsize=13, fontweight="bold")
    axm.tick_params(labelsize=8.5)
    axm.set_xlabel("경도", fontsize=10)
    axm.set_ylabel("위도", fontsize=10)

    axt = fig.add_subplot(gs[0, 1:])
    for j in order:
        axt.plot(ti[sel], los[j, sel] - float(np.median(los[j, sel])), "-",
                 lw=1.5, color="#2E7FE0", alpha=.75)
    axt.set_ylabel("MT-InSAR Displacement [mm]", color="#2E7FE0", fontsize=11)
    axt.tick_params(axis="y", colors="#2E7FE0", labelsize=9)
    axt.tick_params(axis="x", labelsize=9)
    a2 = axt.twinx()
    a2.plot(tr, vr, "-o", lw=2.4, ms=6, color="#E8641A",
            mfc="none", mew=2.0, label=f"{what}")
    a2.set_ylabel(f"{what} [mm]", color="#E8641A", fontsize=11)
    a2.tick_params(axis="y", colors="#E8641A", labelsize=9)
    axt.set_xlabel("Data Acquisition Date", fontsize=11)
    axt.grid(alpha=.2)
    axt.plot([], [], "-", lw=3, color="#2E7FE0", label="InSAR-derived Displacement")
    axt.legend(loc="lower left", fontsize=9.5, framealpha=.9)
    a2.legend(loc="lower right", fontsize=9.5, framealpha=.9)

    for k, j in enumerate(order[:3]):
        ax = fig.add_subplot(gs[1, 1 + k])
        scatter_panel(ax, gv, X[j], PALETTE[k % len(PALETTE)], f"P{j:02d}", fs=10)
        ax.set_xlabel(xl, fontsize=10)
        if k == 0:
            ax.set_ylabel("InSAR Displacement [mm]", fontsize=10)

    fig.suptitle(f"{nm} — 현장 계측({what}) ↔ MT-InSAR  ·  "
                 f"{lo:.2f}~{hi:.2f} · 짝 {len(common)}개월 · 점 {los.shape[0]}개",
                 fontsize=15, fontweight="bold", y=0.985)
    p = out / f"{nm}_composite.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print("wrote", p)

    js = out / f"{nm}_r2.json"
    js.write_text(json.dumps(
        {"bridge": nm, "field_quantity": what, "read_as": how,
         "window": [round(lo, 2), round(hi, 2)], "n_months": len(common),
         "n_points": int(los.shape[0]), "incidence_deg": round(inc, 1),
         "_주의": "여러 점 중 상관이 큰 것을 골라 그린 것이다. 고른 값의 R² 는 "
                "선택 효과를 포함한다 — 우연 기준선은 ps_match.json 참조.",
         "panels": [{"point": int(j), "station_m": (None if not np.isfinite(st[j])
                                                    else round(float(st[j]), 1)),
                     "r": round(float(r[j]), 3), "r2": round(float(r[j]) ** 2, 3)}
                    for j in order]}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("wrote", js)
    print(f"\n{nm} · {what} · 짝 {len(common)}개월")
    for k, j in enumerate(order):
        print(f"  P{j:02d}  교축 {st[j]:7.1f} m   r={r[j]:+.3f}   R²={r[j]**2:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
