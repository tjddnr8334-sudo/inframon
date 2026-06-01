# C구간 — FRAM · 공명 진단/CRI · 상태: PARTIAL(stub) + REAL(Phase 5 고도화)

> **real 구현됨**(`fram/real_engine.py`, `run_fram_real`, 핫스왑 `--engine fram=real`): stub 휴리스틱 결함 3종 수정 + 함수망 공명 — ①**load 하드코딩 제거**(Vi[load]를 `|∇thermal|·0.3` 대신 PINN 실 comp_load에서), ②**R_ij 공간정보 복원**(`_pointwise_resonance`로 전 점 동일 broadcast→점별 윈도우 상관), ③**절대 보정**(self-max 정규화→포화함수 `sat(x,s)=x/(x+s)`로 변위속도/가속/공간기울기를 mm/yr 등 물리스케일 기준 [0,1] 매핑, 스케일은 cfg `fram_vel_scale` 등 조정), ④**함수망 공명(N-K)**(`_network_resonance`: R_ij 기능 결합 네트워크의 스펙트럼 반경=시스템 공명 강도 S(t)[M], 초기 윈도우 신뢰도 램프, 점별 결합항을 S로 증폭해 CRI 반영 — 쌍별 평균이 못 잡는 창발적 다기능 공명. 선택 출력 `network_resonance[M]`). 계약·CRI[N,M]·resonance_Rij[4,4,M] 보존. PINN real과 결합 시 진짜 하중 변동 반영. ⚠️ 남음: isotonic 캘리브, ROC(Morandi) 검증, 실시간.

---


> 파이프라인 종착점. InSAR+PINN 결과로 4종 공명 지표를 계산해 **공명 위험 지수 CRI**와 4단계 경보를 산출. 대시보드가 소비.
> 공통 계약: [00_공통_계약과_파이프라인.md](00_공통_계약과_파이프라인.md) · 전체: [`../개발_맥락_맵.md`](../개발_맥락_맵.md) §5.5

## 역할
PINN의 기능별 변동 `V_func_series`로 기능 간 공명을, InSAR 변위로 공간/발산 항을 계산 → **CRI[N,M]** + 경보(정상/주의/경고/위험).

## 입출력 계약
- **공개 API**: `run_fram(store, insar, pinn, cfg) -> FRAMOutput`; 내부 `_windowed_corr(series, win=6)`
- **입력**: `InSAROutput`(`los`, `longitudinal`, `xyz`, `member`, `dates`) + `PINNOutput`(`V_func_series[4,M]`, `comp_anomaly/settle/thermal`)
- **출력(FRAMOutput)**: `R_ij`, `amplification`, `CRI[N,M]`, `cri_global_max`, `warning(FRAMWarning)`
- **하류**: 없음(최종 산출물) — dashboard가 `/fram/CRI` 등 소비

## 현재 구현 (PARTIAL, 휴리스틱)
4종 지표를 NumPy로 실제 계산하나 물리 방정식이 아닌 변동량의 미분/상관/정규화 프록시:
- **A(증폭률)**: 4기능 변동 Vi 스택 → 전역 max 정규화 합 대비 `|d(los)/dt|`. **`Vi[1]`(load)이 `thermal gradient * 0.3` 하드코딩 가짜값.**
- **R_ij(기능간 상관)**: win=6 윈도우 corrcoef 비대각합 → **모든 점에 동일 broadcast(공간 정보 손실)**.
- **R_spatial**: xyz x좌표 정렬 후 V_total 공간 1차 gradient.
- **R_div**: los 시간 2차미분 `|d²/dt²|`.
- **CRI** = w1·A + w2·ΣR_ij + w3·R_spatial + w4·R_div, [0,1] clip. 기본 가중치 `(0.4,0.3,0.15,0.15)`.
- **경보**: `cri_max`를 임계 `(0.3,0.6,0.85)`과 비교. `critical_members`(마지막 시점 CRI≥mid 점의 멤버), `lead_time_days`(전역 CRI가 mid 첫 초과 후 잔여일수).

## 입력 계약상 의존 (PINN 작업자도 주의)
- ⚠️ `V_func_series[4,M]` 행 순서가 `('thermal','load','bearing','foundation')`라고 **가정하고 인덱싱**. PINN이 순서를 바꾸면 FRAM이 조용히 틀림.

## 로드맵 (Phase 5, 게이트 G5: ROC-AUC≥0.9)
- ✅ 휴리스틱 결함 해소: load 하드코딩 제거, R_ij 공간 broadcast 해소, self-max 정규화 → 절대 기준 보정.
- ✅ 함수망 공명(N-K): R_ij 결합 네트워크 스펙트럼 반경으로 시스템 공명 강도 산출 → CRI 증폭(`_network_resonance`).
- ✅ Morandi 합성 검증(G5): 가속 침하 전조 시나리오(`synthetic.make_collapse_scenario`)에서 CRI 가 failing 점을 ROC-AUC=0.946(≥0.9) 판별.
- ✅ isotonic 캘리브레이션(`calibration.py`): PAVA 로 CRI→붕괴확률 단조 매핑(`IsotonicCalibrator`). AUC 보존·Brier 0.234→0.059. `cfg.fram_calibrator`로 FRAM 이 `calibrated_risk[N,M]` 출력.
- ✅ 경보 보완(설계 §5.7 실현): **기능별 상태**(thermal/load/bearing/foundation → 정상/주의/위험, `function_states`), **보정 경보 근거**(캘리브레이터 있으면 절대확률 기준 level, `basis`), **전방 lead_time**(CRI 추세 외삽 → 위험 도달 예측 `lead_time_forecast_days`). 대시보드 FRAM 탭 노출.
- ⬜ FastAPI/Prefect 실시간, 6측면 함수망, R_div 를 PINN 예측 발산으로 재정의.

## 이 엔진 고유 리스크
> 아래 1~2는 **stub 한정**. real 엔진은 ③절대 보정(sat)·isotonic 캘리브로 절대 의미를, ④함수망 공명의 초기 윈도우 신뢰도 램프로 노이즈를 이미 해소했다.
- (stub) 모든 지표가 **self-max 정규화** → 절대 위험 수준이 아닌 "데이터셋 내 상대값". 절대 임계 `(0.3,0.6,0.85)`과 결합 의미가 약함. → real: sat 절대보정 + CRI→확률 isotonic 캘리브로 해소.
- (stub) **단조/저변동 데이터에서 노이즈 증폭으로 가짜 위험** 발생 가능. → real: 초기 윈도우 신뢰도 램프로 완화.
- `win=6`, `A/3.0` 등 마법상수 다수.
- README는 "거의 실제"라 칭하나 실제로는 **물리 검증 전 휴리스틱** — 표현 신뢰 주의.
- 캘리브용 실패 라벨 희소 → Morandi 합성 재현으로 대응 필요.
