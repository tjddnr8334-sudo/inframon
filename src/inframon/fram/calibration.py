"""Isotonic 캘리브레이션 — CRI(순위형 위험점수)를 붕괴 확률로 단조 보정한다.

CRI 는 포화 가중합이라 [0,1] 이지만 **실제 붕괴 확률이 아니다**(절대 임계의 의미가
약함). 라벨된 데이터(예: `synthetic.make_collapse_scenario`)로 isotonic 회귀를 적합해
`CRI → P(failure)` 단조 매핑을 학습한다. 단조 변환이므로 **순위(ROC-AUC)는 보존**하고
확률 보정(Brier↓)만 한다. sklearn 없이 PAVA 로 구현(코어 경량).

학습은 라벨이 필요하므로 오프라인(합성/과거 사건)에서 적합해 직렬화하고, 추론 때
`IsotonicCalibrator.from_dict(...)` 로 불러 적용한다(`cfg.fram_calibrator`).
"""

from __future__ import annotations

import numpy as np


def isotonic_regression(y: np.ndarray, w: np.ndarray | None = None) -> np.ndarray:
    """PAVA — 주어진 순서에서 가중 SSE 최소 **비감소** 적합값을 반환.

    블록 스택 방식: 인접 위반(이전 평균 ≥ 현재)이면 풀링(pool)해 단조성을 회복한다.
    """
    y = np.asarray(y, dtype=np.float64)
    n = y.size
    w = np.ones(n) if w is None else np.asarray(w, dtype=np.float64)
    means: list[float] = []
    wts: list[float] = []
    cnts: list[int] = []
    for i in range(n):
        cm, cw, cc = float(y[i]), float(w[i]), 1
        while means and means[-1] >= cm:               # 위반 → 이전 블록과 풀링
            pm, pw, pc = means.pop(), wts.pop(), cnts.pop()
            cm = (pm * pw + cm * cw) / (pw + cw)
            cw += pw
            cc += pc
        means.append(cm)
        wts.append(cw)
        cnts.append(cc)
    out = np.empty(n)
    idx = 0
    for m, c in zip(means, cnts):
        out[idx : idx + c] = m
        idx += c
    return out


class IsotonicCalibrator:
    """점수→확률 단조 매핑(직렬화 가능). 학습 밖에서 적합 후 추론에 적용."""

    def __init__(self, x: np.ndarray | None = None, y: np.ndarray | None = None) -> None:
        self.x_ = None if x is None else np.asarray(x, dtype=np.float64)
        self.y_ = None if y is None else np.asarray(y, dtype=np.float64)

    def fit(self, scores: np.ndarray, labels: np.ndarray) -> IsotonicCalibrator:
        """점수 오름차순으로 라벨을 PAVA 적합 → 보간용 (임계 점수, 확률) 학습."""
        x = np.asarray(scores, dtype=np.float64)
        y = np.asarray(labels, dtype=np.float64)
        if x.size == 0:
            raise ValueError("캘리브레이션에 빈 입력이 들어왔습니다")
        order = np.argsort(x, kind="mergesort")
        xs, ys = x[order], y[order]
        fitted = isotonic_regression(ys)
        # 동일 점수(ties)는 보간 단조성을 위해 평균으로 합치고 누적 max 로 비감소 보장.
        ux, inv = np.unique(xs, return_inverse=True)
        acc = np.zeros(ux.size)
        cnt = np.zeros(ux.size)
        np.add.at(acc, inv, fitted)
        np.add.at(cnt, inv, 1.0)
        uv = np.maximum.accumulate(acc / cnt)
        self.x_, self.y_ = ux, np.clip(uv, 0.0, 1.0)
        return self

    def predict(self, scores: np.ndarray) -> np.ndarray:
        """학습한 매핑으로 보정 확률(선형보간, 양끝 클램프). 입력 형상 유지."""
        if self.x_ is None or self.y_ is None:
            raise ValueError("적합되지 않은 캘리브레이터입니다 (fit/from_dict 먼저)")
        s = np.asarray(scores, dtype=np.float64)
        return np.clip(np.interp(s, self.x_, self.y_), 0.0, 1.0)

    def to_dict(self) -> dict:
        return {"x": self.x_.tolist(), "y": self.y_.tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> IsotonicCalibrator:
        return cls(np.asarray(d["x"], dtype=np.float64), np.asarray(d["y"], dtype=np.float64))


def brier_score(prob: np.ndarray, label: np.ndarray) -> float:
    """평균 제곱오차 Σ(p-y)²/n — 보정 품질(낮을수록 좋음)."""
    p = np.asarray(prob, dtype=np.float64)
    y = np.asarray(label, dtype=np.float64)
    return float(np.mean((p - y) ** 2))
