"""잔차고도(PSI DEM 오차) 추정 — 점이 **지면 위인가 교면 위인가**를 고도로 말한다.

지오코딩·지형위상 제거는 DEM(지면)을 기준으로 한다. 데크 위 산란체는 DEM 보다 δh 만큼
높으므로, 제거되지 않은 지형위상이 **수직기선 B⊥ 에 비례해** 남는다:

    φ_topo = (4π/λ) · B⊥ · Δh / (R · sinθ)       →  LOS[mm] = 1000 · B⊥ · Δh / (R sinθ)

간섭쌍마다 B⊥ 가 다르므로, 점별로 시계열을 `c + v·t + K_j·Δh` 로 최소제곱 적합하면
Δh 가 나온다. 이것이 PSI 의 DEM 오차 추정이고, 건기연 브리프 (b) 'DEM 대비 상대고도'다.

**감도가 낮다는 것을 숨기지 않는다.** Sentinel-1 은 B⊥ 가 ±100 m 안이라 K 가
±0.18 mm/m 이다 — Δh 10 m 가 LOS 1.8 mm 다. 25시점·잡음 7 mm 면 점별 σ_Δh ≈ 16 m 로
데크(+10 m)와 지면(0)을 점 하나로 가르지 못한다. 그래서 점별 σ 를 함께 내고, 교면 위
점들을 **묶어서** 지면 점과 비교하는 집단 검정을 제공한다(N 점 평균 → σ/√N).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

RADAR_WAVELENGTH_M = 0.05546576


@dataclass
class ResidualHeight:
    dh_m: np.ndarray            # [N] 잔차고도(기준점 대비, DEM 기준 상대고도)
    sigma_m: np.ndarray         # [N] 1σ
    vel_mm_yr: np.ndarray       # [N] 함께 추정된 속도
    resid_rms_mm: np.ndarray    # [N] 적합 잔차 RMS
    K_mm_per_m: np.ndarray      # [M] 시점별 감도
    meta: dict = field(default_factory=dict)

    def group_test(self, on_deck: np.ndarray) -> dict:
        """교면 위 점 vs 그 밖 점 — 평균 Δh 차이와 그 유의성(양측 z)."""
        on = np.asarray(on_deck, bool)
        a, b = self.dh_m[on], self.dh_m[~on]
        sa, sb = self.sigma_m[on], self.sigma_m[~on]
        if a.size == 0 or b.size == 0:
            return {"ok": False, "reason": "교면 위 또는 밖 점이 없다"}
        wa, wb = 1.0 / np.maximum(sa, 1e-6) ** 2, 1.0 / np.maximum(sb, 1e-6) ** 2
        ma, mb = float(np.sum(wa * a) / wa.sum()), float(np.sum(wb * b) / wb.sum())
        ea, eb = float(1.0 / np.sqrt(wa.sum())), float(1.0 / np.sqrt(wb.sum()))
        diff = ma - mb
        se = float(np.hypot(ea, eb))
        z = diff / se if se > 0 else float("nan")
        return {"ok": True, "n_on": int(on.sum()), "n_off": int((~on).sum()),
                "mean_on_m": ma, "se_on_m": ea, "mean_off_m": mb, "se_off_m": eb,
                "diff_m": diff, "se_diff_m": se, "z": z,
                "verdict": ("교면 위 점이 지면 점보다 유의하게 높다(z>2)" if z > 2 else
                            ("⚠ '교면 위' 점이 지면보다 유의하게 **낮다**(z<−2) — 교면 산란체가 "
                             "아닐 수 있다(제방·교대 주변 지면). 평면 선택을 다시 볼 것" if z < -2 else
                             "고도 차이를 유의하게 확인하지 못함(|z|≤2) — 감도 부족 또는 차이 없음"))}


def read_bperp_dim(dim_path: str | Path) -> dict:
    """SNAP `.dim` 의 Baselines 메타에서 (B⊥[m], 시간기선[일], 경사거리[m], 입사각[deg])."""
    txt = Path(dim_path).read_text(encoding="utf-8", errors="replace")
    perps = [float(x) for x in re.findall(r'name="Perp Baseline"[^>]*>([-\d.]+)<', txt)]
    temps = [float(x) for x in re.findall(r'name="Temp Baseline"[^>]*>([-\d.]+)<', txt)]
    nz = [(p, t) for p, t in zip(perps, temps) if abs(p) > 1e-6]
    bperp, btemp = (nz[0] if nz else (float("nan"), float("nan")))
    R = re.search(r'slant_range_to_first_pixel[^>]*>([\d.]+)<', txt)
    inc = re.search(r'incidence_near[^>]*>([\d.]+)<', txt)
    return {"bperp_m": bperp, "btemp_days": btemp,
            "slant_range_m": float(R.group(1)) if R else float("nan"),
            "incidence_deg": float(inc.group(1)) if inc else float("nan")}


def collect_bperp(proc_dir: str | Path, master: str, pattern: str = "snaphu_{m}_*/wrapped.dim"
                  ) -> dict[str, dict]:
    """star 네트워크 처리 폴더에서 슬레이브 날짜 → 기선 정보."""
    proc = Path(proc_dir)
    out: dict[str, dict] = {}
    for dim in sorted(proc.glob(pattern.format(m=master))):
        sec = dim.parent.name.split("_")[-1]
        out[sec] = read_bperp_dim(dim)
    return out


def estimate_residual_height(los_mm: np.ndarray, days: np.ndarray, bperp_m: np.ndarray,
                             slant_range_m: float, incidence_deg,
                             *, wavelength_m: float = RADAR_WAVELENGTH_M) -> ResidualHeight:
    """점별 `c + v·t + K_j·Δh` 최소제곱 → Δh ± σ.

    los_mm[N,M]·days[M]·bperp_m[M] 는 같은 시점 순서여야 한다(기준 시점은 B⊥=0, t=0).
    incidence_deg 는 스칼라 또는 [N].
    """
    los = np.asarray(los_mm, float)
    t = np.asarray(days, float) / 365.25
    bp = np.asarray(bperp_m, float)
    N, M = los.shape
    inc = np.broadcast_to(np.radians(np.asarray(incidence_deg, float)), (N,))
    dh = np.full(N, np.nan)
    sig = np.full(N, np.nan)
    vel = np.full(N, np.nan)
    rms = np.full(N, np.nan)
    K_ref = 1000.0 * bp / (slant_range_m * np.sin(np.median(inc)))     # 대표 감도(보고용)
    for i in range(N):
        y = los[i]
        ok = np.isfinite(y) & np.isfinite(bp)
        if ok.sum() < 5:
            continue
        K = 1000.0 * bp[ok] / (slant_range_m * np.sin(inc[i]))          # mm per m
        X = np.column_stack([np.ones(ok.sum()), t[ok], K])
        beta, *_ = np.linalg.lstsq(X, y[ok], rcond=None)
        r = y[ok] - X @ beta
        dof = max(ok.sum() - 3, 1)
        s2 = float((r ** 2).sum() / dof)
        try:
            cov = s2 * np.linalg.inv(X.T @ X)
            sig[i] = float(np.sqrt(max(cov[2, 2], 0.0)))
        except np.linalg.LinAlgError:
            sig[i] = np.nan
        dh[i], vel[i], rms[i] = float(beta[2]), float(beta[1]), float(np.sqrt(s2))
    return ResidualHeight(
        dh_m=dh, sigma_m=sig, vel_mm_yr=vel, resid_rms_mm=rms, K_mm_per_m=K_ref,
        meta={"n_epochs": int(M), "bperp_min_m": float(np.nanmin(bp)),
              "bperp_max_m": float(np.nanmax(bp)), "bperp_std_m": float(np.nanstd(bp)),
              "slant_range_m": float(slant_range_m),
              "height_ambiguity_m_at_100m": float(wavelength_m * slant_range_m
                                                  * np.sin(np.median(inc)) / 200.0),
              "note": ("Δh 는 기준점(reference) 대비 상대값이며 DEM 오차를 포함한다. "
                       "Sentinel-1 B⊥ 가 작아 점별 σ 가 크다 — 집단 검정으로 읽을 것")})


def write_to_track(track_h5: str | Path, rh: ResidualHeight, *, group_test: dict | None = None,
                   source: str = "") -> None:
    """트랙 h5 에 `residual_height_m`·`residual_height_sigma_m` 를 쓴다(있으면 덮는다)."""
    import json

    import h5py

    with h5py.File(str(track_h5), "a") as f:
        for k, v in (("residual_height_m", rh.dh_m), ("residual_height_sigma_m", rh.sigma_m),
                     ("residual_height_vel_mm_yr", rh.vel_mm_yr)):
            if k in f:
                del f[k]
            f.create_dataset(k, data=np.asarray(v, np.float32))
        f.attrs["residual_height"] = json.dumps(
            {**rh.meta, "source": source, "group_test": group_test or {}}, ensure_ascii=False)


def run_snap_star(track_h5: str | Path, proc_dir: str | Path, master: str, *,
                  on_deck: np.ndarray | None = None) -> tuple[ResidualHeight, dict | None]:
    """SNAP star 네트워크 트랙 한 번에: 기선 수집 → 추정 → (집단 검정) → 트랙 기록."""
    import h5py

    bp = collect_bperp(proc_dir, master)
    if not bp:
        raise FileNotFoundError(f"{proc_dir} 에서 snaphu_{master}_*/wrapped.dim 을 찾지 못했습니다")
    from datetime import datetime

    with h5py.File(str(track_h5), "r") as f:
        ep = [str(int(x)) if not isinstance(x, (bytes, str)) else
              (x.decode() if isinstance(x, bytes) else x) for x in f["epochs"][()]]
        los = np.asarray(f["los_mm"][()], float)
        inc = np.asarray(f["incidenceAngle"][()], float) if "incidenceAngle" in f else 39.0
    d0 = datetime.strptime(master, "%Y%m%d")
    days = np.array([(datetime.strptime(e, "%Y%m%d") - d0).days for e in ep], float)
    missing = [e for e in ep if e != master and e not in bp]
    if missing:
        raise ValueError(f"기선이 없는 시점: {missing}")
    bperp = np.array([0.0 if e == master else bp[e]["bperp_m"] for e in ep])
    R = float(np.median([v["slant_range_m"] for v in bp.values()]))
    rh = estimate_residual_height(los, days, bperp, R, inc)
    g = rh.group_test(on_deck) if on_deck is not None else None
    write_to_track(track_h5, rh, group_test=g,
                   source=f"SNAP star {master} · {len(bp)}쌍 · {Path(proc_dir)}")
    return rh, g


def run_sarvey(track_h5: str | Path, baselines_json: str | Path, *,
               on_deck: np.ndarray | None = None) -> tuple[ResidualHeight, dict | None]:
    """SARvey 트랙 + `ifg_network.h5` 에서 뽑은 기선 JSON → 잔차고도.

    SARvey 는 pbase 를 시점별로 준다(첫 시점 기준). JSON: {dates[YYYY-MM-DD], pbase_m,
    slant_range_m, loc_inc(rad)}. 트랙 시점과 날짜로 맞춘다 — 순서가 달라도 된다.
    """
    import json
    from datetime import datetime

    import h5py

    bl = json.loads(Path(baselines_json).read_text(encoding="utf-8"))
    bdates = [d.replace("-", "") for d in bl["dates"]]
    pb = dict(zip(bdates, bl["pbase_m"]))
    with h5py.File(str(track_h5), "r") as f:
        ep = [(x.decode() if isinstance(x, bytes) else str(int(x) if not isinstance(x, str) else x))
              for x in f["epochs"][()]]
        los = np.asarray(f["los_mm"][()], float)
        inc = np.asarray(f["incidenceAngle"][()], float) if "incidenceAngle" in f \
            else float(np.degrees(bl.get("loc_inc", 0.68)))
    missing = [e for e in ep if e not in pb]
    if missing:
        raise ValueError(f"기선이 없는 시점 {len(missing)}개: {missing[:3]}…")
    d0 = datetime.strptime(ep[0], "%Y%m%d")
    days = np.array([(datetime.strptime(e, "%Y%m%d") - d0).days for e in ep], float)
    bperp = np.array([pb[e] for e in ep], float)
    bperp = bperp - bperp[0]                      # 첫 시점(기준) 대비
    rh = estimate_residual_height(los, days, bperp, float(bl["slant_range_m"]), inc)
    g = rh.group_test(on_deck) if on_deck is not None else None
    write_to_track(track_h5, rh, group_test=g,
                   source=f"SARvey pbase · {len(ep)}시점 · {Path(baselines_json).name}")
    return rh, g
