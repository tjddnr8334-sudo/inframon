/**
 * VWorld 지도 어댑터 — inframon InSAR 측점을 VWorld(브이월드) 지도에 렌더링.
 *
 * VWorld Map API 2.0 은 OpenLayers 기반이라, 이 어댑터는 OpenLayers(`ol`) + VWorld 타일
 * 소스로 구성한다. 따라서:
 *   - 단독 데모: createVWorldMap() 으로 VWorld 베이스맵을 새로 만든다.
 *   - Bmaps 이식: Bmaps 가 이미 가진 ol.Map(또는 vw.ol3.Map 의 내부 ol.Map) 인스턴스를
 *     InsarPointLayer 에 넘기면 그 위에 측점 레이어만 얹는다.
 *
 * 전제: 전역 `ol`(OpenLayers)이 로드되어 있어야 한다(VWorld 2.0 스크립트 또는 ol.js).
 *       VWorld 타일은 apiKey + 호출 도메인 등록(vworld.kr)이 필요하다.
 *
 * API 좌표는 WGS84(lat, lon) → 지도 투영(EPSG:3857)로 ol.proj.fromLonLat 변환.
 */

import { criColor } from "./inframon-client.js";

function ol() {
  const o = globalThis.ol;
  if (!o) {
    throw new Error(
      "OpenLayers(전역 ol)가 없습니다. VWorld 2.0 API는 OpenLayers 기반입니다 — " +
      "VWorld 스크립트 또는 ol.js 를 먼저 로드하세요. (번들러 사용 시 globalThis.ol = ol)",
    );
  }
  return o;
}

// VWorld 제공 레이어 종류.
export const VWORLD_LAYERS = {
  base: "Base", gray: "gray", midnight: "midnight", satellite: "Satellite", hybrid: "Hybrid",
};

/** VWorld WMTS REST 타일 레이어. 표준 XYZ({z}/{y}/{x}) 스킴. */
export function vworldTileLayer({ apiKey, layer = "base" }) {
  const o = ol();
  const name = VWORLD_LAYERS[layer] ?? layer;
  const url = `https://api.vworld.kr/req/wmts/1.0.0/${apiKey}/${name}/{z}/{y}/{x}.png`;
  return new o.layer.Tile({
    source: new o.source.XYZ({ url, crossOrigin: "anonymous", attributions: "© VWorld" }),
  });
}

/** 단독 데모용 — VWorld 베이스맵을 가진 ol.Map 생성. center=[lon,lat]. */
export function createVWorldMap(target, { apiKey, layer = "base", center = [127.8, 36.5], zoom = 7 } = {}) {
  const o = ol();
  return new o.Map({
    target,
    layers: [vworldTileLayer({ apiKey, layer })],
    view: new o.View({ center: o.proj.fromLonLat(center), zoom }),
  });
}

/**
 * InSAR 측점 벡터 레이어 — 주어진 ol.Map 위에 측점을 그리고 갱신/클릭/툴팁을 관리.
 *
 *   const layer = new InsarPointLayer(map, { onPick: (id) => loadSeries(id) });
 *   layer.update(points, { colorBy: "cri" });   // API points 배열
 *   layer.fit();
 */
export class InsarPointLayer {
  constructor(map, { onPick = null, tooltip = true } = {}) {
    const o = ol();
    this.map = map;
    this.onPick = onPick;
    this.source = new o.source.Vector();
    this.layer = new o.layer.Vector({ source: this.source, zIndex: 10 });
    map.addLayer(this.layer);

    // 클릭 → 측점 선택
    map.on("singleclick", (evt) => {
      map.forEachFeatureAtPixel(evt.pixel, (f) => {
        const id = f.get("point_id");
        if (id != null && this.onPick) { this.onPick(id); return true; }
        return false;
      });
    });

    if (tooltip) this._initTooltip();
  }

  _initTooltip() {
    const o = ol();
    const el = document.createElement("div");
    Object.assign(el.style, {
      position: "absolute", background: "rgba(0,0,0,.8)", color: "#fff", padding: "4px 8px",
      borderRadius: "4px", fontSize: "12px", pointerEvents: "none", whiteSpace: "nowrap", display: "none",
    });
    this.map.getTargetElement().appendChild(el);
    const overlay = new o.Overlay({ element: el, offset: [10, 0], positioning: "center-left" });
    this.map.addOverlay(overlay);
    this.map.on("pointermove", (evt) => {
      if (evt.dragging) return;
      const f = this.map.forEachFeatureAtPixel(evt.pixel, (ft) => ft);
      const target = this.map.getTargetElement();
      if (f && f.get("point_id") != null) {
        el.style.display = "block";
        el.innerHTML = `#${f.get("point_id")} ${f.get("member")}<br>변위 ${f.get("value_mm")} mm · CRI ${f.get("cri") ?? "-"}`;
        overlay.setPosition(evt.coordinate);
        target.style.cursor = "pointer";
      } else {
        el.style.display = "none";
        target.style.cursor = "";
      }
    });
  }

  /** API points 배열로 레이어 재구성. colorBy: "cri" | "value". */
  update(points, { colorBy = "cri" } = {}) {
    const o = ol();
    this.source.clear();
    const vmax = Math.max(1e-6, ...points.map((p) => Math.abs(p.value_mm)));
    let skipped = 0;
    for (const p of points) {
      // 위경도 범위 방어(합성/잘못된 SRS 좌표 걸러냄).
      if (!(p.lat >= -90 && p.lat <= 90 && p.lon >= -180 && p.lon <= 180)) { skipped++; continue; }
      const color = colorBy === "cri" ? criColor(p.cri) : criColor(Math.abs(p.value_mm) / vmax);
      const f = new o.Feature({ geometry: new o.geom.Point(o.proj.fromLonLat([p.lon, p.lat])) });
      f.setProperties({ point_id: p.point_id, member: p.member, value_mm: p.value_mm, cri: p.cri });
      f.setStyle(new o.style.Style({
        image: new o.style.Circle({
          radius: 5,
          fill: new o.style.Fill({ color }),
          stroke: new o.style.Stroke({ color: "#333", width: 1 }),
        }),
      }));
      this.source.addFeature(f);
    }
    return { rendered: points.length - skipped, skipped };
  }

  /** 측점 범위에 뷰 맞춤. */
  fit(padding = 60) {
    const ext = this.source.getExtent();
    if (ext && Number.isFinite(ext[0]) && ext[0] !== Infinity) {
      this.map.getView().fit(ext, { padding: [padding, padding, padding, padding], maxZoom: 18, duration: 300 });
    }
  }

  clear() { this.source.clear(); }

  /** 지도에서 레이어 제거(정리). */
  dispose() { this.map.removeLayer(this.layer); }
}
