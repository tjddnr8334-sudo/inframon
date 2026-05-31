"""InSAR 처리 파이프라인(F) 오케스트레이션 — SARvey 기반, 프로그램의 일급 구성요소.

레시피(A~E 산출: bridge_target/track_selection/...)를 입력으로 InSAR 변위 시계열을 만들어
inframon `/insar` 계약으로 넣는다. 두 모드:

  - "demo": 외부 SAR 도구 없이 **어디서나** 동작. 레시피의 교량 bbox·취득일에 맞춰 물리적으로
            그럴듯한 합성 변위 시계열을 생성 → Track H5 → /insar → (PINN→FRAM)까지 관통.
            프로그램 전체를 end-to-end 로 시연/검증한다(실데이터 불필요).
  - "real": Linux/WSL 에서 실제 도구로 실행. scripts/wsl_sarvey/*.sh 단계 명령을 레시피로
            파라미터화해 plan 으로 구성한다(실행은 그 환경에서). Windows 에선 plan 출력/디스패치.

⚠️ 이 모듈은 사용자의 실연구 데이터에 의존하지 않는다. real 모드는 호출자가 지정한 작업
디렉터리에서만 동작한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

ENGINES_DEFAULT = {"download": "isce2", "stack": "isce2", "miaplpy": "miaplpy", "sarvey": "sarvey"}


# ───────────────────────────── demo 모드 ─────────────────────────────
def synthesize_track_h5(recipe_dir: str | Path, out_h5: str | Path,
                        *, n_points: int = 200, seed: int = 42) -> Path:
    """레시피(bbox·취득일)에 맞춰 합성 변위 시계열을 Track H5 로 쓴다(demo)."""
    recipe_dir = Path(recipe_dir)
    bt = json.loads((recipe_dir / "bridge_target.json").read_text(encoding="utf-8"))
    trk = json.loads((recipe_dir / "track_selection.json").read_text(encoding="utf-8"))
    mn_lon, mn_lat, mx_lon, mx_lat = bt["bbox"]
    dates = list(trk["scene_dates"])
    if len(dates) < 2:
        raise ValueError("track_selection.scene_dates 가 2개 이상이어야 합니다(demo).")

    rng = np.random.default_rng(seed)
    N, M = n_points, len(dates)
    lon = rng.uniform(mn_lon, mx_lon, N)
    lat = rng.uniform(mn_lat, mx_lat, N)
    d0 = datetime.strptime(dates[0], "%Y%m%d")
    t = np.array([(datetime.strptime(d, "%Y%m%d") - d0).days for d in dates], dtype=float) / 365.0

    seasonal = np.sin(2 * np.pi * t)[None, :]
    los = np.zeros((N, M))
    # 열팽창: 교량 축(경도) 위치에 비례
    span = (lon - lon.min()) / (np.ptp(lon) + 1e-9)
    los += span[:, None] * 4.0 * seasonal
    # 선형 침하
    los += rng.uniform(-2.5, -0.4, N)[:, None] * t[None, :]
    # 중앙부 이상 손상 클러스터(후반 가속) → FRAM 전조 데모
    cx, cy = (mn_lon + mx_lon) / 2, (mn_lat + mx_lat) / 2
    dist = np.hypot(lon - cx, lat - cy)
    bad = dist < np.percentile(dist, 15)
    los[bad] += -3.0 * np.clip(t - t.max() * 0.5, 0, None)[None, :] ** 2
    los += rng.normal(0, 0.3, (N, M))

    coh = rng.uniform(0.5, 0.95, N)
    epochs = np.array([int(d) for d in dates], dtype=np.int32)

    import h5py
    out_h5 = Path(out_h5)
    out_h5.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(out_h5, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack([lon, lat]).astype(np.float64))
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=los.astype(np.float32))
        f.create_dataset("coh", data=coh.astype(np.float32))
        f.attrs["FILE_TYPE"] = "inframon_demo_synth"
        f.attrs["bridge"] = str(bt.get("name", "?"))
    return out_h5


def run_demo(recipe_dir: str | Path, project_h5: str | Path,
             *, n_points: int = 200, seed: int = 42, full_pipeline: bool = True,
             pinn_mode: str = "stub", fram_mode: str = "stub"):
    """demo: 합성 Track H5 → /insar → (옵션) PINN→FRAM 까지. 반환: 마지막 계약 객체.

    pinn_mode="real" 이면 PyTorch PINN(Euler-Bernoulli+FEM)으로 처리(느림).
    """
    from ..config import PipelineConfig
    from ..contracts.io import ProjectStore
    from ..orchestrator.engines import resolve
    from .track_reader import import_track_h5

    project_h5 = Path(project_h5)
    project_h5.parent.mkdir(parents=True, exist_ok=True)
    track_h5 = project_h5.parent / "_demo_track.h5"
    synthesize_track_h5(recipe_dir, track_h5, n_points=n_points, seed=seed)

    cfg = PipelineConfig()
    with ProjectStore(project_h5, mode="w") as store:
        insar = import_track_h5(store, track_h5)
        if not full_pipeline:
            return insar
        pinn = resolve("pinn", pinn_mode)(store, insar, cfg)
        fram = resolve("fram", fram_mode)(store, insar, pinn, cfg)
    return fram


# ───────────────────────────── real 모드 ─────────────────────────────
def plan_real(recipe_dir: str | Path, work_dir: str | Path,
              *, isce_stack: str = "$ISCE_STACK", envs: dict | None = None,
              project_h5: str = "data/project.h5") -> list[str]:
    """real 파이프라인 단계 명령(Linux/WSL). 각 줄을 해당 conda 환경에서 순서대로 실행."""
    envs = {**ENGINES_DEFAULT, **(envs or {})}
    s = "scripts/wsl_sarvey"
    return [
        f"# F 실처리 (Linux/WSL 전용). 도구: ISCE2/MiaplPy/SARvey. ISCE_STACK={isce_stack}",
        f"conda activate {envs['download']} && bash {s}/10_download.sh {recipe_dir} {work_dir}",
        f"conda activate {envs['stack']} && ISCE_STACK={isce_stack} bash {s}/20_stack_isce.sh {recipe_dir} {work_dir}",
        f"conda activate {envs['miaplpy']} && bash {s}/30_miaplpy.sh {recipe_dir} {work_dir}",
        f"conda activate {envs['sarvey']} && bash {s}/40_sarvey.sh {recipe_dir} {work_dir}",
        f"python3 {s}/50_export_to_inframon.py --sarvey-h5 {work_dir}/sarvey/outputs/<RESULT>_ts.h5 "
        f"--out {work_dir}/track.h5",
        f"python -m inframon --import-track-h5 {work_dir}/track.h5 --out {project_h5}",
    ]


def run(recipe_dir, *, mode="demo", project_h5="data/project.h5", work_dir="data/insar_work",
        n_points=200, seed=42):
    """진입점. demo 는 실제 실행, real 은 plan 을 반환(실행은 호출자/Linux)."""
    if mode == "demo":
        return run_demo(recipe_dir, project_h5, n_points=n_points, seed=seed)
    if mode == "real":
        return plan_real(recipe_dir, work_dir, project_h5=str(project_h5))
    raise ValueError(f"mode 는 'demo' | 'real' (got {mode!r})")
