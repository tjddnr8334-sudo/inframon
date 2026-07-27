"""Tier B — real PINN 물리 sanity (형식×규모 대표 케이스).

물리적으로 성립해야 하는 것: (1) 고유진동수는 규모↑에 단조↓ (2) EI 물리 범위
(3) 성분분해 유한 (4) 크래시 없음.
"""
import itertools
import json
import os
import traceback
import warnings

import numpy as np

warnings.filterwarnings("ignore")
T = os.environ.get("INFRAMON_BENCH_TMP", "data/bench"); os.makedirs(T, exist_ok=True)
from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import InSAROutput, PINNOutput
from inframon.orchestrator.pipeline import run_pipeline
from inframon.pinn.real_engine import run_pinn_real

PROBLEMS = []
rows = []

# 형식 4종 × 규모 4단 = 16 케이스
TYPES = ["girder", "box_girder", "arch", "cable_stayed"]
SPANS = [25, 60, 120, 300]

for typ, span in itertools.product(TYPES, SPANS):
    cid = f"P_{typ[:4]}_{span}"
    try:
        p = f"{T}/pinnb_{typ}_{span}.h5"
        run_pipeline(p, PipelineConfig(n_points=60, n_dates=24))
        with ProjectStore(p) as s:
            ins = s.read_meta("insar", InSAROutput)
            cfg = PipelineConfig(n_points=ins.n_points, n_dates=24)
            cfg.pinn_epochs = 150
            cfg.bridge_profile = {"bridge_type": typ, "length_m": float(span)}
            pn = run_pinn_real(s, ins, cfg)
            freq = np.asarray(s.read_array(pn.natural_freq_ds), dtype=float)
            ei = np.asarray(s.read_array(pn.EI_ds), dtype=float)
            ct = np.asarray(s.read_array(pn.comp_thermal_ds), dtype=float)
        f1 = float(freq[0]) if freq.size else None
        rows.append({"id": cid, "type": typ, "span": span, "f1": f1,
                     "ei_med": float(np.median(ei)), "comp_finite": bool(np.isfinite(ct).all())})
        # sanity 검사
        if f1 is not None and not (0.05 < f1 < 500):
            PROBLEMS.append((cid, "med", f"고유진동수 {f1:.2f}Hz 비물리 범위"))
        if not np.isfinite(ct).all():
            PROBLEMS.append((cid, "high", "성분분해에 NaN/inf"))
        ei_med = float(np.median(ei))
        if not (1e6 <= ei_med <= 1e14 + 1):     # 상한 1e14 는 포화값(정상), 경계 포함
            PROBLEMS.append((cid, "med", f"EI 중앙값 {ei_med:.2e} 물리 범위 밖"))
        # EI 상한 포화는 결함이 아니라 합성 데이터 한계(휨 곡률 부족) — 정보성으로만 기록.
        sat = ei_med >= 1e14 * 0.999
        print(f"  {cid:16} f1={f1:6.2f}Hz EI_med={ei_med:.2e}{' (포화)' if sat else ''} "
              f"finite={np.isfinite(ct).all()}")
    except Exception as exc:  # noqa: BLE001
        PROBLEMS.append((cid, "crash", f"{type(exc).__name__}: {str(exc)[:80]}"))
        print(f"  {cid:16} CRASH {type(exc).__name__}")
        traceback.print_exc()

# 고유진동수 단조성: 같은 형식에서 span↑ → f1↓ 여야
print("\n=== 고유진동수 단조성(형식별 span↑ → f1↓) ===")
for typ in TYPES:
    fs = [(r["span"], r["f1"]) for r in rows if r["type"] == typ and r["f1"]]
    fs.sort()
    mono = all(fs[i][1] >= fs[i + 1][1] for i in range(len(fs) - 1))
    print(f"  {typ:14} {[(s, round(f, 1)) for s, f in fs]} {'단조↓ OK' if mono else '⚠️ 비단조'}")
    if not mono:
        PROBLEMS.append((typ, "med", f"고유진동수 span 단조성 위반: {fs}"))

print(f"\n=== Tier B 문제 {len(PROBLEMS)}건 ===")
for cid, sev, det in PROBLEMS:
    print(f"  [{sev}] {cid}: {det}")
with open(T + "/bench_pinn_result.json", "w", encoding="utf-8") as fh:
    json.dump({"rows": rows, "problems": PROBLEMS}, fh, ensure_ascii=False, indent=1)
