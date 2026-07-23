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

- **정직성**: 근거가 부족하면 값을 내지 말고 **사유와 함께 비활성**하세요. 조용한 폴백으로
  그럴듯한 값을 만드는 것이 이 저장소에서 가장 위험한 실패입니다(결과가 정상처럼 보입니다).
  새 수치에는 한계·가정을 같이 실으세요.

## 기여 절차

**쓰기 권한이 있는 협업자**

```bash
git clone https://github.com/tjddnr8334-sudo/inframon.git
cd inframon && pip install -e ".[dev]"
git switch -c fix/무엇을-고치는지        # main 에 직접 push 하지 않습니다
# ... 작업 ...
python -m pytest -q && ruff check src tests
git push -u origin fix/무엇을-고치는지
gh pr create --fill                      # 또는 GitHub 웹에서 PR
```

**권한이 없으면** 저장소를 fork 해서 같은 흐름으로 PR 을 보내면 됩니다(공개 저장소입니다).

**저장소 소유자가 협업자를 추가하는 법** — Settings → Collaborators → Add people, 또는

```bash
gh api -X PUT repos/tjddnr8334-sudo/inframon/collaborators/<GITHUB아이디> -f permission=push
```

`permission` 은 `pull`(읽기)·`push`(쓰기)·`admin`. 보통 `push` 면 충분합니다.

CI(`.github/workflows/tests.yml`)가 모든 PR 에서 pytest 를 돌립니다.
`contracts/`·`tests/golden/` 변경은 `CODEOWNERS` 에 따라 소유자 리뷰가 필요합니다.

## 교량 설정을 고치고 싶다면 — `configs/`

교량 하나를 정의하는 **소용량 텍스트 입력**(레시피·부재 테이블·좌표 정합·한계값)은
`configs/<교량>/` 에 있고 **git 으로 추적됩니다**. 여기를 고쳐 PR 하면 됩니다.

`data/` 는 통째로 무시됩니다 — SLC·`project.h5`·GeoTIFF 가 쌓이는 작업 디렉터리라
git 에 올릴 것이 아닙니다. 자세한 구분과 올리면 안 되는 것은
[`configs/README.md`](configs/README.md).

## PR 체크리스트
1. `pytest` 통과, `ruff` 통과.
2. 새 기능은 테스트 추가(pure 로직은 pytest, 대시보드는 최소 렌더 스모크).
3. 계약 변경 시 이유 설명 + 스키마 버전 상향 + 골든 갱신.
4. 비밀정보·실데이터 커밋 금지(`.gitignore` 가 `data/`·`*.h5` 제외).
   API 키·serviceKey·비공개 측량 성과는 `configs/` 에도 넣지 마세요.
5. **실제로 돌려보고** 무엇을 확인했는지 PR 에 적으세요. 테스트 통과 ≠ 동작 확인입니다.

## 이슈
버그·기능 제안 환영 — `.github/ISSUE_TEMPLATE` 의 양식을 씁니다. InSAR 도구 자체의
범용 개선은 해당 upstream(SARvey 등)에도 기여를 고려해 주세요.
