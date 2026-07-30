# OpenSees 독립 FE 교차검증 — PINN 구조 역산 검증

## 왜

기존 PINN 검증(`fem_crosscheck.py`, `tests/test_pinn_real.py`)은 **닫힌해가 있는 경우**
(단순지지·프리즘·등분포)만 대조한다. 정작 PINN 이 쓰이는 실제 영역 — **전단변형(깊은 보)·
다경간 연속·경계조건** — 은 닫힌해가 없어 검증 공백이었다. OpenSees(오픈·재현가능 FE)로
이 공백을 메운다. FRAM 의 Morandi ROC-AUC, InSAR 의 GNSS 대조와 같은 **독립 기준 벤치**다.

이는 **tier-② 물리·모델 일치** 검증이지 실교량(tier-③) 검증이 아니다. "PINN 이 물리를
옳게 푼다"를 보일 뿐 "실교량과 맞는다"는 수준측량 등 지상실측(`--validate-leveling`)의 몫.

## 어떻게 (정/역 왕복, 순환논리 회피)

1. **forward** — OpenSees 로 알려진 단면(→알려진 EI)의 보에 등분포하중 → 처짐형상 w(x) +
   고유치해석 → 진동수. 기본은 **Timoshenko(전단 포함)** "진짜" 응답.
2. **inverse** — 그 처짐형상을 PINN 의 실제 역산에 투입해 EI 를 되찾고 진동수를 예측.
3. **비교** — EI 회수오차·진동수오차. 세장비 L/h 를 스윕하면 모델오차 곡선이 나온다.

**순환논리 회피**: PINN 의 모달 해석은 내부적으로 Euler–Bernoulli(E-B)를 쓴다. OpenSees 를
같은 E-B 로 짜면 자명히 일치해 무의미하므로, forward 는 **전단 포함 Timoshenko** 로 짠다.
E-B(shear=False)는 두 FE 구현이 일치하는지 확인하는 **대조군**으로만 쓴다(아래 3번).

두 계층:
- **Tier 1** (`pinn_opensees_crosscheck.py`) — 식별 **공식**(`_identify_EI_from_pde`)을
  원시 유한차분으로. openseespy 만 있으면 됨(torch 불필요, 빠름).
- **Tier 2** (`pinn_opensees_fullpipe.py`) — OpenSees 처짐을 합성 프로젝트에 주입해 **진짜
  `run_pinn_real`**(신경망 피팅 → autograd 4차도함수)을 돌려 NN 스무딩 포함 전체 파이프라인.

```bash
# openseespy 는 Windows DLL 이슈로 WSL/Linux 권장(SARvey 와 같은 툴체인)
pip install "inframon[fem]"          # openseespy
python scripts/bench/pinn_opensees_crosscheck.py   # Tier 1
python scripts/bench/pinn_opensees_fullpipe.py     # Tier 2 (torch 필요)
```

## 무엇을 찾았나 (2026-07)

### Tier 1 — 공식·엔진은 정확, 전단·연속 모델오차 정량화

| 검사 | 결과 | 해석 |
|---|---|---|
| E-B 대조군(shear=False) | 진동수오차 **0.0003%** | 두 FE 구현 일치 — 순환논리 없음 sanity ✅ |
| 깨끗한 형상 EI 회수 | **0.000%** (L/h 80→8 전 구간) | 전단은 4차도함수에 기여 안 해 EI 회수는 정확 |
| 전단 모델오차(진동수) | L/h=80 **0.02%** → L/h=8 **1.84%** | E-B 기반 PINN 은 슬렌더 정확, 깊은 보에서 전단연화 놓쳐 진동수 과대 |
| 고정단 경계 | 진동수오차 1.48% (SS 0.30%) | 경계가 뻣뻣할수록 전단효과 큼 |
| 다경간 연속(2·3경간) | **EI 24~30%·진동수 12~16% 오차** | 단일경간 형상가정이 연속보 형상(지점부 부모멘트)에 안 맞음 |

### Tier 2 — 초기 발견: 절대 EI 가 부풀려짐 (→ 수정함, 아래)

지상 진실값(OpenSees EI)에 대해 처음으로 절대 EI 신뢰도를 정량화했더니 문제가 드러났다.

| 검사 | 결과(수정 전) | 원인 |
|---|---|---|
| 잡음 강건성 | 처짐 잡음 0→2mm 에서 EI 배율 2.52→2.60(불변) | NN 스무딩 자체는 작동 |
| 학습량 민감도 | 200ep **17×** → 1000ep 2.5× → 2000ep 2.8× | under-training + spectral-bias |
| 수렴 후 절대 EI | **~2.5–2.8× 과대** | 작은 MLP 가 autograd 4차도함수를 과소평가(spectral bias) |

## 수정 (2026-07): 형상기반 x⁴ 계수 식별

4차도함수는 신경망 autograd 든 평활 스플라인이든 잡음·편향에 취약하다(스플라인은 잡음에서
불안정 — 검증함). 근본 해법은 **미분을 안 하는 것**이다. 보 방정식 `EI·w⁗=q` 의 특수해가
`w_p=q·x⁴/(24EI)` 이고 경계조건이 만드는 동차해는 3차 이하이므로, 처짐을 x̂∈[0,1] **4차
다항으로 최소제곱 피팅한 x̂⁴ 계수 c4 = q·L⁴/(24EI)** 가 되어 **경계조건과 무관하게** EI 를
준다(`_ei_from_shape`). 연직 관측이 있을 때 이 방식을 쓰고, 없으면(데모·단일트랙) 기존
autograd 경로로 폴백한다(골든 회귀 불변). 다경간은 경간별로 피팅한다.

| 검사 | 수정 전 | **수정 후** |
|---|---|---|
| 절대 EI 배율(잡음 0) | 2.5× | **1.00×** |
| 잡음 2mm EI 배율 | (2.6×) | **0.98×** |
| 진동수오차(잡음 0) | 60% | **0.3%** |
| 학습량 200→2000ep | 17×→2.8× | **1.00× 불변**(NN 4차도함수 미의존) |
| 다경간 3경간 EI 배율 | 7.8×(n=1 가정) | **1.00×**(경간별 식별) |

수정 후 PINN 은 **절대 EI·진동수·상대 EI(t) 추세를 모두** 신뢰 가능하다. `_ei_from_shape`
는 해석 처짐형상(단순지지·고정·캔틸레버)으로 numpy 단위 테스트(`tests/test_pinn_real.py`),
전체 파이프라인은 OpenSees 로 검증(`tests/test_fem_opensees.py`).

이는 tier-② 물리·모델 일치 검증이지 실교량(tier-③) 검증이 아니다 — 실교량 정합은 수준측량
(`--validate-leveling`) 등 지상실측의 몫이다.
