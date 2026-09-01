"""⑭ Pontifex 연동 — 감사 게이트·레코드 계약·HTTP 경로.

남의 플랫폼에 올리는 일은 되돌리기 어렵다. 그래서 (1) 무효 산출물은 기본으로 막고,
(2) 레코드가 플랫폼이 문서로 약속한 모양과 맞고, (3) 실패했을 때 사람이 다음에 무엇을
해야 하는지 말하는지를 고정한다. HTTP 는 로컬 목 서버로 확인한다(Docker 불필요).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import h5py
import numpy as np
import pytest

from inframon.pontifex import (LEVELS, PontifexError, build_records, push,
                               register_bridge)


def _project(path, *, cri=None, dates=(b"20240107", b"20240119", b"20240131"),
             member=None, source="SNAP(Windows) star-network snaphu-unwrapped → LOS",
             lonlat=(127.0, 37.0)):
    # dates 가 비면 date_labels 를 안 쓰는 합성 산출물 — CRI 시점은 그대로 둔다.
    n, m = (len(member) if member is not None else 6), (len(dates) or 3)
    if cri is None:
        cri = np.linspace(0.0, 0.9, n * m).reshape(n, m)
    with h5py.File(path, "w") as f:
        ins = f.create_group("insar")
        ins.create_dataset("los", data=np.full((n, m), 30.0, dtype=np.float32))
        ins.create_dataset("xyz", data=np.column_stack(
            [np.full(n, lonlat[0]), np.full(n, lonlat[1]), np.zeros(n)]))
        if dates:
            ins.create_dataset("date_labels", data=np.array(dates, dtype="S8"))
        if member is not None:
            ins.create_dataset("member", data=np.asarray(member, dtype=np.int32))
        ins.attrs["track_source"] = json.dumps({"path": "t.h5", "attrs": {"source": source}})
        fr = f.create_group("fram")
        fr.create_dataset("CRI", data=np.asarray(cri, dtype=np.float32))
        fr.attrs["reference_range"] = json.dumps({"worst_cri": float(np.max(cri))})
        f.create_group("pinn").attrs["inputs"] = json.dumps({"total_length_m": 100.0})
    return path


# ── 레코드 계약 ─────────────────────────────────────────────────────────
def test_records_have_every_required_field(tmp_path):
    """플랫폼 필수 필드(bridge_id·observed_at·warning_level)가 빠지면 400 이 난다."""
    recs = build_records(_project(tmp_path / "p.h5"), 40001)
    assert len(recs["summary_records"]) == 3            # 시점마다 한 건
    for r in recs["summary_records"]:
        assert r["bridge_id"] == 40001 and r["source"] == "inframon"
        assert r["observed_at"].count("-") == 2         # ISO YYYY-MM-DD
        assert r["warning_level"] in (0, 1, 2, 3)
        assert 0.0 <= r["cri_global_max"] <= 1.0


def test_observed_at_is_the_real_acquisition_date(tmp_path):
    recs = build_records(_project(tmp_path / "p.h5"), 1)
    assert [r["observed_at"] for r in recs["summary_records"]] == [
        "2024-01-07", "2024-01-19", "2024-01-31"]


def test_synthetic_output_without_dates_is_refused(tmp_path):
    """관측일이 없는 합성 산출물에 오늘 날짜를 붙여 올리면 가짜 관측이 된다."""
    with pytest.raises(PontifexError, match="관측일"):
        build_records(_project(tmp_path / "p.h5", dates=()), 1)


def test_member_records_use_platform_enum(tmp_path):
    p = _project(tmp_path / "p.h5", member=[0, 0, 1, 1, 2, 3])
    recs = build_records(p, 1)
    kinds = {r["member_type"] for r in recs["member_records"]}
    assert kinds <= {"deck", "pier", "abutment", "bearing"} and kinds
    for r in recs["member_records"]:
        assert r["warning_level"] in (0, 1, 2, 3)


def test_warning_level_follows_fram_grades():
    assert LEVELS == {"정상": 0, "주의": 1, "경고": 2, "위험": 3}


def test_level_thresholds_map_cri(tmp_path):
    cri = np.array([[0.1, 0.45, 0.7, 0.95]] * 2)
    p = _project(tmp_path / "p.h5", cri=cri,
                 dates=(b"20240107", b"20240119", b"20240131", b"20240212"))
    got = [r["warning_level"] for r in build_records(p, 1)["summary_records"]]
    assert got == [0, 1, 2, 3]


# ── 감사 게이트 ─────────────────────────────────────────────────────────
def test_unreportable_artifact_is_blocked(tmp_path):
    """래핑 위상 산출물은 기본적으로 올리지 못한다."""
    p = _project(tmp_path / "w.h5", source="SNAP star-network wrapped-phase → LOS")
    with pytest.raises(PontifexError) as e:
        push(p, 1, dry_run=True, target=(37.0, 127.0))
    msg = str(e.value)
    assert "보고 불가" in msg and "언래핑" in msg
    assert "--pontifex-force" in msg                     # 다음 행동을 알려준다


def test_force_allows_but_keeps_the_reason(tmp_path):
    p = _project(tmp_path / "w.h5", source="SNAP star-network wrapped-phase → LOS")
    r = push(p, 1, dry_run=True, allow_unreportable=True, target=(37.0, 127.0))
    assert r.audit_verdict == "보고 불가" and r.warnings   # 사유가 사라지지 않는다


def test_dry_run_does_not_touch_network(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("dry-run 인데 전송했다")

    monkeypatch.setattr("inframon.pontifex._post", _boom)
    r = push(_project(tmp_path / "p.h5"), 1, dry_run=True, target=(37.0, 127.0))
    assert r.dry_run and r.summary_n == 3


# ── HTTP 경로 (로컬 목 서버) ─────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    received: dict = {}

    def _read(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):                                   # noqa: N802
        body = self._read()
        _Handler.received = {"path": self.path, "body": body,
                             "token": self.headers.get("X-Pontifex-Token")}
        if self.path.endswith("/api/ingest/bridge/"):
            out = {"id": 40001, "seq_no": 33121, "name": body.get("name"),
                   "region": {"code": "11110", "name": "강남구"},
                   "detail_url": "/bridge/40001/"}
        else:
            out = {"summary_n": len(body.get("summary_records", [])),
                   "member_n": len(body.get("member_records", [])),
                   "affected_bridges": [body.get("summary_records", [{}])[0]
                                        .get("bridge_id")], "errors": []}
        data = json.dumps(out).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):                           # 테스트 출력 오염 방지
        pass


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_push_sends_records_and_token(tmp_path, server):
    p = _project(tmp_path / "p.h5")
    r = push(p, 40001, base=server, token="secret", target=(37.0, 127.0))
    assert r.summary_n == 3
    assert _Handler.received["path"] == "/api/ingest/sensing/"
    assert _Handler.received["token"] == "secret"
    assert _Handler.received["body"]["summary_records"][0]["bridge_id"] == 40001


def test_register_bridge_returns_platform_id(server):
    got = register_bridge("봉동교(상)", 37.5665, 126.9780, base=server)
    assert got["id"] == 40001
    body = _Handler.received["body"]
    assert body["lat"] == 37.5665 and body["lon"] == 126.9780   # WGS84 십진도 그대로


def test_unreachable_platform_explains_next_step(tmp_path):
    p = _project(tmp_path / "p.h5")
    with pytest.raises(PontifexError, match="docker compose ps"):
        push(p, 1, base="http://127.0.0.1:9", target=(37.0, 127.0))
