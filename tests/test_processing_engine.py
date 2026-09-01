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
    # 단일 파일 어댑터(stamps)로 위임 규약을 본다. sarvey 는 폴더·레이아웃 분기가
    # 있어 별도 테스트(test_sarvey_routes_by_layout)에서 다룬다.
    src = tmp_path / "stamps.mat"
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
    r = pe.run("stamps", 37.0, 127.0, tmp_path, out, source=src)
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

    src = tmp_path / "s.mat"
    src.write_bytes(b"x")
    monkeypatch.setattr(pe, "_adapter", lambda stem: _Mod())
    with pytest.raises(pe.EngineError, match="Track H5"):
        pe.run("stamps", 37.0, 127.0, tmp_path, tmp_path / "none.h5", source=src)


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


# ── 회귀: 실제 Hyp3RunResult 로 full 을 돌려도 ⑧이 뒤집히지 않는가 ──
# 이전엔 필드만 흉내 낸 목으로 검사해서, 실제 클래스에 as_dict·pairs 가 없다는 사실이
# 테스트를 통과했다. ⑧은 done 을 낸 뒤 보고용 부기에서 AttributeError 로 error 로
# 뒤집히고 ⑨⑫⑬⑭ 가 전부 skip 됐다. 목이 아니라 **실물**로 고정한다.
def test_hyp3_full_does_not_flip_stage8_to_error(tmp_path, monkeypatch):
    import inframon.pipeline_bridge as pb
    from inframon.insar.hyp3_backend import Hyp3RunResult

    h5 = tmp_path / "hyp3_track.h5"
    h5.write_bytes(b"x")
    native = Hyp3RunResult(track_h5=str(h5), n_points=42, ref_date="20240119",
                           epochs=["20240119", "20240131"], n_ok=3, n_fail=0,
                           burst_id="136231_IW2")
    monkeypatch.setattr(pe, "run", lambda *a, **k: pe.EngineResult(
        engine="hyp3", track_h5=str(h5), n_points=42, detail="쌍 3", native=native,
        supports_deck_ps_ds=pe.supports_deck_ps_ds("hyp3")))
    monkeypatch.setattr(pb, "_twin_and_register", lambda *a, **k: None)

    rep = pb.PipelineReport(lat=37.0, lon=127.0)
    pb._run_heavy(rep, {"bridge": {"geometry": [(37.0, 127.0), (37.001, 127.001)]}},
                  37.0, 127.0, tmp_path, None, 8, False,
                  ifc=None, bim_elements=None, registry=None, bridge_id="t",
                  twin_value=None, engine="hyp3", engine_source=None)

    s8 = [s for s in rep.stages if s.step.startswith("⑧")]
    assert [s.status for s in s8] == ["done"], [(s.step, s.status, s.detail) for s in s8]
    skipped = [s for s in rep.stages if s.status == "skip" and "선행 산출물 없음" in s.detail]
    assert not skipped, [s.step for s in skipped]
    # ⑨는 '쌍이 없어 재추출하지 않음' 으로 정상 skip 하고 track_h5 를 그대로 하류로 넘긴다.
    s9 = next(s for s in rep.stages if s.step.startswith("⑨"))
    assert s9.status == "skip" and "시계열까지 산출" in s9.detail
    # hyp3 는 버스트 광역 산출이라 교량 마스킹이 없다 — 그 사실을 숨기지 않는다.
    assert "마스킹 없음" in s9.detail


def test_deck_ps_ds_capability_is_single_source_of_truth():
    """⑨ 가능 여부는 엔진 이름 한 곳에서만 판정한다(plan 문구와 full 분기가 갈리지 않게)."""
    assert pe.supports_deck_ps_ds("snap")
    for name in ("hyp3", *pe.IMPORT_ENGINES):
        assert not pe.supports_deck_ps_ds(name), name


# ── 데크 마스킹: 가져오기형이 광역 PSI 필드를 그대로 넘기지 않는가 ──
def test_deck_bbox_uses_bridge_length(tmp_path):
    """연장을 알면 그만큼, 모르면 보수적으로 ±1km — 좁게 잘라 점을 다 날리지 않는다."""
    mn_lon, mn_lat, mx_lon, mx_lat = pe.deck_bbox(37.0, 127.0, length_m=100.0)
    half_m = (mx_lat - mn_lat) / 2 * 111_320.0
    assert 75 < half_m < 85                      # 연장/2 + 여유 30m
    wide = pe.deck_bbox(37.0, 127.0)             # 연장 미상
    assert (wide[3] - wide[1]) / 2 * 111_320.0 > 900


def test_import_engine_passes_bbox_to_adapter(tmp_path, monkeypatch):
    """엔진이 받은 lat/lon 을 버리지 않고 어댑터 bbox 로 넘긴다."""
    h5 = tmp_path / "t.h5"; h5.write_bytes(b"x")
    src = tmp_path / "ts.h5"; src.write_bytes(b"x")
    seen = {}

    class _Mod:
        @staticmethod
        def convert(*a, **kw):
            seen.update(kw)
            return 7, 3

    monkeypatch.setattr(pe, "_adapter", lambda stem: _Mod())
    monkeypatch.setattr(Path, "exists", lambda self: True)
    r = pe.run("stamps", 37.0, 127.0, tmp_path, h5, source=str(src), bridge_length_m=100.0)
    assert seen["bbox"] is not None
    assert seen["bbox"][0] < 127.0 < seen["bbox"][2]
    assert r.extra["deck_bbox"] is not None
    assert "교량범위 마스킹" in r.detail


def test_deck_mask_can_be_disabled(tmp_path, monkeypatch):
    h5 = tmp_path / "t.h5"; h5.write_bytes(b"x")
    seen = {}

    class _Mod:
        @staticmethod
        def convert(*a, **kw):
            seen.update(kw)
            return 5, 2

    monkeypatch.setattr(pe, "_adapter", lambda stem: _Mod())
    monkeypatch.setattr(Path, "exists", lambda self: True)
    pe.run("stamps", 37.0, 127.0, tmp_path, h5, source=str(tmp_path / "x.mat"),
           deck_mask=False)
    assert seen["bbox"] is None


def test_empty_after_masking_explains_and_offers_way_out(tmp_path, monkeypatch):
    """잘라서 0점이면 조용히 넘어가지 않고 원인·우회로를 말한다."""
    class _Mod:
        @staticmethod
        def convert(*a, **kw):
            raise ValueError("bbox 안에 점이 0개입니다")

    monkeypatch.setattr(pe, "_adapter", lambda stem: _Mod())
    monkeypatch.setattr(Path, "exists", lambda self: True)
    with pytest.raises(pe.EngineError) as exc:
        pe.run("stamps", 37.0, 127.0, tmp_path, tmp_path / "t.h5",
               source=str(tmp_path / "x.mat"))
    msg = str(exc.value)
    assert "남는 점이 없습니다" in msg and "deck_mask=False" in msg


def test_sarvey_routes_by_layout(tmp_path, monkeypatch):
    """실 SARvey p2(coord_xy/phase)는 58_, 구 export(displacement)는 50_ 로 간다."""
    import h5py
    import numpy as np

    export_h5 = tmp_path / "export.h5"
    with h5py.File(export_h5, "w") as f:
        f.create_dataset("displacement", data=np.zeros((3, 2)))
    ts_h5 = tmp_path / "p2_coh70_ts.h5"
    with h5py.File(ts_h5, "w") as f:
        f.create_dataset("coord_xy", data=np.zeros((3, 2), dtype=np.int64))
        f.create_dataset("phase", data=np.zeros((3, 2)))

    picked = []

    class _Mod:
        @staticmethod
        def convert(*a, **kw):
            return 3, 2

    def _fake_adapter(stem):
        picked.append(stem)
        return _Mod()

    monkeypatch.setattr(pe, "_adapter", _fake_adapter)
    out = tmp_path / "o.h5"; out.write_bytes(b"x")
    pe.run("sarvey", 37.0, 127.0, tmp_path, out, source=str(export_h5))
    assert picked[-1] == pe._SARVEY_EXPORT_ADAPTER
    pe.run("sarvey", 37.0, 127.0, tmp_path, out, source=str(ts_h5))
    assert picked[-1] == pe._ADAPTERS["sarvey"]


def test_sarvey_accepts_outputs_directory(tmp_path, monkeypatch):
    """source 로 outputs 폴더를 주면 p2_*_ts.h5 를 스스로 고른다."""
    outputs = tmp_path / "outputs"; outputs.mkdir()
    (outputs / "p2_coh70_ts.h5").write_bytes(b"x")
    (outputs / "p2_coh80_ts.h5").write_bytes(b"x")
    seen = {}

    class _Mod:
        @staticmethod
        def convert(src, out, **kw):
            seen["ts"] = kw.get("ts")
            return 4, 2

    monkeypatch.setattr(pe, "_adapter", lambda stem: _Mod())
    out = tmp_path / "o.h5"; out.write_bytes(b"x")
    pe.run("sarvey", 37.0, 127.0, tmp_path, out, source=str(outputs))
    assert seen["ts"] == "p2_coh80_ts.h5"        # coherence 임계가 높은 쪽 우선
