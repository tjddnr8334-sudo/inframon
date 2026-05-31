"""대시보드 데이터 접근 계층 (streamlit 비의존, 단위 테스트 가능).

UI(`app.py`)에서 분리해 project.h5 읽기·패널용 데이터 준비를 담는다. 읽기 전용
뷰어라 raw h5py 로 가볍게 접근하되, 한 곳에 모아 중복을 없앤다(§5.7 정리).
선택 출력(`network_resonance`, `calibrated_risk`)은 없으면 None 으로 안전 처리.
"""

from __future__ import annotations

import json

import h5py
import numpy as np


def has_group(path: str, group: str) -> bool:
    with h5py.File(path, "r") as f:
        return f"/{group}" in f and len(f[f"/{group}"].keys()) > 0


def read_arrays(path: str, *names: str):
    """데이터셋 경로들을 읽는다(없으면 None). 단일이면 값, 복수면 리스트."""
    with h5py.File(path, "r") as f:
        out = [f[n][()] if n in f else None for n in names]
    return out if len(out) > 1 else out[0]


def read_meta(path: str, group: str) -> dict:
    with h5py.File(path, "r") as f:
        raw = f[f"/{group}"].attrs.get("meta", "{}") if f"/{group}" in f else "{}"
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


def fram_panel_data(path: str) -> dict:
    """FRAM 탭이 그릴 데이터 묶음 — 핵심 CRI + 선택 출력(함수망 공명·보정확률).

    반환 키:
      cri[N,M], xyz[N,3], warning(dict), cri_max,
      network_resonance[M]|None (함수망 N-K 시스템 공명 강도),
      calibrated_risk[N,M]|None (isotonic 보정 붕괴확률), calibrated_max|None.
    """
    cri, xyz, net, cal = read_arrays(
        path, "/fram/CRI", "/insar/xyz", "/fram/network_resonance", "/fram/calibrated_risk"
    )
    warning = read_meta(path, "fram").get("warning", {})
    return {
        "cri": cri,
        "xyz": xyz,
        "warning": warning,
        "cri_max": None if cri is None else float(np.max(cri)),
        "network_resonance": net,
        "calibrated_risk": cal,
        "calibrated_max": None if cal is None else float(np.max(cal)),
    }


def fram_function_diagram(path: str, k: int | None = None) -> dict | None:
    """FRAM 기능 공명 다이어그램 데이터 — 4기능 변동(레이더) + R_ij 결합행렬(시점 k).

    `/pinn/V_func_series`[4,M] 의 시점 k 기능별 변동과 `/fram/R_ij`[4,4,M] 의 시점 k
    기능 간 결합을 돌려준다(설계 5.6 FRAM 기능망 동역학의 단면). pinn/fram 없으면 None.
    """
    vfs, rij = read_arrays(path, "/pinn/V_func_series", "/fram/R_ij")
    if vfs is None or rij is None:
        return None
    vfs, rij = np.asarray(vfs), np.asarray(rij)
    func_names = read_meta(path, "pinn").get("func_names") or [
        "thermal", "load", "bearing", "foundation"]
    n_dates = int(vfs.shape[1])
    kk = (n_dates - 1) if k is None else max(0, min(int(k), n_dates - 1))
    return {
        "func_names": list(func_names),
        "variability": vfs[:, kk].astype(float),       # [n_func]
        "coupling": rij[:, :, kk].astype(float),        # [n_func, n_func]
        "k": kk,
        "n_dates": n_dates,
    }
