"""PINN 구조 역산의 독립 FE 교차검증 — OpenSees(Timoshenko) 정해 대 PINN 공식.

`fem_crosscheck.py`(닫힌해)가 못 다루는 영역까지 검증을 넓힌다: **전단변형·다경간
연속·경계조건**. 각 케이스에서 OpenSees 로 알려진 단면(→알려진 EI) 보의 처짐·진동수를
만들고(forward), 그 처짐형상을 PINN 의 실제 역산(`_identify_EI_from_pde`)에 넣어 EI 를
되찾고 진동수(`_fem_beam_frequencies`, E-B)를 예측해(inverse) 오차를 잰다.

**무엇을 보나.** PINN 의 모달 해석은 E-B 를 쓰므로, OpenSees 를 전단 포함 Timoshenko 로
짜면 두 정식화의 차이가 **모델오차**로 드러난다 — 슬렌더 보는 작고, 깊은 보는 전단으로
커진다. 이는 tier-② 물리·모델 일치이지 실교량 검증(tier-③)이 아니다.

**포터블.** openseespy(선택 의존)만 있으면 어디서나 돌아간다. torch 불필요(공식만 검증).
잡음 강건성/NN 스무딩은 실제 PINN 전체를 돌리는 Tier 2(pinn_opensees_fullpipe.py)에서.

    python scripts/bench/pinn_opensees_crosscheck.py   # → data/bench/opensees_crosscheck.json
"""
import json
import os

from inframon.fem_opensees import crosscheck

T = os.environ.get("INFRAMON_BENCH_TMP", "data/bench")
os.makedirs(T, exist_ok=True)

E = 3.0e10          # 콘크리트 탄성계수 [Pa]
RHO = 2400.0        # 밀도 [kg/m³]
PROBLEMS = []
rows = []


def _run(tag, **kw):
    r = crosscheck(E=E, rho=RHO, **kw)
    d = r.as_dict()
    d["tag"] = tag
    rows.append(d)
    return r


print("=== 1) 세장비 스윕 (단순지지, L=40m, 전단 포함) — 전단 모델오차 곡선 ===")
print("%6s %6s %11s %8s %8s %11s" % ("L/h", "h(m)", "EI오차%", "f_OS", "f_PINN", "f오차%"))
for h in [0.5, 1.0, 2.0, 3.0, 4.0, 5.0]:
    r = _run(f"slender_h{h}", L=40.0, b=1.0, h=h, boundary="simply_supported")
    print("%6.1f %6.2f %11.4f %8.3f %8.3f %11.3f" % (
        r.slenderness, h, r.ei_err_pct, r.f1_opensees, r.f1_pinn, r.f1_err_pct))
    # EI 회수는 (전단 성분이 4차도함수 0이라) 정확해야; 진동수는 깊을수록 벌어짐(정상)
    if r.ei_err_pct > 1.0:
        PROBLEMS.append((r.as_dict()["tag"], "med", f"깨끗한 형상 EI 회수오차 {r.ei_err_pct:.2f}% (>1%)"))

print("\n=== 2) 경계조건 (L=40m, h=2m, L/h=20) ===")
for bnd in ["simply_supported", "fixed"]:
    r = _run(f"bnd_{bnd}", L=40.0, b=1.0, h=2.0, boundary=bnd)
    print(f"  {bnd:16} EI오차 {r.ei_err_pct:6.3f}%  f_OS {r.f1_opensees:.3f}  "
          f"f_PINN {r.f1_pinn:.3f}  f오차 {r.f1_err_pct:.3f}%")

print("\n=== 3) 대조군: OpenSees도 E-B(shear=False) → PINN E-B 와 자명히 일치 ===")
r = _run("control_eb", L=40.0, b=1.0, h=3.0, shear=False)
print(f"  L/h={r.slenderness:.1f}  진동수오차 {r.f1_err_pct:.4f}%  EI오차 {r.ei_err_pct:.4f}%")
if r.f1_err_pct > 0.5:
    PROBLEMS.append(("control_eb", "high",
                     f"E-B 대조군 진동수오차 {r.f1_err_pct:.3f}% (>0.5% — 두 FE 구현 불일치)"))

print("\n=== 4) 다경간 연속보 (L=90m, h=2m) — 단일경간 형상가정의 한계 ===")
for ns in [1, 2, 3]:
    r = _run(f"span_{ns}", L=90.0, b=1.0, h=2.0, n_spans=ns)
    print(f"  {ns}경간  EI오차 {r.ei_err_pct:7.3f}%  f_OS {r.f1_opensees:.3f}  "
          f"f_PINN {r.f1_pinn:.3f}  f오차 {r.f1_err_pct:.3f}%")

print(f"\n=== 문제 {len(PROBLEMS)}건 ===")
for tag, sev, det in PROBLEMS:
    print(f"  [{sev}] {tag}: {det}")

# 정직한 해석 요약
slender = [r for r in rows if r["tag"].startswith("slender")]
if slender:
    fmax = max(r["f1_err_pct"] for r in slender)
    fmin = min(r["f1_err_pct"] for r in slender)
    print(f"\n해석: 전단 모델오차(진동수)는 슬렌더 {fmin:.2f}% → 깊은 보 {fmax:.2f}% 로 증가."
          "\n  E-B 기반 PINN 은 슬렌더 보에서 정확하고, 세장비 L/h<10 깊은 보에서 전단연화를"
          "\n  놓쳐 진동수를 과대예측한다. EI 회수 자체는 (전단이 4차도함수에 기여 안 해)"
          "\n  깨끗한 형상에서 정확. → 깊은 보는 Timoshenko 기반 진동수 보정이 필요.")

out = {"rows": rows, "problems": PROBLEMS, "E_Pa": E, "rho": RHO}
with open(T + "/opensees_crosscheck.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
print(f"\n저장: {T}/opensees_crosscheck.json")
