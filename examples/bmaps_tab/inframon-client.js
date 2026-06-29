/**
 * inframon InSAR API 클라이언트 — Bmaps "InSAR 위성 변위 분석" 탭 연동용.
 *
 * 프레임워크 비의존(순수 fetch, ES module). React/Vue/바닐라 어디서든 import 해서 쓴다.
 * 설계: docs/Bmaps_연동_인터페이스.md  /  서버: src/inframon/api
 *
 *   import { InframonClient } from "./inframon-client.js";
 *   const api = new InframonClient("http://insar-host:8000");
 *   const bridges = await api.listBridges();
 *   const pts = await api.points("KICT-2024-00137", { metric: "los", date: "latest" });
 */

export class InframonApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "InframonApiError";
    this.status = status; // 404 | 409 | 400 | 503 ...
    this.code = code;     // "not_found" | "schema_mismatch" | ...
  }
}

export class InframonClient {
  /** @param {string} baseUrl 예: "http://127.0.0.1:8000" */
  constructor(baseUrl) {
    this.base = baseUrl.replace(/\/$/, "") + "/api/v1";
  }

  async _get(path) {
    const res = await fetch(this.base + path, { headers: { Accept: "application/json" } });
    if (!res.ok) {
      let code = "http_error", msg = res.statusText;
      try {
        const body = await res.json();
        // app.py 오류 규약: {"error": {"code","message"}} 또는 FastAPI {"detail"}
        if (body.error) { code = body.error.code; msg = body.error.message; }
        else if (body.detail) { msg = body.detail; }
      } catch { /* 본문 없음 */ }
      throw new InframonApiError(res.status, code, msg);
    }
    return res.json();
  }

  /** 서버 상태 + 교량 수. */
  health() { return this._get("/health"); }

  /** 전체 교량 목록 [{bridge_id,name,wgs84_center,warning_level,cri_global_max,has_insar,...}]. */
  async listBridges() { return (await this._get("/bridges")).bridges; }

  /** 탭 헤더 요약(경보·최대CRI·관측기간·coherence). */
  summary(bridgeId) { return this._get(`/bridges/${encodeURIComponent(bridgeId)}/insar/summary`); }

  /** 변위 측점 배열. metric: "los"|"longitudinal", date: "latest"|시점인덱스. */
  points(bridgeId, { metric = "los", date = "latest" } = {}) {
    const q = `?metric=${metric}&date=${date}`;
    return this._get(`/bridges/${encodeURIComponent(bridgeId)}/insar/points${q}`);
  }

  /** 동일 데이터의 GeoJSON FeatureCollection(지도 라이브러리 직결용). */
  pointsGeoJSON(bridgeId, { metric = "los", date = "latest" } = {}) {
    const q = `?metric=${metric}&date=${date}`;
    return this._get(`/bridges/${encodeURIComponent(bridgeId)}/insar/points.geojson${q}`);
  }

  /** 측점 1개 시계열(LOS/종방향/성분/CRI/EI) — 점 클릭 상세. */
  series(bridgeId, pointId) {
    return this._get(`/bridges/${encodeURIComponent(bridgeId)}/insar/points/${pointId}/series`);
  }

  /** CRI 추세(시점별 최대). */
  cri(bridgeId) { return this._get(`/bridges/${encodeURIComponent(bridgeId)}/insar/cri`); }

  /** 4기능 공명·결합행렬 진단(시점 k, 기본 최신). */
  functionNetwork(bridgeId, k = null) {
    const q = k == null ? "" : `?k=${k}`;
    return this._get(`/bridges/${encodeURIComponent(bridgeId)}/insar/function-network${q}`);
  }

  // ── KAIA 핸드오프 다운로드(InSAR+PINN 통일 데이터 → VLM 입력) ──

  /** 변위 CSV(점×시점) URL. <a download> 또는 window.open 으로 바로 받게 한다. */
  exportCsvUrl(bridgeId) {
    return `${this.base}/bridges/${encodeURIComponent(bridgeId)}/insar/export.csv`;
  }

  /** VLM 입력 패키지(ZIP) URL. figures=false 면 그림 없이 가볍게. */
  vlmPackageUrl(bridgeId, { figures = true } = {}) {
    return `${this.base}/bridges/${encodeURIComponent(bridgeId)}/insar/vlm-package.zip?figures=${figures}`;
  }

  /** ZIP 패키지를 Blob 으로 받아온다(브라우저 다운로드 트리거용). */
  async vlmPackageBlob(bridgeId, { figures = true } = {}) {
    const res = await fetch(this.vlmPackageUrl(bridgeId, { figures }));
    if (!res.ok) throw new InframonApiError(res.status, "http_error", res.statusText);
    return res.blob();
  }
}

// ── 표시용 헬퍼(탭 공통 색/스케일) ─────────────────────────────
/** 경보 등급 → 배지 색. */
export const WARNING_COLORS = { 정상: "#2e7d32", 주의: "#f9a825", 경고: "#ef6c00", 위험: "#c62828" };

/** CRI(0~1) → 히트 색(초록→노랑→빨강). 지도 점 색칠용. */
export function criColor(cri) {
  if (cri == null) return "#888";
  const c = Math.max(0, Math.min(1, cri));
  const r = Math.round(255 * Math.min(1, c * 2));
  const g = Math.round(255 * Math.min(1, (1 - c) * 2));
  return `rgb(${r},${g},0)`;
}
