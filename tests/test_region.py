"""⑥ 광역 모니터링 — 순수 로직(Streamlit 무관): 대장 시군구 필터, 엔진 결과 판독, 한계값 병기, 교량 내보내기."""

from __future__ import annotations

import csv
import json
import sqlite3

import numpy as np

from inframon.region import judge, results
from inframon.region.registry_csv import bridges_in_sigungu

COLS = ["교량명", "시설물종별등급구분", "시도명", "시군구명", "시군구코드", "교량시작점위도", "교량시작점경도",
        "교량종료점위도", "교량종료점경도", "교량연장", "교량폭", "상부구조형식", "교량준공연도", "최종안전점검결과",
        "관리기관명"]


def _csv(tmp_path, rows, enc="cp949"):
    p = tmp_path / "national_bridge_standard_test.csv"
    with open(p, "w", newline="", encoding=enc) as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLS})
    return p


def test_bridges_in_sigungu_filters_and_parses(tmp_path):
    p = _csv(tmp_path, [
        dict(교량명="내곡교", 시설물종별등급구분="2", 시도명="강원특별자치도", 시군구명="강릉시", 교량시작점위도="37.7467",
             교량시작점경도="128.8878", 교량종료점위도="37.7481", 교량종료점경도="128.8873", 교량연장="162", 상부구조형식="RC슬래브교"),
        dict(교량명="각동교", 시설물종별등급구분="99", 시도명="강원특별자치도", 시군구명="영월군", 교량시작점위도="37.121",
             교량시작점경도="128.539", 교량연장="210"),
        dict(교량명="좌표없음교", 시도명="강원특별자치도", 시군구명="강릉시", 교량연장="30"),
    ])
    b = bridges_in_sigungu(p, sido="강원", sigungu="강릉시")
    assert [x.name for x in b] == ["내곡교"]            # 좌표 없는 행은 위치를 몰라 제외
    assert b[0].facility_class == "2종" and b[0].length_m == 162 and len(b[0].geometry) == 2
    allb = bridges_in_sigungu(p, sido="강원")
    assert {x.name for x in allb} == {"내곡교", "각동교"}
    assert next(x for x in allb if x.name == "각동교").facility_class == "기타"
    assert len({x.bridge_id for x in allb}) == 2


def _engine_root(tmp_path):
    root = tmp_path / "res"
    root.mkdir()
    state = {"title": "강릉시", "made": "2026-10-05 12:00", "bridges": [
        {"id": "r1", "n": "A교", "c": "강릉시", "lat": 37.7, "lon": 128.9, "cls": "2종", "t": "PSCI거더교", "len": 100,
         "r": {"lv": 2, "rn": 0.62, "r10": 1.4, "dn": 31.0, "bn": 0.0004, "sp": 30, "nps": 7, "rv": False, "trk": "a54"},
         "tracks": {"a54": {"level": 2, "n_ps": 7}}},
        {"id": "r2", "n": "B교", "c": "강릉시", "lat": 37.71, "lon": 128.91, "r": {"lv": -1, "why": "산란체 없음"}},
        {"id": "r3", "n": "C교", "c": "속초시", "lat": 38.2, "lon": 128.6, "r": None},
    ]}
    (root / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    (root / "alerts.json").write_text(json.dumps({"alerts": [{"kind": "신규 경보", "id": "r1"}]}), encoding="utf-8")
    (root / "health.json").write_text(json.dumps({"events": [{"level": "경고", "kind": "자료", "msg": "x"}]}), encoding="utf-8")
    con = sqlite3.connect(str(root / "history.sqlite"))
    con.execute("create table series(bridge text, track text, kind text, date text, disp_mm real)")
    con.executemany("insert into series values (?,?,?,?,?)",
                    [("r1", "a54", k, d, v) for k in ("los", "vert", "vert_tc") for d, v in (("20180614", 0.0), ("20260409", -20.0))])
    con.execute("create table cycles(id integer primary key, run_time text)")
    con.execute("create table judgements(cycle integer, bridge text, name text, track text, level integer, status text, "
                "r_now real, r_10y real, d_now_mm real, n_ps integer, conf text, review integer, proxy integer, last_date text)")
    con.execute("insert into cycles(run_time) values ('2026-10-05')")
    con.execute("insert into judgements values (1,'r1','A교','a54',2,'주의',0.62,1.4,31,7,'보통',0,0,'20260409')")
    con.commit()
    con.close()
    return root


def test_results_readers(tmp_path):
    root = _engine_root(tmp_path)
    rows = results.bridge_rows(results.load_state(root))
    assert [r["status"] for r in rows] == ["주의", "판정 불가", "미처리"]
    assert results.counts(rows) == {"주의": 1, "판정 불가": 1, "미처리": 1}
    s = results.series(root, "r1")
    assert set(s["a54"]) == {"los", "vert", "vert_tc"} and s["a54"]["vert_tc"][-1] == ("20260409", -20.0)
    assert results.judgement_history(root, "r1")[0]["status"] == "주의"
    assert len(results.load_alerts(root)["alerts"]) == 1 and results.load_health(root)["events"][0]["level"] == "경고"


def test_judge_uses_life_limits_with_source():
    r = judge.ratios(12.5, 0.001)
    assert abs(r["ratio_now"] - 0.5) < 1e-9          # 12.5/25 = 0.5, 0.001/(1/500) = 0.5
    assert r["settlement_mm"] == 25.0 and "settlement" in r["source"]
    assert judge.ratios(None, None)["ratio_now"] is None


def test_export_bridge_to_project(tmp_path):
    h5py = __import__("h5py")
    lat0, lon0, lat1, lon1 = 37.7467, 128.8878, 37.7481, 128.8873
    n, m = 40, 12
    t = np.linspace(0, 1, n)
    lat = lat0 + t * (lat1 - lat0); lon = lon0 + t * (lon1 - lon0)
    lat = np.r_[lat, lat0 + 0.01]; lon = np.r_[lon, lon0 + 0.01]          # 마지막 점은 교량에서 ~1 km — 잘려야 한다
    dates = np.array(["2018%02d01" % (k + 1) for k in range(m)])
    disp = np.outer(np.ones(n + 1), np.linspace(0, -5, m))
    npz = tmp_path / "t_stamps_qc.npz"
    np.savez(npz, lon=lon, lat=lat, dates=dates, disp_mm=disp, coh=np.full(n + 1, 0.9))
    from inframon.region.export import export_bridge
    row = {"n": "내곡교", "lat": (lat0 + lat1) / 2, "lon": (lon0 + lon1) / 2,
           "geo": [[[lon0, lat0], [lon1, lat1]]], "r": {"inc": 37.6}}
    res = export_bridge(row, npz, tmp_path / "out")
    assert res["ok"] and res["n_points"] == n and res["n_dates"] == m
    with h5py.File(res["track_h5"]) as f:
        assert f["los_mm"].shape == (n, m) and f.attrs["unit"] == "mm"
    from inframon.contracts.io import ProjectStore
    from inframon.contracts.schema import InSAROutput
    with ProjectStore(res["project"], mode="r") as store:
        sym = store.validate("insar", store.read_meta("insar", InSAROutput))   # /insar 계약(형상·dtype) 통과
    assert sym["N"] == n and sym["M"] == m
