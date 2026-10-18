"""광역(지자체 단위) 교량 InSAR 감시 — 시·군의 **모든 교량**을 Sentinel-1 갱신 주기마다 판정.

엔진(정합·StaMPS·품질필터·열신축보정·허용변위 판정)은 별도 저장소
``bridge-insar-monitor`` 가 CLI 로 수행한다(GPL InSAR 도구는 CLI 로만 호출 — CONTRIBUTING).
이 서브패키지는 그 결과를 읽어 inframon 안에서 쓰게 한다.

- :mod:`.registry_csv` — 전국교량표준데이터 CSV → 시도·시군구 교량 목록 (``public_data`` 재사용)
- :mod:`.results`      — 엔진 산출물(state.json · alerts.json · health.json · history.sqlite) 판독 (Streamlit 무관)
- :mod:`.judge`        — inframon ``life.limits`` 기준 허용변위 비율 재계산(엔진 기준과 병기)
- :mod:`.export`       — 교량 하나의 StaMPS 점 → Track H5 → ``project.h5`` (PINN·FRAM·잔존수명 입력)
- :mod:`.cycle`        — 엔진 갱신 주기 실행(``python -m bim update``)
"""
