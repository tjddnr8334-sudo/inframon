"""산출물 감사 — '보고에 써도 되는 파일인가' 판정.

겉으로 다 같아 보이는 project.h5 중에서 (래핑 위상 / 광역 필드 / 퇴화한 PINN 경간 /
재현 기록 없음)을 골라내지 못하면 잘못된 수치가 과제 보고로 새어 나간다. 판정 규칙을
여기에 고정한다 — 특히 **애매하면 낮춘다**는 원칙.
"""

from __future__ import annotations

import json

import h5py
import numpy as np
import pytest

from inframon.audit import COND, NO, OK, audit_artifact, format_table


@pytest.fixture(autouse=True)
def _no_standard_data(monkeypatch):
    """기본은 '표준데이터 없음' — 리포에 CSV 가 있고 없고에 따라 판정이 흔들리면 안 된다.

    경간 비교를 보는 테스트는 각자 nearest_bridge_profile 을 주입한다.
    """
    monkeypatch.setattr("inframon.public_data.find_bridge_csv", lambda *a, **k: None)


def _project(path, *, los=None, lonlat=(127.0, 37.0), n=50, m=5,
             source="SNAP(Windows) star-network snaphu-unwrapped → LOS",
             span_m=100.0, cri=0.5, track_path="E:/work/track.h5", spread=0.0001):
    """감사 대상 최소 골격 — /insar/los·xyz + pinn.inputs + fram.reference_range."""
    if los is None:
        rng = np.random.default_rng(0)
        los = rng.normal(0.0, 30.0, (n, m))          # 언래핑된 크기
    with h5py.File(path, "w") as f:
        ins = f.create_group("insar")
        ins.create_dataset("los", data=np.asarray(los, dtype=np.float32))
        off = np.linspace(-spread, spread, np.shape(los)[0])
        ins.create_dataset("xyz", data=np.column_stack(
            [lonlat[0] + off, lonlat[1] + off * 0.5, np.zeros(np.shape(los)[0])]))
        src = {"path": track_path, "attrs": {"source": source}}
        ins.attrs["track_source"] = json.dumps(src)
        if span_m is not None:
            # 실 산출물 대부분이 그렇듯 기본은 '표준데이터 제원을 썼다' — OSM 폴백 사례는
            # 해당 테스트가 직접 덮어쓴다.
            f.create_group("pinn").attrs["inputs"] = json.dumps(
                {"total_length_m": span_m, "profile_source": "data_go_kr:전국교량표준데이터"})
        if cri is not None:
            f.create_group("fram").attrs["reference_range"] = json.dumps({"worst_cri": cri})
    return path


def test_unwrapped_on_target_with_record_is_reportable(tmp_path):
    p = _project(tmp_path / "good.h5")
    a = audit_artifact(p, target=(37.0, 127.0))
    assert a.verdict == OK, a.reasons
    assert a.unwrapped is True and a.n_within_deck == a.n_points
    assert a.cri_worst == 0.5 and a.has_run_record


def test_wrapped_source_text_blocks_reporting(tmp_path):
    """track source 가 'wrapped-phase' 라고 적혀 있으면 값과 무관하게 보고 불가."""
    p = _project(tmp_path / "w.h5",
                 source="SNAP(Windows) star-network wrapped-phase → LOS")
    a = audit_artifact(p, target=(37.0, 127.0))
    assert a.verdict == NO
    assert a.unwrapped is False and any("언래핑" in r for r in a.reasons)


def test_wrapped_detected_from_values_when_unlabeled(tmp_path):
    """표기가 없어도 λ/4 안에 균일하게 퍼져 있으면 래핑으로 본다."""
    rng = np.random.default_rng(1)
    los = rng.uniform(-13.86, 13.86, (60, 5))
    p = _project(tmp_path / "u.h5", los=los, source="unknown tool")
    a = audit_artifact(p, target=(37.0, 127.0))
    assert a.unwrapped is False and a.verdict == NO


def test_small_signal_is_unknown_not_wrapped(tmp_path):
    """변위가 작아 구분이 안 되면 '래핑'이라 단정하지 않고 조건부로 낮춘다."""
    rng = np.random.default_rng(2)
    p = _project(tmp_path / "s.h5", los=rng.normal(0, 2.0, (60, 5)), source="unknown")
    a = audit_artifact(p, target=(37.0, 127.0))
    assert a.unwrapped is None and a.verdict == COND
    assert any("불명" in r for r in a.reasons)


def test_zero_points_on_bridge_blocks_reporting(tmp_path):
    p = _project(tmp_path / "far.h5", lonlat=(127.5, 37.5))
    a = audit_artifact(p, target=(37.0, 127.0))
    assert a.verdict == NO and a.n_within_deck == 0
    assert any("30m 내 0점" in r for r in a.reasons)


def test_wide_field_is_conditional_not_blocked(tmp_path):
    """교량 위 점이 1% 미만이면 차단은 아니되 조건부 — 잘라 쓰라는 신호."""
    rng = np.random.default_rng(3)
    n = 2000
    los = rng.normal(0, 30.0, (n, 4))
    p = tmp_path / "wide.h5"
    _project(p, los=los, spread=0.1)                 # ±11km 로 흩뿌림(광역 필드)
    a = audit_artifact(p, target=(37.0, 127.0))
    assert a.verdict == COND and 0 < a.deck_frac < 0.01
    assert any("광역 필드" in r for r in a.reasons)


def test_degenerate_pinn_span_blocks_reporting(tmp_path, monkeypatch):
    """PINN 경간이 실연장의 2배를 넘으면 그 위의 EI·CRI 는 못 쓴다(honam 339배 사례)."""
    import inframon.audit as au

    class _Prof:
        name = "칠백로"
        length_m = 50.0

    monkeypatch.setattr(au, "_official_span", au._official_span)      # 원함수 유지
    monkeypatch.setattr("inframon.public_data.find_bridge_csv", lambda *_: "x.csv")
    monkeypatch.setattr("inframon.public_data.nearest_bridge_profile",
                        lambda *a, **k: _Prof())
    p = _project(tmp_path / "span.h5", span_m=16967.7)
    a = audit_artifact(p, target=(37.0, 127.0))
    assert a.span_ratio > 300 and a.verdict == NO
    assert any("실연장" in r for r in a.reasons)


def test_missing_run_record_is_conditional(tmp_path):
    p = _project(tmp_path / "norec.h5", track_path="")
    a = audit_artifact(p, target=(37.0, 127.0))
    assert not a.has_run_record and a.verdict == COND
    assert any("재현 불가" in r for r in a.reasons)


def test_pipeline_report_counts_as_record(tmp_path):
    (tmp_path / "pipeline_report.json").write_text("{}", encoding="utf-8")
    p = _project(tmp_path / "rec.h5", track_path="")
    a = audit_artifact(p, target=(37.0, 127.0))
    assert a.has_run_record and "pipeline_report.json" in a.record_note


def test_non_artifact_file_is_reported_not_crashed(tmp_path):
    p = tmp_path / "empty.h5"
    with h5py.File(p, "w") as f:
        f.create_dataset("x", data=[1, 2, 3])
    a = audit_artifact(p)
    assert a.verdict == NO and any("산출물이 아닙니다" in r for r in a.reasons)


def test_missing_file_does_not_raise(tmp_path):
    a = audit_artifact(tmp_path / "nope.h5")
    assert a.exists is False and a.verdict == NO


def test_target_found_from_sibling_registry(tmp_path):
    """⑭ 레지스트리(wgs84_center)에서 대상 좌표를 스스로 찾는다."""
    (tmp_path / "bridge_registry.json").write_text(json.dumps(
        {"bridges": [{"bridge_id": "X", "wgs84_center": [37.0, 127.0]}]}), encoding="utf-8")
    a = audit_artifact(_project(tmp_path / "p.h5"))
    assert a.target == (37.0, 127.0) and a.n_within_deck is not None


def test_target_found_from_recipe_folder(tmp_path):
    """data/honam_project.h5 ↔ data/recipe_honam_asc/bridge_target.json 관계를 찾는다."""
    (tmp_path / "recipe_honam_asc").mkdir()
    (tmp_path / "recipe_honam_asc" / "bridge_target.json").write_text(
        json.dumps({"selected_lat": 37.0, "selected_lon": 127.0}), encoding="utf-8")
    a = audit_artifact(_project(tmp_path / "honam_project.h5"))
    assert a.target == (37.0, 127.0)


def test_table_marks_every_verdict(tmp_path):
    rows = [audit_artifact(_project(tmp_path / "a.h5"), target=(37.0, 127.0)),
            audit_artifact(_project(tmp_path / "b.h5", lonlat=(128.0, 38.0)),
                           target=(37.0, 127.0))]
    md = format_table(rows)
    assert md.count("|") > 10 and "보고 가능" in md and "보고 불가" in md


def test_far_standard_data_match_is_not_treated_as_agreement(tmp_path, monkeypatch):
    """멀리서 매칭된 표준데이터의 '실연장' 은 다른 교량 것이다 — 일치해도 근거가 못 된다.

    정자교 재처리에서 567m 떨어진 금곡교가 매칭돼 경간이 108m vs 108m(×1)로 '일치'
    했지만, 그 108m 은 금곡교 제원이었다.
    """
    class _Prof:
        name = "금곡교"
        length_m = 108.0
        extra = {"match_dist_m": 566.9}

    monkeypatch.setattr("inframon.public_data.find_bridge_csv", lambda *_: "x.csv")
    monkeypatch.setattr("inframon.public_data.nearest_bridge_profile",
                        lambda *a, **k: _Prof())
    a = audit_artifact(_project(tmp_path / "p.h5", span_m=108.0), target=(37.0, 127.0))
    assert a.official_dist_m == 566.9
    assert a.verdict == COND
    assert any("떨어진" in r and "금곡교" in r for r in a.reasons)


def test_close_standard_data_match_still_checks_span(tmp_path, monkeypatch):
    """가까이 매칭됐으면 경간 비교는 그대로 유효하다(퇴화 입력 차단이 살아 있어야 한다)."""
    class _Prof:
        name = "칠백로"
        length_m = 50.0
        extra = {"match_dist_m": 12.0}

    monkeypatch.setattr("inframon.public_data.find_bridge_csv", lambda *_: "x.csv")
    monkeypatch.setattr("inframon.public_data.nearest_bridge_profile",
                        lambda *a, **k: _Prof())
    a = audit_artifact(_project(tmp_path / "p.h5", span_m=16967.7), target=(37.0, 127.0))
    assert a.verdict == NO and a.span_ratio > 300


def test_same_bridge_far_registration_is_not_called_a_wrong_bridge(tmp_path, monkeypatch):
    """표준데이터 등록 좌표(교량시작점)가 멀 뿐 같은 교량이면 문구가 달라야 한다.

    청양교↔청양교(671m)와 정자교↔금곡교(567m)는 성격이 전혀 다르다.
    """
    import json as _json

    class _Prof:
        name = "청양교"
        length_m = 90.0
        extra = {"match_dist_m": 671.0}

    monkeypatch.setattr("inframon.public_data.find_bridge_csv", lambda *_: "x.csv")
    monkeypatch.setattr("inframon.public_data.nearest_bridge_profile",
                        lambda *a, **k: _Prof())
    (tmp_path / "bridge_target.json").write_text(
        _json.dumps({"name": "청양교 chyg", "selected_lat": 37.0, "selected_lon": 127.0}),
        encoding="utf-8")
    a = audit_artifact(_project(tmp_path / "p.h5", span_m=90.0))
    assert a.target_name == "청양교 chyg" and a.verdict == COND
    assert any("이름은 일치" in r for r in a.reasons)
    assert not any("다른 교량 제원일 수 있다" in r for r in a.reasons)


def test_reports_what_pinn_actually_used(tmp_path, monkeypatch):
    """PINN 이 먼 CSV 기록을 **쓰지 않았으면** '잘못된 제원'처럼 적으면 안 된다.

    정자교는 표준데이터에 없어 565m 떨어진 금곡교가 최근접이지만, PINN 은 OSM 제원을 썼다.
    """
    import json as _json

    class _Prof:
        name = "금곡교"
        length_m = 108.0
        extra = {"match_dist_m": 565.0}

    monkeypatch.setattr("inframon.public_data.find_bridge_csv", lambda *_: "x.csv")
    monkeypatch.setattr("inframon.public_data.nearest_bridge_profile",
                        lambda *a, **k: _Prof())
    p = tmp_path / "p.h5"
    _project(p, span_m=107.0)
    import h5py
    with h5py.File(p, "a") as f:                       # PINN 이 OSM 제원을 썼다고 기록
        f["pinn"].attrs["inputs"] = _json.dumps(
            {"total_length_m": 107.0, "profile_source": "osm"})
    a = audit_artifact(p, target=(37.0, 127.0))
    assert a.pinn_profile_source == "osm" and a.verdict == COND
    assert any("표준데이터에 이 교량이 없다" in r and "osm" in r for r in a.reasons)
    assert not any("다른 교량 제원일 수 있다" in r for r in a.reasons)


def test_csv_specs_actually_used_still_flagged_when_far(tmp_path, monkeypatch):
    """반대로 PINN 이 먼 CSV 기록을 실제로 썼다면 그건 경고해야 한다."""
    class _Prof:
        name = "금곡교"
        length_m = 108.0
        extra = {"match_dist_m": 565.0}

    monkeypatch.setattr("inframon.public_data.find_bridge_csv", lambda *_: "x.csv")
    monkeypatch.setattr("inframon.public_data.nearest_bridge_profile",
                        lambda *a, **k: _Prof())
    import json as _json

    import h5py
    p = tmp_path / "p.h5"
    _project(p, span_m=108.0)
    with h5py.File(p, "a") as f:
        f["pinn"].attrs["inputs"] = _json.dumps(
            {"total_length_m": 108.0, "profile_source": "data_go_kr:전국교량표준데이터"})
    a = audit_artifact(p, target=(37.0, 127.0))
    assert any("다른 교량 제원일 수 있다" in r for r in a.reasons)


def test_no_pinn_span_does_not_crash_the_audit(tmp_path, monkeypatch):
    """PINN 을 안 돌린 산출물에서도 감사는 죽지 않고 리포트를 돌려준다."""
    class _Prof:
        name = "금곡교"
        length_m = 108.0
        extra = {"match_dist_m": 565.0}

    monkeypatch.setattr("inframon.public_data.find_bridge_csv", lambda *_: "x.csv")
    monkeypatch.setattr("inframon.public_data.nearest_bridge_profile",
                        lambda *a, **k: _Prof())
    a = audit_artifact(_project(tmp_path / "p.h5", span_m=None), target=(37.0, 127.0))
    assert a.pinn_span_m is None and a.verdict in (OK, COND, NO)
