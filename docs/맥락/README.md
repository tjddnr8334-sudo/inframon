# 엔진별 집중 문서 (맥락)

작업·실행 중인 **엔진 하나만** 빠르게 파악하기 위한 문서 모음입니다.
각 파일은 그 엔진을 건드릴 때 필요한 내용 — **역할 · 입출력 계약 · 현재 구현 · 교체 시 보존사항 · 로드맵 · 그 엔진 고유 리스크** — 만 모아 자체 완결적으로 정리했습니다.

전체 맥락(파이프라인 전체·성숙도 지도·교차 리스크)은 한 단계 위 [`../개발_맥락_맵.md`](../개발_맥락_맵.md) 참조.

## 구간별 문서

| 구간 | 엔진 | 문서 | 상태 | 한 줄 |
|---|---|---|---|---|
| (상류) | **CV** | [CV.md](CV.md) | STUB | 영상 → ROI/부재 라벨/격자 |
| **A** | **InSAR** | [A_InSAR.md](A_InSAR.md) | 선별 A~E + 인제스트 REAL / 코어 F는 WSL2 | ROI → 변위 시계열 (OSM·ASF·ERA5·SARvey) |
| **B** | **PINN** | [B_PINN.md](B_PINN.md) | STUB | 변위 → 성분분해/구조응답/V |
| **C** | **FRAM** | [C_FRAM.md](C_FRAM.md) | PARTIAL | InSAR+PINN → 공명 CRI/경보 |
| (공통) | 계약·파이프라인 | [00_공통_계약과_파이프라인.md](00_공통_계약과_파이프라인.md) | REAL | 모든 엔진이 의존하는 척추 |

> **어느 엔진을 건드리든 먼저** [00_공통_계약과_파이프라인.md](00_공통_계약과_파이프라인.md)의 "계약을 깨지 않는 규칙"을 확인하세요. 엔진 내부는 자유지만 `project.h5` 입출력 계약을 어기면 다운스트림이 조용히 망가집니다.

## 빠른 명령

```powershell
python -m inframon --demo                 # 전체 파이프라인(전 STUB) 1회 실행 → data/project.h5
python -m inframon --inspect-data <root>  # InSAR 실데이터 인벤토리 점검만
pytest -q                                 # 계약/스모크 회귀
streamlit run src/inframon/dashboard/app.py
```
