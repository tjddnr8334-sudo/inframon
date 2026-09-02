"""B-Maps 연속 반영 — 감사 게이트·변경 감지·기록.

교량 모니터링은 반복 작업이다. 반복될수록 (1) 무효 산출물이 섞이지 않고, (2) 같은 것을
반복 전송하지 않으며, (3) 무엇을 왜 걸렀는지 남아야 한다. 그 셋을 여기서 고정한다.
"""

from __future__ import annotations

import json

import h5py
import numpy as np
import pytest

from inframon.bmap_sync import discover, fingerprint, format_report, load_state, sync


def _project(path, *, wrapped=False, freq=5.4, span=45.0, cri=0.5, lonlat=(127.0, 37.0)):
    src = ("SNAP star-network wrapped-phase → LOS" if wrapped
           else "SNAP star-network snaphu-unwrapped → LOS")
    n, m = 8, 3
    with h5py.File(path, "w") as f:
        ins = f.create_group("insar")
        ins.create_dataset("los", data=np.full((n, m), 30.0, dtype=np.float32))
        ins.create_dataset("xyz", data=np.column_stack(
            [np.full(n, lonlat[0]), np.full(n, lonlat[1]), np.zeros(n)]))
        ins.create_dataset("date_labels",
                           data=np.array([b"20240107", b"20240119", b"20240131"], dtype="S8"))
        ins.attrs["track_source"] = json.dumps({"path": "t.h5", "attrs": {"source": src}})
        fr = f.create_group("fram")
        fr.create_dataset("CRI", data=np.full((n, m), cri, dtype=np.float32))
        fr.attrs["reference_range"] = json.dumps({"worst_cri": cri})
        pg = f.create_group("pinn")
        pg.attrs["inputs"] = json.dumps(
            {"total_length_m": span, "structural_span_m": span,
             "profile_source": "data_go_kr:전국교량표준데이터", "EI_identified": True})
        pg.create_dataset("natural_freq", data=np.array([freq, freq * 2.7]))
    return path


@pytest.fixture(autouse=True)
def _no_standard_data(monkeypatch):
    monkeypatch.setattr("inframon.public_data.find_bridge_csv", lambda *a, **k: None)


@pytest.fixture()
def sent(monkeypatch):
    """실제 전송 대신 호출을 기록한다."""
    calls = []

    class _Res:
        summary_n, member_n, warnings, audit_verdict = 3, 1, [], "보고 가능"
        dry_run = False

    def _push(h5, bid, **kw):
        calls.append({"h5": str(h5), "bridge_id": bid, **kw})
        return _Res()

    monkeypatch.setattr("inframon.pontifex.push", _push)
    return calls


def test_valid_artifact_is_pushed(tmp_path, sent):
    p = _project(tmp_path / "project.h5")
    items = sync([{"project_h5": str(p), "bridge_id": 40001, "name": "청양교",
                   "lat": 37.0, "lon": 127.0}],
                 base="http://x", state_path=tmp_path / "state.json")
    assert items[0].action == "pushed" and items[0].summary_n == 3
    assert len(sent) == 1 and sent[0]["bridge_id"] == 40001


def test_unreportable_artifact_is_blocked_not_sent(tmp_path, sent):
    """래핑 위상 산출물은 플랫폼에 닿지 않는다 — 되돌리기 어려운 일이다."""
    p = _project(tmp_path / "project.h5", wrapped=True)
    items = sync([{"project_h5": str(p), "bridge_id": 1, "lat": 37.0, "lon": 127.0}],
                 base="http://x", state_path=tmp_path / "state.json")
    assert items[0].action == "blocked" and not sent
    assert any("언래핑" in r for r in items[0].reasons)


def test_impossible_frequency_is_blocked(tmp_path, sent):
    p = _project(tmp_path / "project.h5", freq=232.0, span=90.0)
    items = sync([{"project_h5": str(p), "bridge_id": 1, "lat": 37.0, "lon": 127.0}],
                 base="http://x", state_path=tmp_path / "state.json")
    assert items[0].action == "blocked" and not sent


def test_strict_mode_rejects_conditional(tmp_path, sent):
    """--bmap-strict: ✅ 보고 가능만 올린다(대상 좌표 미지정 → 조건부)."""
    p = _project(tmp_path / "project.h5")
    items = sync([{"project_h5": str(p), "bridge_id": 1}],      # 좌표 없음 → 조건부
                 base="http://x", state_path=tmp_path / "state.json",
                 allow_conditional=False)
    assert items[0].action == "blocked" and not sent


def test_unchanged_artifact_is_not_resent(tmp_path, sent):
    """같은 내용을 반복 전송하지 않는다 — 지문이 같으면 건너뛴다."""
    p = _project(tmp_path / "project.h5")
    t = [{"project_h5": str(p), "bridge_id": 1, "lat": 37.0, "lon": 127.0}]
    st = tmp_path / "state.json"
    assert sync(t, base="http://x", state_path=st)[0].action == "pushed"
    assert sync(t, base="http://x", state_path=st)[0].action == "skipped_unchanged"
    assert len(sent) == 1


def test_changed_artifact_is_resent(tmp_path, sent):
    p = _project(tmp_path / "project.h5")
    t = [{"project_h5": str(p), "bridge_id": 1, "lat": 37.0, "lon": 127.0}]
    st = tmp_path / "state.json"
    sync(t, base="http://x", state_path=st)
    _project(p, cri=0.9)                                  # 새 산출(내용 변경)
    assert sync(t, base="http://x", state_path=st)[0].action == "pushed"
    assert len(sent) == 2


def test_missing_bridge_id_is_reported_not_invented(tmp_path, sent):
    """플랫폼 교량 id 는 사람이 정하는 값이다 — 지어내지 않는다."""
    p = _project(tmp_path / "project.h5")
    items = sync([{"project_h5": str(p), "lat": 37.0, "lon": 127.0}],
                 base="http://x", state_path=tmp_path / "state.json")
    assert items[0].action == "failed" and "bridge_id" in items[0].error
    assert not sent


def test_dry_run_neither_sends_nor_records(tmp_path, sent):
    p = _project(tmp_path / "project.h5")
    st = tmp_path / "state.json"
    items = sync([{"project_h5": str(p), "bridge_id": 1, "lat": 37.0, "lon": 127.0}],
                 base="http://x", state_path=st, dry_run=True)
    assert items[0].action == "dry-run"
    assert not st.exists()                                # 기록도 남기지 않는다


def test_state_records_reasons_for_blocked(tmp_path, sent):
    """왜 걸렀는지 남아야 며칠 뒤 '왜 갱신이 안 됐지'에 답할 수 있다."""
    p = _project(tmp_path / "project.h5", wrapped=True)
    st = tmp_path / "state.json"
    sync([{"project_h5": str(p), "bridge_id": 1, "lat": 37.0, "lon": 127.0}],
         base="http://x", state_path=st)
    rec = load_state(st)["items"][str(p)]
    assert rec["action"] == "blocked" and rec["reasons"]
    assert load_state(st)["updated_utc"].startswith("20")


def test_fingerprint_changes_with_content(tmp_path):
    a = fingerprint(_project(tmp_path / "a.h5", cri=0.4))
    b = fingerprint(_project(tmp_path / "b.h5", cri=0.9))
    assert a != b and len(a) == 16


def test_discover_reads_target_beside_artifact(tmp_path):
    d = tmp_path / "bridge"
    d.mkdir()
    _project(d / "project.h5")
    (d / "bridge_target.json").write_text(
        json.dumps({"name": "청양교", "selected_lat": 36.45, "selected_lon": 126.8,
                    "pontifex_id": 40001}), encoding="utf-8")
    got = discover(tmp_path)
    assert got[0]["name"] == "청양교" and got[0]["bridge_id"] == 40001
    assert got[0]["lat"] == 36.45


def test_report_lists_blocked_reasons(tmp_path, sent):
    p = _project(tmp_path / "project.h5", wrapped=True)
    items = sync([{"project_h5": str(p), "bridge_id": 1, "name": "가교",
                   "lat": 37.0, "lon": 127.0}],
                 base="http://x", state_path=tmp_path / "state.json")
    txt = format_report(items)
    assert "차단 1" in txt and "가교" in txt and "언래핑" in txt


def test_register_saves_platform_id_for_next_run(tmp_path, sent, monkeypatch):
    """등록으로 받은 id 를 산출물 곁에 적어야 다음 실행이 새 교량을 또 만들지 않는다."""
    calls = []

    def _reg(name, lat, lon, **kw):
        calls.append(name)
        return {"id": 40001, "name": name, "region": {"name": "청양군"},
                "detail_url": "/bridge/40001/"}

    monkeypatch.setattr("inframon.pontifex.register_bridge", _reg)
    d = tmp_path / "b"
    d.mkdir()
    _project(d / "project.h5")
    (d / "bridge_target.json").write_text(
        json.dumps({"name": "청양교", "selected_lat": 37.0, "selected_lon": 127.0}),
        encoding="utf-8")
    t = discover(tmp_path)
    st = tmp_path / "state.json"
    items = sync(t, base="http://x", state_path=st, register=True)
    assert items[0].action == "pushed" and calls == ["청양교"]
    saved = json.loads((d / "bridge_target.json").read_text(encoding="utf-8"))
    assert saved["pontifex_id"] == 40001
    # 두 번째 실행은 등록하지 않고 저장된 id 를 쓴다
    _project(d / "project.h5", cri=0.7)              # 내용 변경 → 재전송 대상
    items2 = sync(discover(tmp_path), base="http://x", state_path=st, register=True)
    assert items2[0].action == "pushed" and calls == ["청양교"]


def test_register_needs_name_and_coords(tmp_path, sent, monkeypatch):
    monkeypatch.setattr("inframon.pontifex.register_bridge",
                        lambda *a, **k: pytest.fail("이름·좌표 없이 등록하면 안 된다"))
    p = _project(tmp_path / "project.h5")
    items = sync([{"project_h5": str(p)}], base="http://x",
                 state_path=tmp_path / "s.json", register=True)
    assert items[0].action == "failed" and "bridge_id" in items[0].error
