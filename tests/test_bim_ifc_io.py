"""IFC 어댑터 — ifcopenshell 부재 시의 실패 경로.

⚠️ 개발 환경에 ifcopenshell 이 없어 **실 IFC 읽기/쓰기는 검증되지 않았다**.
검증된 것은 정합 코어(georef·elements·psets·align)와, 라이브러리가 없을 때
조용히 폴백하지 않고 명확히 실패하는 동작이다.
"""
from __future__ import annotations

import pytest

from inframon.bim import ifc_io
from inframon.bim.georef import AlignmentError


def test_available_reflects_import():
    try:
        import ifcopenshell  # noqa: F401
        assert ifc_io.available() is True
    except ImportError:
        assert ifc_io.available() is False


@pytest.mark.skipif(ifc_io.available(), reason="ifcopenshell 설치됨 — 실패 경로 테스트 불가")
@pytest.mark.parametrize("call", [
    lambda: ifc_io.read_map_conversion("x.ifc"),
    lambda: ifc_io.read_elements("x.ifc"),
    lambda: ifc_io.inspect("x.ifc"),
    lambda: ifc_io.write_psets("a.ifc", {}, "b.ifc"),
])
def test_missing_ifcopenshell_fails_loudly_with_hint(call):
    """조용히 빈 결과를 돌려주면 '부재가 0개'가 정상처럼 보인다 — 반드시 실패해야 한다."""
    with pytest.raises(AlignmentError) as exc:
        call()
    assert "ifcopenshell" in str(exc.value)
    assert "pip install" in str(exc.value)


@pytest.mark.skipif(not ifc_io.available(), reason="ifcopenshell 없음")
def test_write_psets_refuses_to_overwrite_source(tmp_path):
    """BIM 원본은 다른 팀 산출물 — 덮어쓰지 않는다."""
    f = tmp_path / "m.ifc"
    f.write_text("", encoding="utf-8")
    with pytest.raises(AlignmentError, match="덮어쓸 수 없습니다"):
        ifc_io.write_psets(str(f), {}, str(f))
