"""열화 추세 추정 — 강건 회귀·유의성 검정·가속 검정 (순수 numpy).

잔존수명은 결국 "지금 속도로 가면 언제 한계에 닿는가"이고, 그 답의 품질은 **속도
추정의 품질**이 전부다. 그래서 여기서 세 가지를 지킨다.

1. **강건 추정(Theil–Sen)** — InSAR 시계열에는 unwrapping error 로 인한 큰 이상치가
   섞인다. 최소자승은 이상치 하나에 기울기가 끌려간다. Theil–Sen(쌍별 기울기 중앙값)은
   29% 파괴점을 갖는다.
2. **신뢰구간(Sen 1968)** — 기울기의 비모수 신뢰구간을 Kendall 통계량 분산으로 낸다.
   점추정만 쓰면 잔존수명이 과신된다. 보고 기본값은 **하한**이므로 이 CI 가 필수다.
3. **유의성 검정** — CI 가 0 을 포함하면 "열화 신호 없음"이다. 이때 margin/rate 를
   계산하면 잔존수명 50만년이 나온다. 반드시 **검열(censored)** 로 처리해야 한다.

모든 함수는 시간축을 **년(year)** 으로 받는다.
"""

from __future__ import annotations

import numpy as np

# 표준정규 양측 분위수 — scipy 없이 쓰려고 상수로 둔다(코어 의존성 최소 원칙).
_Z = {0.10: 1.6448536269514722, 0.05: 1.9599639845400545, 0.01: 2.5758293035489004}


def z_for(alpha: float) -> float:
    """양측 신뢰수준 alpha 의 표준정규 분위수. 표에 없으면 0.05 로 폴백."""
    return _Z.get(round(float(alpha), 3), _Z[0.05])


def _pair_index(m: int) -> tuple[np.ndarray, np.ndarray]:
    i, j = np.triu_indices(m, k=1)
    return i, j


def theil_sen(
    t_years: np.ndarray,
    y: np.ndarray,
    *,
    alpha: float = 0.05,
    max_block_elems: int = 4_000_000,
) -> dict:
    """[N,M] 시계열의 점별 Theil–Sen 기울기와 Sen 신뢰구간.

    Args:
        t_years: [M] 관측 시각[년] (단조 증가)
        y:       [N,M] 값(변위 mm 등)
        alpha:   양측 유의수준(기본 0.05 → 95% CI)

    Returns:
        slope [N], lo [N], hi [N], sigma [N] (유효 표준오차=(hi-lo)/(2z)), intercept [N]

    쌍별 기울기는 N×M(M−1)/2 개라 메모리가 커진다(2661점×201시점 ≈ 5300만).
    `max_block_elems` 로 점을 블록 분할해 상수 메모리로 처리한다.
    """
    t = np.asarray(t_years, dtype=np.float64).ravel()
    Y = np.atleast_2d(np.asarray(y, dtype=np.float64))
    n, m = Y.shape
    if t.shape[0] != m:
        raise ValueError(f"t_years 길이 {t.shape[0]} != 시계열 시점 수 {m}")
    if m < 4:
        raise ValueError(f"Theil–Sen 에는 최소 4시점이 필요합니다(입력 {m})")

    ii, jj = _pair_index(m)
    dt = t[jj] - t[ii]
    ok = dt > 0                       # 같은 날 중복 취득 쌍 제외(0 나눗셈 방지)
    ii, jj, dt = ii[ok], jj[ok], dt[ok]
    n_pairs = ii.shape[0]

    # Sen CI 의 순위 오프셋 — Kendall S 통계량의 분산(동점 없음 가정)
    var_s = m * (m - 1) * (2 * m + 5) / 18.0
    c = z_for(alpha) * np.sqrt(var_s)
    k_lo = int(np.clip(np.floor((n_pairs - c) / 2.0) - 1, 0, n_pairs - 1))
    k_hi = int(np.clip(np.ceil((n_pairs + c) / 2.0), 0, n_pairs - 1))

    slope = np.empty(n, dtype=np.float64)
    lo = np.empty(n, dtype=np.float64)
    hi = np.empty(n, dtype=np.float64)

    block = max(1, int(max_block_elems // max(n_pairs, 1)))
    for a in range(0, n, block):
        b = min(n, a + block)
        s = (Y[a:b][:, jj] - Y[a:b][:, ii]) / dt          # [nb, n_pairs]
        s.sort(axis=1)
        mid = n_pairs // 2
        slope[a:b] = s[:, mid] if n_pairs % 2 else 0.5 * (s[:, mid - 1] + s[:, mid])
        lo[a:b] = s[:, k_lo]
        hi[a:b] = s[:, k_hi]

    # 절편: 중앙값 잔차(Theil–Sen 표준 관행) — median(y − slope·t)
    intercept = np.median(Y - slope[:, None] * t[None, :], axis=1)
    sigma = (hi - lo) / (2.0 * z_for(alpha))
    return {"slope": slope, "lo": lo, "hi": hi, "sigma": sigma, "intercept": intercept,
            "n_pairs": int(n_pairs), "alpha": float(alpha)}


def significant(lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """기울기 CI 가 0 을 포함하지 않는가 → 유의한 열화 추세가 있는가. [N] bool

    0 을 포함하면 관측 구간에서 변화가 잡히지 않은 것이다. 이 점의 잔존수명은
    "매우 김"이 아니라 **정의되지 않음(검열)** 이다.
    """
    return (np.asarray(lo) > 0) | (np.asarray(hi) < 0)


def adverse_rate(fit: dict) -> tuple[np.ndarray, np.ndarray]:
    """부호 무관 열화율과 그 보수적 상한. [N], [N]

    교량에서는 침하(음)든 융기(양)든 둘 다 이상 거동이므로 **크기**로 다룬다.
    상한은 CI 중 0 에서 먼 쪽 끝 — 잔존수명 하한(margin/rate_upper)에 쓴다.
    """
    slope, lo, hi = fit["slope"], fit["lo"], fit["hi"]
    rate = np.abs(slope)
    rate_hi = np.maximum(np.abs(lo), np.abs(hi))
    return rate, rate_hi


def quadratic_acceleration(t_years: np.ndarray, y: np.ndarray, *, alpha: float = 0.05) -> dict:
    """2차항 검정 — 열화가 가속 중인가. y = a + b·t + c·t².

    선형 외삽은 열화가 가속되면 낙관적이다. c 가 유의하고 **b 와 같은 부호**면
    변위 크기가 점점 빨리 커지는 것이므로 가속으로 본다.

    Returns: c [N], b [N], t_stat [N], accel [N] bool
    """
    t = np.asarray(t_years, dtype=np.float64).ravel()
    Y = np.atleast_2d(np.asarray(y, dtype=np.float64))
    n, m = Y.shape
    if m < 5:                                  # 3모수 + 자유도 최소 2
        z = np.zeros(n)
        return {"c": z, "b": z, "t_stat": z, "accel": np.zeros(n, dtype=bool)}
    X = np.stack([np.ones(m), t, t ** 2], axis=1)          # [M,3]
    coef, *_ = np.linalg.lstsq(X, Y.T, rcond=None)         # [3,N]
    resid = Y.T - X @ coef                                 # [M,N]
    dof = m - 3
    s2 = (resid ** 2).sum(axis=0) / dof                    # [N]
    xtx_inv = np.linalg.pinv(X.T @ X)
    se_c = np.sqrt(np.maximum(s2 * xtx_inv[2, 2], 0.0)) + 1e-300
    b, c = coef[1], coef[2]
    t_stat = c / se_c
    accel = (np.abs(t_stat) > z_for(alpha)) & (np.sign(c) == np.sign(b)) & (c != 0)
    return {"c": c, "b": b, "t_stat": t_stat, "accel": accel}


def time_to_limit_quadratic(b: np.ndarray, c: np.ndarray, margin: np.ndarray) -> np.ndarray:
    """가속(2차) 모델에서 남은 여유 `margin` 을 소진하기까지의 시간[년]. [N]

    현재 시각을 원점으로 두고 변위 크기 증가분 |b|·τ + |c|·τ² = margin 을 푼다
    (가속 국면이므로 두 항 모두 악화 방향). 실근이 없으면 +inf.
    """
    B, C, Mg = np.abs(np.asarray(b, dtype=np.float64)), np.abs(np.asarray(c, dtype=np.float64)), np.asarray(margin, dtype=np.float64)
    out = np.full(B.shape, np.inf)
    lin = C <= 1e-30
    with np.errstate(divide="ignore", invalid="ignore"):
        out[lin] = np.where(B[lin] > 0, Mg[lin] / B[lin], np.inf)
        q = ~lin
        disc = B[q] ** 2 + 4.0 * C[q] * Mg[q]
        good = disc >= 0
        tau = np.full(disc.shape, np.inf)
        tau[good] = (-B[q][good] + np.sqrt(disc[good])) / (2.0 * C[q][good])
        out[q] = tau
    return np.where(np.isfinite(out) & (out >= 0), out, np.inf)
