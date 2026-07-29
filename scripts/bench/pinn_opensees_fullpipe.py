"""Tier 2 — 실제 PINN 전체 파이프라인을 OpenSees 형상에 돌려 검증(openseespy + torch).

Tier 1(`pinn_opensees_crosscheck.py`)은 식별 **공식**을 원시 유한차분으로 검증한다.
여기서는 OpenSees 로 만든 알려진 EI 보의 처짐을 합성 프로젝트의 연직 채널에 주입하고
**진짜 `run_pinn_real`**(신경망 피팅 → autograd 4차도함수 → EI 식별)을 돌려, NN 스무딩을
포함한 **전체 파이프라인**을 지상 진실값(OpenSees EI·진동수)에 대조한다.

이 벤치가 실제로 찾은 것(2026-07):
- **잡음 강건성 ✓** — NN 이 매끈하게 회귀하므로, 처짐에 InSAR급 잡음(0.5~2mm)을 줘도 EI
  회수 배율이 거의 변하지 않는다(원시 FD 는 같은 잡음에 EI 오차 100%로 붕괴).
- **절대 EI 는 부풀려진다** — 작은 MLP 가 4차도함수를 과소평가(spectral bias)해 EI 를
  체계적으로 크게 식별한다. 학습량에 민감: 200 epochs 에서 ~17×, 1000+ 수렴 후에도 ~2.5×
  바닥이 남는다. 진동수는 √EI 라 그만큼 과대예측된다.
  → **절대 EI/진동수는 order-of-magnitude 로만, 상대 EI(t) 추세를 신뢰**해야 한다는
    기존 캐비앗을 지상 진실값에 대해 정량 확인. 개선하려면 학습량↑ 또는 d4 를 콜로케이션
    형상의 해석/차분 미분으로 대체(신경망 autograd 대신).

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

# 수렴 바닥(2000ep) 배율 보고 — 절대 EI 신뢰도의 정량 상한
conv = next((r for r in rows if r["tag"] == "epochs_2000"), None)
if conv:
    floor = conv["ei_scale"]
    print(f"\n해석: 수렴 후에도 EI 배율 ~{floor:.1f}× (spectral bias 바닥). 절대 EI 는 이만큼"
          "\n  부풀려질 수 있어 order-of-magnitude 로만, 상대 EI(t) 추세를 신뢰. NN 스무딩으로"
          "\n  잡음엔 강건(위 1번). 개선책: 학습량↑ 또는 d4 를 콜로케이션 형상 차분으로 산출.")

out = {"rows": rows, "problems": PROBLEMS}
with open(T + "/opensees_fullpipe.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
print(f"\n문제 {len(PROBLEMS)}건 · 저장: {T}/opensees_fullpipe.json")
