"""IFC 어댑터 — ifcopenshell 이 **없을 때**의 동작.

정합 코어(georef·elements·psets·align)는 이 의존성 없이 동작하므로, 라이브러리가
없는 사용자도 부재 테이블(JSON/CSV)로 전 과정을 쓸 수 있어야 하고 IFC 경로는
**조용한 폴백 없이** 안내와 함께 실패해야 한다. 빈 결과를 돌려주면 "부재가 0개"가
정상처럼 보인다.

설치 여부와 무관하게 검증하려고 `sys.modules` 로 부재를 흉내 낸다
(실 IFC 왕복은 `test_bim_ifc_roundtrip.py`).
"""
from __future__ import annotations

import sys

import pytest

from inframon.bim import ifc_io
from inframon.bim.georef import AlignmentError


@pytest.fixture()
def no_ifcopenshell(monkeypatch):
    """`import ifcopenshell` 이 ImportError 를 내도록 만든다."""
    monkeypatch.setitem(sys.modules, "ifcopenshell", None)
    yield


def test_available_reports_false_when_absent(no_ifcopenshell):
    assert ifc_io.available() is False


def test_available_reports_true_when_present():
    pytest.importorskip("ifcopenshell")
    assert ifc_io.available() is True


@pytest.mark.parametrize("name,call", [
    ("read_map_conversion", lambda: ifc_io.read_map_conversion("x.ifc")),
    ("read_elements", lambda: ifc_io.read_elements("x.ifc")),
    ("inspect", lambda: ifc_io.inspect("x.ifc")),
    ("write_psets", lambda: ifc_io.write_psets("a.ifc", {}, "b.ifc")),
    ("extract_elements_json", lambda: ifc_io.extract_elements_json("x.ifc", "x.json")),
])
def test_every_ifc_entry_point_fails_loudly_with_install_hint(no_ifcopenshell, name, call):
    with pytest.raises(AlignmentError) as exc:
        call()
    msg = str(exc.value)
    assert "ifcopenshell" in msg and "pip install" in msg
    # 대안(부재 테이블)을 알려줘야 사용자가 막히지 않는다
    assert "부재 테이블" in msg


def test_write_psets_refuses_to_overwrite_source(tmp_path):
    """BIM 원본은 다른 팀 산출물 — 덮어쓰지 않는다(라이브러리 유무와 무관한 방어)."""
    pytest.importorskip("ifcopenshell")
    f = tmp_path / "m.ifc"
    f.write_text("", encoding="utf-8")
    with pytest.raises(AlignmentError, match="덮어쓸 수 없습니다"):
        ifc_io.write_psets(str(f), {}, str(f))


# ── IFC2X3: IFC4 전용 엔티티를 물으면 예외가 아니라 "없음"이어야 한다 ──
# 국내 실무 모델(MIDAS 산출물 등)이 IFC2X3 라, by_type 이 RuntimeError 를 던지면
# 실 IFC 를 아예 못 읽는다. 실제로 691부재짜리 설계 IFC 에서 이 경로가 죽었다.

def test_by_type_safe_는_스키마에_없는_엔티티에_빈_목록을_준다():
    ios = pytest.importorskip("ifcopenshell")
    from inframon.bim.ifc_io import by_type_safe

    f = ios.file(schema="IFC2X3")
    assert by_type_safe(f, "IfcMapConversion") == []
    assert by_type_safe(f, "IfcProjectedCRS") == []


def test_IFC2X3_는_지오참조_없음으로_읽힌다(tmp_path):
    ios = pytest.importorskip("ifcopenshell")
    from inframon.bim.ifc_io import read_map_conversion

    p = tmp_path / "old.ifc"
    ios.file(schema="IFC2X3").write(str(p))
    assert read_map_conversion(p) is None      # 예외가 아니라 None


def test_IFC2X3_inspect_가_사유를_남기고_끝난다(tmp_path):
    ios = pytest.importorskip("ifcopenshell")
    from inframon.bim.ifc_io import inspect

    p = tmp_path / "old.ifc"
    ios.file(schema="IFC2X3").write(str(p))
    r = inspect(p)
    assert r["schema"] == "IFC2X3"
    assert r["has_map_conversion"] is False
    assert r["projected_crs"] == []
    assert any("IFC2X3" in n for n in r["notes"])
