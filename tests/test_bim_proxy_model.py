"""실측 제원으로 만드는 프록시 부재 모델.

일반 프록시 IFC(30m 3경간)를 실 교량에 얹으면 결합은 되지만 부재가 실제와 다르다 —
청양교(90m 2경간) 데크 관측점이 상판이 아니라 교각 두부에 붙었다. 실측으로 세우면
데크 점이 데크에 붙는다. 그 성질을 고정한다.
"""

from __future__ import annotations

import json

import pytest

from inframon.bim.elements import load_elements
from inframon.bim.proxy_model import bridge_elements, elements_from_profile, save_elements_json


def test_members_follow_measured_specs():
    els = bridge_elements(length_m=90.0, width_m=22.0, n_spans=2, clearance_m=10.0)
    kinds = {e.name: e for e in els}
    assert set(kinds) == {"Deck#1", "Pier#1", "PierCap#1", "Abutment#1", "Abutment#2"}
    deck = kinds["Deck#1"]
    assert deck.member == "deck"
    assert deck.bbox_min[2] == 10.0                    # 형하고 = 상판 아래
    assert deck.bbox_max[0] - deck.bbox_min[0] == 90.0  # 연장
    assert deck.bbox_max[1] - deck.bbox_min[1] == 22.0  # 폭
    # 교각은 경간 경계(중앙)에 하나 — 2경간이므로
    assert kinds["Pier#1"].bbox_min[2] == 0.0 and kinds["Pier#1"].bbox_max[2] == 10.0


def test_span_count_drives_pier_count():
    for n, piers in ((1, 0), (2, 1), (5, 4)):
        els = bridge_elements(length_m=100.0, width_m=10.0, n_spans=n, clearance_m=5.0)
        assert sum(1 for e in els if e.ifc_type == "IfcColumn") == piers


def test_guid_is_stable_across_runs():
    """무작위 GUID 면 재실행마다 부재 결합이 끊긴다 — 제원이 같으면 같아야 한다."""
    a = bridge_elements(length_m=90.0, width_m=22.0, n_spans=2, clearance_m=10.0, name="청양교")
    b = bridge_elements(length_m=90.0, width_m=22.0, n_spans=2, clearance_m=10.0, name="청양교")
    assert [e.guid for e in a] == [e.guid for e in b]
    c = bridge_elements(length_m=90.0, width_m=22.0, n_spans=2, clearance_m=10.0, name="정자교")
    assert [e.guid for e in a] != [e.guid for e in c]
    assert all(len(e.guid) == 22 for e in a)           # IFC GlobalId 와 같은 길이


class _Prof:
    def __init__(self, **kw):
        self.length_m = kw.get("length_m")
        self.width_m = kw.get("width_m")
        self.section_depth_m = kw.get("section_depth_m")
        self.extra = kw.get("extra", {})


def test_from_profile_uses_measured_span_count_and_height():
    els = elements_from_profile(_Prof(length_m=90.0, width_m=22.0,
                                      extra={"n_spans": 2, "height_m": 10.0}))
    deck = next(e for e in els if e.member == "deck")
    assert deck.bbox_min[2] == 10.0
    assert sum(1 for e in els if e.ifc_type == "IfcColumn") == 1


def test_from_profile_refuses_without_measurements():
    """연장·폭이 없으면 지어내지 않고 거부한다."""
    with pytest.raises(ValueError, match="실측이 없어"):
        elements_from_profile(_Prof(length_m=None, width_m=None))


def test_json_roundtrip_is_readable_by_align_path(tmp_path):
    """저장한 JSON 을 bim.load_elements 가 그대로 읽어야 결합 경로에 붙는다."""
    els = bridge_elements(length_m=90.0, width_m=22.0, n_spans=2, clearance_m=10.0)
    p = save_elements_json(els, tmp_path / "els.json", meta={"bridge": "청양교"})
    back = load_elements(p)
    assert len(back) == len(els)
    assert {e.guid for e in back} == {e.guid for e in els}
    doc = json.loads(open(p, encoding="utf-8").read())
    assert "실 IFC 가 아니다" in doc["note"]            # 출처를 숨기지 않는다
