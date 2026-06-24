"""Bmaps 연동 API — InSAR 위성 변위 분석 탭용 읽기 전용 REST 계층.

설계: docs/Bmaps_연동_인터페이스.md

- registry.py  — 다중 교량 레지스트리(bridge_id → project.h5)
- transform.py — project.h5 계약 → API DTO 변환(좌표/단위/날짜/라벨)
- app.py       — FastAPI 엔드포인트(§3)

contracts/ 는 성역. 이 패키지는 계약을 *읽어 변환*만 하고 새 계산을 넣지 않는다.
"""

from __future__ import annotations
