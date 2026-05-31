# B구간 — PINN · 물리 성분분해/역산 · 상태: STUB + REAL(Phase 4)

> **real 구현됨**(`pinn/real_engine.py`, `run_pinn_real`): PyTorch PINN + **Euler-Bernoulli 보 PDE**(autograd 4차 미분, 균일하중 가정 잔차) + **FEM 모달 해석**(Hermite 보요소→고유진동수, 해석해와 5% 이내 검증). 분해(thermal=α·L_fixed·계절 / settle=선형 / load=PINN MLP / anomaly=MLP), **절대 EI 식별**(비차원 PDE 균형 `EI=q·L⁴/(w_scale·⟨|∂⁴ŵ/∂x̂⁴|⟩)`, 가정 자중 q=Q0 → `_identify_EI_from_pde`, 단순보 해석해 회수 검증)→점별 변조(곡률↑=손상의심 저강성), V_load=PDE 이탈. 핫스왑 `--engine pinn=real`. 계약·V_func_series[4,M] 순서 보존(stub과 동일). torch 지연 import. ⚠️ EI 식별은 분포하중 휨이 있어야 작동 — 합성 데이터는 휨 미약→강체 상한 포화(실데이터 필요). 기존 `log_EI`(gradient 0 결함) 제거.

---


> 변위를 물리 성분으로 분해하고 구조응답·물리 파라미터를 역산. FRAM 입력(`V_func_series`)의 품질을 직접 결정.
> 공통 계약: [00_공통_계약과_파이프라인.md](00_공통_계약과_파이프라인.md) · 전체: [`../개발_맥락_맵.md`](../개발_맥락_맵.md) §5.4

## 역할
InSAR 변위를 물리 성분(**열팽창/하중/침하/이상**)으로 분해하고, 구조응답(처짐/변형률/응력/고유진동수)과 물리 파라미터(EI, alpha)를 역산. 기능별 변동 V를 FRAM에 넘긴다.

## 입출력 계약
- **공개 API**: `run_pinn(store, insar, cfg) -> PINNOutput`
- **읽는 입력**: `longitudinal_ds`, `dates_ds`, `member_ds` (사용 안 함: `los_ds`, `xyz_ds`, `l_from_fixed_ds`, `coherence_ds`)
- **출력(PINNOutput)**: `comp_thermal/settle/anomaly`, `strain/stress/deflection`, `EI/alpha`, `natural_freq`, `V_thermal/V_load/V_settle/V_anomaly`, **`V_func_series[4,M]`**
- **하류(FRAM)가 직접 읽는 것**: `V_func_series_ds`, `comp_anomaly_ds`, `comp_settle_ds`, `comp_thermal_ds`

## 현재 구현 (STUB)
- 신경망/PDE 아님 — 설계행렬 `[1, t, sin, cos]`에 대한 `np.linalg.lstsq` **선형회귀**.
- 구조응답은 가짜: 처짐 = 종방향변위 복사, 변형률 = `np.gradient` 2회, 응력 = E·strain.
- `natural_freq=[3.2,8.1,15.7]`, `EI=1e9`(pier면 0.7배), `alpha=1.2e-5` **하드코딩**. **PyTorch import 없음.**
- V 산출: `V_thermal`(=1−R²), `V_load`(상수 0.1, 가장 약한 대용), `V_settle`(|침하속도| 정규화), `V_anomaly`(잔차 std 정규화).

## 교체 시 보존할 계약 ⚠️ 최우선
- **`V_func_series_ds`는 `[4,M]`, 행 순서 = `('thermal','load','bearing','foundation')`** 정확히 일치. FRAM이 이 순서로 R_ij/A를 계산하므로 어긋나면 **조용히 오작동**.
- FRAM이 직접 읽는 `V_func_series_ds`, `comp_anomaly_ds`, `comp_settle_ds`, `comp_thermal_ds`의 `[N,M]` 차원·의미 유지.
- V 값은 `[0,1]` 범위 유지(게이트 G4 검증 항목).

## 로드맵 (Phase 4, 게이트 G4: forward L2<1% / EI오차<10% / V∈[0,1])
- ✅ PyTorch PINN + 비차원화(x̂∈[0,1]·L⁴) + autograd 4차 PDE 손실(Euler-Bernoulli) + **절대 EI 식별**(PDE 균형, 해석해 회수) + alpha 역산 + 4성분 분해 + FEM 연계.
- ✅ **V를 통계 대용치에서 PDE 잔차/예측오차로 재정의** — FRAM 입력 품질 직결.
- ⬜ 실데이터 게이트(G4 수치 목표), 하중 독립 절대 EI 측정.

## 이 엔진 고유 리스크
- 4차 PDE **수렴 불안정** → 비차원화 필수. 합성 데이터로 forward 수렴 먼저 검증 후 실데이터.
- "그럴듯한데 틀린" 결과 방지 위해 PDE residual을 항상 로깅·시각화 권장.
- 현재 `los`/`xyz`/`coherence`를 안 쓰므로, 실구현에서 입력 확장 시 계약 외 추가 입력 명시 필요.
