"""4-Track CS 융합 — 여러 처리 *방법*(A=PS/B=SBAS/C=QPS/D=PhaseLinking)의 변위
시계열을 하나의 강건한 합의(consensus) 시계열로 융합 + 트랙 간 일치도(실데이터 검증 지표).

asc/desc 기하 융합(`fusion.py`)과 다르다: 그건 서로 다른 *관측 기하* 2개로 연직/수평을
분리. 여기선 같은 변위를 추정한 서로 다른 *처리 방법* 결과들을 합의로 융합한다.

CS/robust 융합: 각 방법 고유 오차·이상치를 **sparse outlier** 로 보고, 점·시점마다
**coherence 가중 중앙값(L1 합의)** 으로 억제한다 (중앙값 = L1 편차 최소화 = 압축센싱의
sparsity 촉진과 같은 원리). 단일 방법의 튀는 값에 강건.

**트랙 간 불일치(MAD) = 신뢰도/검증 지표**: 독립적으로 처리한 방법들이 서로 일치할수록
그 점의 변위를 신뢰할 수 있다. → 실데이터 검증(cross-validation)에 직접 활용.

산출: 융합 Track H5(pixel_lonlat/epochs/los_mm/coh + agreement/confidence/n_used).
→ import_track_h5 로 /insar 인제스트 → PINN/FRAM. (설계 STATUS: 4-Track → CS 융합 → PINN)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .fusion import _abs_days, _match_nearest, _to_local_m
from .track_reader import TrackData


@dataclass
class TrackFusion:
    lonlat: np.ndarray          # [N,2] (ref 트랙 기준)
    date_labels: np.ndarray     # [M] S8
    epochs: np.ndarray          # [M] int YYYYMMDD
    los_mm: np.ndarray          # [N,M] 융합 변위
    coherence: np.ndarray       # [N] 평균 coherence
    agreement_mm: np.ndarray    # [N] 트랙 간 MAD 평균 (낮을수록 일치)
    confidence: np.ndarray      # [N] ∈[0,1] (일치도→신뢰도)
    n_used: np.ndarray          # [N] 점별 기여 트랙 수
    meta: dict = field(default_factory=dict)


def _wmedian(vals: np.ndarray, w: np.ndarray) -> float:
    """가중 중앙값(L1 합의)."""
    o = np.argsort(vals)
    v, cw = vals[o], np.cumsum(w[o])
    if cw[-1] <= 0:
        return float(np.median(vals))
    return float(v[min(int(np.searchsorted(cw, 0.5 * cw[-1])), len(v) - 1)])


def fuse_tracks(tracks: list[TrackData], *, ref_index: int = 0,
                max_dist_m: float = 30.0, agree_scale_mm: float = 3.0) -> TrackFusion:
    """여러 방법-트랙을 ref 트랙의 점·시점에 정합해 CS(가중중앙값) 합의 융합한다."""
    if len(tracks) < 2:
        raise ValueError("CS 융합에는 트랙이 2개 이상 필요합니다.")
    ref = tracks[ref_index]
    N, M = ref.los.shape
    ref_days = _abs_days(ref.date_labels)
    lon0, lat0 = float(ref.lonlat[:, 0].mean()), float(ref.lonlat[:, 1].mean())
    ref_xy = _to_local_m(ref.lonlat, lon0, lat0)

    stacks = [ref.los.astype(float)]                                   # [N,M]
    cohs = [np.broadcast_to(ref.coherence.astype(float)[:, None], (N, M)).copy()]
    matched_frac = {ref_index: 1.0}

    for j, t in enumerate(tracks):
        if j == ref_index:
            continue
        t_xy = _to_local_m(t.lonlat, lon0, lat0)
        idx, ok = _match_nearest(ref_xy, t_xy, max_dist_m)
        t_days = _abs_days(t.date_labels)
        aligned = np.full((N, M), np.nan)
        cohj = np.full((N, M), np.nan)
        order = np.argsort(t_days)
        td_s = t_days[order]
        for i in np.where(ok)[0]:
            yv = t.los[idx[i]].astype(float)[order]
            aligned[i] = np.interp(ref_days, td_s, yv, left=np.nan, right=np.nan)
            cohj[i] = float(t.coherence[idx[i]])
        stacks.append(aligned)
        cohs.append(cohj)
        matched_frac[j] = float(ok.mean())

    S = np.stack(stacks, axis=0)                                       # [K,N,M]
    Wt = np.stack(cohs, axis=0)
    K = S.shape[0]

    fused = np.full((N, M), np.nan)
    for i in range(N):
        for k in range(M):
            v, w = S[:, i, k], Wt[:, i, k]
            m = np.isfinite(v) & np.isfinite(w)
            if m.any():
                fused[i, k] = _wmedian(v[m], np.clip(w[m], 1e-6, None))
    fused = np.where(np.isfinite(fused), fused, 0.0).astype(np.float32)

    # 트랙 간 일치도(MAD) — 실데이터 검증 지표
    with np.errstate(invalid="ignore"):
        med = np.nanmedian(S, axis=0)                                  # [N,M]
        mad = np.nanmedian(np.abs(S - med[None]), axis=0)              # [N,M]
        agreement_mm = np.where(np.isfinite(mad), mad, 0.0).mean(axis=1).astype(np.float32)
    confidence = np.exp(-agreement_mm / max(agree_scale_mm, 1e-6)).astype(np.float32)
    n_used = np.isfinite(S).any(axis=2).sum(axis=0).astype(np.int32)   # [N]
    coh_mean = np.nanmean(np.where(np.isfinite(Wt), Wt, np.nan), axis=(0, 2)).astype(np.float32)
    coh_mean = np.where(np.isfinite(coh_mean), coh_mean, ref.coherence.astype(np.float32))

    epochs = np.asarray(ref.date_labels).astype(str)
    epochs = np.array([int(s) for s in epochs], dtype=np.int32)
    return TrackFusion(
        lonlat=ref.lonlat.copy(), date_labels=np.asarray(ref.date_labels), epochs=epochs,
        los_mm=fused, coherence=coh_mean, agreement_mm=agreement_mm,
        confidence=confidence, n_used=n_used,
        meta={"n_tracks": K, "method": "cs_weighted_median_L1",
              "matched_frac": matched_frac, "max_dist_m": max_dist_m,
              "agree_scale_mm": agree_scale_mm},
    )


def write_fused_track_h5(result: TrackFusion, out_path: str) -> str:
    """융합 결과를 Track H5(pixel_lonlat/epochs/los_mm/coh + 검증 지표)로 저장."""
    import h5py
    with h5py.File(out_path, "w") as f:
        f.create_dataset("pixel_lonlat", data=result.lonlat.astype(np.float64))
        f.create_dataset("epochs", data=result.epochs)
        f.create_dataset("los_mm", data=result.los_mm)
        f.create_dataset("coh", data=result.coherence)
        f.create_dataset("agreement_mm", data=result.agreement_mm)   # 검증 지표
        f.create_dataset("confidence", data=result.confidence)
        f.create_dataset("n_used", data=result.n_used)
        f.attrs["FILE_TYPE"] = "inframon_cs_fused"
        f.attrs["n_tracks"] = result.meta["n_tracks"]
        f.attrs["fusion_method"] = result.meta["method"]
    return out_path


def fusion_report(result: TrackFusion, *, conf_thresh: float = 0.6) -> dict:
    """실데이터 검증 요약 — 트랙 간 일치도 통계."""
    return {
        "n_points": int(result.los_mm.shape[0]),
        "n_tracks": int(result.meta["n_tracks"]),
        "agreement_mm_median": float(np.median(result.agreement_mm)),
        "agreement_mm_p90": float(np.percentile(result.agreement_mm, 90)),
        "confident_frac": float((result.confidence >= conf_thresh).mean()),
        "mean_tracks_per_point": float(result.n_used.mean()),
        "matched_frac": result.meta["matched_frac"],
    }
