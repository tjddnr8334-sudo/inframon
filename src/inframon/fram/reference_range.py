"""CRI **정상범위(reference range) 캘리브레이션** — 의료 검사수치의 기준치처럼.

붕괴 사례는 드물지만 **건강한 교량은 많다**. isotonic 캘리브(붕괴 라벨 필요, `calibration.py`)
와 달리, 여기서는 **건강 교량 코호트만으로**(라벨 불필요) CRI 의 정상 분포를 학습해,
새 교량의 CRI 가 정상 인구에서 어디쯤인지(정상/주의/경고/위험)를 판독한다.

의료 비유: 혈압·혈당의 reference range 처럼 CRI 도 "건강 집단의 중앙 95%"를 정상으로 잡고,
그 밖은 경계/이상으로 표시한다. 이렇게 하면 임계값이 임의의 절대수(0.3/0.6/0.85)가 아니라
**실측 건강 분포에서 유도**돼, 어느 시스템이 열어도 같은 판독이 되는 **이식 가능한 건강지표**
(교량의 누적 의료기록)가 된다.

로버스트 통계(median·MAD·백분위)로 이상치·비정규성에 강하게 적합한다. sklearn 불필요.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# 밴드 라벨(한 방향: CRI 높을수록 위험) — FRAMWarning.level 과 호환.
BANDS = ("정상", "주의", "경고", "위험")

_MAD_TO_SIGMA = 1.4826        # MAD → 정규분포 표준편차 환산(로버스트 z 용)


def _binom_sf_ge(k: int, n: int, p: float) -> float:
    """P(X ≥ k), X~Binomial(n, p) — 정확 하위CDF 합(꼬리 k 는 작아 저렴). 의존성 없음.

    분포이동 판정용: 관측 초과점 k 가 건강 기대(n·p)보다 유의하게 많은지의 상측 p-값.
    """
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    from math import comb
    q = 1.0 - p
    cdf = 0.0
    for i in range(0, k):                 # P(X ≤ k-1)
        cdf += comb(n, i) * (p ** i) * (q ** (n - i))
    return float(max(0.0, min(1.0, 1.0 - cdf)))


@dataclass
class ReferenceRange:
    """건강 교량 코호트에서 학습한 CRI 정상범위(직렬화 가능).

    한 방향(높을수록 위험) 기준치. 밴드 경계:
      · 정상 : cri ≤ p97_5            (건강 집단 중앙 95% 안)
      · 주의 : p97_5 < cri ≤ p99      (상위 5%, 관찰)
      · 경고 : p99 < cri ≤ abnormal_high  (건강 집단 밖, 점검 권고)
      · 위험 : cri > abnormal_high    (건강 분포에서 뚜렷이 이탈)
    `abnormal_high = p99 + k_ext·(MAD·1.4826)` (기본 k_ext=3σ 상당).
    """

    median: float
    mad: float                 # median absolute deviation(로버스트 산포)
    p50: float
    p97_5: float
    p99: float
    abnormal_high: float
    lo: float                  # 관측 최소(참고)
    hi: float                  # 관측 최대(참고)
    n: int                     # 코호트 표본수
    p_abnormal: float = 0.005  # 건강 코호트에서 abnormal_high 초과 비율(위험 임계의 기대 초과율)
    metric: str = "cri_point_max"   # 어떤 통계로 적합했는지(점별 시간최대 CRI 등)
    source: str = "synthetic_healthy"
    # ★ 관측규모(비교 유효성): CRI 는 노이즈·관측기간에 따라 바닥이 달라져 노이즈 불변이
    # 아니다 → 새 교량은 **비슷한 관측조건**에서 비교해야 한다(사과-사과). 이 정상범위가
    # 학습된 조건을 기록해 부적합 비교를 경고할 수 있게 한다.
    regime: dict = field(default_factory=dict)   # {noise_mm, span_days, n_epochs}

    # ---- 판독 ----
    def robust_z(self, cri):
        """로버스트 z-점수 = (cri − median) / (1.4826·MAD). 정규 근사 표준편차 단위."""
        s = _MAD_TO_SIGMA * self.mad + 1e-12
        return (np.asarray(cri, float) - self.median) / s

    def band(self, cri) -> np.ndarray:
        """CRI(스칼라/배열) → 밴드 라벨 배열."""
        v = np.asarray(cri, float)
        idx = np.zeros(v.shape, dtype=int)
        idx = np.where(v > self.p97_5, 1, idx)
        idx = np.where(v > self.p99, 2, idx)
        idx = np.where(v > self.abnormal_high, 3, idx)
        return np.array(BANDS)[idx]

    def percentile_of(self, cri, cohort: np.ndarray | None = None) -> np.ndarray:
        """건강 분포 대비 백분위(0~100). 코호트 없으면 저장 백분위로 단조 보간 근사."""
        v = np.asarray(cri, float)
        if cohort is not None and cohort.size:
            xs = np.sort(np.asarray(cohort, float))
            return np.searchsorted(xs, v, side="right") / xs.size * 100.0
        xp = [self.lo, self.p50, self.p97_5, self.p99, self.hi]
        fp = [0.0, 50.0, 97.5, 99.0, 100.0]
        return np.clip(np.interp(v, xp, fp), 0.0, 100.0)

    def classify(self, cri_values, *, alpha: float = 0.05) -> dict:
        """CRI 배열 → 교량 경보 등급(**분포이동 유의성** 기반) + 판독 요약.

        **왜 최악점 밴드가 아니라 분포이동인가**: 건강 교량도 점이 많으면 통계적으로 2.5%가
        p97.5 를, 1%가 p99 를 넘는다(꼬리). 고노이즈(저코히런스 데크)에선 개별 점이 노이즈로
        경고밴드에 닿기 쉬워, "최악점 밴드=등급"은 건강 교량을 오경보한다(검증서 확인).
        대신 각 임계에서 **관측 초과점 수가 건강 기대치보다 이항검정으로 유의하게 많은가**를
        보고, 그런 최고 임계를 등급으로 삼는다. abnormal_high(=p99+k·σ) 초과가 유의 → 위험.
        개별 점 몇 개가 밴드에 닿아도 초과가 기대 범위면 정상(노이즈 꼬리로 간주).
        """
        v = np.asarray(cri_values, float).ravel()
        v = v[np.isfinite(v)]
        if v.size == 0:
            return {"level": "정상", "worst_cri": 0.0, "worst_percentile": 0.0,
                    "worst_robust_z": 0.0, "band_counts": {b: 0 for b in BANDS},
                    "n_out_of_range": 0, "tail_excess": {}}
        N = int(v.size)
        bands = self.band(v)
        worst_i = int(np.argmax(v))
        counts = {b: int((bands == b).sum()) for b in BANDS}
        # 분포이동 판정: (임계, 건강 기대 초과율, 등급) 을 심각도 높은 순으로.
        thresholds = [(self.abnormal_high, float(self.p_abnormal), "위험"),
                      (self.p99, 0.01, "경고"),
                      (self.p97_5, 0.025, "주의")]
        level = "정상"
        tail: dict = {}
        for thr, p0, lvl in thresholds:
            k = int((v >= thr).sum())
            pval = _binom_sf_ge(k, N, p0)          # P(초과 ≥ k | 건강)
            tail[lvl] = {"count": k, "expected": round(p0 * N, 2), "p_value": round(pval, 5)}
            if level == "정상" and k >= 1 and pval < alpha:
                level = lvl                        # 심각도 높은 임계부터 → 첫 유의가 최종 등급
        return {
            "level": level,
            "worst_cri": float(v[worst_i]),
            "worst_percentile": float(self.percentile_of(v[worst_i])),
            "worst_robust_z": float(self.robust_z(v[worst_i])),
            "band_counts": counts,
            "n_out_of_range": int(counts["경고"] + counts["위험"]),
            "tail_excess": tail,
        }

    def regime_mismatch(self, *, noise_mm=None, span_days=None, n_epochs=None) -> str | None:
        """새 교량 관측조건이 학습 regime 과 크게 다르면 경고 문자열(없으면 None).

        CRI 바닥은 노이즈·기간·**에폭 수**에 의존하므로(에폭 적으면 secular/공명 추정이
        불안정해 CRI 분포가 통째로 올라가 오경보), 이 중 하나라도 크게 벗어나면 부적합
        비교로 표시한다. 이러면 분포이동 판정이 "경고"라도 **잠정**임을 알 수 있다.
        """
        r = self.regime or {}
        msgs = []
        if noise_mm and r.get("noise_mm") and (noise_mm > 2 * r["noise_mm"]
                                               or noise_mm < 0.5 * r["noise_mm"]):
            msgs.append(f"노이즈 {noise_mm:.1f}mm vs 기준 {r['noise_mm']:.1f}mm")
        if span_days and r.get("span_days") and span_days < 0.6 * r["span_days"]:
            msgs.append(f"관측기간 {span_days:.0f}d < 기준 {r['span_days']:.0f}d")
        if n_epochs and r.get("n_epochs") and n_epochs < 0.7 * r["n_epochs"]:
            msgs.append(f"에폭 {n_epochs}회 < 기준 {r['n_epochs']}회(추정 불안정)")
        return " · ".join(msgs) if msgs else None

    def to_dict(self) -> dict:
        return {"median": self.median, "mad": self.mad, "p50": self.p50,
                "p97_5": self.p97_5, "p99": self.p99, "abnormal_high": self.abnormal_high,
                "lo": self.lo, "hi": self.hi, "n": self.n, "p_abnormal": self.p_abnormal,
                "metric": self.metric, "source": self.source, "regime": self.regime}

    @classmethod
    def from_dict(cls, d: dict) -> "ReferenceRange":
        return cls(**{k: d[k] for k in (
            "median", "mad", "p50", "p97_5", "p99", "abnormal_high",
            "lo", "hi", "n")}, p_abnormal=d.get("p_abnormal", 0.005),
            metric=d.get("metric", "cri_point_max"),
            source=d.get("source", "synthetic_healthy"), regime=d.get("regime", {}))


def fit_reference_range(values, *, k_ext: float = 2.0,
                        metric: str = "cri_point_max",
                        source: str = "synthetic_healthy") -> ReferenceRange:
    """건강 교량 코호트의 CRI 표본 → ReferenceRange(로버스트 적합).

    values: 건강 교량들에서 모은 CRI 스칼라 표본(예: 점별 시간최대 CRI 를 이어붙임).
    로버스트: median·MAD 로 중심·산포, 백분위(97.5/99)로 정상경계, abnormal_high 는
    p99 위로 k_ext·σ 만큼. 표본이 극소면 산포 0 을 피하려 소량 바닥값 부여.
    """
    v = np.asarray(values, float).ravel()
    v = v[np.isfinite(v)]
    if v.size == 0:
        raise ValueError("정상범위 적합에 빈 CRI 표본이 들어왔습니다")
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    mad = max(mad, 1e-4)                       # 완전 동일값 코호트 방어
    p50 = float(np.percentile(v, 50))
    p97_5 = float(np.percentile(v, 97.5))
    p99 = float(np.percentile(v, 99))
    abn = min(1.0, p99 + k_ext * _MAD_TO_SIGMA * mad)
    # 경계 단조성 보장(p97_5 ≤ p99 ≤ abnormal_high)
    p99 = max(p99, p97_5)
    abn = max(abn, p99)
    # 건강 코호트에서 abnormal_high 초과 비율(위험 임계의 기대 초과율). 바닥값으로 단일점
    # 오판(작은 코호트에서 우연 1점→위험)을 방지.
    p_abn = max(float(np.mean(v >= abn)), 0.5 / max(v.size, 1), 0.002)
    return ReferenceRange(median=med, mad=mad, p50=p50, p97_5=p97_5, p99=p99,
                          abnormal_high=abn, lo=float(v.min()), hi=float(v.max()),
                          n=int(v.size), p_abnormal=p_abn, metric=metric, source=source)


def synthetic_healthy_cri(n_bridges: int = 24, *, n_points: int = 60, n_dates: int = 24,
                          seed: int = 0, noise_mm: float = 10.0,
                          seasonal_mm: float = 4.0, max_trend_mm_yr: float = 1.5):
    """건강 교량 코호트의 **점별 시간최대 CRI** 표본을 합성으로 생성(라벨 없는 정상 인구).

    각 교량: 가역 계절 열팽창 + 미세 선형추세(≤max_trend) + 측정 노이즈(σ=noise_mm).
    실 FRAM 엔진(run_fram_real)으로 CRI 를 산출해 점별 max 를 모은다 → 실제 파이프라인이
    건강 교량에 내는 CRI 분포를 그대로 반영. 반환: 1D CRI 표본 배열.
    """
    import tempfile

    from ..config import PipelineConfig
    from ..contracts.io import ProjectStore
    from ..insar.track_reader import write_insar_contract
    from ..pinn.engine import run_pinn
    from .real_engine import run_fram_real

    rng = np.random.default_rng(seed)
    dates = np.arange(n_dates, dtype=float) * 24.0            # ~1.5년 span(계절 제거 가능)
    t = dates / 365.0
    x = np.linspace(0.0, 120.0, n_points)
    xyz = np.column_stack([x, np.zeros(n_points), np.zeros(n_points)])
    l_from_fixed = np.abs(x - x.mean()).astype(np.float32)
    cfg = PipelineConfig(n_points=n_points, n_dates=n_dates,
                         engines={"cv": "stub", "insar": "stub", "pinn": "real", "fram": "real"})
    samples = []
    with tempfile.TemporaryDirectory() as td:
        for b in range(n_bridges):
            seasonal = seasonal_mm * (l_from_fixed[:, None] / max(x.max(), 1.0)) \
                * np.sin(2 * np.pi * t)[None, :]
            trend = rng.uniform(-max_trend_mm_yr, max_trend_mm_yr * 0.3, n_points)[:, None] * t[None, :]
            los = (seasonal + trend + rng.normal(0.0, noise_mm, (n_points, n_dates))).astype(np.float32)
            with ProjectStore(f"{td}/hb_{b}.h5", mode="w") as store:
                insar = write_insar_contract(
                    store, xyz=xyz, member=np.zeros(n_points, np.int8),
                    coherence=np.full(n_points, 0.7, np.float32), l_from_fixed=l_from_fixed,
                    los=los, longitudinal=los, dates=dates, date_labels=None)
                pinn = run_pinn(store, insar, cfg)
                fram = run_fram_real(store, insar, pinn, cfg)
                cri = store.read_array(fram.CRI_ds)
            samples.append(cri.max(axis=1))                  # 점별 시간최대 CRI
    regime = {"noise_mm": float(noise_mm), "span_days": float(dates[-1] - dates[0]),
              "n_epochs": int(n_dates)}
    return np.concatenate(samples), regime


def build_default_reference_range(**kw) -> ReferenceRange:
    """합성 건강 코호트로 기본 정상범위를 적합(패키지 기본치·부트스트랩용).

    실 Sentinel-1 모니터링 규모(노이즈 σ~10mm·~1.5년) 건강 교량 인구를 반영.
    """
    values, regime = synthetic_healthy_cri(**kw)
    ref = fit_reference_range(values, source="synthetic_healthy")
    ref.regime = regime
    return ref


def fit_reference_range_from_projects(project_h5s, *, exclude_out_of_range: ReferenceRange | None = None
                                      ) -> ReferenceRange:
    """**실측 건강 교량** project.h5 목록의 /fram/CRI 를 모아 현장 정상범위를 적합.

    합성 기본치를 현장 인구 기준치로 교체하는 다음 단계(이식성). 각 project 의 점별
    시간최대 CRI 를 표본으로 모으고, 관측 노이즈·span 의 중앙값을 regime 으로 기록한다.
    `exclude_out_of_range` 를 주면 그 기준치의 정상범위 밖(경고·위험) 점을 제외해 **건강
    점만으로** 적합(오염 방어) — 라벨 없이도 로버스트하게 건강 인구를 근사.
    """
    import h5py

    from ..fram.real_engine import _robust_secular_rate

    samples = []
    noises = []
    spans = []
    used = 0
    for p in project_h5s:
        try:
            with h5py.File(str(p), "r") as f:
                if "fram" not in f or "CRI" not in f["fram"]:
                    continue
                cri = np.asarray(f["fram"]["CRI"][()], float)
                los = np.asarray(f["insar"]["longitudinal"][()], float) if (
                    "insar" in f and "longitudinal" in f["insar"]) else None
                dates = np.asarray(f["insar"]["dates"][()], float) if (
                    "insar" in f and "dates" in f["insar"]) else None
        except (OSError, KeyError):
            continue
        pt_max = cri.max(axis=1) if cri.ndim == 2 else cri
        if exclude_out_of_range is not None:               # 건강 점만(경고·위험 제외)
            keep = pt_max <= exclude_out_of_range.p99
            pt_max = pt_max[keep]
        if pt_max.size == 0:
            continue
        samples.append(pt_max)
        used += 1
        if los is not None and dates is not None and len(dates) >= 3:
            _, noise = _robust_secular_rate(los, dates)
            noises.append(float(np.median(noise)))
            spans.append(float(dates[-1] - dates[0]))
    if not samples:
        raise ValueError("건강 코호트 project.h5 에서 /fram/CRI 를 하나도 못 읽었습니다")
    ref = fit_reference_range(np.concatenate(samples), source=f"field_healthy(n={used})")
    ref.regime = {"noise_mm": round(float(np.median(noises)), 2) if noises else None,
                  "span_days": round(float(np.median(spans)), 1) if spans else None,
                  "n_epochs": None, "n_bridges": used}
    return ref


# 패키지 동봉 기본 정상범위(합성 건강 코호트 적합, 실 S1 규모). 없으면 즉석 적합해 저장.
_DEFAULT_JSON = Path(__file__).with_name("reference_range_default.json")


def default_reference_range(*, rebuild: bool = False) -> ReferenceRange:
    """패키지 동봉 기본 정상범위를 로드(없으면 합성 코호트로 적합해 저장).

    새 교량에 붙일 부트스트랩 기준치. 실측 건강 교량이 모이면 `fit_reference_range` 로
    교체 권장(현장 인구 반영). rebuild=True 면 강제 재적합.
    """
    if not rebuild and _DEFAULT_JSON.exists():
        return ReferenceRange.from_dict(json.loads(_DEFAULT_JSON.read_text(encoding="utf-8")))
    ref = build_default_reference_range()
    try:                                       # 읽기전용 설치(.exe·site-packages)면 저장 생략
        _DEFAULT_JSON.write_text(json.dumps(ref.to_dict(), ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except OSError:
        pass
    return ref
