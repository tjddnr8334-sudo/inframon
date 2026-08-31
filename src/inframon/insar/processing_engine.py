"""⑧ InSAR 처리 엔진 선택 — "SLC/도구산출 → Track H5" 를 갈아끼운다.

파이프라인 ⑧이 SNAP 에 하드코딩돼 있어 SARvey·HyP3·MintPy·MiaplPy·StaMPS 를 쓰려면
파이프라인 밖에서 수동으로 돌려야 했다. 여기서 **엔진 이름 하나로** 고를 수 있게 한다.

계약은 하나뿐: `run(name, lat, lon, out_dir, out_h5, **opts) -> EngineResult` 가
**Track H5 경로**를 돌려준다. 하류(⑨ PS/DS·⑫ PINN·⑬ 트윈·⑭ BMAP)는 그 경로만
소비하므로 엔진이 바뀌어도 수정할 게 없다(`orchestrator/engines.py` 의 관용구를 본떴다.
단 그쪽 축은 "Track H5 → CV 정합 인제스트"라 층이 다르므로 별도 레지스트리다).

엔진 두 종류:
  · **처리형**(snap·hyp3) — 좌표만 주면 취득·처리까지 스스로 한다.
  · **가져오기형**(sarvey·miaplpy·mintpy·stamps) — 사용자가 WSL 등에서 이미 돌린
    산출물을 `source=` 로 지목하면 Track H5 계약으로 변환한다. 이 도구들은 리포에
    실행 드라이버가 없고 변환 어댑터(`scripts/wsl_sarvey/5x_*.py`)만 있기 때문에,
    "없는 실행기를 있는 척" 하지 않고 입력을 요구하는 쪽이 정직하다.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# 처리형은 좌표만으로 완주, 가져오기형은 source(도구 산출물) 필요.
PROCESS_ENGINES = ("snap", "hyp3")
IMPORT_ENGINES = ("sarvey", "miaplpy", "mintpy", "stamps")
ENGINE_NAMES = PROCESS_ENGINES + IMPORT_ENGINES

_ADAPTERS = {          # 가져오기형 → scripts/wsl_sarvey 어댑터 스크립트
    "sarvey": "50_export_to_inframon",
    "miaplpy": "52_miaplpy_to_inframon",
    "mintpy": "54_mintpy_to_inframon",
    "stamps": "56_stamps_to_inframon",
}


class EngineError(RuntimeError):
    """엔진 선택·실행 실패(미지 엔진·입력 누락·처리 실패)."""


@dataclass
class EngineResult:
    """⑧ 산출 — 하류가 보는 건 track_h5 뿐이고 나머지는 보고용."""

    engine: str
    track_h5: str
    n_points: int = 0
    detail: str = ""
    native: object | None = None      # 엔진 고유 결과(SNAP 은 SnapRunResult → ⑨에 쓰임)
    extra: dict = field(default_factory=dict)
    # ⑨ 데크 PS/DS 재추출이 가능한가 = **쌍별 GeoTIFF(pairs·reference·burst)를 주는가**.
    # native 가 None 인지로 판정하면 안 된다 — hyp3 도 native 를 주지만 쌍 정보가 없어
    # ⑨가 res.pairs 를 만지는 순간 AttributeError 로 ⑧이 사후에 실패로 뒤집힌다.
    supports_deck_ps_ds: bool = False


_REGISTRY: dict[str, Callable[..., EngineResult]] = {}


def register(name: str, fn: Callable[..., EngineResult]) -> None:
    _REGISTRY[name] = fn


def resolve(name: str) -> Callable[..., EngineResult]:
    key = (name or "").strip().lower()
    if key not in _REGISTRY:
        raise EngineError(f"알 수 없는 InSAR 처리 엔진: {name!r} — 가능: {', '.join(available())}")
    return _REGISTRY[key]


def available() -> list[str]:
    return sorted(_REGISTRY)


def describe(name: str) -> str:
    """UI·plan 표시용 한 줄 설명."""
    return {
        "snap": "SNAP(Windows 네이티브) — WSL 불필요, 스타 네트워크·burst 처리",
        "hyp3": "HyP3(ASF 클라우드) — 로컬 SAR 연산 없음, 월 크레딧 소모",
        "sarvey": "SARvey(WSL) 산출 가져오기 — 최고품질 PSI, 희소 스택 대응",
        "miaplpy": "MiaplPy 산출 가져오기 — phase-linking(DS 강점)",
        "mintpy": "MintPy 산출 가져오기 — SBAS/QPS 지오코딩 시계열",
        "stamps": "StaMPS 산출 가져오기 — PS 고전 기법(.mat)",
    }.get((name or "").lower(), name)


def needs_source(name: str) -> bool:
    """가져오기형이면 True — 사용자가 도구 산출물을 지목해야 한다."""
    return (name or "").lower() in IMPORT_ENGINES


# ⑨(데크 30m PS/DS 재추출)는 쌍별 GeoTIFF 를 주는 엔진에서만 가능하다.
# plan 문구와 full 실행 분기가 **같은 사실**을 보게 하려고 여기 한 곳에 둔다.
DECK_PS_DS_ENGINES = ("snap",)


def supports_deck_ps_ds(name: str) -> bool:
    """⑨ 데크 PS/DS 재추출이 가능한 엔진인가(쌍 정보를 주는가)."""
    return (name or "").lower() in DECK_PS_DS_ENGINES


# ── 처리형 ────────────────────────────────────────────────────────────────
def _run_snap(lat, lon, out_dir, out_h5, *, token=None, count=8,
              start="2024-01-01", end="2025-07-01", **_) -> EngineResult:
    from .snap_acquire import acquire
    from .snap_backend import run as snap_run

    acq = acquire(lat, lon, str(out_dir), count=count, start=start, end=end, token=token)
    scenes = [str(x) for x in Path(acq.slc_dir).glob("*.zip")]
    res = snap_run(scenes, lat, lon, out_dir=str(out_dir), out_h5=str(out_h5),
                   era5_master=True)
    ok = sum(p.ok for p in res.pairs)
    return EngineResult(engine="snap", track_h5=str(res.track_h5),
                        n_points=int(getattr(res, "n_points", 0) or 0),
                        detail=f"{res.reference} · 쌍 {ok}/{len(res.pairs)}",
                        native=res, extra={"slc_dir": acq.slc_dir})


def _run_hyp3(lat, lon, out_dir, out_h5, *, token=None, count=8,
              start="2024-01-01", end="2025-07-01", **_) -> EngineResult:
    from .hyp3_backend import run as hyp3_run

    r = hyp3_run(lat, lon, str(Path(out_dir) / "hyp3_products"), str(out_h5),
                 count=count, start=start, end=end, token=token)
    fail = f" · 실패 {r.n_fail}" if r.n_fail else ""
    return EngineResult(engine="hyp3", track_h5=r.track_h5, n_points=r.n_points,
                        detail=f"burst {r.burst_id or '-'} · 기준 {r.ref_date} · "
                               f"쌍 {r.n_ok}{fail}",
                        native=r)


# ── 가져오기형 ────────────────────────────────────────────────────────────
def _adapter(stem: str):
    """scripts/wsl_sarvey/<stem>.py 를 모듈로 로드(패키지가 아니라 스크립트라 직접 로드)."""
    path = Path(__file__).resolve().parents[3] / "scripts" / "wsl_sarvey" / f"{stem}.py"
    if not path.exists():
        raise EngineError(f"변환 어댑터를 찾지 못함: {path}")
    spec = importlib.util.spec_from_file_location(stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_engine(name: str):
    def _run(lat, lon, out_dir, out_h5, *, source=None, **opts) -> EngineResult:
        if not source:
            raise EngineError(
                f"{name} 엔진은 이미 처리된 산출물이 필요합니다 — source= 로 지목하세요"
                f"({describe(name)}). 좌표만으로 처리까지 하려면 snap·hyp3 를 쓰세요.")
        src = Path(source)
        if not src.exists():
            raise EngineError(f"{name} 산출물이 없습니다: {src}")
        mod = _adapter(_ADAPTERS[name])
        out_h5 = str(out_h5)
        if name == "miaplpy":       # 3파일(시계열·기하·coherence)
            geom = opts.get("geometry_h5") or str(src.parent / "geometryRadar.h5")
            coh = opts.get("coherence_h5") or str(src.parent / "temporalCoherence.h5")
            n, m = mod.convert(str(src), geom, coh, out_h5)
        elif name == "mintpy":      # 시계열 + coherence
            coh = opts.get("coherence_h5") or str(src.parent / "temporalCoherence.h5")
            n, m = mod.convert(str(src), coh, out_h5)
        else:                        # sarvey(단일 H5) · stamps(.mat)
            n, m = mod.convert(str(src), out_h5)
        return EngineResult(engine=name, track_h5=out_h5, n_points=int(n),
                            detail=f"{src.name} → N={n} · M={m} (가져오기)")
    return _run


register("snap", _run_snap)
register("hyp3", _run_hyp3)
for _n in IMPORT_ENGINES:
    register(_n, _import_engine(_n))


def run(name: str, lat: float, lon: float, out_dir, out_h5, **opts) -> EngineResult:
    """엔진 이름으로 ⑧을 실행한다. 산출 Track H5 는 계약(track_preflight)을 만족해야 한다."""
    res = resolve(name)(lat, lon, out_dir, out_h5, **opts)
    # 엔진이 스스로 신고하게 두지 않고 여기서 한 번에 세운다 — plan 문구(⑨)와 full 분기가
    # 같은 사실(DECK_PS_DS_ENGINES)을 보게 하기 위해서다.
    res.supports_deck_ps_ds = supports_deck_ps_ds(res.engine or name)
    if not res.track_h5 or not Path(res.track_h5).exists():
        raise EngineError(f"{name} 엔진이 Track H5 를 만들지 못했습니다: {res.track_h5}")
    return res
