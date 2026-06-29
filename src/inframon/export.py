"""project.h5 계약 → KAIA 변위 CSV (롱포맷, 점×시점).

KAIA 파이프라인 3단계 산출물 — InSAR 수직변위 + PINN 가상센싱 전체변위를 한 표로 떨군다.
이후 VLM 안전보고서·지식그래프의 입력 포맷이 된다.

api/transform.py 의 변환 원시함수(좌표 5179→WGS84·단위 mm·epoch→ISO·member 라벨)를
재사용한다 — contracts/ 는 성역, 여기서도 ProjectStore(mode="r")로 읽어 변환만 한다.

테이블 스키마(있는 산출물만 채움, 없으면 빈칸):
  bridge_id, point_id, member, date, lat, lon, elev_m, coherence,
  los_mm, longitudinal_mm, vertical_mm, cri, EI, alpha
- 한 행 = (측점, 취득일). EI/alpha 는 점별 정적값(시점마다 반복).
- vertical_mm 은 asc+desc 융합(vertical_ds) 있을 때만, cri 는 FRAM, EI/alpha 는 PINN 있을 때만.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import numpy as np

from .api import transform
from .api.transform import WGS84
from .contracts.io import ProjectStore

COLUMNS = [
    "bridge_id", "point_id", "member", "date",
    "lat", "lon", "elev_m", "coherence",
    "los_mm", "longitudinal_mm", "vertical_mm", "cri", "EI", "alpha",
]


def build_rows(store: ProjectStore, *, bridge_id: str = "", to_crs: str = WGS84) -> list[dict[str, Any]]:
    """project.h5 → 롱포맷 행 리스트(점×시점). InSAR 필수, PINN/FRAM/연직은 선택."""
    ins = transform._insar(store)                       # 없으면 ResultNotFound
    pinn = transform._pinn(store)
    fram = transform._fram(store)

    latlon = transform.xyz_to_latlon(store.read_array(ins.xyz_ds), to_crs)   # [N,3] lat,lon,elev
    member = store.read_array(ins.member_ds)
    coh = store.read_array(ins.coherence_ds)
    pid = store.read_array(ins.point_id_ds)
    los = store.read_array(ins.los_ds)                  # [N,M] mm
    lon_disp = store.read_array(ins.longitudinal_ds)    # [N,M] mm
    dates = [transform.epoch_days_to_iso(d) for d in store.read_array(ins.dates_ds)]
    N, M = int(ins.n_points), int(ins.n_dates)

    vert = store.read_array(ins.vertical_ds) if ins.vertical_ds else None     # [N,M] mm
    cri = store.read_array(fram.CRI_ds) if fram is not None else None         # [N,M]
    EI = store.read_array(pinn.EI_ds) if pinn is not None else None           # [N]
    alpha = store.read_array(pinn.alpha_ds) if pinn is not None else None     # [N]

    rows: list[dict[str, Any]] = []
    for i in range(N):
        base = {
            "bridge_id": bridge_id,
            "point_id": int(pid[i]),
            "member": transform.member_label(member[i]),
            "lat": round(float(latlon[i, 0]), 7),
            "lon": round(float(latlon[i, 1]), 7),
            "elev_m": round(float(latlon[i, 2]), 2),
            "coherence": round(float(coh[i]), 3),
            "EI": (float(EI[i]) if EI is not None else ""),
            "alpha": (float(alpha[i]) if alpha is not None else ""),
        }
        for k in range(M):
            row = dict(base)
            row["date"] = dates[k]
            row["los_mm"] = round(float(los[i, k]), 3)
            row["longitudinal_mm"] = round(float(lon_disp[i, k]), 3)
            row["vertical_mm"] = (round(float(vert[i, k]), 3) if vert is not None else "")
            row["cri"] = (round(float(cri[i, k]), 4) if cri is not None else "")
            rows.append(row)
    return rows


def export_csv(h5_path: str | Path, csv_path: str | Path, *,
               bridge_id: str = "", to_crs: str = WGS84) -> dict[str, Any]:
    """project.h5 → CSV 파일. 요약 dict(행수·점수·시점수·연직/PINN/FRAM 포함여부) 반환."""
    with ProjectStore(Path(h5_path), mode="r") as store:
        ins = transform._insar(store)
        has_vert = ins.vertical_ds is not None
        has_pinn = transform._pinn(store) is not None
        has_fram = transform._fram(store) is not None
        rows = build_rows(store, bridge_id=bridge_id, to_crs=to_crs)

    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: Excel(한국어 환경)에서 UTF-8 CSV 한글이 깨지지 않게 BOM 부여.
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    return {
        "csv": str(csv_path), "rows": len(rows),
        "n_points": int(ins.n_points), "n_dates": int(ins.n_dates),
        "has_vertical": has_vert, "has_pinn": has_pinn, "has_fram": has_fram,
    }
