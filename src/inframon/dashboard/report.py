"""교량별 모니터링 리포트(PDF) 생성 — project.h5 → 요약 지표·차트.

reportlab 없이 matplotlib PdfPages 로 1~2쪽 PDF 를 만든다. 대시보드 버튼/CLI 양쪽에서 사용.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import h5py
import numpy as np


def _meta(f: h5py.File, group: str) -> dict:
    try:
        return json.loads(f[group].attrs["meta"])
    except Exception:  # noqa: BLE001
        return {}


def _dates(f: h5py.File):
    if "/insar/date_labels" in f:
        return [str(d.decode() if isinstance(d, bytes) else d) for d in f["/insar/date_labels"][()]]
    return None


def build_report(project_path: str | Path, out_pdf: str | Path,
                 bridge_name: str | None = None) -> Path:
    """project.h5 → PDF 리포트. 반환: 저장 경로."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    project_path, out_pdf = Path(project_path), Path(out_pdf)
    with h5py.File(project_path, "r") as f:
        im, fm = _meta(f, "/insar"), _meta(f, "/fram")
        los = f["/insar/los"][()] if "/insar/los" in f else None
        xyz = f["/insar/xyz"][()] if "/insar/xyz" in f else None
        coh = f["/insar/coherence"][()] if "/insar/coherence" in f else None
        cri = f["/fram/CRI"][()] if "/fram/CRI" in f else None
        cal = f["/fram/calibrated_risk"][()] if "/fram/calibrated_risk" in f else None
        EI = f["/pinn/EI"][()] if "/pinn/EI" in f else None
        alpha = f["/pinn/alpha"][()] if "/pinn/alpha" in f else None
        nat = f["/pinn/natural_freq"][()] if "/pinn/natural_freq" in f else None
        dates = _dates(f)

    N = im.get("n_points", los.shape[0] if los is not None else 0)
    M = im.get("n_dates", los.shape[1] if los is not None else 0)
    warn = fm.get("warning", {})
    level = warn.get("level", "—")
    members = ", ".join(warn.get("critical_members", [])) or "—"
    name = bridge_name or im.get("bridge_name") or project_path.stem
    period = f"{dates[0]}~{dates[-1]}" if dates else f"{M} 시점"

    Path(out_pdf).parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(out_pdf) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))            # A4
        fig.suptitle(f"inframon 교량 모니터링 리포트\n{name}", fontsize=15, fontweight="bold")
        # 요약 텍스트
        ax = fig.add_axes([0.07, 0.72, 0.86, 0.16]); ax.axis("off")
        lines = [
            f"기간: {period}    측정점: {N}    시점: {M}",
            f"평균 coherence: {float(np.mean(coh)):.3f}" if coh is not None else "",
            f"최대 CRI: {fm.get('cri_global_max', float('nan')):.3f}    경보: {level}    위험부재: {members}",
        ]
        if cal is not None:
            lines.append(f"최대 붕괴확률(isotonic): {float(np.max(cal)) * 100:.1f} %")
        if EI is not None:
            lines.append(f"평균 EI: {float(np.mean(EI)):.2e}    평균 α: {float(np.mean(alpha)):.2e} /°C"
                         f"    고유진동수: {', '.join(f'{x:.2f}' for x in np.atleast_1d(nat))} Hz")
        lines.append(f"생성: {datetime.utcnow().strftime('%Y-%m-%d')}    소스: {project_path.name}")
        ax.text(0, 1, "\n".join(x for x in lines if x), va="top", fontsize=10, family="monospace")

        # CRI_max 시계열
        if cri is not None:
            ax1 = fig.add_axes([0.1, 0.44, 0.82, 0.22])
            ax1.plot(np.arange(M), cri.max(axis=0), color="crimson", lw=1.5)
            ax1.set_title("전역 최대 CRI 시계열"); ax1.set_xlabel("시점 index"); ax1.set_ylabel("CRI")
            ax1.axhline(0.85, ls="--", c="red", alpha=.5); ax1.axhline(0.6, ls="--", c="orange", alpha=.5)
            ax1.grid(alpha=.3)

        # 점별 최종 LOS 변위 산포(경위도)
        if los is not None and xyz is not None:
            ax2 = fig.add_axes([0.1, 0.08, 0.82, 0.28])
            sc = ax2.scatter(xyz[:, 0], xyz[:, 1], c=los[:, -1], cmap="RdBu_r", s=6)
            ax2.set_title("최종 시점 LOS 변위 (mm)"); ax2.set_xlabel("lon"); ax2.set_ylabel("lat")
            fig.colorbar(sc, ax=ax2, shrink=0.8, label="LOS mm")
        pdf.savefig(fig); plt.close(fig)
    return out_pdf


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "data/project.h5"
    out = sys.argv[2] if len(sys.argv) > 2 else "data/report.pdf"
    print("wrote", build_report(p, out))
