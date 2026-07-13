"""FRAM 종별 경보 차등 — 1종 조기경보, None 불변(골든 안전)."""
from __future__ import annotations
from inframon.fram.real_engine import grade_alert_factor


def test_grade_alert_factor():
    assert grade_alert_factor("1종") == 0.85      # 임계↓ → 조기경보
    assert grade_alert_factor("2종") == 1.0
    assert grade_alert_factor("3종") == 1.15      # 임계↑
    assert grade_alert_factor("기타") == 1.1
    assert grade_alert_factor(None) == 1.0        # 미지정 → 불변(골든 안전)


def test_grade_shifts_level():
    # CRI 0.5, 기본 임계 (0.3,0.5,0.7): 2종=경고, 1종(×0.85→0.255,0.425,0.595)=경고,
    # 3종(×1.15→0.345,0.575,0.805)=주의. 1종이 3종보다 엄격.
    base = (0.30, 0.50, 0.70)
    def level(cri, grade):
        f = grade_alert_factor(grade); lo, mid, hi = (min(t*f, 1.0) for t in base)
        return "위험" if cri >= hi else "경고" if cri >= mid else "주의" if cri >= lo else "정상"
    assert level(0.55, "1종") == "경고"           # 0.55 ≥ mid(0.425)
    assert level(0.55, "3종") == "주의"           # 0.55 < mid(0.575)
    assert level(0.55, "2종") == "경고"
