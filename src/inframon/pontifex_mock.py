"""Pontifex 인제스트 API **모의 서버** — 실서버(Docker) 없이 전송 경로를 끝까지 검증한다.

파트너 플랫폼은 GeoDjango+PostGIS+Docker 라 이 PC 에 못 띄웠다. 그래서 ⑭ BMAP 전송이
"코드는 됐다"에서 멈춰 있었다. 여기서는 파트너 문서(4-A JSON API)의 계약 그대로 받는
표준 라이브러리 HTTP 서버를 둔다:

  POST   /api/ingest/bridge/   {name, lon, lat, ...}             → {id, seq_no, region, detail_url}
  POST   /api/ingest/sensing/  {summary_records[], member_records[]} → {summary_n, member_n}
  DELETE /api/ingest/sensing/?bridge_id=&source=                  → {summary_n, member_n}
  GET    /api/bridges/search?q=            이름 검색(좌표 없음 — 실 API 와 같다)
  GET    /api/regions/search?q=            시·군·구 코드
  GET    /api/bridges.geojson?region=CODE  지역 교량(id·이름·좌표)
  GET    /api/bridge/<id>/timeseries       올라간 시계열
  GET    /api/bridges/ · /api/bridges/<id>/sensing/  (모의 서버 전용) 등록 목록 · 받은 레코드

`X-Pontifex-Token` 헤더를 검사한다(문서와 동일). 받은 것은 메모리와 `<state.json>` 에
남겨 유저가 무엇이 전송됐는지 그대로 볼 수 있다.

    python -m inframon --pontifex-mock 38000            # 서버 띄우기
    python -m inframon --pontifex-push project.h5 --pontifex-base http://127.0.0.1:38000 ...

이것은 계약 검증용이다. 실 플랫폼의 화면·DB 는 파트너 쪽에서 확인해야 한다.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 파트너 4-A 계약의 필수 필드(실 서버 ingest_views 와 같다 — CRI 는 선택이다)
_REQUIRED_SUMMARY = ("bridge_id", "observed_at", "warning_level")
_REQUIRED_MEMBER = ("bridge_id", "member_type", "warning_level")


class _State:
    def __init__(self, path: Path | None, token: str | None, bridges: list[dict] | None = None):
        self.path, self.token = path, token
        # 미리 넣어 둔 교량 = 플랫폼이 이미 갖고 있는 대장. {id,name,lat,lon,region:{code,name},addr1}
        self.bridges: list[dict] = [dict(b) for b in bridges or []]
        self.sensing: dict[int, dict] = {}
        self.lock = threading.Lock()

    def save(self) -> None:
        if self.path:
            self.path.write_text(json.dumps(
                {"bridges": self.bridges, "sensing": self.sensing}, ensure_ascii=False, indent=1),
                encoding="utf-8")


def _handler(state: _State):
    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _auth(self) -> bool:
            if state.token and self.headers.get("X-Pontifex-Token") != state.token:
                self._send(401, {"error": "invalid token"})
                return False
            return True

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length", "0") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

        def do_GET(self):                                    # noqa: N802
            if not self._auth():
                return
            from urllib.parse import parse_qs, urlsplit
            u = urlsplit(self.path)
            path, q = u.path.rstrip("/"), {k: v[0] for k, v in parse_qs(u.query).items()}
            if path == "/api/bridges":
                self._send(200, {"bridges": state.bridges})
            elif path == "/api/bridges/search":
                want = q.get("q", "")
                self._send(200, [
                    {"id": b["id"], "name": b["name"],
                     "region_name": f"{b.get('addr1') or ''} {(b.get('region') or {}).get('name', '')}".strip()}
                    for b in state.bridges if want and want in str(b.get("name"))])
            elif path == "/api/regions/search":
                seen = {(b["region"]["code"], b["region"]["name"]) for b in state.bridges
                        if (b.get("region") or {}).get("code")}
                self._send(200, [{"code": c, "name": n, "level": 2} for c, n in sorted(seen)
                                 if q.get("q", "") in n])
            elif path == "/api/bridges.geojson":
                if any(k.startswith("bbox") for k in q):     # 실 서버 1.0 은 bbox 에서 500 이다
                    return self._send(500, {"error": "Unsupported lookup 'bbox'"})
                self._send(200, {"type": "FeatureCollection", "features": [
                    {"type": "Feature",
                     "geometry": {"type": "Point", "coordinates": [b["lon"], b["lat"]]},
                     "properties": {"id": b["id"], "name": b["name"]}}
                    for b in state.bridges
                    if not q.get("region") or (b.get("region") or {}).get("code") == q["region"]]})
            elif path.startswith("/api/bridge/") and path.endswith("/timeseries"):
                bid = int(path.split("/")[3])
                self._send(200, [
                    {"observed_at": r["observed_at"], "source": r.get("source", "inframon"),
                     "warning_level": r["warning_level"], "cri_global_max": r.get("cri_global_max")}
                    for r in state.sensing.get(bid, {}).get("summary_records", [])])
            elif path.startswith("/api/bridges/") and path.endswith("/sensing"):
                bid = int(path.split("/")[3])
                self._send(200, state.sensing.get(bid, {"summary_records": [], "member_records": []}))
            else:
                self._send(404, {"error": "not found"})

        def do_DELETE(self):                                 # noqa: N802
            if not self._auth():
                return
            from urllib.parse import parse_qs, urlsplit
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path.rstrip("/") != "/api/ingest/sensing":
                return self._send(404, {"error": "not found"})
            if not q.get("bridge_id") and not q.get("source"):
                return self._send(400, {"error": "scope_required"})
            n = {"summary_records": 0, "member_records": 0}
            with state.lock:
                for bid, slot in state.sensing.items():
                    if q.get("bridge_id") and int(q["bridge_id"]) != int(bid):
                        continue
                    for key in n:
                        keep = [r for r in slot[key]
                                if q.get("source") and r.get("source", "inframon") != q["source"]]
                        n[key] += len(slot[key]) - len(keep)
                        slot[key] = keep
                state.save()
            self._send(200, {"summary_n": n["summary_records"], "member_n": n["member_records"]})

        def do_POST(self):                                   # noqa: N802
            if not self._auth():
                return
            body = self._body()
            with state.lock:
                if self.path.rstrip("/") == "/api/ingest/bridge":
                    miss = [k for k in ("name", "lat", "lon") if k not in body]
                    if miss:
                        return self._send(400, {"error": f"missing {miss}"})
                    bid = max([40000] + [int(b["id"]) for b in state.bridges]) + 1
                    # 실 API 는 region 을 객체로 돌려준다(name·sigungu) — 클라이언트가 .get 한다
                    rec = {**body, "id": bid, "seq_no": bid,
                           "region": {"name": body.get("addr1") or "미지정",
                                      "sigungu": body.get("addr2") or ""},
                           "detail_url": f"/bridge/{bid}/"}
                    state.bridges.append(rec)
                    state.save()
                    return self._send(201, rec)
                if self.path.rstrip("/") == "/api/ingest/sensing":
                    s_recs = body.get("summary_records") or []
                    m_recs = body.get("member_records") or []
                    for r in s_recs:
                        miss = [k for k in _REQUIRED_SUMMARY if k not in r]
                        if miss:
                            return self._send(400, {"error": f"summary missing {miss}"})
                    for r in m_recs:
                        miss = [k for k in _REQUIRED_MEMBER if k not in r]
                        if miss:
                            return self._send(400, {"error": f"member missing {miss}"})
                    for key, recs in (("summary_records", s_recs), ("member_records", m_recs)):
                        for r in recs:
                            slot = state.sensing.setdefault(
                                int(r["bridge_id"]), {"summary_records": [], "member_records": []})
                            if key == "summary_records":     # 실 서버처럼 (소스, 관측일)로 덮어쓴다
                                slot[key] = [x for x in slot[key] if (
                                    x.get("source", "inframon"), x["observed_at"]) != (
                                    r.get("source", "inframon"), r["observed_at"])]
                            slot[key].append(r)
                    state.save()
                    return self._send(200, {"summary_n": len(s_recs), "member_n": len(m_recs)})
            self._send(404, {"error": "not found"})

        def log_message(self, fmt, *args):                  # noqa: D102 — 조용히
            pass

    return H


def serve(port: int = 38000, *, token: str | None = None, state_path: str | Path | None = None,
          bind: str = "127.0.0.1", bridges: list[dict] | None = None) -> ThreadingHTTPServer:
    """서버를 만들어 돌려준다(호출자가 serve_forever / shutdown). 기본 바인딩은 localhost —
    파트너 전달본 검토에서 지적한 0.0.0.0 노출을 여기서는 하지 않는다."""
    st = _State(Path(state_path) if state_path else None, token, bridges)
    srv = ThreadingHTTPServer((bind, port), _handler(st))
    srv.state = st                                           # type: ignore[attr-defined]
    return srv


def serve_forever(port: int = 38000, **kw) -> None:
    srv = serve(port, **kw)
    print(f"Pontifex 모의 서버: http://{srv.server_address[0]}:{srv.server_address[1]}  "
          f"(계약 검증용 · Ctrl+C 로 종료)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
