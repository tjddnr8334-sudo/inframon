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

import numpy as np

DEFAULT_BIN_M = 7.5           # 교축 구간 폭(5~10m 권장 — 경간·PS 밀도에 맞춘다)
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
    deck_length_m: float = 0.0
    bin_m: float = DEFAULT_BIN_M
    shift: dict | None = None     # 적용한 쉬프트 보정 근거
    meta: dict = field(default_factory=dict)

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
        out: dict[str, Any] = {"lonlat": np.asarray(f["pixel_lonlat"][()], float)}
        for k in ("coh", "temp_coh", "amplitude_dispersion", "los_velocity_mm_yr",
                  "incidenceAngle", "los_mm", "scatterer_class"):
            if k in f:
                out[k] = np.asarray(f[k][()])
        out["attrs"] = {k: v for k, v in f.attrs.items()}
    return out


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


def build_profile(track_h5: str | Path, geometry_latlon, *,
                  bin_m: float = DEFAULT_BIN_M, min_quality: float = 0.6,
                  correct_shift: bool = True, bridge_height_m: float | None = None,
                  bridge_width_m: float | None = None,
                  max_offset_m: float | None = None) -> ChainageProfile:
    """트랙 + 데크선 → 교축 프로파일.

    `correct_shift=True` 면 지오코딩 쉬프트를 먼저 되돌린다(δh 는 `bridge_height_m`,
    입사각·heading 은 트랙에서 읽는다). 쉬프트는 교축 종·횡 성분으로 갈리므로, 보정은
    chainage 와 offset 을 **둘 다** 바꾼다.

    `max_offset_m` 은 "교량 위"의 정의다 — 주지 않으면 폭의 절반(반폭)을 쓴다. 추출 버퍼가
    폭보다 넓으면 데크 밖 지반 점이 섞여 프로파일이 오염된다(청양교: 버퍼 30m vs 반폭 11m).
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
        g = for_bridge(geometry_latlon, heading_deg=heading,
                       incidence_deg=float(np.nanmedian(inc)),
                       dh_m=float(bridge_height_m), width_m=bridge_width_m)
        r = apply_correction(ll, np.full(ll.shape[0], float(bridge_height_m)), inc,
                             heading, crs_is_lonlat=True, set_height=False)
        ll = np.asarray(r["xyz"], float)[:, :2]
        shift_meta = {**g.as_dict(), "applied": True,
                      "mean_abs_m": float(np.mean(r["shift_m"]))}

    station, offset = project_to_polyline(ll, geometry_latlon)
    offset = _signed_offset(ll, geometry_latlon, offset)   # 좌/우를 부호로 — 데크 단면이 보인다
    lim = (float(max_offset_m) if max_offset_m is not None
           else (float(bridge_width_m) / 2.0 if bridge_width_m else None))
    sel = np.isfinite(qual) & (qual >= float(min_quality)) & np.isfinite(val)
    if lim is not None:
        sel &= np.abs(offset) <= lim              # 데크 폭 밖(지반)은 교량 위가 아니다

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
                           meta={"quality": qname, "min_quality": float(min_quality),
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
