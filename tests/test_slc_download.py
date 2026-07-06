"""실 SLC 자동 다운로드 — 레시피 장면 파싱·자격 우선순위·필터·스킵·다운로드(네트워크 격리)."""

from __future__ import annotations

import json
import sys
import types

import pytest

from inframon.insar import slc_download as sd
from inframon.insar.slc_download import (
    SlcAuthError,
    SlcRecipeError,
    _select_slc_vv,
    download_recipe_slc,
    scene_names_from_recipe,
)


def _write_recipe(tmp_path, names):
    (tmp_path / "processing_manifest.json").write_text(
        json.dumps({"stack": {"scene_names": names}}), encoding="utf-8")
    return tmp_path


def _prop(name, level="SLC", pol="VV", url=None, nbytes=int(7.8e9)):
    return {"sceneName": name, "processingLevel": level, "polarization": pol,
            "url": url or f"https://asf/{name}.zip", "bytes": nbytes}


# ── 순수 함수 ──
def test_scene_names_from_recipe(tmp_path):
    _write_recipe(tmp_path, ["A", "B", "C"])
    assert scene_names_from_recipe(tmp_path) == ["A", "B", "C"]


def test_scene_names_missing_manifest_raises(tmp_path):
    with pytest.raises(SlcRecipeError):
        scene_names_from_recipe(tmp_path)


def test_scene_names_empty_raises(tmp_path):
    (tmp_path / "processing_manifest.json").write_text('{"stack": {"scene_names": []}}',
                                                       encoding="utf-8")
    with pytest.raises(SlcRecipeError):
        scene_names_from_recipe(tmp_path)


def test_select_filters_slc_vv_dedup_and_missing():
    names = ["S1", "S2", "S3"]
    props = [
        _prop("S1"),
        _prop("S1"),                       # 중복
        _prop("S2", level="METADATA_SLC"), # SLC 아님 → 제외
        _prop("S2", pol="VV+VH"),          # VV 포함 → 통과
        _prop("X9"),                       # 요청에 없던 것(무시)
    ]
    sel, missing = _select_slc_vv(props, names)
    got = {p["sceneName"] for p in sel}
    assert got == {"S1", "S2"}             # 중복·비SLC 제거
    assert missing == ["S3"]               # 검색에서 못 찾음


# ── 자격 우선순위(가짜 asf_search 주입) ──
@pytest.fixture
def fake_asf(monkeypatch):
    mod = types.ModuleType("asf_search")

    class _Session:
        def auth_with_token(self, t):
            self.mode = ("token", t); return self

        def auth_with_creds(self, u, pw):
            self.mode = ("creds", u, pw); return self

    mod.ASFSession = _Session
    monkeypatch.setitem(sys.modules, "asf_search", mod)
    return mod


def test_build_session_token_first(fake_asf):
    sess, auth = sd.build_session(token="TOK", username="u", password="p")
    assert auth == "token" and sess.mode == ("token", "TOK")


def test_build_session_creds(fake_asf):
    sess, auth = sd.build_session(username="u", password="p")
    assert auth == "creds" and sess.mode == ("creds", "u", "p")


def test_build_session_no_creds_raises(fake_asf, monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError
    monkeypatch.setattr("netrc.netrc", _boom)
    with pytest.raises(SlcAuthError):
        sd.build_session()


# ── 전체 다운로드 흐름(네트워크 3지점 격리) ──
def test_download_recipe_flow(tmp_path, monkeypatch):
    _write_recipe(tmp_path, ["S1", "S2", "S3"])
    monkeypatch.setattr(sd, "build_session", lambda **k: (object(), "creds"))
    monkeypatch.setattr(sd, "_granule_search",
                        lambda names: [_prop("S1"), _prop("S2")])  # S3 누락
    got = {}
    monkeypatch.setattr(sd, "_download_urls",
                        lambda urls, out, session: got.update(urls=urls, out=out))

    r = download_recipe_slc(tmp_path, username="u", password="p")
    assert r.requested == 3 and r.selected == 2
    assert r.downloaded == 2 and r.skipped_existing == 0
    assert r.missing == ["S3"] and r.auth == "creds"
    assert len(got["urls"]) == 2
    assert r.gigabytes > 0


def test_download_skips_existing(tmp_path, monkeypatch):
    _write_recipe(tmp_path, ["S1", "S2"])
    (tmp_path / "SLC").mkdir()
    (tmp_path / "SLC" / "S1.zip").write_bytes(b"already")   # 기존 파일
    monkeypatch.setattr(sd, "build_session", lambda **k: (object(), "netrc"))
    monkeypatch.setattr(sd, "_granule_search", lambda names: [_prop("S1"), _prop("S2")])
    urls_downloaded = []
    monkeypatch.setattr(sd, "_download_urls",
                        lambda urls, out, session: urls_downloaded.extend(urls))

    r = download_recipe_slc(tmp_path)
    assert r.skipped_existing == 1 and r.downloaded == 1
    assert all("S2" in u for u in urls_downloaded)          # S1 은 스킵


def test_download_limit(tmp_path, monkeypatch):
    _write_recipe(tmp_path, ["S1", "S2", "S3"])
    monkeypatch.setattr(sd, "build_session", lambda **k: (object(), "token"))
    monkeypatch.setattr(sd, "_granule_search",
                        lambda names: [_prop("S1"), _prop("S2"), _prop("S3")])
    monkeypatch.setattr(sd, "_download_urls", lambda urls, out, session: None)
    r = download_recipe_slc(tmp_path, token="T", limit=2)
    assert r.selected == 2 and r.downloaded == 2


def test_download_auth_error_propagates(tmp_path, monkeypatch):
    _write_recipe(tmp_path, ["S1"])
    def _boom(**k):
        raise SlcAuthError("no creds")
    monkeypatch.setattr(sd, "build_session", _boom)
    with pytest.raises(SlcAuthError):
        download_recipe_slc(tmp_path)
