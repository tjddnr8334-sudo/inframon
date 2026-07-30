"""Tier 2 — 실제 PINN 전체 파이프라인을 OpenSees 형상에 돌려 검증(openseespy + torch).

Tier 1(`pinn_opensees_crosscheck.py`)은 식별 **공식**을 원시 유한차분으로 검증한다.
여기서는 OpenSees 로 만든 알려진 EI 보의 처짐을 합성 프로젝트의 연직 채널에 주입하고
**진짜 `run_pinn_real`**(신경망 피팅 → autograd 4차도함수 → EI 식별)을 돌려, NN 스무딩을
포함한 **전체 파이프라인**을 지상 진실값(OpenSees EI·진동수)에 대조한다.

경위(2026-07):
- 초기 발견 — 작은 MLP 의 autograd 4차도함수가 spectral bias 로 곡률을 과소평가해 절대 EI
  를 ~2.5× 부풀렸다(학습량에 민감, 200 epochs 에서 ~17×).
- **수정** — 4차도함수(신경망이든 스플라인이든)는 잡음/편향에 취약하므로, EI 를 **관측
  처짐형상의 4차 다항 x⁴ 계수**(w=q·x⁴/24EI+저차, 경계무관)로 미분 없이 식별하도록 바꿨다
  (`_ei_from_shape`). 이 벤치가 그 수정의 검증이다:
  - **절대 EI 정확 ✓** — 배율 ~1.0 (이전 ~2.5×).
  - **잡음 강건 ✓** — 처짐에 InSAR급 잡음(0.5~2mm)을 줘도 배율 유지(원시 FD 는 100% 붕괴).
  - **학습량 무관 ✓** — EI 가 NN 4차도함수에 더는 의존하지 않아 200~2000 epochs 에서 불변.

이는 tier-② 물리·모델 일치 검증이지 실교량(tier-③) 검증이 아니다.

    python scripts/bench/pinn_opensees_fullpipe.py   # → data/bench/opensees_fullpipe.json
"""
import json
import os
import warnings

from inframon.fem_opensees import crosscheck, crosscheck_via_pinn

warnings.filterwarnings("ignore")
T = os.environ.get("INFRAMON_BENCH_TMP", "data/bench")
os.makedirs(T, exist_ok=True)

rows = []
PROBLEMS = []

print("=== 1) 잡음 강건성: 실제 PINN(NN 스무딩) vs 원시 FD (L/h=20) ===")
print("%8s %12s %12s" % ("잡음mm", "실PINN EI배율", "원시FD EI오차%"))
for nz in [0.0, 0.5, 1.0, 2.0]:
    r2 = crosscheck_via_pinn(L=40.0, b=1.0, h=2.0, noise_mm=nz, epochs=1000, seed=1)
    r1 = crosscheck(L=40.0, b=1.0, h=2.0, noise_mm=nz, shear=True, seed=1)
    scale = r2.EI_recovered / r2.EI_true
    d = r2.as_dict(); d["tag"] = f"noise_{nz}"; d["ei_scale"] = round(scale, 3)
    d["fd_ei_err_pct"] = round(r1.ei_err_pct, 2); rows.append(d)
    print("%8.1f %12.2f %12.1f" % (nz, scale, r1.ei_err_pct))
# 잡음 강건성 검사: PINN EI 배율이 잡음에 거의 불변이어야(NN 스무딩)
scales = [r["ei_scale"] for r in rows if r["tag"].startswith("noise")]
if scales and (max(scales) - min(scales)) / (min(scales) + 1e-9) > 0.3:
    PROBLEMS.append(("noise", "med", f"PINN EI 배율이 잡음에 30%+ 변동: {scales}"))

print("\n=== 2) 학습량 민감도 (under-training vs spectral-bias 바닥, 잡음0) ===")
print("%8s %12s %12s" % ("epochs", "EI배율", "진동수오차%"))
for ep in [200, 500, 1000, 2000]:
    r = crosscheck_via_pinn(L=40.0, b=1.0, h=2.0, noise_mm=0.0, epochs=ep, seed=1)
    scale = r.EI_recovered / r.EI_true
    rows.append({"tag": f"epochs_{ep}", "epochs": ep, "ei_scale": round(scale, 3),
                 "f1_err_pct": round(r.f1_err_pct, 2)})
    print("%8d %12.2f %12.1f" % (ep, scale, r.f1_err_pct))

# 수정 검증: EI 배율이 잡음·학습량 전반에서 ~1.0 이어야(형상기반 x⁴ 식별)
all_scales = [r["ei_scale"] for r in rows if "ei_scale" in r]
if all_scales and max(abs(s - 1.0) for s in all_scales) > 0.35:
    PROBLEMS.append(("ei_scale", "high",
                     f"EI 배율이 1.0 에서 35%+ 벗어남: {all_scales}"))
conv = next((r for r in rows if r["tag"] == "epochs_2000"), None)
if conv:
    print(f"\n해석: 형상기반 x⁴ 식별로 절대 EI 배율 ~{conv['ei_scale']:.2f}× (수정 전 ~2.5×)."
          "\n  잡음·학습량에 강건(위 1·2번). 절대 EI 와 상대 EI(t) 추세 모두 신뢰 가능해졌다.")

out = {"rows": rows, "problems": PROBLEMS}
with open(T + "/opensees_fullpipe.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
print(f"\n문제 {len(PROBLEMS)}건 · 저장: {T}/opensees_fullpipe.json")
