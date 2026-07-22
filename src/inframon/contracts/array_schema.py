"""배열 레벨 계약 — project.h5 안 데이터셋의 형상/dtype/심볼 일관성 규약.

`schema.py` 의 Pydantic 계약은 *경로 문자열*(`*_ds`)만 타입 지정한다. 이 모듈은
그 경로가 실제로 가리키는 HDF5 데이터셋이 약속된 차원(N, M, H, W, K, n_func)과
dtype 을 갖는지 검증한다.

차원 심볼은 한 번의 검증 호출 안에서 그룹·엔진을 가로질러 결속(bind)된다. 즉
한 번 `N=60` 으로 묶이면 insar/pinn/fram 의 모든 `N` 이 60 이어야 하고, 모든 `M`,
모든 `n_func(F)` 도 마찬가지다. 그래서 엔진을 stub↔real 로 갈아끼우다 형상이
조용히 어긋나면(예: pinn 이 N 을 바꿔버림) 즉시 `ContractViolation` 으로 잡힌다.

설계 철학("계약은 성역"): 지금까지 계약은 경로 문자열까지만 성역이었고 배열은
무방비였다. 이 모듈이 그 빈틈을 메운다.
"""

from __future__ import annotations

from typing import Iterator

from pydantic import BaseModel

# numpy dtype.kind 집합 — f=float, i=int, u=uint, b=bool, c=complex
_FLOAT = frozenset("fc")
_INT = frozenset("iu")
_BOOL = frozenset("b")
_MASK = frozenset("biu")  # 마스크는 stub(uint8)·real(bool/int) 모두 허용
_MASK_OR_FRAC = frozenset("biuf")  # layover: 이진 마스크 또는 분율 강도(0~1) 모두 허용
_KIND_NAMES = {"f": "float", "c": "complex", "i": "int", "u": "uint", "b": "bool"}

# 차원 심볼: 문자열="자유 심볼"(검증 중 결속·그룹 간 공유), 정수=고정 길이
# (그룹) -> {Pydantic 필드명: (차원 튜플, 허용 dtype kind)}
ARRAY_SPECS: dict[str, dict[str, tuple[tuple, frozenset]]] = {
    "cv": {
        "roi_mask_ds": (("H", "W"), _MASK),
        "member_label_ds": (("H", "W"), _MASK),  # dict 의 각 값
        "shadow_ds": (("H", "W"), _MASK),
        "layover_ds": (("H", "W"), _MASK_OR_FRAC),
        "grid_density_ds": (("H", "W"), _FLOAT),
    },
    "insar": {
        "point_id_ds": (("N",), _INT),
        "xyz_ds": (("N", 3), _FLOAT),
        "member_ds": (("N",), _INT),
        "coherence_ds": (("N",), _FLOAT),
        "l_from_fixed_ds": (("N",), _FLOAT),
        "los_ds": (("N", "M"), _FLOAT),
        "longitudinal_ds": (("N", "M"), _FLOAT),
        "dates_ds": (("M",), _FLOAT),
        "temporal_coherence_ds": (("N",), _FLOAT),
        "vertical_ds": (("N", "M"), _FLOAT),  # 선택(asc+desc 융합) — None 이면 검증 생략
    },
    "pinn": {
        "comp_thermal_ds": (("N", "M"), _FLOAT),
        "comp_load_ds": (("N", "M"), _FLOAT),
        "comp_settle_ds": (("N", "M"), _FLOAT),
        "comp_anomaly_ds": (("N", "M"), _FLOAT),
        "strain_ds": (("N", "M"), _FLOAT),
        "stress_ds": (("N", "M"), _FLOAT),
        "deflection_ds": (("N", "M"), _FLOAT),
        "natural_freq_ds": (("K",), _FLOAT),
        "EI_ds": (("N",), _FLOAT),
        "alpha_ds": (("N",), _FLOAT),
        "V_thermal_ds": (("N",), _FLOAT),
        "V_load_ds": (("N",), _FLOAT),
        "V_settle_ds": (("N",), _FLOAT),
        "V_anomaly_ds": (("N",), _FLOAT),
        "V_func_series_ds": (("F", "M"), _FLOAT),
        # 가상센싱(상부거더 전체 변위장) — 가상센서 수 V. 선택(real) — None 이면 검증 생략.
        "vsens_x_ds": (("V",), _FLOAT),
        "vsens_l_from_fixed_ds": (("V",), _FLOAT),
        "vsens_total_ds": (("V", "M"), _FLOAT),
        "vsens_deflection_ds": (("V", "M"), _FLOAT),
        "vsens_thermal_ds": (("V", "M"), _FLOAT),
        "vsens_settle_ds": (("V", "M"), _FLOAT),
        "vsens_anomaly_ds": (("V", "M"), _FLOAT),
        # 가상센싱 2D 상판 면 — 격자점 수 G. 선택(real) — None 이면 검증 생략.
        "deck_xy_ds": (("G", 2), _FLOAT),
        "deck_s_ds": (("G",), _FLOAT),
        "deck_w_ds": (("G",), _FLOAT),
        "deck_total_ds": (("G", "M"), _FLOAT),
        "deck_deflection_ds": (("G", "M"), _FLOAT),
    },
    # 잔존수명(opt-in 후처리). N 은 insar/pinn/fram 과 같은 심볼로 결속된다.
    "life": {
        "rsl_point_ds": (("N",), _FLOAT),
        "rsl_lower_ds": (("N",), _FLOAT),
        "rate_ds": (("N",), _FLOAT),
        "rate_sigma_ds": (("N",), _FLOAT),
        "sublimit_ds": (("N",), _INT),
    },
    "fram": {
        "resonance_Rij_ds": (("F", "F", "M"), _FLOAT),
        "amplification_ds": (("N", "M"), _FLOAT),
        "spatial_prop_ds": (("N", "M"), _FLOAT),
        "divergence_ds": (("N", "M"), _FLOAT),
        "CRI_ds": (("N", "M"), _FLOAT),
        "network_resonance_ds": (("M",), _FLOAT),  # 선택(real) — None 이면 검증 생략
        "calibrated_risk_ds": (("N", "M"), _FLOAT),  # 선택 — 캘리브레이터 있을 때만
    },
}

# Pydantic 스칼라 -> 차원 심볼 (선언 스칼라 ↔ 실제 배열 차원 교차검증)
_SCALAR_SYMBOLS: dict[str, dict[str, str]] = {
    "insar": {"n_points": "N", "n_dates": "M"},
    "pinn": {"n_points": "N", "n_dates": "M", "n_virtual": "V", "n_deck": "G"},
    "fram": {"n_points": "N", "n_dates": "M"},
    "life": {"n_points": "N"},
}


class ContractViolation(ValueError):
    """배열 계약 위반 — 데이터셋 누락/형상/dtype/심볼 불일치."""


def _kinds_str(kinds: frozenset) -> str:
    return "|".join(_KIND_NAMES.get(k, k) for k in sorted(kinds))


def _bind(symbols: dict[str, int], sym: str, size: int, where: str) -> None:
    """자유 심볼을 크기에 결속. 이미 다른 값이면 계약 위반."""
    if sym in symbols and symbols[sym] != size:
        raise ContractViolation(
            f"{where}: 차원 심볼 {sym!r} 불일치 — 기존 {symbols[sym]} vs 신규 {size}. "
            f"(엔진 간 N/M/n_func 가 어긋났습니다)"
        )
    symbols[sym] = size


def _iter_ds_fields(meta: BaseModel) -> Iterator[tuple[str, object]]:
    """meta 의 `*_ds` 필드(이름, 값)를 순회한다."""
    for name in type(meta).model_fields:
        if name.endswith("_ds"):
            yield name, getattr(meta, name)


def _paths_of(value: object) -> list[str]:
    """`*_ds` 값에서 실제 데이터셋 경로 목록을 뽑는다(str / dict / None 대응)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [v for v in value.values() if isinstance(v, str)]
    return []


def _check_dataset(
    store,
    group: str,
    field: str,
    path: str,
    spec: tuple[tuple, frozenset] | None,
    symbols: dict[str, int],
) -> None:
    where = f"{group}.{field}({path})"
    if not store.has_array(path):
        raise ContractViolation(f"{where}: 선언된 데이터셋이 project.h5 에 없습니다")
    if spec is None:  # 카탈로그에 없는 필드 — 존재만 확인하고 통과(전방호환)
        return
    dims, kinds = spec
    shape, dtype = store.dataset_info(path)
    if len(shape) != len(dims):
        raise ContractViolation(
            f"{where}: 차원 수 {len(shape)} != 기대 {len(dims)} (기대 형상 {dims}, 실제 {tuple(shape)})"
        )
    for axis, (dim, size) in enumerate(zip(dims, shape)):
        if isinstance(dim, int):
            if size != dim:
                raise ContractViolation(
                    f"{where}: axis {axis} 길이 {size} != 고정 {dim}"
                )
        else:
            _bind(symbols, dim, int(size), f"{where} axis {axis}")
    if dtype.kind not in kinds:
        raise ContractViolation(
            f"{where}: dtype kind {dtype.kind!r}({dtype}) — 기대 {_kinds_str(kinds)}"
        )


def validate_group(
    store, group: str, meta: BaseModel, symbols: dict[str, int] | None = None
) -> dict[str, int]:
    """한 그룹의 모든 `*_ds` 데이터셋을 계약대로 검증한다.

    `symbols` 를 넘기면 그 심볼 표를 공유·갱신하여 그룹 간 차원 일관성까지 본다.
    위반 시 `ContractViolation` 을 던지고, 통과하면 갱신된 심볼 표를 돌려준다.
    """
    symbols = {} if symbols is None else symbols

    # 1) 선언 스칼라 -> 심볼 결속 (배열 차원과의 교차검증 기준점)
    for attr, sym in _SCALAR_SYMBOLS.get(group, {}).items():
        val = getattr(meta, attr, None)
        if isinstance(val, int):
            _bind(symbols, sym, val, f"{group}.{attr}")
    func_names = getattr(meta, "func_names", None)
    if isinstance(func_names, (list, tuple)):
        _bind(symbols, "F", len(func_names), f"{group}.func_names")
    image_shape = getattr(meta, "image_shape", None)
    if isinstance(image_shape, (tuple, list)) and len(image_shape) == 2:
        _bind(symbols, "H", int(image_shape[0]), f"{group}.image_shape[0]")
        _bind(symbols, "W", int(image_shape[1]), f"{group}.image_shape[1]")

    # 2) 모든 *_ds 데이터셋 검증
    specs = ARRAY_SPECS.get(group, {})
    for field, value in _iter_ds_fields(meta):
        for path in _paths_of(value):
            _check_dataset(store, group, field, path, specs.get(field), symbols)
    return symbols


def validate_all(store, metas: dict[str, BaseModel]) -> dict[str, int]:
    """여러 그룹을 하나의 공유 심볼 표로 검증(엔진 간 N/M/n_func 일관성 포함)."""
    symbols: dict[str, int] = {}
    for group, meta in metas.items():
        validate_group(store, group, meta, symbols)
    return symbols
