"""교축 1D 프로파일 — PS 를 교량 축을 따라 세워 연속 프로파일로 만든다.

교량 위 PS 점은 흩어져 있고, 산포 그림만으로는 "어디가 나쁜가"를 읽기 어렵다. 교량은
사실상 1차원 구조물이므로 **교축을 따라 세우면** 종방향 거동이 보인다:

  ① 점을 데크 폴리라인에 투영 → (chainage 교축거리, offset 교축직각거리)
     쉬프트 보정을 먼저 한다 — 지오코딩은 DEM 을 쓰므로 데크 점이 δh/tanθ 만큼 밀려 있고,
     그 밀림은 교축 종/횡 성분으로 갈린다(`deck_shift`).
  ② 선별: 엄격 기준은 점이 적어 **결측 구간**이 생긴다. 후보를 넓힌 뒤 시간 결맞음
     (γ_temp)으로 노이즈를 걷어내면 전 구간에 점이 남는다.
  ③ 교축 방향 등간격 구간 집계(chainage binning) → 구간 대표값(중앙값)±표준오차

산포도(①②)와 프로파일(③)은 같은 데이터를 다르게 본 것이다. ③이 종방향 이상 위치를
짚어 주고, ①②는 그 값이 어떤 점들로 만들어졌는지 보여준다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import json

import numpy as np

DEFAULT_BIN_M = 7.5           # 교축 구간 폭(5~10m 권장 — 경간·PS 밀도에 맞춘다)

# ── PS 선별 기준 ─────────────────────────────────────────────────────────
# 엄격: ADI ≤ 0.25 (고전 PS 기준). 데크 위에는 이 기준을 넘는 점이 거의 없어 결측
# 구간이 생긴다. 완화: ADI ≤ 0.40 으로 후보를 넓히고 시간 결맞음 γ_temp ≥ 0.60 으로
# 다시 걸러 노이즈를 걷어낸다. ADI 가 없는 트랙(SARvey 등)은 γ_temp 만 쓴다.
ADI_STRICT = 0.25
ADI_RELAXED = 0.40
COH_MIN = 0.60
# 노이즈 점 제거 — 같은 구간 이웃 대비 값이 튀는 점(로버스트 z > 3)·시계열 잡음이 이웃
# 중앙의 3배를 넘는 점. 선별 통과 후에도 남는 '점 하나짜리 이상'을 프로파일에서 뺀다.
NOISE_Z = 3.0
# 속도 불확실도 QC(건기연 브리프 4단계 — '속도 불확실도·코히어런스·잔차고도로 저품질 제거').
# 하드 ADI/γ 컷 대신 시계열 선형 적합의 95% CI 반폭으로 거른다. 규칙 격자를 최대한 살리고
# 신뢰구간을 결과와 함께 낸다 — 청양교에서 γ≥0.6 은 7점, CI≤1.0 ∧ γ≥0.4 는 45점.
CI95_MAX_MM_YR = 1.0          # 95% CI 반폭 상한[mm/yr]
COH_LOOSE = 0.40              # CI 모드에서 함께 거는 느슨한 결맞음 하한
ABUTMENT_ZONE_M = 8.0         # 교대 위 기준점 구역 — 교축 양끝 이 길이 안의 점
REF_BAND_MM_YR = 0.5          # 참고범위 ±0.5 mm/yr (브리프 (c) 연녹색 띠)
MIN_BIN_POINTS = 2            # 구간 대표값을 낼 최소 점수

# ── 프로파일을 "구조로 볼 수 있는가" 게이트 ────────────────────────────────
# 구간대표값의 산포(σ_between)가 구간내 잔차(σ_within)와 구별되지 않으면, 그 프로파일은
# 종방향 구조가 아니라 잡음이다. 청양교가 그랬다 — σ_b/σ_w=0.98, 커버리지 33%.
# 2D 그림은 표준오차 막대·결측표시로 정직하지만, 3D 데크 색띠로 옮기면 그 단서가
# 사라져 잡음이 손상 패턴으로 읽힌다. 그래서 색띠는 이 게이트를 통과할 때만 그린다.
MIN_SNR = 1.5                 # σ_between / σ_within 하한
MIN_COVERAGE = 0.70           # 대표값이 있는 구간 비율 하한


@dataclass
class ChainageProfile:
    """교축 프로파일 산출."""

    chainage_m: np.ndarray        # [N] 교축 거리
    offset_m: np.ndarray          # [N] 교축 직각 거리(부호 없음 — 폴리라인 투영 거리)
    value: np.ndarray             # [N] 점별 값(기본 LOS 변위속도 mm/yr)
    selected: np.ndarray          # [N] bool — 선별 통과
    quality: np.ndarray           # [N] 선별에 쓴 품질값(γ_temp 또는 1-ADI)
    bin_center_m: np.ndarray      # [B]
    bin_value: np.ndarray         # [B] 구간 대표값(중앙값)
    bin_sem: np.ndarray           # [B] 표준오차
    bin_n: np.ndarray             # [B] 구간 점수
    ci95: np.ndarray | None = None      # [N] 점별 속도 95% CI 반폭(있을 때)
    reference: dict | None = None       # 교대 기준점 적용 근거
    deck_length_m: float = 0.0
    bin_m: float = DEFAULT_BIN_M
    shift: dict | None = None     # 적용한 쉬프트 보정 근거
    meta: dict = field(default_factory=dict)

    @property
    def bin_ci95(self) -> np.ndarray:
        """구간 대표값의 95% CI 반폭 = 1.96 × SE."""
        return 1.96 * self.bin_sem

    def no_deformation(self) -> np.ndarray:
        """구간별 '변형 경향 없음' — 95% CI 가 0 을 포함(브리프 판정 규칙)."""
        return np.abs(self.bin_value) <= self.bin_ci95

    def coverage(self) -> float:
        """점이 있는 구간 비율 — 1.0 이면 결측 구간 없음."""
        return float(np.mean(self.bin_n >= MIN_BIN_POINTS)) if self.bin_n.size else 0.0

    def gaps(self) -> list[tuple[float, float]]:
        """결측 구간 [(시작, 끝)] — 대표값을 낼 점이 없는 교축 구간."""
        out: list[tuple[float, float]] = []
        half = self.bin_m / 2
        start = None
        for c, n in zip(self.bin_center_m, self.bin_n):
            if n < MIN_BIN_POINTS and start is None:
                start = c - half
            elif n >= MIN_BIN_POINTS and start is not None:
                out.append((start, c - half))
                start = None
        if start is not None and self.bin_center_m.size:
            out.append((start, float(self.bin_center_m[-1]) + half))
        return out

    def signal_to_noise(self) -> float:
        """σ_between / σ_within — 구간간 차이가 구간내 흔들림보다 큰가.

        1 근처면 "구간을 나눠도 잡음"이다. NaN 은 판단 불가(구간내 잔차를 낼 점이 없음).
        """
        ok = self.bin_n >= MIN_BIN_POINTS
        if ok.sum() < 2:
            return float("nan")
        within = []
        for i in np.where(ok)[0]:
            m = (self.selected
                 & (self.chainage_m >= self.bin_center_m[i] - self.bin_m / 2)
                 & (self.chainage_m < self.bin_center_m[i] + self.bin_m / 2))
            if m.sum() >= 2:
                v = self.value[m]
                within.append(v - np.median(v))
        if not within:
            return float("nan")
        sw = float(np.std(np.concatenate(within)))
        sb = float(np.nanstd(self.bin_value[ok]))
        return sb / sw if sw > 0 else float("inf")

    def is_publishable(self) -> tuple[bool, str]:
        """교축 프로파일을 **3D 색띠로** 실어도 되는가 — (통과여부, 사유).

        2D 그림은 언제나 그려도 된다(불확실성이 함께 보인다). 여기서 막는 것은 그
        단서가 사라지는 표현이다.
        """
        cov = self.coverage()
        snr = self.signal_to_noise()
        if cov < MIN_COVERAGE:
            return False, (f"커버리지 {cov * 100:.0f}% < {MIN_COVERAGE * 100:.0f}% "
                           f"— 결측 {len(self.gaps())}구간을 색으로 메우게 된다")
        if not np.isfinite(snr):
            return False, "구간내 잔차를 낼 점이 부족해 신호/잡음을 판단할 수 없다"
        if snr < MIN_SNR:
            return False, (f"σ_b/σ_w = {snr:.2f} < {MIN_SNR} "
                           f"— 구간간 차이가 구간내 잡음과 구별되지 않는다")
        return True, f"커버리지 {cov * 100:.0f}% · σ_b/σ_w = {snr:.2f}"

    def describe(self) -> str:
        sel = int(self.selected.sum())
        return (f"교축 {self.deck_length_m:.0f}m · 점 {sel}/{self.selected.size} 선별 · "
                f"구간 {self.bin_m:.0f}m × {self.bin_n.size} · "
                f"커버리지 {self.coverage() * 100:.0f}% · 결측 {len(self.gaps())}구간")


def _read_track(track_h5: str | Path) -> dict[str, Any]:
    import h5py

    with h5py.File(str(track_h5), "r") as f:
        if "pixel_lonlat" in f:                          # 트랙 h5
            out: dict[str, Any] = {"lonlat": np.asarray(f["pixel_lonlat"][()], float)}
            for k in ("coh", "temp_coh", "amplitude_dispersion", "los_velocity_mm_yr",
                      "incidenceAngle", "los_mm", "scatterer_class",
                      "residual_height_m", "residual_height_sigma_m"):
                if k in f:
                    out[k] = np.asarray(f[k][()])
            if "epochs" in f:
                out["dates"] = np.asarray(f["epochs"][()])
            out["attrs"] = {k: v for k, v in f.attrs.items()}
            return out
        if "insar/xyz" in f:                             # 프로젝트 h5 (/insar 계약)
            g = f["insar"]
            out = {"lonlat": np.asarray(g["xyz"][()], float)[:, :2]}
            alias = {"coh": ("temporal_coherence", "coherence"),
                     "amplitude_dispersion": ("amplitude_dispersion",),
                     "los_velocity_mm_yr": ("velocity_mm_yr",),
                     "incidenceAngle": ("incidence_deg",), "los_mm": ("los",),
                     "dates": ("date_labels", "dates")}     # 라벨(YYYYMMDD)이 있으면 우선
            for k, cands in alias.items():
                for c in cands:
                    if c in g:
                        out[k] = np.asarray(g[c][()])
                        break
            attrs: dict = {}
            try:
                ts = json.loads(str(g.attrs.get("track_source", "{}")))
                attrs = dict(ts.get("attrs", {}) or {})
            except Exception:                            # noqa: BLE001
                pass
            out["attrs"] = attrs
            return out
    raise ValueError(f"{track_h5}: pixel_lonlat(트랙) 도 /insar/xyz(프로젝트) 도 없습니다")


def velocity_ci(los: np.ndarray, days: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """점별 LOS 시계열 선형 적합 → (속도[mm/yr], 95% CI 반폭[mm/yr]).

    브리프 (c) 의 오차막대가 이것이다. CI 가 0 을 포함하면 "관측기간 중 유의한 변형 경향
    없음"이다 — 값 자체보다 이 판정이 보고에 쓰인다.
    """
    los = np.asarray(los, float)
    t = np.asarray(days, float) / 365.25
    n = t.size
    X = np.column_stack([np.ones(n), t])
    ok = np.isfinite(los).all(axis=1)
    vel = np.full(los.shape[0], np.nan)
    ci = np.full(los.shape[0], np.nan)
    if n < 3 or not ok.any():
        return vel, ci
    beta, *_ = np.linalg.lstsq(X, los[ok].T, rcond=None)
    r = los[ok].T - X @ beta
    s2 = (r ** 2).sum(axis=0) / max(n - 2, 1)
    var_b = s2 / max(((t - t.mean()) ** 2).sum(), 1e-12)
    vel[ok] = beta[1]
    ci[ok] = 1.96 * np.sqrt(var_b)
    return vel, ci


def _epoch_days(tr: dict) -> np.ndarray | None:
    """트랙/프로젝트의 날짜 → 첫 시점 기준 일수."""
    d = tr.get("dates")
    if d is None:
        return None
    d = np.asarray(d)
    # 문자열이든 정수든 YYYYMMDD 꼴이면 날짜로 푼다(SNAP 트랙은 int32 YYYYMMDD 에
    # 기준일이 맨 앞이라 정렬돼 있지 않다). 그 외 숫자는 이미 '일수'로 본다.
    if d.dtype.kind in "SU" or (d.dtype.kind in "iu" and d.size and int(d.min()) > 19000000):
        from datetime import datetime
        dd = [datetime.strptime(x.decode() if isinstance(x, bytes) else str(int(x)), "%Y%m%d")
              for x in d]
        t0 = min(dd)
        return np.array([(x - t0).days for x in dd], float)
    return np.asarray(d, float)


def reference_to_abutment(los: np.ndarray, station: np.ndarray, length_m: float, *,
                          on_deck: np.ndarray | None = None,
                          zone_m: float = ABUTMENT_ZONE_M) -> tuple[np.ndarray, dict]:
    """교대 위 안정점을 0 mm 기준으로 — 시점별로 교대 구역 점들의 중앙값을 뺀다.

    브리프 '기준점 잡기: 교대 위 안정된 점을 0 m 기준으로'. 교대는 지반에 직접 놓여
    상부구조 거동에서 자유롭다. 교대 구역 점이 2개 미만이면 손대지 않고 사유를 남긴다.
    """
    los = np.asarray(los, float)
    st = np.asarray(station, float)
    zone = (st <= zone_m) | (st >= length_m - zone_m)
    if on_deck is not None:
        zone &= np.asarray(on_deck, bool)
    n = int(zone.sum())
    if n < 2:
        return los, {"applied": False, "n_ref": n,
                     "reason": f"교대 구역(양끝 {zone_m:g} m) 점이 {n}개 — 2개 미만이라 기준점 미적용"}
    ref = np.nanmedian(los[zone], axis=0)
    return los - ref[None, :], {"applied": True, "n_ref": n, "zone_m": zone_m,
                                "ref_station_m": [float(v) for v in st[zone]]}


def select_points(tr: dict, *, mode: str = "relaxed",
                  adi_strict: float = ADI_STRICT, adi_relaxed: float = ADI_RELAXED,
                  coh_min: float = COH_MIN) -> tuple[np.ndarray, str]:
    """PS 선별 마스크 — `strict`(ADI ≤ 0.25) 또는 `relaxed`(ADI ≤ 0.40 ∧ γ_temp ≥ 0.60).

    반환 (mask[N], 무엇으로 걸렀는지). ADI 가 없으면 γ_temp 만으로 걸러지고 그 사실을
    문자열에 남긴다 — "ADI 기준을 적용했다"고 잘못 읽히면 안 된다.
    """
    n = len(tr["lonlat"])
    adi = tr.get("amplitude_dispersion")
    adi = np.asarray(adi, float) if adi is not None else None
    if adi is not None and not np.isfinite(adi).any():
        adi = None
    coh = None
    for k in ("coh", "temp_coh"):
        if k in tr:
            coh = np.asarray(tr[k], float)
            break
    if mode == "ci":
        # 속도 불확실도 QC — los 시계열이 있어야 한다. 느슨한 결맞음만 같이 건다.
        los, days = tr.get("los_mm"), _epoch_days(tr)
        if los is None or days is None:
            raise ValueError("CI 모드는 LOS 시계열과 날짜가 필요합니다.")
        _, ci = velocity_ci(los, days)
        mask = np.isfinite(ci) & (ci <= CI95_MAX_MM_YR)
        how = [f"95%CI ≤ {CI95_MAX_MM_YR:g} mm/yr"]
        if coh is not None:
            mask &= np.isfinite(coh) & (coh >= COH_LOOSE)
            how.append(f"γ_temp ≥ {COH_LOOSE:g}")
        return mask, " ∧ ".join(how)
    if mode == "strict":
        if adi is None:
            if coh is None:
                raise ValueError("선별에 쓸 ADI 도 결맞음도 없습니다.")
            return (np.isfinite(coh) & (coh >= 0.8)), "γ_temp ≥ 0.80 (ADI 없음 — 대체 엄격)"
        return (np.isfinite(adi) & (adi <= adi_strict)), f"ADI ≤ {adi_strict:g}"
    # relaxed
    mask = np.ones(n, bool)
    how = []
    if adi is not None:
        mask &= np.isfinite(adi) & (adi <= adi_relaxed)
        how.append(f"ADI ≤ {adi_relaxed:g}")
    if coh is not None:
        mask &= np.isfinite(coh) & (coh >= coh_min)
        how.append(f"γ_temp ≥ {coh_min:g}")
    if not how:
        raise ValueError("선별에 쓸 ADI 도 결맞음도 없습니다.")
    if adi is None:
        how.append("(ADI 없음)")
    return mask, " ∧ ".join(how)


def remove_noise(sel: np.ndarray, station: np.ndarray, val: np.ndarray,
                 los: np.ndarray | None, *, bin_m: float, z_max: float = NOISE_Z
                 ) -> tuple[np.ndarray, np.ndarray]:
    """선별 통과 점 중 **노이즈 점**을 뺀다. 반환 (정제 mask, 제거된 점 mask).

    ① 값이 같은 교축 구간(±bin_m) 이웃의 중앙값에서 로버스트 z(MAD) 로 z_max 를 넘는 점
    ② LOS 시계열 잔차 표준편차가 선별 점 중앙값의 3배를 넘는 점
    이웃이 3점 미만이면 ①은 판단하지 않는다(고립점을 노이즈로 몰지 않는다). 이웃 MAD 는
    전체 선별 점 MAD 의 절반을 하한으로 둔다 — 이웃 2~3점이 우연히 같은 값이면 MAD≈0 이
    되어 멀쩡한 점의 z 가 폭발한다(정자교에서 7점 중 3점이 그렇게 빠졌다).
    """
    keep = sel.copy()
    noisy = np.zeros_like(sel)
    idx = np.where(sel)[0]
    if idx.size == 0:
        return keep, noisy
    # ① 구간 이웃 대비 튀는 값
    gmed = np.median(val[idx])
    gmad = np.median(np.abs(val[idx] - gmed)) * 1.4826
    spread = float(np.percentile(val[idx], 95) - np.percentile(val[idx], 5))
    floor = max(0.5 * gmad, 0.05 * spread, 1e-6)      # 전역 MAD 도 0 일 수 있다
    for i in idx:
        nb = idx[(np.abs(station[idx] - station[i]) <= bin_m) & (idx != i)]
        if nb.size < 3:
            continue
        med = np.median(val[nb])
        mad = max(np.median(np.abs(val[nb] - med)) * 1.4826, floor)
        if abs(val[i] - med) / mad > z_max:
            noisy[i] = True
    # ② 시계열 잡음
    if los is not None and np.ndim(los) == 2 and los.shape[0] == sel.size:
        resid = np.nanstd(np.diff(los, axis=1), axis=1)
        ref = np.nanmedian(resid[idx])
        if np.isfinite(ref) and ref > 0:
            noisy |= sel & (resid > 3.0 * ref)
    keep &= ~noisy
    return keep, noisy


def _quality(tr: dict) -> tuple[np.ndarray, str]:
    """선별 품질값 — ADI 가 있으면 1−ADI(=ASI), 없으면 시간 결맞음."""
    adi = tr.get("amplitude_dispersion")
    if adi is not None:
        a = np.asarray(adi, float)
        if np.isfinite(a).any():
            return np.where(np.isfinite(a), 1.0 - a, np.nan), "ASI(1−ADI)"
    for k in ("coh", "temp_coh"):
        if k in tr:
            return np.asarray(tr[k], float), "γ_temp"
    raise ValueError("선별에 쓸 품질값(ADI 또는 결맞음)이 트랙에 없습니다.")


def _velocity(tr: dict) -> np.ndarray:
    """점별 LOS 변위속도[mm/yr] — 없으면 시계열 양끝 기울기로 근사."""
    v = tr.get("los_velocity_mm_yr")
    if v is not None and np.isfinite(np.asarray(v, float)).any():
        return np.asarray(v, float)
    los = np.asarray(tr.get("los_mm"), float)
    if los is None or los.ndim != 2:
        raise ValueError("변위속도도 LOS 시계열도 없습니다.")
    return (los[:, -1] - los[:, 0])          # 관측기간 총 변위(속도 아님 — meta 에 표기)


def resolve_shift_dh(tr: dict, station0: np.ndarray, offset0: np.ndarray, length_m: float,
                     *, fallback_m: float | None, half_width_m: float | None,
                     min_z: float = 1.0) -> tuple[float | None, dict]:
    """쉬프트 크기 δh — **잔차고도 집단평균이 있으면 그것, 없으면 형하고 가정**.

    쉬프트 = δh/tanθ 인데 δh 를 지금까지 형하고로 가정했다. 트랙에 잔차고도(PSI 높이)가
    있으면 교면 위 점과 밖 점의 가중평균 차이를 δh 로 쓴다 — 가정이 관측으로 바뀐다.

    점별 잔차고도는 쓰지 않는다. σ 가 20 m 급이라 점별로 적용하면 쉬프트 잡음이 σ/tanθ ≈
    27 m 로 화소(11 m)보다 커져 배치가 망가진다. 집단 차이가 z < min_z 면 관측이 가정보다
    나을 근거가 없으므로 가정을 유지하고 그 사실을 남긴다.
    교면 위 판정은 보정 **전** 좌표로 하되 반폭+화소/2 로 넉넉히 잡는다(보정으로 안팎이
    바뀌는 점을 한쪽으로 몰지 않기 위해).
    """
    rh = tr.get("residual_height_m")
    rs = tr.get("residual_height_sigma_m")
    meta = {"dh_source": "형하고 가정", "dh_m": fallback_m}
    if rh is None or rs is None or half_width_m is None:
        return fallback_m, meta
    rh, rs = np.asarray(rh, float), np.asarray(rs, float)
    ok = np.isfinite(rh) & np.isfinite(rs) & (rs > 0)
    if ok.sum() < 4:
        return fallback_m, meta
    from .deck_shift import PIXEL_M
    on = ok & (np.abs(offset0) <= half_width_m + PIXEL_M / 2) & (station0 >= -2) \
        & (station0 <= length_m + 2)
    off = ok & ~on
    if on.sum() < 2 or off.sum() < 2:
        meta["reason"] = f"교면 위 {int(on.sum())} · 밖 {int(off.sum())} — 집단평균 불가"
        return fallback_m, meta
    wa, wb = 1.0 / rs[on] ** 2, 1.0 / rs[off] ** 2
    ma, mb = float(np.sum(wa * rh[on]) / wa.sum()), float(np.sum(wb * rh[off]) / wb.sum())
    se = float(np.hypot(1.0 / np.sqrt(wa.sum()), 1.0 / np.sqrt(wb.sum())))
    diff = ma - mb
    z = diff / se if se > 0 else 0.0
    info = {"residual_diff_m": diff, "residual_se_m": se, "z": z,
            "n_on": int(on.sum()), "n_off": int(off.sum())}
    if diff <= 0 or z < min_z:
        meta.update(info, reason=f"잔차고도 차이 {diff:+.1f}±{se:.1f} m (z={z:.2f}) — "
                                 f"가정({fallback_m}) 유지")
        return fallback_m, meta
    meta.update(info, dh_source="잔차고도 집단평균(교면 위 − 밖)", dh_m=float(diff),
                fallback_m=fallback_m)
    return float(diff), meta


def build_profile(track_h5: str | Path, geometry_latlon, *,
                  bin_m: float = DEFAULT_BIN_M, min_quality: float | None = None,
                  mode: str = "relaxed", denoise: bool = True,
                  reference: str | None = None,
                  correct_shift: bool = True, bridge_height_m: float | None = None,
                  bridge_width_m: float | None = None,
                  max_offset_m: float | None = None) -> ChainageProfile:
    """트랙 + 데크선 → 교축 프로파일.

    `correct_shift=True` 면 지오코딩 쉬프트를 먼저 되돌린다(δh 는 `bridge_height_m`,
    입사각·heading 은 트랙에서 읽는다). 쉬프트는 교축 종·횡 성분으로 갈리므로, 보정은
    chainage 와 offset 을 **둘 다** 바꾼다.

    `max_offset_m` 은 "교량 위"의 정의다 — 주지 않으면 폭의 절반(반폭)을 쓴다. 추출 버퍼가
    폭보다 넓으면 데크 밖 지반 점이 섞여 프로파일이 오염된다(청양교: 버퍼 30m vs 반폭 11m).

    선별: `mode="strict"`(ADI ≤ 0.25) · `"relaxed"`(ADI ≤ 0.40 ∧ γ_temp ≥ 0.60, 기본) ·
    `"ci"`(속도 95% CI 반폭 ≤ 1.0 mm/yr ∧ γ ≥ 0.40 — 브리프 방식, 규칙 격자를 최대한 살린다).
    `reference="abutment"` 면 교대 구역 점 중앙값을 시점별로 빼 교대를 0 mm 기준으로 한다.
    LOS 시계열이 있으면 속도·95% CI 를 그 시계열에서 다시 계산한다(저장 속도와 일관되게).
    `denoise=True` 면 통과 점 중 이웃 대비 튀는 점·시계열 잡음 점을 뺀다(`remove_noise`).
    `min_quality` 를 주면 예전 방식(단일 품질값 하한)으로 돌아간다 — 호환용.

    입력은 트랙 h5(`pixel_lonlat`) 또는 프로젝트 h5(`/insar/xyz`) 어느 쪽이든 된다.
    """
    from .deck_geometry import project_to_polyline

    tr = _read_track(track_h5)
    ll = tr["lonlat"]
    qual, qname = _quality(tr)
    val = _velocity(tr)
    shift_meta = None

    if correct_shift and bridge_height_m:
        from .deck_shift import for_bridge
        from .geolocation import apply_correction

        inc = np.asarray(tr.get("incidenceAngle", 39.0), float)
        from .track_reader import normalize_heading_deg
        heading = float(normalize_heading_deg(
            float(tr["attrs"].get("HEADING", 0.0) or 0.0)))     # 라디안 유입 방어
        # δh: 잔차고도 집단평균이 있으면 관측값, 없으면 형하고 가정
        st0, of0 = project_to_polyline(ll, geometry_latlon)
        dh, dh_meta = resolve_shift_dh(
            tr, st0, of0, float(_polyline_length_m(np.asarray(geometry_latlon, float))),
            fallback_m=float(bridge_height_m),
            half_width_m=(float(bridge_width_m) / 2.0 if bridge_width_m else None))
        g = for_bridge(geometry_latlon, heading_deg=heading,
                       incidence_deg=float(np.nanmedian(inc)),
                       dh_m=float(dh), width_m=bridge_width_m)
        r = apply_correction(ll, np.full(ll.shape[0], float(dh)), inc,
                             heading, crs_is_lonlat=True, set_height=False)
        ll = np.asarray(r["xyz"], float)[:, :2]
        shift_meta = {**g.as_dict(), "applied": True,
                      "mean_abs_m": float(np.mean(r["shift_m"])), **dh_meta}

    station, offset = project_to_polyline(ll, geometry_latlon)
    offset = _signed_offset(ll, geometry_latlon, offset)   # 좌/우를 부호로 — 데크 단면이 보인다
    lim = (float(max_offset_m) if max_offset_m is not None
           else (float(bridge_width_m) / 2.0 if bridge_width_m else None))
    poly0 = np.asarray(geometry_latlon, float)
    length0 = float(_polyline_length_m(poly0))
    ref_meta = None
    ci95 = None
    los = tr.get("los_mm")
    days = _epoch_days(tr)
    if los is not None and np.ndim(los) == 2 and days is not None:
        los = np.asarray(los, float)
        if reference == "abutment":
            on = (np.abs(offset) <= lim) if lim is not None else None
            los, ref_meta = reference_to_abutment(los, station, length0, on_deck=on)
            tr = {**tr, "los_mm": los}
        v_fit, ci95 = velocity_ci(los, days)
        if np.isfinite(v_fit).any():
            val = v_fit                                 # 시계열과 일관된 속도
    if min_quality is not None:                   # 호환: 단일 품질값 하한
        sel = np.isfinite(qual) & (qual >= float(min_quality))
        how = f"{qname} ≥ {float(min_quality):g}"
    else:
        sel, how = select_points(tr, mode=mode)
    sel &= np.isfinite(val)
    if lim is not None:
        sel &= np.abs(offset) <= lim              # 데크 폭 밖(지반)은 교량 위가 아니다
    n_before = int(sel.sum())
    noisy = np.zeros_like(sel)
    if denoise:
        sel, noisy = remove_noise(sel, station, val, tr.get("los_mm"), bin_m=bin_m)

    poly = np.asarray(geometry_latlon, float)
    length = float(_polyline_length_m(poly))
    edges = np.arange(0.0, max(length, station.max() if station.size else 0.0) + bin_m, bin_m)
    centers = edges[:-1] + bin_m / 2
    bv = np.full(centers.size, np.nan)
    bs = np.full(centers.size, np.nan)
    bn = np.zeros(centers.size, dtype=int)
    for i in range(centers.size):
        m = sel & (station >= edges[i]) & (station < edges[i + 1])
        bn[i] = int(m.sum())
        if bn[i] >= MIN_BIN_POINTS:
            v = val[m]
            bv[i] = float(np.median(v))
            bs[i] = float(np.std(v, ddof=1) / np.sqrt(v.size)) if v.size > 1 else 0.0

    return ChainageProfile(chainage_m=station, offset_m=offset, value=val, selected=sel,
                           quality=qual, bin_center_m=centers, bin_value=bv, bin_sem=bs,
                           bin_n=bn, deck_length_m=length, bin_m=bin_m, shift=shift_meta,
                           ci95=ci95, reference=ref_meta,
                           meta={"quality": qname, "selection": how, "mode": mode,
                                 "n_selected_before_denoise": n_before,
                                 "n_noise_removed": int(noisy.sum()),
                                 "max_offset_m": lim,
                                 "value": ("LOS 변위속도[mm/yr]"
                                           if tr.get("los_velocity_mm_yr") is not None
                                           else "관측기간 총 LOS 변위[mm]")})


def _signed_offset(lonlat: np.ndarray, poly_latlon, dist: np.ndarray) -> np.ndarray:
    """교축 직각 거리에 **좌/우 부호**를 준다.

    폴리라인 투영은 거리(양수)만 준다. 그러면 데크 양쪽 점이 한쪽에 겹쳐 보여 폭 방향
    분포를 읽을 수 없다. 데크 종축 기준 외적 부호로 좌(−)/우(+)를 가른다.
    """
    poly = np.asarray(poly_latlon, float)
    lat0 = float(np.median(poly[:, 0]))
    k = np.cos(np.radians(lat0))
    a = np.array([(poly[0][1]) * k, poly[0][0]]) * 111_320.0
    b = np.array([(poly[-1][1]) * k, poly[-1][0]]) * 111_320.0
    axis = b - a
    nrm = np.linalg.norm(axis)
    if nrm <= 0:
        return np.asarray(dist, float)
    axis = axis / nrm
    P = np.column_stack([np.asarray(lonlat, float)[:, 0] * k,
                         np.asarray(lonlat, float)[:, 1]]) * 111_320.0
    rel = P - a
    side = np.sign(rel[:, 0] * axis[1] - rel[:, 1] * axis[0])   # 2D 외적 부호
    side[side == 0] = 1.0
    return np.asarray(dist, float) * side


def _polyline_length_m(poly_latlon: np.ndarray) -> float:
    lat0 = float(np.median(poly_latlon[:, 0]))
    k = np.cos(np.radians(lat0))
    d = np.diff(poly_latlon, axis=0)
    return float(np.sum(np.hypot(d[:, 0] * 111_320.0, d[:, 1] * 111_320.0 * k)))


def _use_korean_font(plt) -> None:
    """한글 라벨이 깨지지 않게(없으면 영문만 정상 — 그림 자체는 나온다)."""
    import matplotlib.font_manager as fm

    installed = {f.name for f in fm.fontManager.ttflist}
    for cand in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR",
                 "맑은 고딕", "Batang", "Gulim"):
        if cand in installed:
            plt.rcParams["font.family"] = cand
            break
    plt.rcParams["axes.unicode_minus"] = False


def plot_profile(strict: ChainageProfile, relaxed: ChainageProfile,
                 out_png: str | Path, *, title: str = "", value_label: str | None = None,
                 half_width_m: float | None = None) -> str:
    """① 엄격 선별 → ② 완화+재선별 → ③ 교축 구간집계, 3단 그림.

    같은 데이터를 세 번 보는 것이다: 산포 두 장은 "어떤 점으로 만들었나", 마지막 프로파일은
    "교축 어디가 다른가". 결측 구간을 붉게 칠해 **점이 없어서 모르는 곳**을 숨기지 않는다.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _use_korean_font(plt)
    fig, axes = plt.subplots(1, 3, figsize=(16.5, 4.2))
    hw = half_width_m or relaxed.meta.get("max_offset_m") or 10.0
    vlab = value_label or relaxed.meta.get("value", "값")

    for ax, p, tag in ((axes[0], strict, "①"), (axes[1], relaxed, "②")):
        q = p.meta.get("quality", "품질")
        ax.axhspan(-hw, hw, color="#e8edf7", zorder=0)          # 데크 폭
        ax.scatter(p.chainage_m[~p.selected], p.offset_m[~p.selected], marker="x",
                   s=26, c="#9aa5b1", label=f"기각 {q}<{p.meta['min_quality']:.2f}")
        ax.scatter(p.chainage_m[p.selected], p.offset_m[p.selected], s=30,
                   c="#27408b", label=f"채택 {q}≥{p.meta['min_quality']:.2f}")
        for a, b in p.gaps():                                    # 결측 구간
            ax.axvspan(a, b, color="#f6dede", zorder=0)
        ax.set_xlabel("교축 거리 chainage [m]")
        ax.set_ylabel("교축 직각 offset [m]")
        ax.set_ylim(-hw * 1.8, hw * 1.8)
        ax.set_title(f"{tag} {q}≥{p.meta['min_quality']:.2f} — 채택 "
                     f"{int(p.selected.sum())}점 · 커버리지 {p.coverage()*100:.0f}%", fontsize=10)
        ax.legend(fontsize=8, loc="upper right")
        ax.grid(alpha=0.25)

    ax = axes[2]
    m = relaxed.selected
    ax.scatter(relaxed.chainage_m[m], relaxed.value[m], s=22, c="#b9c0c9",
               label="개별 PS", zorder=2)
    ok = relaxed.bin_n >= MIN_BIN_POINTS
    ax.errorbar(relaxed.bin_center_m[ok], relaxed.bin_value[ok], yerr=relaxed.bin_sem[ok],
                fmt="s-", ms=5, lw=1.4, c="#27408b", ecolor="#27408b", capsize=3,
                label=f"구간 대표값(중앙값±SE) · {relaxed.bin_m:.0f}m", zorder=3)
    ax.axhline(0.0, color="#333", lw=0.9)
    for a, b in relaxed.gaps():
        ax.axvspan(a, b, color="#f6dede", zorder=0)
    ax.set_xlabel("교축 거리 chainage [m]")
    ax.set_ylabel(vlab)
    ax.set_title(f"③ 교축 1D 투영 + {relaxed.bin_m:.0f}m 구간 집계", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    if title:
        fig.suptitle(title, fontsize=12)
        fig.tight_layout(rect=(0, 0, 1, 0.94))
    else:
        fig.tight_layout()
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return str(out)


def station_lonlat(geometry_latlon, station_m) -> np.ndarray:
    """교축 거리(station) → 데크 폴리라인 위 (lon, lat). 구간집계 스테이션을 지도에 놓을 때.

    1D 투영은 횡방향 오프셋을 버리므로, 집계 스테이션은 **데크 중심선 위**에 놓인다 —
    점이 보도·난간 쪽으로 밀려 있어도 교축 대표값의 위치는 축 위다.
    """
    poly = np.asarray(geometry_latlon, float)                  # [[lat,lon],...]
    lat0 = float(np.median(poly[:, 0]))
    k = np.cos(np.radians(lat0)) * 111_320.0
    xy = np.column_stack([poly[:, 1] * k, poly[:, 0] * 111_320.0])
    seg = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    s = np.atleast_1d(np.asarray(station_m, float))
    s = np.clip(s, 0.0, cum[-1])
    x = np.interp(s, cum, xy[:, 0])
    y = np.interp(s, cum, xy[:, 1])
    return np.column_stack([x / k, y / 111_320.0])


def stations_for_twin(prof: ChainageProfile, geometry_latlon, *, z_m: float) -> list[dict]:
    """구간집계 결과를 트윈이 그릴 스테이션 목록으로 — 데크 중심선 위, 데크 상단 높이.

    대표값이 없는 구간(n < MIN_BIN_POINTS)도 넣되 `has_value=False` 로 표시한다 —
    결측 구간을 화면에서 지우면 '점이 없어 모르는 곳'이 사라진다.
    """
    ll = station_lonlat(geometry_latlon, prof.bin_center_m)
    out = []
    for i, c in enumerate(prof.bin_center_m):
        n = int(prof.bin_n[i])
        has = n >= MIN_BIN_POINTS
        out.append({"chainage_m": float(c), "lon": float(ll[i, 0]), "lat": float(ll[i, 1]),
                    "z": float(z_m), "n": n, "has_value": has,
                    "value": (float(prof.bin_value[i]) if has else None),
                    "sem": (float(prof.bin_sem[i]) if has else None)})
    return out
