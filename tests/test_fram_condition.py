"""FRAM 상태·노후화 경보 차등 — 안전점검(A~E)·준공연도(공용연수). 미상 불변(골든 안전)."""
from __future__ import annotations

from inframon.fram.real_engine import age_alert_factor, inspection_alert_factor


def test_inspection_alert_factor():
    assert inspection_alert_factor("A") == 1.0        # 우수 → 불변
    assert inspection_alert_factor("B") == 0.97
    assert inspection_alert_factor("C") == 0.90
    assert inspection_alert_factor("D") == 0.80       # 미흡 → 조기경보
    assert inspection_alert_factor("E") == 0.70       # 불량 → 최조기경보
    assert inspection_alert_factor("C등급") == 0.90    # 앞글자 인식
    assert inspection_alert_factor(None) == 1.0       # 미상 → 불변(골든 안전)
    assert inspection_alert_factor("") == 1.0


def test_age_alert_factor():
    # 공용 10년 이내 불변
    assert age_alert_factor("2020", as_of_year=2026) == 1.0
    assert age_alert_factor(2016, as_of_year=2026) == 1.0
    # 공용 50년(준공 1976, 기준 2026): age 50 → 1-(40/80)*0.18 = 0.91
    assert age_alert_factor("1976", as_of_year=2026) == 0.91
    # 공용 ≥90년 → 하한 0.82
    assert age_alert_factor("1930", as_of_year=2026) == 0.82
    # 준공일자(YYYYMMDD)도 앞 4자리 연도 인식
    assert age_alert_factor("19761130", as_of_year=2026) == 0.91
    # 미상/이상치 → 불변(골든 안전)
    assert age_alert_factor(None) == 1.0
    assert age_alert_factor("") == 1.0
    assert age_alert_factor("abc") == 1.0
    assert age_alert_factor("9999", as_of_year=2026) == 1.0   # 범위 밖


def test_condition_shifts_alert_level():
    # 종별·지형과 곱해지는 방식과 동일: 상태 나쁘고 노후하면 낮은 CRI 에서 조기경보
    base = (0.30, 0.50, 0.70)

    def level(cri, insp, built, as_of=2026):
        f = inspection_alert_factor(insp) * age_alert_factor(built, as_of_year=as_of)
        lo, mid, hi = (min(t * f, 1.0) for t in base)
        return "위험" if cri >= hi else "경고" if cri >= mid else "주의" if cri >= lo else "정상"

    # CRI 0.66: 신설·A등급(f=1.0, hi=0.70) → 경고 ; 노후·E등급 → 위험(조기)
    assert level(0.66, "A", "2020") == "경고"
    assert level(0.66, "E", "1970") == "위험"          # E(0.70)×age(0.82)=0.574 → hi=0.402
