"""공통 데이터 레이어 — project.h5 입출력 (설계 문서 6.1).

ProjectStore 가 단일 HDF5 파일을 감싸고,
  - 큰 배열은 데이터셋으로 (/cv, /insar, /pinn, /fram 그룹 아래)
  - 각 모듈의 Pydantic 메타데이터는 그룹 attribute('meta')에 JSON으로
저장한다.

이 한 파일이 모듈 간 유일한 데이터 통로다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from typing import TypeVar

import h5py
import numpy as np
from pydantic import BaseModel

from . import array_schema
from .array_schema import ContractViolation
from .schema import SCHEMA_VERSION

T = TypeVar("T", bound=BaseModel)

GROUPS = ("cv", "insar", "pinn", "fram")
# 런타임 메타데이터(run_id/cfg/해시) 전용 그룹. 데이터셋이 아닌 attribute 만 담아
# 골든 회귀(데이터셋 walk)·결정론성 비교에 영향을 주지 않는다.
META_GROUP = "_meta"


class ProjectStore:
    """project.h5 래퍼. with 문으로 사용한다."""

    def __init__(self, path: str | Path, mode: str = "a"):
        self.path = Path(path)
        self._f = h5py.File(self.path, mode)
        for g in GROUPS:
            self._f.require_group(g)

    # ── 컨텍스트 매니저 ──
    def __enter__(self) -> "ProjectStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._f.close()

    # ── 배열 ──
    def write_array(self, ds_path: str, arr: np.ndarray) -> str:
        """배열을 데이터셋 경로(예: '/cv/roi_mask')에 저장하고 경로를 돌려준다."""
        if ds_path in self._f:
            del self._f[ds_path]
        self._f.create_dataset(ds_path, data=np.asarray(arr), compression="gzip")
        return ds_path

    def read_array(self, ds_path: str) -> np.ndarray:
        return self._f[ds_path][()]

    def has_array(self, ds_path: str) -> bool:
        """데이터셋 존재 여부(계약 검증용)."""
        return ds_path in self._f and isinstance(self._f[ds_path], h5py.Dataset)

    def dataset_info(self, ds_path: str) -> tuple[tuple[int, ...], np.dtype]:
        """전체 로드 없이 (형상, dtype)만 읽는다."""
        ds = self._f[ds_path]
        return tuple(ds.shape), ds.dtype

    # ── 메타데이터(Pydantic) ──
    def write_meta(self, group: str, obj: BaseModel) -> None:
        self._f[group].attrs["meta"] = obj.model_dump_json()
        self._f[group].attrs["meta_type"] = type(obj).__name__

    def read_meta(self, group: str, model: type[T]) -> T:
        raw = self._f[group].attrs["meta"]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        stored = str(json.loads(raw).get("schema_version", SCHEMA_VERSION))
        if stored.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
            raise ContractViolation(
                f"{group} 그룹 schema_version major 불일치 — 파일={stored}, 코드={SCHEMA_VERSION}. "
                f"옛 project.h5 는 현 코드와 호환되지 않습니다(재생성 필요)."
            )
        return model.model_validate_json(raw)

    def has_meta(self, group: str) -> bool:
        return "meta" in self._f[group].attrs

    def write_json_attr(self, group: str, name: str, value: dict[str, Any]) -> None:
        """작은 JSON 메타데이터를 그룹 attribute로 저장한다."""
        self._f[group].attrs[name] = json.dumps(value, ensure_ascii=False)

    def read_json_attr(self, group: str, name: str) -> dict[str, Any]:
        raw = self._f[group].attrs[name]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    # ── 배열 계약 검증 (array_schema 위임) ──
    def validate(
        self, group: str, meta: BaseModel, symbols: dict[str, int] | None = None
    ) -> dict[str, int]:
        """그룹의 `*_ds` 데이터셋이 형상/dtype/심볼 계약을 지키는지 검증한다.

        `symbols` 를 단계마다 넘겨 공유하면 엔진 간 N/M/n_func 일관성까지 강제된다.
        위반 시 `ContractViolation` 을 던진다.
        """
        return array_schema.validate_group(self, group, meta, symbols)

    def validate_all(self, metas: dict[str, BaseModel]) -> dict[str, int]:
        """여러 그룹을 하나의 공유 심볼 표로 한 번에 검증한다."""
        return array_schema.validate_all(self, metas)

    # ── 실행 출처(provenance) 매니페스트 ──
    def compute_dataset_hashes(self) -> dict[str, str]:
        """모든 데이터셋 값의 sha256(앞 16자) — 재현성·변경 추적용."""
        hashes: dict[str, str] = {}

        for g in GROUPS:
            if g not in self._f:
                continue

            def visit(name: str, obj: Any, _g: str = g) -> None:
                # visititems 는 그룹 기준 상대 이름을 주므로 그룹명을 접두사로 붙인다.
                if isinstance(obj, h5py.Dataset):
                    buf = np.ascontiguousarray(obj[()]).tobytes()
                    hashes[f"{_g}/{name}"] = hashlib.sha256(buf).hexdigest()[:16]

            self._f[g].visititems(visit)
        return dict(sorted(hashes.items()))

    def write_manifest(
        self,
        *,
        run_id: str,
        created: str,
        cfg: dict[str, Any],
        engine_modes: dict[str, str],
        stage_fingerprints: dict[str, str] | None = None,
        dataset_hashes: dict[str, str] | None = None,
    ) -> None:
        """실행 출처를 `_meta` 그룹 attribute 로 기록(데이터셋 walk 에 안 잡힘)."""
        g = self._f.require_group(META_GROUP)
        g.attrs["run_id"] = run_id
        g.attrs["created"] = created
        g.attrs["schema_version"] = SCHEMA_VERSION
        g.attrs["config"] = json.dumps(cfg, ensure_ascii=False, default=str)
        g.attrs["engine_modes"] = json.dumps(engine_modes, ensure_ascii=False)
        if stage_fingerprints is not None:
            g.attrs["stage_fingerprints"] = json.dumps(stage_fingerprints, ensure_ascii=False)
        if dataset_hashes is not None:
            g.attrs["dataset_hashes"] = json.dumps(dataset_hashes, ensure_ascii=False)

    def read_manifest(self) -> dict[str, Any]:
        """기록된 실행 매니페스트를 dict 로 돌려준다(없으면 빈 dict)."""
        if META_GROUP not in self._f:
            return {}
        g = self._f[META_GROUP]
        out: dict[str, Any] = {}
        for k in ("run_id", "created", "schema_version"):
            if k in g.attrs:
                v = g.attrs[k]
                out[k] = v.decode("utf-8") if isinstance(v, bytes) else v
        for k in ("config", "engine_modes", "stage_fingerprints", "dataset_hashes"):
            if k in g.attrs:
                raw = g.attrs[k]
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                out[k] = json.loads(raw)
        return out
