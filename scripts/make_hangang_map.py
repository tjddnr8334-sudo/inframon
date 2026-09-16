#!/usr/bin/env python3
"""한강 교량 지도 — OSM 위에 **우리가 돌린 결과**를 얹는다.

교량별 그림·트윈·표가 따로따로 흩어져 있어서, "한강에서 우리가 무엇을 했나"를 한 화면에
보여 줄 자리가 없었다. 이 지도가 그 자리다.

  · OSM 타일 위에 **데크선**(우리가 고른 그 선)과 **교면 PS 측점**(속도 색)을 그린다
  · 교량을 누르면 그 교량의 결과가 뜬다 — 측점 수·부재 결합·LOS 변위속도 ± 95% CI·
    보고서 판정과의 대조, 그리고 산출물 링크(3D 트윈 · 브리프 · 전과정 · 결과.md)
  · 레이어로 데크선/측점/판정 마커를 따로 끄고 켠다

숫자를 손으로 옮기지 않는다 — `bridge.json` · `twin.glb.meta.json` ·
`hangang_gnss_insar.json` 을 그대로 읽어 만든다. 다시 돌리면 그날 산출물로 갱신된다.

인터넷이 필요하다(OSM 타일 · Leaflet CDN). 오프라인 자리에서는 `docs/img/` 의 정적
그림들을 쓴다.

    python scripts/make_hangang_map.py

산출: docs/bridges/한강_지도.html
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def bridge_rows(root: Path, names: list[str], verdicts: dict) -> list[dict]:
    """교량마다 지도에 올릴 것 — 데크선·측점·결과 요약·산출물 링크."""
    out = []
    for nm in names:
        fo = root / nm
        bj = fo / "bridge.json"
        if not bj.exists():
            continue
        b = json.loads(bj.read_text(encoding="utf-8"))
        meta_p = fo / "twin.glb.meta.json"
        pts, n_bound = [], 0
        if meta_p.exists():
            m = json.loads(meta_p.read_text(encoding="utf-8"))
            for f in (m.get("features") or []):
                if f.get("lon") is None:
                    continue
                pts.append([round(f["lat"], 6), round(f["lon"], 6),
                            round(float(f.get("value") or 0.0), 2)])
                if f.get("element_globalid"):
                    n_bound += 1
        v = verdicts.get(nm) or {}
        ins = v.get("insar") or {}
        files = {k: (fo / n).name for k, n in
                 (("twin", "twin.viewer.html"), ("cri", "twin_cri.viewer.html"),
                  ("brief", "brief.png"), ("chain", "chain.png"),
                  ("twin_ps", "twin_ps.png"), ("md", "결과.md"))
                 if (fo / n).exists()}
        out.append({
            "name": nm,
            "deck": [[round(p[0], 6), round(p[1], 6)] for p in (b.get("geometry") or [])],
            "lat": b["lat"], "lon": b["lon"],
            "length_m": b.get("length_m"), "width_m": b.get("width_m"),
            "n_spans": b.get("n_spans"), "type": b.get("bridge_type"),
            "span_layout": (b.get("sources") or {}).get("span_layout", ""),
            "superstructure": (b.get("sources") or {}).get("superstructure", ""),
            "points": pts, "n_points": len(pts), "n_bound": n_bound,
            "v": ins.get("ann", {}).get("v"), "ci": ins.get("ann", {}).get("ci"),
            "n_epochs": ins.get("n_epochs"), "span": ins.get("span"),
            "verdict": v.get("verdict", ""), "trend": v.get("report_trend", ""),
            "agree": v.get("agree", ""), "gnss": bool(v.get("gnss")),
            "files": files,
        })
    return out


HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>한강 교량 — OSM 위의 InSAR 결과</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html,body{margin:0;height:100%%;font:13px/1.55 "Malgun Gothic",system-ui,sans-serif}
#map{position:absolute;inset:0}
.leaflet-popup-content{margin:10px 12px;min-width:270px}
.pt{font-weight:700;font-size:15px;color:#102A43;margin-bottom:2px}
.sub{color:#5A636B;font-size:11.5px;margin-bottom:7px}
table.kv{border-collapse:collapse;width:100%%;margin:2px 0 6px}
table.kv td{padding:2px 0;vertical-align:top}
table.kv td.k{color:#5A636B;width:82px;white-space:nowrap}
.ok{color:#2E7D32;font-weight:700}.no{color:#E06C2C;font-weight:700}
.lnk a{display:inline-block;margin:2px 6px 0 0;padding:2px 7px;border:1px solid #D4DAE0;
border-radius:4px;color:#1F6FB2;text-decoration:none;font-size:11.5px}
.lnk a:hover{background:#E7F0F8}
#panel{position:absolute;z-index:1000;left:12px;bottom:22px;background:rgba(255,255,255,.95);
border:1px solid #D4DAE0;border-radius:8px;padding:10px 12px;max-width:290px}
#panel h1{font-size:14px;margin:0 0 4px}#panel p{margin:3px 0;color:#5A636B;font-size:11.5px}
.bar{height:10px;border-radius:2px;margin:5px 0 2px;
background:linear-gradient(90deg,#2C4E89,#9FC3DD,#F7F7F7,#F2B27C,#C03028)}
.lab{display:flex;justify-content:space-between;color:#5A636B;font-size:10.5px}
</style></head><body>
<div id="map"></div>
<div id="panel">
  <h1>한강 교량 — OSM 위의 InSAR 결과</h1>
  <p>%(n)d개소 · 교면 측점 %(pts)d점 · 부재 결합 %(bound)d점</p>
  <p>점 색 = LOS 변위속도 [mm/yr]</p>
  <div class="bar"></div>
  <div class="lab"><span>%(vmin).1f</span><span>0</span><span>+%(vmax).1f</span></div>
  <p style="margin-top:7px">교량을 누르면 그 교량의 결과와 산출물 링크가 뜬다.</p>
</div>
<script>
const B = %(data)s;
const VMAX = %(vmax)f;
const map = L.map('map', {preferCanvas:true}).setView([37.54, 126.99], 12);
// 팝업이 화면 밖으로 잘리지 않게 — 오른쪽 레이어 컨트롤·왼쪽 범례를 피해 자동으로 민다
map.options.popupPane = undefined;
// 바탕지도 — 기본은 **Esri** 다. tile.openstreetmap.org 는 이용정책상 클라이언트에
// 따라 차단되어 지도 대신 'Access blocked' 타일이 깔리는 일이 있고(실제로 그랬다),
// CARTO 는 이제 키를 요구해 'API KEY REQUIRED' 워터마크가 찍힌다. Esri 는 키 없이
// 뜨므로 그것을 기본으로 두고, OSM 표준지도는 고를 수 있게 남긴다.
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/';
const ATTR_ESRI = 'Esri · HERE · Garmin · OpenStreetMap 기여자';
const base = {
  '일반 지도 (Esri)': L.tileLayer(ESRI + 'World_Street_Map/MapServer/tile/{z}/{y}/{x}',
    {maxZoom:19, attribution: ATTR_ESRI}),
  '지형 (Esri)': L.tileLayer(ESRI + 'World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
    {maxZoom:19, attribution: ATTR_ESRI}),
  '위성 영상 (Esri)': L.tileLayer(ESRI + 'World_Imagery/MapServer/tile/{z}/{y}/{x}',
    {maxZoom:19, attribution:'Esri · Maxar · Earthstar Geographics'}),
  'OSM 표준': L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    {maxZoom:19, attribution:'&copy; OpenStreetMap 기여자'}),
};
base['일반 지도 (Esri)'].addTo(map);
// 타일이 안 뜨면 이유를 화면에 적는다 — 빈 회색 화면만 보면 원인을 알 수 없다.
let tileFail = 0;
Object.values(base).forEach(l => l.on('tileerror', () => {
  if (++tileFail !== 8) return;
  const d = document.createElement('div');
  d.style.cssText = 'position:absolute;left:50%%;top:14px;transform:translateX(-50%%);'
    + 'z-index:1000;background:#fff3cd;border:1px solid #e0b34a;border-radius:8px;'
    + 'padding:10px 16px;font:600 13px/1.5 Malgun Gothic,sans-serif;color:#7a5b12';
  d.textContent = '바탕지도 타일을 못 불러왔습니다 — 인터넷 연결을 확인하세요. '
    + '교량선과 측점은 인터넷 없이도 그대로 보입니다.';
  document.body.appendChild(d);
}));

function col(v){                       // 발산형 — 파랑(멀어짐) ~ 빨강(가까워짐)
  const t = Math.max(-1, Math.min(1, (v||0)/VMAX));
  const stops = [[-1,[44,78,137]],[-0.5,[159,195,221]],[0,[247,247,247]],
                 [0.5,[242,178,124]],[1,[192,48,40]]];
  for(let i=0;i<stops.length-1;i++){
    const [a,ca]=stops[i], [b,cb]=stops[i+1];
    if(t>=a && t<=b){const f=(t-a)/(b-a||1);
      return `rgb(${ca.map((c,j)=>Math.round(c+(cb[j]-c)*f)).join(',')})`;}
  }
  return '#777';
}
const POP = {autoPan:true, autoPanPadding:[180, 60], maxWidth:330, keepInView:true};
const decks = L.layerGroup().addTo(map);
const pts   = L.layerGroup().addTo(map);
const marks = L.layerGroup().addTo(map);

function popup(b){
  const f = b.files || {};
  const lk = [];
  if(f.twin)    lk.push(`<a href="${b.name}/${f.twin}" target="_blank">3D 트윈</a>`);
  if(f.cri)     lk.push(`<a href="${b.name}/${f.cri}" target="_blank">CRI 3D</a>`);
  if(f.chain)   lk.push(`<a href="${b.name}/${f.chain}" target="_blank">전 과정</a>`);
  if(f.twin_ps) lk.push(`<a href="${b.name}/${f.twin_ps}" target="_blank">평면·입면·3D</a>`);
  if(f.brief)   lk.push(`<a href="${b.name}/${f.brief}" target="_blank">브리프</a>`);
  if(f.md)      lk.push(`<a href="${b.name}/${encodeURIComponent(f.md)}" target="_blank">결과.md</a>`);
  const vel = (b.v==null) ? '산출 없음'
    : `${b.v>=0?'+':''}${b.v.toFixed(2)} ± ${b.ci.toFixed(2)} mm/yr`;
  const ok = (b.agree||'').startsWith('일치');
  return `<div class="pt">${b.name}${b.gnss?' ◆GNSS':''}</div>
  <div class="sub">${b.type||''} · 연장 ${b.length_m?b.length_m.toFixed(0):'?'} m ·
    폭 ${b.width_m?b.width_m.toFixed(1):'?'} m · ${b.n_spans||'?'}경간</div>
  <table class="kv">
   <tr><td class="k">교면 측점</td><td>${b.n_points}점 · 부재 결합 ${b.n_bound}점</td></tr>
   <tr><td class="k">LOS 속도</td><td>${vel}${b.n_epochs?` <span style="color:#5A636B">(${b.n_epochs}시점 ${b.span?b.span[0]+'~'+b.span[1]:''})</span>`:''}</td></tr>
   <tr><td class="k">보고서</td><td>${b.verdict||'—'}<br><span style="color:#5A636B">${b.trend||''}</span></td></tr>
   <tr><td class="k">대조</td><td class="${ok?'ok':'no'}">${b.agree||'—'}</td></tr>
   <tr><td class="k">경간 배치</td><td style="color:#5A636B">${b.span_layout||'—'}</td></tr>
   ${b.superstructure?`<tr><td class="k">상부구조</td><td style="color:#5A636B">${b.superstructure}</td></tr>`:''}
  </table>
  <div class="lnk">${lk.join('')}</div>`;
}

for(const b of B){
  if(b.deck.length>1)
    L.polyline(b.deck, {color:'#123A5E', weight:4, opacity:.85})
     .bindPopup(()=>popup(b), POP).addTo(decks);
  for(const [la,lo,v] of b.points)
    L.circleMarker([la,lo], {radius:3.2, color:'#111', weight:.5,
      fillColor:col(v), fillOpacity:.95}).bindPopup(()=>popup(b), POP).addTo(pts);
  const ok = (b.agree||'').startsWith('일치');
  L.marker([b.lat,b.lon], {icon:L.divIcon({className:'', iconSize:[150,20],
    html:`<div style="white-space:nowrap;font-weight:700;color:#102A43;
      text-shadow:0 0 3px #fff,0 0 3px #fff,0 0 3px #fff">
      ${b.name} <span style="color:${ok?'#2E7D32':'#E06C2C'}">${ok?'○':'△'}</span>
      <span style="font-weight:400;color:#5A636B">${b.n_points}점</span></div>`})})
    .bindPopup(()=>popup(b), POP).addTo(marks);
}
L.control.layers(base, {'데크선':decks, '교면 측점':pts, '교량명·판정':marks},
                 {collapsed:false}).addTo(map);
const all = B.flatMap(b=>b.deck.length?b.deck:[[b.lat,b.lon]]);
if(all.length) map.fitBounds(L.latLngBounds(all), {padding:[40,40]});
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    ap.add_argument("--list", default="docs/bridges/hangang16.json")
    ap.add_argument("--verdicts", default="docs/bridges/hangang_gnss_insar.json")
    ap.add_argument("--out", default="docs/bridges/한강_지도.html")
    a = ap.parse_args()

    root = Path(a.root)
    names = [x["name"] for x in json.loads(Path(a.list).read_text(encoding="utf-8"))]
    vj = Path(a.verdicts)
    vd = {}
    if vj.exists():
        for r in (json.loads(vj.read_text(encoding="utf-8")).get("bridges") or []):
            vd[r["name"]] = r
    rows = bridge_rows(root, names, vd)
    if not rows:
        print("교량 산출물을 못 찾았다")
        return 1

    vals = [p[2] for b in rows for p in b["points"]]
    vmax = max(2.0, round(float(sorted(map(abs, vals))[int(len(vals) * 0.95)]), 1)) \
        if vals else 3.0
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(HTML % {
        "data": json.dumps(rows, ensure_ascii=False),
        "n": len(rows), "pts": sum(b["n_points"] for b in rows),
        "bound": sum(b["n_bound"] for b in rows),
        "vmax": vmax, "vmin": -vmax,
    }, encoding="utf-8")
    print(f"wrote {out}  ({len(rows)}개소 · 측점 {sum(b['n_points'] for b in rows)}점 · "
          f"색 범위 ±{vmax:g} mm/yr)")
    for b in rows:
        print(f"  {b['name']:<9}{b['n_points']:>5}점 · 결합 {b['n_bound']:>4} · "
              f"{'링크 ' + str(len(b['files'])) + '개':<9} {b['agree']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
