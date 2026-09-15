#!/usr/bin/env python3
"""디지털 트윈 위의 PS 점 — 3D 뷰어가 보여 주는 것을 종이에 그대로 옮긴다.

교량 폴더의 `twin.viewer.html` 안에는 뷰어가 그리는 것이 그대로 들어 있다 —
PS 점 좌표(POS)·값(VALS)·IFC 부재 AABB(BOXES)·부재 결합 GlobalId(GUIDS). 같은 원점,
같은 투영이라 **부재 위에 점이 앉는다.** 여기서는 그 배열을 다시 읽어 평면·입면 두 장으로
그린다. 회의 자료에 넣을 수 있고, 뷰어를 못 여는 자리에서도 같은 것을 볼 수 있다.

  · 평면 — 교축 거리 × 직각 거리. 부재(데크·교각·교대) 위에 PS 점을 얹는다.
  · 입면 — 교축 거리 × 표고. 점이 교면 높이에 앉는지 바로 보인다.
  · 점 색 = LOS 변위속도(mm/yr), 발산형. 테두리 있는 점 = IFC 부재에 결합된 점.

    python scripts/make_twin_ps_figure.py                     # 한강 15개소
    python scripts/make_twin_ps_figure.py --bridges 성수대교 암사대교

산출:
    docs/bridges/<교량>/twin_ps.png     교량마다 평면+입면
    docs/img/hangang_트윈_PS.png        전 교량 평면 한 장
    docs/img/hangang_트윈_3D.png        전 교량 3D — 슬래브·교각·교대 위의 점
    docs/bridges/트윈_index.html        3D 트윈을 한 자리에서 여는 목록
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from inframon.insar.chainage import _use_korean_font

MEMBER_COLOR = {"deck": "#4A6B8A", "pier": "#8A8F96", "abutment": "#7A8288"}
MEMBER_KO = {"deck": "슬래브(상판)", "pier": "교각", "abutment": "교대"}
RED, GRAY, NAVY = "#C03028", "#8A8F96", "#123A5E"


def _grab(html: str, name: str):
    """`NAME=[...]` / `NAME={...}` 를 괄호 균형으로 잘라 JSON 으로 읽는다."""
    m = re.search(rf"\b{name}=(\[|\{{)", html)
    if not m:
        return None
    i = m.end() - 1
    open_c, close_c = html[i], {"[": "]", "{": "}"}[html[i]]
    depth, j, in_str, esc = 0, i, False, False
    while j < len(html):
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                return json.loads(html[i:j + 1])
        j += 1
    return None


def read_twin(folder: Path) -> dict | None:
    """트윈 뷰어 + 사이드카 → 데크 정렬 좌표계의 점·부재."""
    html_p = folder / "twin.viewer.html"
    meta_p = folder / "twin.glb.meta.json"
    if not html_p.exists():
        return None
    html = html_p.read_text(encoding="utf-8")
    pos = _grab(html, "POS")
    if not pos:
        return None
    vals = _grab(html, "VALS") or []
    guids = _grab(html, "GUIDS") or []
    boxes = _grab(html, "BOXES") or []
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    bj = folder / "bridge.json"
    bsrc = (json.loads(bj.read_text(encoding="utf-8")).get("sources") or {}
            if bj.exists() else {})

    P = np.asarray(pos, float)                      # x=동, y=표고, z=−북
    rot = float(boxes[0].get("rotY", 0.0)) if boxes else 0.0

    def to_deck(x: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """뷰어 평면(x, z) → 교축 u · 직각 v.

        뷰어는 부재 메시를 Y축으로 rotY 만큼 돌린다 — three.js 의 Y 회전은 로컬 x축
        (1,0) 을 월드 (cos, −sin) 으로 보낸다. 그 역이 아래 식이다. 점과 부재에 같은
        식을 써야 부재 위에 점이 앉는다.
        """
        c, s = np.cos(rot), np.sin(rot)
        return c * x - s * z, s * x + c * z

    u, v = to_deck(P[:, 0], P[:, 2])
    els = []
    for b in boxes:
        cx, cy, cz = b["center"]
        sx, sy, sz = b["size"]
        bu, bv = to_deck(np.array([cx]), np.array([cz]))
        els.append({"u": float(bu[0]), "v": float(bv[0]), "h": float(cy),
                    "su": sx, "sv": sz, "sh": sy,
                    "member": b.get("member") or "", "name": b.get("name") or ""})
    bound = np.array([bool(g) for g in guids]) if guids else np.zeros(len(P), bool)
    lg = meta.get("legend") or {}
    return {
        "u": u, "v": v, "h": P[:, 1],
        "val": np.asarray(vals, float) if len(vals) == len(P) else np.zeros(len(P)),
        "bound": bound, "elements": els,
        "vmin": float(lg.get("vmin", -3.0)), "vmax": float(lg.get("vmax", 3.0)),
        "units": lg.get("units", "mm/yr"),
        "deck_z": (meta.get("georef") or {}).get("deck_z_median_m"),
        "n_points": int(len(P)), "n_bound": int(bound.sum()),
        "n_elements": len(els),
        "span_layout": bsrc.get("span_layout", ""),
    }


def _draw_elements(ax, els, *, axis: str) -> None:
    """부재 AABB 를 직사각형으로. axis='plan' 이면 u×v, 'elev' 면 u×h."""
    for e in sorted(els, key=lambda e: 0 if e["member"] == "deck" else 1):
        if axis == "plan":
            x, y, w, h = e["u"] - e["su"] / 2, e["v"] - e["sv"] / 2, e["su"], e["sv"]
        else:
            x, y, w, h = e["u"] - e["su"] / 2, e["h"] - e["sh"] / 2, e["su"], e["sh"]
        ax.add_patch(Rectangle((x, y), w, h,
                               facecolor=MEMBER_COLOR.get(e["member"], "#6C7A89"),
                               edgecolor="#2B3947", lw=0.5,
                               alpha=.55 if e["member"] == "deck" else .42, zorder=1))


def _box3(ax, lo, hi, color, alpha) -> None:
    """3D 축에 직육면체 하나 — 부재 프록시(슬래브·교각·교대)를 그린다."""
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [[v[0], v[1], v[2], v[3]], [v[4], v[5], v[6], v[7]],
             [v[0], v[1], v[5], v[4]], [v[2], v[3], v[7], v[6]],
             [v[1], v[2], v[6], v[5]], [v[0], v[3], v[7], v[4]]]
    ax.add_collection3d(Poly3DCollection(faces, facecolor=color, edgecolor="#2C3E50",
                                         linewidths=.4, alpha=alpha))


def panel3d(ax, d: dict, *, s: float = 46, leaders: bool = True, ticks: bool = True):
    """③ 3D 트윈 — 슬래브·교각·교대 위에 PS 점이 앉는 그림.

    부재는 IFC 프록시 AABB 를 그대로 상자로 세운다(데크 정렬 좌표계). 점은 실제 표고에
    찍되 슬래브 윗면보다 조금 띄워 가려지지 않게 하고, 슬래브까지 지시선을 내린다.
    """
    els = d["elements"]
    if not els:
        return None
    deck = [e for e in els if e["member"] == "deck"]
    top = max((e["h"] + e["sh"] / 2 for e in deck), default=float(np.max(d["h"])))
    bot = min((e["h"] - e["sh"] / 2 for e in els), default=0.0)
    for e in sorted(els, key=lambda e: 0 if e["member"] == "deck" else 1):
        lo = (e["u"] - e["su"] / 2, e["v"] - e["sv"] / 2, e["h"] - e["sh"] / 2)
        hi = (e["u"] + e["su"] / 2, e["v"] + e["sv"] / 2, e["h"] + e["sh"] / 2)
        _box3(ax, lo, hi, MEMBER_COLOR.get(e["member"], "#6C7A89"),
              .50 if e["member"] == "deck" else .38)

    span = max(float(np.ptp(d["u"])), 1.0)
    lift = max((top - bot) * 0.05, span * 0.004)
    zp = np.maximum(d["h"], top) + lift
    if leaders:
        for x, y, z in zip(d["u"], d["v"], zp):
            ax.plot([x, x], [y, y], [top, z], color="#34495E", lw=.55, zorder=9)
    sc = ax.scatter(d["u"], d["v"], zp, s=np.where(d["bound"], s * 1.4, s),
                    c=d["val"], cmap="RdYlBu_r", vmin=d["vmin"], vmax=d["vmax"],
                    ec="#111", lw=.55, depthshade=False, zorder=10)

    lo_v, hi_v = _vlim(d)
    ax.set_xlim(float(np.min(d["u"])) - span * .05, float(np.max(d["u"])) + span * .05)
    ax.set_ylim(lo_v, hi_v)
    ax.set_zlim(bot - (top - bot) * .1, top + lift * 3.2)
    ax.set_box_aspect((3.0, 1.0, 0.66))
    ax.view_init(elev=24, azim=-64)
    if ticks:
        ax.set_xlabel("교축 [m]", fontsize=8, labelpad=1)
        ax.set_ylabel("횡축 [m]", fontsize=8, labelpad=-6)
        ax.set_zlabel("표고 [m]", fontsize=8, labelpad=-6)
        ax.tick_params(labelsize=6.2, pad=-2)
        ax.set_zticks([int(bot), int(top)])
    else:
        ax.set_xticks([]); ax.set_yticks([]); ax.set_zticks([])
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.set_alpha(.06)
    ax.grid(False)
    return sc


def _vlim(d) -> tuple[float, float]:
    """직각 방향 표시 범위 — 데크 폭과 점 산포 중 큰 쪽에 여유를 준다(축척은 과장)."""
    half = max((e["sv"] for e in d["elements"]), default=20.0) / 2
    w = max(half, float(np.abs(d["v"]).max()) if len(d["v"]) else half) * 1.25 + 5
    return -w, w


def _scatter(ax, x, y, d, *, s=26):
    sc = ax.scatter(x[~d["bound"]], y[~d["bound"]], c=d["val"][~d["bound"]],
                    cmap="RdYlBu_r", vmin=d["vmin"], vmax=d["vmax"], s=s,
                    linewidths=0.0, zorder=4)
    if d["bound"].any():
        ax.scatter(x[d["bound"]], y[d["bound"]], c=d["val"][d["bound"]],
                   cmap="RdYlBu_r", vmin=d["vmin"], vmax=d["vmax"], s=s * 1.25,
                   edgecolors="#111", linewidths=0.55, zorder=5)
    return sc


def fig_one(name: str, d: dict, out: Path) -> None:
    from matplotlib.patches import Patch

    fig = plt.figure(figsize=(17.0, 7.8))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.0, 1.02], height_ratios=[1.0, 0.78],
                          left=0.052, right=0.90, top=0.865, bottom=0.085,
                          hspace=0.34, wspace=0.10)

    a = fig.add_subplot(gs[0, 0])
    _draw_elements(a, d["elements"], axis="plan")
    sc = _scatter(a, d["u"], d["v"], d)
    a.set_ylim(*_vlim(d))
    a.set_ylabel("직각 거리 [m]", fontsize=10)
    a.set_title("① 평면 — 부재 위의 PS 점 (직각 방향은 과장했다)", fontsize=11, pad=6)
    a.grid(alpha=.2)

    b = fig.add_subplot(gs[1, 0])
    _draw_elements(b, d["elements"], axis="elev")
    _scatter(b, d["u"], d["h"], d)
    b.set_xlabel("교축 거리 [m]", fontsize=10)
    b.set_ylabel("표고 [m]", fontsize=10)
    b.set_title("② 종단면 — 점이 슬래브 높이에 앉는가", fontsize=11, pad=6)
    b.grid(alpha=.2)

    c = fig.add_subplot(gs[:, 1], projection="3d")
    panel3d(c, d)
    n_mem = {}
    for e in d["elements"]:
        n_mem[e["member"]] = n_mem.get(e["member"], 0) + 1
    made = " · ".join(f"{MEMBER_KO.get(k, k)} {v}" for k, v in
                      sorted(n_mem.items(), key=lambda kv: -kv[1]))
    if d.get("span_layout"):
        made += "\n경간 배치: " + d["span_layout"]
    c.set_title("③ 3D 트윈 — 슬래브·교각·교대 위에 점이 앉는다" + "\n" + made,
                fontsize=11, pad=2)
    c.legend(handles=[Patch(color=MEMBER_COLOR[k], alpha=.5, label=MEMBER_KO[k])
                      for k in ("deck", "pier", "abutment") if k in n_mem],
             fontsize=8.2, loc="upper left", framealpha=.9)

    cb = fig.colorbar(sc, ax=[a, b], fraction=0.03, pad=0.012)
    cb.set_label(f"LOS 변위속도 [{d['units']}]", fontsize=10)

    dz = f" · 데크 표고 중앙값 {d['deck_z']:.1f} m" if d["deck_z"] is not None else ""
    fig.suptitle(f"{name} — 디지털 트윈 위의 PS 점   "
                 f"PS {d['n_points']}점 · IFC 부재 결합 {d['n_bound']}점 "
                 f"· 부재 {d['n_elements']}개{dz}",
                 fontsize=13.5, fontweight="bold", y=0.975)
    fig.text(0.008, 0.013,
             "테두리 있는 점 = IFC 4.3 부재 GlobalId 에 결합된 PS(시계열↔부재 영구결합) · "
             "부재는 표준데이터 실측 제원(연장·경간수·폭·형하고)으로 세운 프록시 · "
             "3D 는 twin.viewer.html",
             fontsize=8.4, color=GRAY)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=135); plt.close(fig)


def fig_grid3d(items: list[tuple[str, dict | None, str]], out: Path) -> None:
    """전 교량 3D 트윈 한 장 — 슬래브·교각·교대 위에 PS 점."""
    from matplotlib.patches import Patch

    ncol = 4
    nrow = int(np.ceil(len(items) / ncol))
    fig_h = 2.25 * nrow + 1.45
    fig = plt.figure(figsize=(4.45 * ncol, fig_h))
    gs = fig.add_gridspec(nrow, ncol, left=0.012, right=0.955,
                          top=1 - 1.15 / fig_h, bottom=0.5 / fig_h,
                          hspace=-0.10, wspace=0.02)
    sc = None
    for k, (name, d, why) in enumerate(items):
        ax = fig.add_subplot(gs[k // ncol, k % ncol], projection="3d")
        if d is None:
            ax.set_axis_off()
            ax.text2D(0.5, 0.5, "트윈 없음" + "\n" + why, ha="center", va="center",
                      fontsize=8.5, color=RED, transform=ax.transAxes)
            ax.set_title(name, fontsize=11, pad=0)
            continue
        got = panel3d(ax, d, s=14, leaders=False, ticks=False)
        sc = sc or got
        ax.set_title(f"{name}   PS {d['n_points']} · 결합 {d['n_bound']} "
                     f"· 부재 {d['n_elements']}", fontsize=10.4, pad=0)
    if sc is not None:
        cb = fig.colorbar(sc, ax=fig.axes, fraction=0.012, pad=0.012)
        cb.set_label("LOS 변위속도 [mm/yr] (교량별 범례 폭은 각자 다름)", fontsize=9)
    fig.legend(handles=[Patch(color=MEMBER_COLOR[k], alpha=.5, label=MEMBER_KO[k])
                        for k in ("deck", "pier", "abutment")],
               ncol=3, fontsize=9.5, loc="lower left",
               bbox_to_anchor=(0.012, 0.004), framealpha=.9)
    fig.suptitle("한강교량 디지털 트윈 — 슬래브·교각·교대 위에 PS 점이 앉는다\n"
                 "IFC 4.3 프록시 부재(표준데이터 실측 제원) + Sentinel-1 측점 · "
                 "테두리 있는 점 = 부재 GlobalId 결합",
                 fontsize=14, fontweight="bold", y=1 - 0.22 / fig_h)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130); plt.close(fig)
    print("wrote", out)


def fig_grid(items: list[tuple[str, dict | None, str]], out: Path) -> None:
    ncol = 4
    nrow = int(np.ceil(len(items) / ncol))
    fig_h = 2.75 * nrow + 1.35
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.4 * ncol, fig_h), squeeze=False)
    sc = None
    for k, (name, d, why) in enumerate(items):
        ax = axes[k // ncol][k % ncol]
        if d is None:
            ax.text(0.5, 0.55, "트윈 없음", ha="center", va="center", fontsize=12,
                    color=RED, transform=ax.transAxes, fontweight="bold")
            ax.text(0.5, 0.40, why, ha="center", va="center", fontsize=7.6, color=RED,
                    transform=ax.transAxes, wrap=True)
            ax.set_title(name, fontsize=11, pad=4)
            ax.set_xticks([]); ax.set_yticks([])
            continue
        _draw_elements(ax, d["elements"], axis="plan")
        sc = _scatter(ax, d["u"], d["v"], d, s=11)
        ax.set_ylim(*_vlim(d))
        ax.set_title(f"{name}   PS {d['n_points']} · 결합 {d['n_bound']}",
                     fontsize=10.8, pad=4)
        ax.grid(alpha=.18)
        ax.tick_params(labelsize=7.5)
    for k in range(len(items), nrow * ncol):
        axes[k // ncol][k % ncol].axis("off")
    if sc is not None:
        cb = fig.colorbar(sc, ax=axes, fraction=0.016, pad=0.012)
        cb.set_label("LOS 변위속도 [mm/yr] (교량별 범례 폭은 각자 다름)", fontsize=9)
    fig.suptitle("한강교량 디지털 트윈 위의 PS 점 — 평면 · IFC 프록시 부재 위에 측점을 얹는다\n"
                 "테두리 있는 점 = IFC 부재 GlobalId 결합 · 교축 거리 × 직각 거리 [m]",
                 fontsize=14, fontweight="bold", y=1 - 0.22 / fig_h)
    fig.subplots_adjust(left=0.035, right=0.90, top=1 - 1.15 / fig_h,
                        bottom=0.55 / fig_h, hspace=0.40, wspace=0.22)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=135); plt.close(fig)
    print("wrote", out)


def write_index(summary: list[dict], root: Path, out: Path) -> None:
    """교량별 3D 트윈을 한 자리에서 연다 — 뷰어는 자립형이라 그냥 열면 된다."""
    rows = []
    for s in summary:
        nm = s["name"]
        if not s["n_points"]:
            rows.append(f'<tr class="na"><td>{nm}</td><td colspan="6">트윈 없음 — '
                        f'{s["why"]}</td></tr>')
            continue
        pct = 100 * s["n_bound"] / s["n_points"]
        cls = "ok" if pct >= 80 else ("mid" if pct >= 30 else "low")
        dz = f'{s["deck_z_m"]:.1f}' if s["deck_z_m"] is not None else "—"
        rows.append(
            f'<tr><td>{nm}</td><td class="n">{s["n_points"]}</td>'
            f'<td class="n {cls}">{s["n_bound"]} ({pct:.0f}%)</td>'
            f'<td class="n">{s["n_elements"]}</td><td class="n">{dz}</td>'
            f'<td><a href="{nm}/twin.viewer.html">속도 3D</a> · '
            f'<a href="{nm}/twin_cri.viewer.html">CRI 3D</a></td>'
            f'<td><a href="{nm}/twin_ps.png">평면·입면</a> · '
            f'<a href="{nm}/{nm}_proxy.ifc">IFC</a></td></tr>')
    have = sum(1 for s in summary if s["n_points"])
    out.write_text(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>한강교량 디지털 트윈 — PS 점</title><style>
body{{margin:0;padding:28px 22px;background:#0f141a;color:#e6edf3;
font:14px/1.6 "Malgun Gothic",system-ui,sans-serif}}
h1{{font-size:20px;margin:0 0 4px}}p.sub{{color:#93a4b4;margin:0 0 18px}}
table{{border-collapse:collapse;width:100%;max-width:1060px}}
th{{background:#123A5E;padding:9px 10px;text-align:left;font-weight:600;font-size:13px}}
td{{padding:8px 10px;border-bottom:1px solid #22303c}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}}
tr.na td{{color:#C03028}}.ok{{color:#5FCF7A}}.mid{{color:#E0A02C}}.low{{color:#E06C2C}}
a{{color:#7FC4FF;text-decoration:none}}a:hover{{text-decoration:underline}}
footer{{color:#6b7d8f;font-size:12px;margin-top:16px;max-width:1060px}}
</style></head><body>
<h1>한강교량 디지털 트윈 — PS 점</h1>
<p class="sub">트윈 {have}/{len(summary)}개소 · PS {sum(s['n_points'] for s in summary)}점 ·
IFC 부재 결합 {sum(s['n_bound'] for s in summary)}점.
3D 뷰어는 자립형(three.js 동봉) — 링크를 그냥 열면 된다.</p>
<table><thead><tr><th>교량</th><th>PS 점</th><th>부재 결합</th><th>IFC 부재</th>
<th>데크 표고 [m]</th><th>3D 트윈</th><th>그림 · 모델</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<footer>부재 결합 = PS 측점이 IFC 4.3 부재 GlobalId 에 묶인 것. 결합률이 낮으면 PS 가
데크 밖(제방·교대 주변)에 있거나 프록시 배치가 어긋난 것이다 — 그림으로 확인할 것.</footer>
</body></html>""", encoding="utf-8")
    print("wrote", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--list", default="docs/bridges/hangang16.json")
    ap.add_argument("--bridges", nargs="*", default=None,
                    help="교량 이름(생략하면 --list 전부)")
    ap.add_argument("--grid-out", default="docs/img/hangang_트윈_PS.png")
    ap.add_argument("--grid3d-out", default="docs/img/hangang_트윈_3D.png")
    ap.add_argument("--no-per-bridge", action="store_true")
    a = ap.parse_args()
    _use_korean_font(plt)

    root = Path(a.root)
    names = a.bridges or [b["name"] for b in
                          json.loads(Path(a.list).read_text(encoding="utf-8"))]

    items, summary = [], []
    for nm in names:
        fo = root / nm
        d = read_twin(fo)
        why = ""
        if d is None:
            bj = fo / "bridge.json"
            if bj.exists():
                notes = json.loads(bj.read_text(encoding="utf-8")).get("notes") or []
                why = notes[-1] if notes else "twin.viewer.html 없음"
            else:
                why = "교량 폴더 없음"
        else:
            if not a.no_per_bridge:
                fig_one(nm, d, fo / "twin_ps.png")
                print(f"wrote {fo / 'twin_ps.png'}")
        items.append((nm, d, why))
        summary.append({"name": nm,
                        "n_points": d["n_points"] if d else 0,
                        "n_bound": d["n_bound"] if d else 0,
                        "n_elements": d["n_elements"] if d else 0,
                        "deck_z_m": d["deck_z"] if d else None,
                        "why": why})

    fig_grid(items, Path(a.grid_out))
    fig_grid3d(items, Path(a.grid3d_out))
    write_index(summary, root, root / "트윈_index.html")

    print(f"\n{'교량':<10}{'PS':>6}{'결합':>6}{'부재':>6}{'데크표고':>9}")
    for s in summary:
        if not s["n_points"]:
            print(f"{s['name']:<10}{'—':>6}{'—':>6}{'—':>6}{'—':>9}  {s['why'][:50]}")
            continue
        dz = f"{s['deck_z_m']:.1f}" if s["deck_z_m"] is not None else "—"
        print(f"{s['name']:<10}{s['n_points']:>6}{s['n_bound']:>6}"
              f"{s['n_elements']:>6}{dz:>9}")
    tot = sum(s["n_points"] for s in summary)
    print(f"\n트윈 {sum(1 for s in summary if s['n_points'])}/{len(summary)}개소 "
          f"· PS 합계 {tot}점 · 부재결합 {sum(s['n_bound'] for s in summary)}점")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
