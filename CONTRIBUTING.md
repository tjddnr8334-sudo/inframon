# Contributing to inframon

기여 환영합니다. inframon 은 **GPLv3** 이며, 기여물도 GPLv3 로 배포됩니다.

## 개발 환경
```bash
pip install -e ".[dev]"          # 코어 + 테스트 도구
python -m inframon --doctor      # 환경·기능 준비도 점검
python -m pytest -q              # 전체 테스트 (실데이터/WSL 불필요, 합성으로 동작)
ruff check src tests             # 린트
```
- 실 InSAR 처리(ISCE2/MiaplPy/SARvey)는 **WSL2/Linux** 필요. 코어·대시보드·엔진 로직은
  합성 데이터로 개발·검증되므로 Windows/맥에서도 기여 가능.

## 원칙 (설계 계약)
- **데이터 계약은 성역**: `contracts/`(Pydantic + `project.h5`)를 깨지 마세요. 엔진은
  계약을 통해서만 통신하고 내부만 stub→real 로 교체합니다(핫스왑).
- **골든 회귀**: 산출 계약·수치가 조용히 바뀌면 `tests/test_golden_regression.py` 가
  실패합니다. 의도된 변경은 `UPDATE_GOLDEN=1 pytest` 로 갱신.
- 기반 InSAR 도구(GPL)는 **CLI 로만 호출**하고 소스를 import 하지 마세요(라이선스 경계 유지).

## PR 체크리스트
1. `pytest` 통과, `ruff` 통과.
2. 새 기능은 테스트 추가(pure 로직은 pytest, 대시보드는 최소 렌더 스모크).
3. 계약 변경 시 이유 설명 + 골든 갱신.
4. 비밀정보·실데이터 커밋 금지(`.gitignore` 가 `data/`·`*.h5` 제외).

## 이슈
버그·기능 제안 환영. InSAR 도구 자체의 범용 개선은 해당 upstream(SARvey 등)에도
기여를 고려해 주세요.
