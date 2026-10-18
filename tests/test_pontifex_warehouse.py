"""Pontifex 창고 연동 — 부재 GUID 레코드 · 법정 근거표 · IFC · 교량관리번호를 한 번에 보낸다."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("yaml")

from inframon.pontifex import element_records, push_bundle  # noqa: E402
from tests.test_pontifex import _project  # noqa: E402

GUID = "2PYG26B$_LD5VD2FU_CUC8"


def _folder(tmp_path):
    p = _project(tmp_path / "project.h5")
    (tmp_path / "bridge.json").write_text(json.dumps(
        {"name": "시험교", "length_m": 108, "n_spans": 4, "max_span_m": 27, "bridge_type": "girder"}),
        encoding="utf-8")
    (tmp_path / "legal_inputs.json").write_text(json.dumps(
        {"safety_grade": "C", "inspections": {"정밀안전점검": "2025-03-02"}}), encoding="utf-8")
    (tmp_path / "시험교_elements.json").write_text(json.dumps(
        {"elements": [{"guid": GUID, "name": "S1", "member": "deck"}]}), encoding="utf-8")
    (tmp_path / "twin_cri.glb.meta.json").write_text(json.dumps({
        "binding": {"key": "element_globalid"},
        "features": [{"element_globalid": GUID, "value": v} for v in (0.2, 0.7, 0.4)]}),
        encoding="utf-8")
    (tmp_path / "시험교_proxy.ifc").write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
    return p


def test_element_records_take_worst_point_per_guid(tmp_path):
    _folder(tmp_path)
    recs = element_records(tmp_path / "twin_cri.glb.meta.json", tmp_path / "시험교_elements.json",
                           7, [0.3, 0.6, 0.85])
    assert len(recs) == 1
    r = recs[0]
    assert r["ifc_guid"] == r["member_id"] == GUID and r["member_type"] == "deck"
    assert r["cri_value"] == 0.7 and r["warning_level"] == 2
    assert r["metrics"]["PointCount"] == 3 and r["metrics"]["CRIMedian"] == 0.4


class _H(BaseHTTPRequestHandler):
    calls: list = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        _H.calls.append((self.path, self.headers.get("Content-Type"), body))
        if self.path.startswith("/api/ingest/sensing/"):
            d = json.loads(body)
            out = {"summary_n": len(d["summary_records"]), "member_n": len(d["member_records"])}
        elif self.path.startswith("/api/ingest/ifc/"):
            out = {"created": True, "ifc": {"element_count": 0}, "match_counts": {}}
        else:
            out = {"bmaps_id": json.loads(body)["bmaps_id"]}
        data = json.dumps(out).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    _H.calls = []
    httpd = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_bundle_sends_sensing_legal_ifc_and_link(tmp_path, server):
    p = _folder(tmp_path)
    res, extra = push_bundle(p, 7, base=server, bmaps_id="KICT-0007", allow_unreportable=True)
    paths = [c[0].split("?")[0] for c in _H.calls]
    assert paths == ["/api/ingest/sensing/", "/api/ingest/ifc/", "/api/ingest/bmaps-link/"]
    sensing = json.loads(_H.calls[0][2])
    assert sensing["member_records"][0]["ifc_guid"] == GUID          # 그룹이 아니라 부재 GUID
    legal = sensing["summary_records"][-1]["summary_json"]["legal"]
    assert legal["facility_class"]["class"] == 2                     # 연장 108m → 2종
    assert legal["safety_grade"]["grade"] == "C"                     # legal_inputs.json 반영
    assert "bridge_id=7" in _H.calls[1][0] and _H.calls[1][1] == "application/octet-stream"
    assert res.member_n == 1 and extra["n_element_records"] == 1


def test_bundle_dry_run_sends_nothing(tmp_path, server):
    p = _folder(tmp_path)
    res, extra = push_bundle(p, 7, base=server, dry_run=True, allow_unreportable=True)
    assert _H.calls == [] and res.dry_run and extra["legal"]["schema"] == "inframon.legal/1.0"
