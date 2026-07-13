"""③ 산지/해상 지형 → FRAM 환경 경보 차등(등급×지형)."""
from __future__ import annotations
from inframon.fram.real_engine import grade_alert_factor, terrain_alert_factor


def test_terrain_alert_factor():
    assert terrain_alert_factor("평지") == 1.0
    assert terrain_alert_factor("산지") == 0.92      # 바람노출 → 조기경보
    assert terrain_alert_factor("해상") == 0.85      # 최대 노출
    assert terrain_alert_factor(None) == 1.0         # 미지정 → 불변(골든 안전)


def test_grade_terrain_combined():
    # 1종·해상 = 0.85×0.85 = 0.7225 (가장 엄격), 3종·평지 = 1.15×1.0 = 1.15
    strict = grade_alert_factor("1종") * terrain_alert_factor("해상")
    loose = grade_alert_factor("3종") * terrain_alert_factor("평지")
    assert strict < 0.73 and loose > 1.14
    # 같은 CRI 0.45, 기본 임계 mid=0.5: 엄격(임계 0.361)=경고↑, 느슨(0.575)=주의
    base_mid = 0.5
    assert 0.45 >= base_mid * strict        # 엄격: 경보 발동
    assert 0.45 < base_mid * loose          # 느슨: 미발동
