"""광역 판정 → Pontifex — 무엇을 올리고 무엇을 올리지 않는가.

광역 감시는 교량 수천 개를 한 번에 판정한다. 그 결과를 남의 플랫폼에 올릴 때 (1) 확인이
필요한 판정이 '경고'로 박히지 않고, (2) 플랫폼이 이미 가진 교량에 붙으며(새로 만들지 않고),
(3) 우리 등급이 플랫폼에서 더 센 말로 바뀌지 않는지를 고정한다.
"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from inframon.region import pontifex_push as rp


def _bridge(bid, name, lat, lon, *, lv=0, rv=False, city="강릉시", trk="a54"):
    r = None if lv is None else {"lv": lv, "rv": rv, "trk": trk, "conf": "보통", "rn": 0.2,
                                 "r10": 0.3, "dn": 5.0, "sa": 25.0, "bn": 0.0, "ba": 0.002,
                                 "nps": 4, "rlo": 0.1, "rhi": 0.3, "nt": ""}
    return {"id": bid, "n": name, "c": city, "lat": lat, "lon": lon, "r": r}


@pytest.fixture()
def root(tmp_path):
    bridges = [
        _bridge("r1", "내곡교", 37.7470, 128.8877, lv=1),
        _bridge("r2", "남대천교", 37.7500, 128.9000, lv=3),                 # 경고(확인 완료)
        _bridge("r3", "확인교", 37.7600, 128.9100, lv=3, rv=True),          # 확인 필요
        _bridge("r4", "불가교", 37.7700, 128.9200, lv=-1),
        _bridge("r5", "미처리교", 37.7800, 128.9300, lv=None),
        _bridge("r6", "대장에없는교", 37.7900, 128.9400, lv=0),
    ]
    (tmp_path / "state.json").write_text(
        json.dumps({"title": "강원특별자치도 강릉시", "bridges": bridges}, ensure_ascii=False),
        encoding="utf-8")
    con = sqlite3.connect(str(tmp_path / "history.sqlite"))
    con.execute("create table cycles(id integer primary key, run_time text)")
    con.execute("create table judgements(cycle integer, bridge text, name text, track text, "
                "level integer, status text, r_now real, r_10y real, d_now_mm real, n_ps integer, "
                "conf text, review integer, proxy integer, last_date text)")
    con.execute("insert into cycles values (1, '2026-10-06')")
    for b in bridges:
        if b["r"]:
            con.execute("insert into judgements(cycle, bridge, track, last_date) values (1,?,?,?)",
                        (b["id"], "a54", "20260409"))
    con.commit()
    con.close()
    return tmp_path


_PLATFORM = [
    {"id": 14606, "name": "내곡교", "lat": 37.7471, "lon": 128.8878, "addr1": "강원도",
     "region": {"code": "32030", "name": "강릉시"}},
    {"id": 14700, "name": "남대천교", "lat": 37.7501, "lon": 128.9001, "addr1": "강원도",
     "region": {"code": "32030", "name": "강릉시"}},
    {"id": 14701, "name": "확인교", "lat": 37.7600, "lon": 128.9100, "addr1": "강원도",
     "region": {"code": "32030", "name": "강릉시"}},
    # 같은 이름의 시가 다른 도에 있어도(가상의 경남 '강릉시') 우리 교량과 가까운 쪽을 쓴다
    {"id": 900, "name": "내곡교", "lat": 35.0, "lon": 128.0, "addr1": "경상남도",
     "region": {"code": "48999", "name": "강릉시"}},
]


@pytest.fixture()
def platform(tmp_path):
    from inframon.pontifex_mock import serve

    srv = serve(0, bridges=_PLATFORM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", srv
    srv.shutdown()
    srv.server_close()


def test_only_confirmed_verdicts_on_known_bridges_are_planned(root, platform):
    p = rp.plan(root, base=platform[0])
    assert {m["pontifex_id"] for m in p["matched"]} == {14606, 14700}
    assert p["skipped"] == {"확인 필요(제외)": 1, "판정 불가": 1, "미처리": 1,
                            "플랫폼에서 못 찾음": 1}
    assert [u["name"] for u in p["unmatched"]] == ["대장에없는교"]


def test_level_names_are_kept_not_inflated(root, platform):
    """우리 '경고'가 플랫폼 '위험'(3)이 되면 안 된다 — 말이 같은 등급(2)으로 간다."""
    recs = {r["bridge_id"]: r for r in rp.plan(root, base=platform[0])["records"]}
    assert recs[14700]["warning_level"] == 2 and recs[14700]["summary_json"]["status"] == "경고"
    assert recs[14606]["warning_level"] == 1 and recs[14606]["summary_json"]["status"] == "관심"


def test_records_are_a_separate_source_without_fake_cri(root, platform):
    """허용변위 비율은 CRI 가 아니다 — CRI 칸에 넣으면 플랫폼 CRI 순위에 섞인다."""
    for r in rp.plan(root, base=platform[0])["records"]:
        assert r["source"] == "inframon_region" and "cri_global_max" not in r
        assert r["observed_at"] == "2026-04-09"                 # 마지막 영상일, 오늘이 아니다
        assert r["summary_json"]["ratio_now"] == 0.2


def test_review_verdicts_go_only_when_asked_and_are_marked(root, platform):
    p = rp.plan(root, base=platform[0], include_review=True)
    rec = next(r for r in p["records"] if r["bridge_id"] == 14701)
    assert rec["summary_json"]["review"] is True


def test_dry_run_sends_nothing(root, platform):
    base, srv = platform
    res = rp.push_region(root, base=base, dry_run=True)
    assert res["planned"] == 2 and res["sent"] == 0 and not srv.state.sensing


def test_push_lands_on_existing_bridges_and_registers_none(root, platform, tmp_path):
    base, srv = platform
    res = rp.push_region(root, base=base, report_path=tmp_path / "out" / "push.json")
    assert res["sent"] == 2 and not res["errors"]
    assert set(srv.state.sensing) == {14606, 14700}
    assert len(srv.state.bridges) == len(_PLATFORM)             # 교량을 새로 만들지 않았다
    assert json.loads((tmp_path / "out" / "push.json").read_text(encoding="utf-8"))["sent"] == 2
    assert "올리지 않음" in rp.format_report(res)


def test_second_cycle_does_not_duplicate(root, platform):
    base, srv = platform
    rp.push_region(root, base=base)
    rp.push_region(root, base=base)
    assert len(srv.state.sensing[14606]["summary_records"]) == 1
