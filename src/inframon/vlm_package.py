"""project.h5 → VLM 입력 패키지 (KAIA 4단계 데이터 핸드오프).

VLM 추론은 타 팀 파트. 우리는 **VLM 이 그대로 삼킬 수 있는 자기기술적·단위통일 패키지**를
산출한다(InSAR 수직변위 + PINN 가상센싱 부분). CAD·정밀안전보고서는 타 소스에서 합류한다.

폴더 번들 `<bridge_id>_vlm/`:
  manifest.json    자기기술 — 교량메타·단위규약·좌표계·스키마·포함산출물·provenance
  displacement.csv 점×시점 롱포맷 (export.py 재사용)
  summary.json     VLM 이 바로 읽는 구조화 다이제스트(핫스팟·부재롤업·PINN·위험참고)
  narrative.md     템플릿 기반(LLM 아님) 자연어 서술 = VLM grounded 컨텍스트
  figures/*.png    Vision 입력용 — VLM 은 이미지를 본다(변위맵·CRI·시계열·성분)

통일 규약: 변위 mm · 거리/고도 m · 날짜 ISO YYYY-MM-DD · 좌표 WGS84(lat,lon)+EPSG:5179 ·
부재 enum(deck/pier/abutment/bearing) · CRI[0,1]. contracts/ 는 성역(읽어 변환만).

위험판정(CRI·경보)은 포함하되 manifest 가 **'내부 물리지표 — 시방서 기반 코드 판정 아님,
참고용'** 으로 명시한다(최종 안전성 평가는 VLM+시방서).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from . import export
from .api import transform
from .api.transform import WGS84
from .contracts.io import ProjectStore

SCHEMA = "inframon.vlm_package/1.0"
HOTSPOT_K = 5

UNITS = {
    "displacement": "mm", "distance_elevation": "m", "date": "ISO-8601 (YYYY-MM-DD)",
    "coordinate_display": "EPSG:4326 (lat, lon)", "coordinate_source": "EPSG:5179",
    "cri": "0-1 (dimensionless)", "EI": "N·m^2", "alpha": "1/K", "natural_freq": "Hz",
}
MEMBERS = ["deck", "pier", "abutment", "bearing"]
RISK_NOTE = ("CRI·경보는 inframon 내부 물리지표(FRAM 공명위험지수)다. "
             "대한민국 시방서/법규 기반의 코드 판정이 아니라 참고용 신호이며, "
             "최종 안전성 평가는 VLM+시방서가 수행한다.")


def _rate_mm_per_yr(series: np.ndarray, days: np.ndarray) -> float:
    """[M] 변위(mm)·[M] epoch days → 선형 추세 속도(mm/yr)."""
    if len(series) < 2:
        return 0.0
    slope = float(np.polyfit(days, series, 1)[0])      # mm/day
    return slope * 365.0


def _caveats(has_vertical: bool, has_pinn: bool, has_fram: bool) -> list[str]:
    """관측 한계 — VLM 이 안전성 판단 시 과대해석을 피하도록 채널 가용성을 해석지침으로 번역.

    InSAR 의 본질적 한계(LOS 1성분·부호규약)와 이 산출물의 가용성(연직/PINN/CRI)을 명시한다.
    """
    cav = [
        "LOS 변위는 위성-지표 시선방향 1성분이다(부호: 음수=위성에서 멀어짐, 통상 침하 경향).",
    ]
    if has_vertical:
        cav.append("asc+desc 융합으로 연직 변위(처짐·침하)가 직접 분리됨 — 연직 해석 신뢰 가능.")
    else:
        cav.append("단일 궤도 — 연직 변위(처짐·침하)는 **직접 측정되지 않음**. LOS·종축만 제공되며, "
                   "처짐/침하 판단은 종축 deprojection 추정에 의존하므로 보수적으로 해석할 것.")
    if not has_pinn:
        cav.append("PINN 구조해석 없음 — 절대 강성(EI)·성분분해(열팽창/하중/침하/이상) 미제공.")
    if not has_fram:
        cav.append("CRI 위험지표 없음 — 위험 정량은 외부(시방서) 평가에 의존.")
    cav.append("InSAR 측점은 강반사 산란체(PS/DS)에 한정 — 교량 전 영역을 균일 표본하지 않음.")
    return cav


def build_summary(store: ProjectStore, *, bridge_id: str = "", to_crs: str = WGS84) -> dict[str, Any]:
    """VLM 이 바로 읽는 구조화 다이제스트."""
    ins = transform._insar(store)
    pinn = transform._pinn(store)
    fram = transform._fram(store)

    latlon = transform.xyz_to_latlon(store.read_array(ins.xyz_ds), to_crs)
    member = np.asarray(store.read_array(ins.member_ds))
    pid = np.asarray(store.read_array(ins.point_id_ds))
    los = np.asarray(store.read_array(ins.los_ds))            # [N,M] mm
    days = np.asarray(store.read_array(ins.dates_ds), float)  # [M]
    dates = [transform.epoch_days_to_iso(d) for d in days]
    N, M = int(ins.n_points), int(ins.n_dates)
    vert = np.asarray(store.read_array(ins.vertical_ds)) if ins.vertical_ds else None
    cri = np.asarray(store.read_array(fram.CRI_ds)) if fram is not None else None

    # 변위 통계
    def stat(a: np.ndarray) -> dict[str, float]:
        return {"min": round(float(a.min()), 3), "max": round(float(a.max()), 3),
                "mean": round(float(a.mean()), 3)}
    disp = {"los_mm": stat(los)}
    if vert is not None:
        disp["vertical_mm"] = stat(vert)

    # 핫스팟 랭킹: CRI(말기) > |연직 말기| > |LOS 속도|
    if cri is not None:
        rank = cri[:, -1]
    elif vert is not None:
        rank = np.abs(vert[:, -1])
    else:
        rank = np.abs(np.array([_rate_mm_per_yr(los[i], days) for i in range(N)]))
    top = np.argsort(rank)[::-1][:min(HOTSPOT_K, N)]

    primary = vert if vert is not None else los     # 누적/속도 기준 채널
    hotspots = []
    for i in top:
        hotspots.append({
            "point_id": int(pid[i]),
            "member": transform.member_label(member[i]),
            "lat": round(float(latlon[i, 0]), 7), "lon": round(float(latlon[i, 1]), 7),
            "cumulative_disp_mm": round(float(primary[i, -1] - primary[i, 0]), 3),
            "rate_mm_per_yr": round(_rate_mm_per_yr(primary[i], days), 3),
            "cri_latest": (round(float(cri[i, -1]), 4) if cri is not None else None),
            "channel": ("vertical" if vert is not None else "los"),
        })

    # 부재별 롤업
    members = []
    for mi, name in enumerate(MEMBERS):
        sel = member == mi
        if not sel.any():
            continue
        ch = primary[sel]
        members.append({
            "member": name, "n_points": int(sel.sum()),
            "max_abs_disp_mm": round(float(np.abs(ch).max()), 3),
            "mean_cri": (round(float(cri[sel, -1].mean()), 4) if cri is not None else None),
        })

    out: dict[str, Any] = {
        "schema": SCHEMA, "bridge_id": bridge_id,
        "observation": {"date_first": dates[0], "date_last": dates[-1],
                        "n_dates": M, "n_points": N},
        "units": UNITS,
        "displacement": disp,
        "settlement_hotspots": hotspots,
        "members": members,
        "pinn": None, "risk_reference": None,
        "channels_present": {"vertical_fused": vert is not None,
                             "pinn": pinn is not None, "fram_cri": cri is not None},
        "observational_caveats": _caveats(vert is not None, pinn is not None, cri is not None),
    }

    if pinn is not None:
        EI = np.asarray(store.read_array(pinn.EI_ds))
        nat = np.asarray(store.read_array(pinn.natural_freq_ds)).ravel()
        # 저강성(손상 의심) = EI 하위 10% 점
        thr = float(np.percentile(EI, 10))
        low = np.where(EI <= thr)[0]
        out["pinn"] = {
            "EI_Nm2": {"min": float(EI.min()), "median": float(np.median(EI)),
                       "max": float(EI.max())},
            "low_stiffness_points": [int(pid[i]) for i in low[:HOTSPOT_K]],
            "natural_frequencies_hz": [round(float(x), 4) for x in nat[:6]],
        }

    if fram is not None:
        w = fram.warning
        out["risk_reference"] = {
            "note": RISK_NOTE,
            "cri_global_max": round(float(fram.cri_global_max), 4),
            "warning_level": w.level,
            "critical_members": list(w.critical_members),
            "function_states": dict(w.function_states),
            "lead_time_forecast_days": w.lead_time_forecast_days,
        }
    return out


def build_manifest(summary: dict[str, Any], *, files: list[str]) -> dict[str, Any]:
    """자기기술 매니페스트 — VLM 팀이 스키마/단위/enum 을 추측하지 않게."""
    return {
        "schema": SCHEMA,
        "producer": "inframon (InSAR + PINN)",
        "scope": "InSAR 수직/시선/종방향 변위 + PINN 가상센싱 구조지표. "
                 "CAD·정밀안전보고서는 외부 소스에서 합류.",
        "bridge_id": summary["bridge_id"],
        "units": UNITS,
        "enums": {"member": MEMBERS, "warning_level": ["정상", "주의", "경고", "위험"]},
        "files": files,
        "csv_columns": export.COLUMNS,
        "risk_disclaimer": RISK_NOTE,
        "observation": summary["observation"],
        "channels_present": summary["channels_present"],
    }


def build_narrative(summary: dict[str, Any]) -> str:
    """템플릿 기반(LLM 아님) 자연어 서술 — VLM 의 grounded 컨텍스트."""
    o = summary["observation"]
    lines = [
        f"# 교량 변위 모니터링 데이터 ({summary['bridge_id'] or '미지정'})",
        "",
        f"관측 기간 {o['date_first']} ~ {o['date_last']} ({o['n_dates']}개 시점), "
        f"InSAR 측점 {o['n_points']}개. 변위 단위 mm, 좌표 WGS84(lat,lon).",
        "",
        "## 변위 요약",
    ]
    d = summary["displacement"]
    lines.append(f"- LOS 변위: {d['los_mm']['min']}~{d['los_mm']['max']} mm (평균 {d['los_mm']['mean']}).")
    if "vertical_mm" in d:
        v = d["vertical_mm"]
        lines.append(f"- 연직 변위(asc+desc 융합): {v['min']}~{v['max']} mm (평균 {v['mean']}). "
                     "음수는 침하(처짐) 경향.")
    lines += ["", "## 변위 핫스팟 (상위)"]
    for h in summary["settlement_hotspots"]:
        cri = f", CRI {h['cri_latest']}" if h["cri_latest"] is not None else ""
        lines.append(f"- 측점 {h['point_id']}({h['member']}) @({h['lat']},{h['lon']}): "
                     f"누적 {h['cumulative_disp_mm']}mm, 속도 {h['rate_mm_per_yr']}mm/yr{cri}.")
    if summary.get("pinn"):
        p = summary["pinn"]
        lines += ["", "## PINN 구조지표",
                  f"- 휨강성 EI(역산): 중앙값 {p['EI_Nm2']['median']:.3e} N·m². "
                  f"저강성(손상 의심) 측점: {p['low_stiffness_points']}.",
                  f"- 고유진동수(Hz): {p['natural_frequencies_hz']}."]
    if summary.get("risk_reference"):
        r = summary["risk_reference"]
        lines += ["", "## 위험 참고지표 (시방서 판정 아님)",
                  f"- 내부 CRI 최대 {r['cri_global_max']}, 경보 '{r['warning_level']}'. "
                  f"위험 부재: {r['critical_members']}.",
                  f"- ⚠️ {r['note']}"]
    if summary.get("observational_caveats"):
        lines += ["", "## 관측 한계 (해석 시 유의)"]
        lines += [f"- {c}" for c in summary["observational_caveats"]]
    return "\n".join(lines) + "\n"


def render_figures(store: ProjectStore, summary: dict[str, Any], out_dir: Path,
                   *, to_crs: str = WGS84) -> list[str]:
    """Vision 입력용 PNG 생성(matplotlib Agg, 헤드리스). 산출물 없으면 해당 그림 생략."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ins = transform._insar(store)
    pinn = transform._pinn(store)
    fram = transform._fram(store)
    latlon = transform.xyz_to_latlon(store.read_array(ins.xyz_ds), to_crs)
    pid = np.asarray(store.read_array(ins.point_id_ds))
    los = np.asarray(store.read_array(ins.los_ds))
    days = np.asarray(store.read_array(ins.dates_ds), float)
    dates = [transform.epoch_days_to_iso(d) for d in days]
    vert = np.asarray(store.read_array(ins.vertical_ds)) if ins.vertical_ds else None
    cri = np.asarray(store.read_array(fram.CRI_ds)) if fram is not None else None
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[str] = []

    # 1) 변위 지도 — 색=연직(있으면) 또는 LOS 말기
    chan = vert if vert is not None else los
    label = "vertical (mm)" if vert is not None else "LOS (mm)"
    fig, ax = plt.subplots(figsize=(7, 5))
    sc = ax.scatter(latlon[:, 1], latlon[:, 0], c=chan[:, -1], cmap="RdYlBu", s=30)
    ax.set_xlabel("lon"); ax.set_ylabel("lat")
    ax.set_title(f"Displacement map @ {dates[-1]} ({summary['bridge_id'] or 'bridge'})")
    fig.colorbar(sc, ax=ax, label=label)
    f1 = out_dir / "displacement_map.png"; fig.savefig(f1, dpi=110, bbox_inches="tight")
    plt.close(fig); made.append(f1.name)

    # 2) CRI 히트맵 (FRAM 있을 때)
    if cri is not None:
        order = np.argsort(latlon[:, 1])           # lon 순(공간 정렬)
        fig, ax = plt.subplots(figsize=(8, 5))
        im = ax.imshow(cri[order], aspect="auto", cmap="hot_r", vmin=0, vmax=1,
                       extent=[0, len(dates), 0, cri.shape[0]])
        ax.set_xlabel("time index"); ax.set_ylabel("point (sorted by lon)")
        ax.set_title("CRI heatmap (reference physical index)")
        fig.colorbar(im, ax=ax, label="CRI [0-1]")
        f2 = out_dir / "cri_heatmap.png"; fig.savefig(f2, dpi=110, bbox_inches="tight")
        plt.close(fig); made.append(f2.name)

    # 3) 핫스팟 시계열
    top_ids = [h["point_id"] for h in summary["settlement_hotspots"]]
    idmap = {int(p): i for i, p in enumerate(pid)}
    fig, ax = plt.subplots(figsize=(8, 5))
    for hid in top_ids:
        i = idmap[hid]
        ax.plot(range(len(dates)), chan[i], marker=".", label=f"pt {hid}")
    ax.set_xlabel("time index"); ax.set_ylabel(label)
    ax.set_title("Hotspot displacement time-series")
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    f3 = out_dir / "hotspot_timeseries.png"; fig.savefig(f3, dpi=110, bbox_inches="tight")
    plt.close(fig); made.append(f3.name)

    # 4) PINN 성분 분해 (최상위 핫스팟 1점)
    if pinn is not None and top_ids:
        i = idmap[top_ids[0]]
        comps = {"thermal": pinn.comp_thermal_ds, "load": pinn.comp_load_ds,
                 "settle": pinn.comp_settle_ds, "anomaly": pinn.comp_anomaly_ds}
        fig, ax = plt.subplots(figsize=(8, 5))
        for name, ds in comps.items():
            ax.plot(range(len(dates)), np.asarray(store.read_array(ds))[i],
                    marker=".", label=name)
        ax.set_xlabel("time index"); ax.set_ylabel("displacement (mm)")
        ax.set_title(f"PINN decomposition — point {top_ids[0]}")
        ax.legend(fontsize=8); ax.grid(alpha=0.3)
        f4 = out_dir / "pinn_components.png"; fig.savefig(f4, dpi=110, bbox_inches="tight")
        plt.close(fig); made.append(f4.name)

    return made


def export_vlm_package(h5_path: str | Path, out_dir: str | Path, *,
                       bridge_id: str = "", to_crs: str = WGS84,
                       with_figures: bool = True, zip_it: bool = False) -> dict[str, Any]:
    """project.h5 → VLM 입력 패키지 폴더(+선택 ZIP). 요약 dict 반환."""
    out_dir = Path(out_dir)
    fig_dir = out_dir / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)

    with ProjectStore(Path(h5_path), mode="r") as store:
        summary = build_summary(store, bridge_id=bridge_id, to_crs=to_crs)
        # CSV
        rows = export.build_rows(store, bridge_id=bridge_id, to_crs=to_crs)
        figs: list[str] = []
        if with_figures:
            figs = render_figures(store, summary, fig_dir, to_crs=to_crs)

    import csv as _csv
    csv_path = out_dir / "displacement.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        wr = _csv.DictWriter(f, fieldnames=export.COLUMNS); wr.writeheader(); wr.writerows(rows)

    files = ["manifest.json", "displacement.csv", "summary.json", "narrative.md"]
    files += [f"figures/{n}" for n in figs]
    manifest = build_manifest(summary, files=files)
    narrative = build_narrative(summary)

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "narrative.md").write_text(narrative, encoding="utf-8")

    result = {"dir": str(out_dir), "files": files, "rows": len(rows),
              "n_points": summary["observation"]["n_points"],
              "n_dates": summary["observation"]["n_dates"],
              "figures": figs, "channels": summary["channels_present"], "zip": None}

    if zip_it:
        import zipfile
        zip_path = out_dir.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in files:
                zf.write(out_dir / rel, arcname=rel)
        result["zip"] = str(zip_path)
    return result
