"""FRAM 6측면 함수망 — 기능을 6측면(I/O/R/C/T/P) 노드로, 기능 간 결합을 그래프로.

설계 §5.3: 각 교량 기능은 Input·Output·Resource·Control·Time 측면으로 정의되고,
한 기능의 **Output 이 다른 기능의 Input/Resource/Control 로** 흘러 결합한다(전형적
FRAM instantiation). 이 결합 토폴로지를 방향 그래프로 두고, 시간변동 공명 R_ij 로
간선을 가중해 **기능별 criticality·구동 기능(driver)·임계 전파 경로**를 진단한다.

R_ij 의 스펙트럼 반경(N-K, real_engine `_network_resonance`)이 '전체 응집도'라면,
이 모듈은 그 응집이 **어느 기능을 통해 어느 경로로** 일어나는지를 구조적으로 본다.
networkx 가 있으면 최장 가중 경로를 쓰고, 없으면 numpy 폴백.
"""

from __future__ import annotations

import numpy as np

ASPECTS = ("Input", "Output", "Resource", "Control", "Time", "Precondition")

# 기능 결합 토폴로지 (from 의 Output → to 의 aspect). 설계 §5.3 물리 기반.
FRAM_COUPLING: tuple[tuple[str, str, str], ...] = (
    ("thermal", "bearing", "Output→Input"),      # 열팽창 신축 → 받침 변위
    ("load", "bearing", "Output→Input"),         # 하중 처짐 → 받침 반력
    ("load", "thermal", "Output→Resource"),      # 하중-열 상호(휨강성 공유)
    ("foundation", "load", "Output→Control"),    # 침하 → 하중지지 능력 저하
    ("bearing", "load", "Output→Resource"),      # 받침 이상 → 하중 분배 변화
)


def function_network(R_ij: np.ndarray, func_names, win_frac: float = 0.25) -> dict:
    """R_ij[F,F,M] 의 최근 결합으로 FRAM 함수망 진단을 만든다.

    반환: aspects, edges([from,to,aspect,weight]), criticality(기능별),
    driver(가장 많이 구동하는 기능), critical_path(최대 가중 경로), cohesion(평균 결합).
    """
    F, _, M = R_ij.shape
    w = max(1, int(M * win_frac))
    C = np.abs(R_ij[:, :, -w:]).mean(axis=2)                  # [F,F] 최근 평균 결합
    idx = {n: i for i, n in enumerate(func_names)}

    edges = []
    out_str = {n: 0.0 for n in func_names}
    in_str = {n: 0.0 for n in func_names}
    for a, b, aspect in FRAM_COUPLING:
        if a in idx and b in idx:
            weight = float(C[idx[a], idx[b]])
            edges.append({"from": a, "to": b, "aspect": aspect, "weight": round(weight, 4)})
            out_str[a] += weight
            in_str[b] += weight

    criticality = {n: round(out_str[n] + in_str[n], 4) for n in func_names}
    driver = max(func_names, key=lambda n: out_str[n]) if edges else None
    cohesion = round(float(np.mean([e["weight"] for e in edges])), 4) if edges else 0.0
    path, path_w = _critical_path(edges, func_names)
    return {
        "aspects": list(ASPECTS),
        "edges": edges,
        "criticality": criticality,
        "driver": driver,
        "critical_path": path,
        "critical_path_weight": round(path_w, 4),
        "cohesion": cohesion,
    }


def _critical_path(edges: list, func_names) -> tuple[list, float]:
    """최대 가중 단순 경로(가장 강한 전파 사슬). networkx 있으면 사용, 없으면 최강 간선."""
    if not edges:
        return [], 0.0
    try:
        import networkx as nx

        g = nx.DiGraph()
        for e in edges:
            g.add_edge(e["from"], e["to"], weight=e["weight"])
        best: list = []
        best_w = -1.0
        nodes = list(g.nodes)
        for s in nodes:
            for t in nodes:
                if s == t:
                    continue
                for p in nx.all_simple_paths(g, s, t):
                    wsum = sum(g[p[i]][p[i + 1]]["weight"] for i in range(len(p) - 1))
                    if wsum > best_w:
                        best_w, best = wsum, p
        return best, max(best_w, 0.0)
    except ImportError:
        strongest = max(edges, key=lambda e: e["weight"])
        return [strongest["from"], strongest["to"]], strongest["weight"]
