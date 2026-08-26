"""⑧ InSAR 처리 엔진 선택 — snap 외 엔진으로 갈아끼워도 하류 계약이 유지되는지.

핵심 계약: 엔진이 무엇이든 **Track H5 경로**를 돌려주면 ⑨ 이후는 그대로 돈다.
가져오기형(sarvey 등)은 실행 드라이버가 없으므로 source 를 요구하고, 없으면
'조용한 실패' 대신 무엇을 해야 하는지 말해야 한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from inframon.insar import processing_engine as pe


def test_registry_lists_all_engines():
    assert set(pe.available()) == set(pe.ENGINE_NAMES)
    assert set(pe.PROCESS_ENGINES) | set(pe.IMPORT_ENGINES) == set(pe.ENGINE_NAMES)


def test_unknown_engine_names_alternatives():
    with pytest.raises(pe.EngineError) as exc:
        pe.resolve("mintpy2")
    assert "snap" in str(exc.value)          # 무엇을 쓸 수 있는지 알려준다


def test_resolve_is_case_insensitive():
    assert pe.resolve("SNAP") is pe.resolve("snap")


def test_needs_source_split():
    assert pe.needs_source("sarvey") and pe.needs_source("stamps")
    assert not pe.needs_source("snap") and not pe.needs_source("hyp3")


def test_describe_every_engine_has_text():
    for n in pe.ENGINE_NAMES:
        assert pe.describe(n) and pe.describe(n) != n


# ── 처리형: 엔진 결과가 하류 계약(track_h5)으로 정규화되는가 ──
def test_snap_engine_normalizes_result(tmp_path, monkeypatch):
    h5 = tmp_path / "track.h5"
    h5.write_bytes(b"x")

    class _Pair:
        ok = True

    class _Res:
        reference = "20240107"
        pairs = [_Pair(), _Pair()]
        track_h5 = str(h5)
        n_points = 1234
        weather = None
        rejected_slaves = []

    class _Acq:
        slc_dir = str(tmp_path / "SLC")

    monkeypatch.setattr("inframon.insar.snap_acquire.acquire", lambda *a, **k: _Acq())
    monkeypatch.setattr("inframon.insar.snap_backend.run", lambda *a, **k: _Res())
    r = pe.run("snap", 37.0, 127.0, tmp_path, h5, token=None)
    assert r.engine == "snap" and r.track_h5 == str(h5) and r.n_points == 1234
    assert r.native is not None                    # SNAP 은 ⑨용 쌍 정보를 넘긴다
    assert r.extra["slc_dir"] == _Acq.slc_dir


def test_hyp3_engine_normalizes_result(tmp_path, monkeypatch):
    h5 = tmp_path / "t.h5"
    h5.write_bytes(b"x")

    class _R:
        track_h5 = str(h5)
        n_points = 20
        ref_date = "20240119"
        n_ok, n_fail = 3, 1
        burst_id = "136231_IW2"

    monkeypatch.setattr("inframon.insar.hyp3_backend.run", lambda *a, **k: _R())
    r = pe.run("hyp3", 37.0, 127.0, tmp_path, h5)
    assert r.engine == "hyp3" and r.n_points == 20
    assert "실패 1" in r.detail                     # 부분 실패를 숨기지 않는다


# ── 가져오기형: source 요구·변환 위임 ──
@pytest.mark.parametrize("name", pe.IMPORT_ENGINES)
def test_import_engine_requires_source(name, tmp_path):
    with pytest.raises(pe.EngineError) as exc:
        pe.run(name, 37.0, 127.0, tmp_path, tmp_path / "t.h5")
    msg = str(exc.value)
    assert "source" in msg and ("snap" in msg or "hyp3" in msg)   # 대안을 알려준다


def test_import_engine_rejects_missing_file(tmp_path):
    with pytest.raises(pe.EngineError, match="없습니다"):
        pe.run("sarvey", 37.0, 127.0, tmp_path, tmp_path / "t.h5",
               source=tmp_path / "nope.h5")


def test_import_engine_delegates_to_adapter(tmp_path, monkeypatch):
    src = tmp_path / "sarvey_ts.h5"
    src.write_bytes(b"x")
    out = tmp_path / "track.h5"
    seen = {}

    class _Mod:
        @staticmethod
        def convert(s, o, **kw):
            seen["src"], seen["out"] = s, o
            Path(o).write_bytes(b"y")
            return (77, 9)

    monkeypatch.setattr(pe, "_adapter", lambda stem: _Mod())
    r = pe.run("sarvey", 37.0, 127.0, tmp_path, out, source=src)
    assert seen["src"] == str(src) and seen["out"] == str(out)
    assert r.n_points == 77 and "M=9" in r.detail


def test_miaplpy_passes_three_files(tmp_path, monkeypatch):
    src = tmp_path / "timeseries.h5"
    src.write_bytes(b"x")
    got = {}

    class _Mod:
        @staticmethod
        def convert(ts, geom, coh, o, **kw):
            got.update(ts=ts, geom=geom, coh=coh)
            Path(o).write_bytes(b"y")
            return (5, 3)

    monkeypatch.setattr(pe, "_adapter", lambda stem: _Mod())
    pe.run("miaplpy", 37.0, 127.0, tmp_path, tmp_path / "o.h5", source=src)
    assert got["geom"].endswith("geometryRadar.h5")      # 형제 파일 기본 추정
    assert got["coh"].endswith("temporalCoherence.h5")


def test_run_fails_when_engine_produces_no_track(tmp_path, monkeypatch):
    """엔진이 성공한 척해도 산출물이 없으면 하류가 조용히 깨진다 — 여기서 막는다."""
    class _Mod:
        @staticmethod
        def convert(s, o, **kw):
            return (0, 0)                # 파일을 안 만든다

    src = tmp_path / "s.h5"
    src.write_bytes(b"x")
    monkeypatch.setattr(pe, "_adapter", lambda stem: _Mod())
    with pytest.raises(pe.EngineError, match="Track H5"):
        pe.run("sarvey", 37.0, 127.0, tmp_path, tmp_path / "none.h5", source=src)


# ── 파이프라인 통합: 엔진 이름이 단계 라벨·⑨ 처리에 반영되는가 ──
def test_plan_labels_selected_engine(tmp_path, monkeypatch):
    import inframon.pipeline_bridge as pb
    monkeypatch.setattr(pb, "_run_heavy", lambda *a, **k: None)
    for mod, fn in (("inframon.insar.osm_bridge", "confirm_bridge"),
                    ("inframon.insar.roi_selection", "select_roi"),
                    ("inframon.insar.snap_acquire", "search_frames")):
        monkeypatch.setattr(f"{mod}.{fn}",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("off")),
                            raising=False)
    rep = pb.run_bridge_pipeline(37.0, 127.0, out_dir=tmp_path, mode="plan", engine="hyp3")
    steps = {s.step: s for s in rep.stages}
    assert "⑧InSAR처리(hyp3)" in steps
    assert "재추출 불필요" in steps["⑨PS/DS(교량30m)"].detail   # SNAP 전용 ⑨는 건너뜀


def test_plan_warns_import_engine_without_source(tmp_path, monkeypatch):
    import inframon.pipeline_bridge as pb
    monkeypatch.setattr(pb, "_run_heavy", lambda *a, **k: None)
    for mod, fn in (("inframon.insar.osm_bridge", "confirm_bridge"),
                    ("inframon.insar.roi_selection", "select_roi"),
                    ("inframon.insar.snap_acquire", "search_frames")):
        monkeypatch.setattr(f"{mod}.{fn}",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("off")),
                            raising=False)
    rep = pb.run_bridge_pipeline(37.0, 127.0, out_dir=tmp_path, mode="plan", engine="sarvey")
    d = next(s.detail for s in rep.stages if s.step == "⑧InSAR처리(sarvey)")
    assert "source 필요" in d
