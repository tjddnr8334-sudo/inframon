"""점별 잔존수명 → 교량 대표값 — **공간 응집 규칙**.

교량 대표값으로 점별 최솟값을 쓰면 안 된다. 수천 점 중 하나는 반드시 노이즈로
짧은 잔존수명을 갖고, 그 한 점이 교량 전체 판정을 지배해 버린다.

실제 결함은 **공간적으로 연속**이다(침하 구역, 손상 구간). 그래서:

> 반경 r 안에서 서로 연결된 점이 **k개 이상 모인 군집**만 유효 열화로 인정하고,
> 그런 군집이 나타나는 가장 이른 잔존수명을 교량 대표값으로 한다.

구현은 잔존수명 오름차순으로 점을 하나씩 켜면서 union-find 로 연결성분을 키우고,
성분 크기가 처음 k 에 닿는 순간의 값을 취한다(정확·O(E α)). FRAM 의
`spatial_prop`(고립점은 결함이 아니다)과 같은 사상이다.
"""

from __future__ import annotations

import numpy as np


class _DSU:
    def __init__(self, n: int):
        self.p = np.arange(n)
        self.size = np.ones(n, dtype=np.int64)

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return int(x)

    def union(self, a: int, b: int) -> int:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return int(self.size[ra])
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.p[rb] = ra
        self.size[ra] += self.size[rb]
        return int(self.size[ra])


def _edges(xy: np.ndarray, radius_m: float, *, block: int = 512):
    n = xy.shape[0]
    ei, ej = [], []
    for a in range(0, n, block):
        b = min(n, a + block)
        d = np.linalg.norm(xy[a:b, None, :] - xy[None, :, :], axis=2)
        ii, jj = np.nonzero((d > 0) & (d <= radius_m))
        ii = ii + a
        keep = ii < jj
        if keep.any():
            ei.append(ii[keep])
            ej.append(jj[keep])
    if not ei:
        return np.empty(0, dtype=int), np.empty(0, dtype=int)
    return np.concatenate(ei), np.concatenate(ej)


def cohesive_min(values: np.ndarray, xy: np.ndarray, *, radius_m: float,
                 min_cluster: int = 3) -> tuple[float | None, dict]:
    """군집 규칙을 만족하는 가장 이른 값. 없으면 (None, meta) = 검열.

    Args:
        values: [N] 점별 잔존수명(검열점은 inf)
        xy:     [N,2] 평면 좌표[m]
        radius_m: 이웃 반경
        min_cluster: 유효 군집 최소 점수 k
    """
    v = np.asarray(values, dtype=np.float64).ravel()
    n = v.shape[0]
    finite = np.isfinite(v)
    if int(finite.sum()) < min_cluster:
        return None, {"reason": f"유의한 열화점 {int(finite.sum())}개 < 최소군집 {min_cluster}",
                      "n_finite": int(finite.sum())}

    ei, ej = _edges(np.asarray(xy, dtype=np.float64), radius_m)
    order = np.argsort(v, kind="stable")               # 잔존수명 이른 순
    rank = np.empty(n, dtype=np.int64)
    rank[order] = np.arange(n)

    # 각 간선은 두 끝점이 모두 켜졌을 때 활성화된다 → 활성화 순위 = max(rank_i, rank_j)
    if ei.size:
        edge_rank = np.maximum(rank[ei], rank[ej])
        eorder = np.argsort(edge_rank, kind="stable")
        ei, ej, edge_rank = ei[eorder], ej[eorder], edge_rank[eorder]
    else:
        edge_rank = np.empty(0, dtype=np.int64)

    dsu = _DSU(n)
    if min_cluster <= 1:
        i0 = int(order[0])
        return float(v[i0]), {"n_finite": int(finite.sum()), "cluster_at": 1,
                              "members": [i0]}

    e = 0
    for r_i in range(n):
        i = order[r_i]
        if not np.isfinite(v[i]):
            break                                      # 이후는 전부 검열점
        while e < edge_rank.shape[0] and edge_rank[e] <= r_i:
            root = dsu.union(int(ei[e]), int(ej[e]))
            if root >= min_cluster:
                # 지배 군집의 구성점을 돌려준다 — 숫자만으로는 조치가 불가능하고,
                # 어디를 봐야 하는지가 있어야 스크리닝으로서 쓸모가 있다.
                lead = dsu.find(int(ei[e]))
                members = [int(k) for k in np.nonzero(
                    [dsu.find(int(x)) == lead and rank[x] <= r_i for x in range(n)])[0]]
                return float(v[i]), {"n_finite": int(finite.sum()),
                                     "cluster_at": int(min_cluster),
                                     "radius_m": round(float(radius_m), 3),
                                     "members": members}
            e += 1
    return None, {"reason": f"반경 {radius_m:.1f}m 안에 {min_cluster}점 이상 모인 열화 군집 없음 "
                            "— 고립점만 존재(노이즈로 판단)",
                  "n_finite": int(finite.sum())}
