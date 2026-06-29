/**
 * InsarTab — Bmaps 가 React 기반일 때 "InSAR 위성 변위 분석" 탭 컴포넌트 예제.
 *
 * 데이터 페칭/상태 패턴만 보여주고, 지도 렌더링은 Bmaps 의 기존 GIS 라이브러리에
 * 위임하도록 `renderMap` prop 으로 분리했다(OpenLayers/Leaflet/VWorld 등 무엇이든).
 * 시각화는 standalone `insar-tab.html` 을 시각 레퍼런스로 참고.
 *
 *   import InsarTab from "./InsarTab.jsx";
 *   <InsarTab apiBase="http://insar-host:8000" bridgeId={selectedBridgeId}
 *             renderMap={(points, { metric, colorOf, onPick }) => <YourMap .../>} />
 */
import { useCallback, useEffect, useState } from "react";
import { InframonClient, WARNING_COLORS, criColor } from "./inframon-client.js";

export default function InsarTab({ apiBase, bridgeId, renderMap }) {
  const [api] = useState(() => new InframonClient(apiBase));
  const [summary, setSummary] = useState(null);
  const [cri, setCri] = useState(null);
  const [metric, setMetric] = useState("los");
  const [dateIndex, setDateIndex] = useState(null);
  const [points, setPoints] = useState([]);
  const [series, setSeries] = useState(null);
  const [error, setError] = useState(null);

  // 교량 선택 시 요약 + CRI 추세 로드
  useEffect(() => {
    if (!bridgeId) return;
    let alive = true;
    setError(null); setSeries(null);
    Promise.all([api.summary(bridgeId), api.cri(bridgeId)])
      .then(([s, c]) => { if (!alive) return; setSummary(s); setCri(c); setDateIndex(c.dates.length - 1); })
      .catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [api, bridgeId]);

  // 지표/시점 변경 시 측점 로드
  useEffect(() => {
    if (!bridgeId || dateIndex == null) return;
    let alive = true;
    api.points(bridgeId, { metric, date: dateIndex })
      .then((d) => alive && setPoints(d.points))
      .catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [api, bridgeId, metric, dateIndex]);

  const pickPoint = useCallback((pointId) => {
    api.series(bridgeId, pointId).then(setSeries).catch((e) => setError(e.message));
  }, [api, bridgeId]);

  // KAIA 핸드오프: VLM 패키지 ZIP 을 Blob 으로 받아 브라우저 다운로드 트리거.
  const downloadVlm = useCallback(async () => {
    try {
      const blob = await api.vlmPackageBlob(bridgeId, { figures: true });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `${bridgeId}_vlm_package.zip`;
      a.click(); URL.revokeObjectURL(url);
    } catch (e) { setError(e.message); }
  }, [api, bridgeId]);

  if (!bridgeId) return <div>교량을 선택하세요.</div>;
  const level = summary?.warning?.level ?? "—";

  return (
    <div className="insar-tab">
      {/* 헤더: 경보 배지 + 요약 */}
      <header style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <span style={{ background: WARNING_COLORS[level] ?? "#888", color: "#fff",
          padding: "4px 12px", borderRadius: 14, fontWeight: 700 }}>{level}</span>
        {summary && (
          <span>측점 {summary.n_points} · 시점 {summary.n_dates} ·
            기간 {summary.date_range.join(" ~ ")} · 최대CRI {summary.cri_global_max ?? "-"}</span>
        )}
        {error && <span style={{ color: "#c62828" }}>⚠ {error}</span>}
      </header>

      {/* 컨트롤 */}
      <div style={{ display: "flex", gap: 10, margin: "6px 0" }}>
        <select value={metric} onChange={(e) => setMetric(e.target.value)}>
          <option value="los">LOS 변위</option>
          <option value="longitudinal">종방향 변위</option>
        </select>
        {cri && dateIndex != null && (
          <>
            <input type="range" min={0} max={cri.dates.length - 1} value={dateIndex}
              onChange={(e) => setDateIndex(+e.target.value)} />
            <span>{cri.dates[dateIndex]}</span>
          </>
        )}
        {/* KAIA 핸드오프 다운로드 — InSAR+PINN 통일 데이터 → VLM 입력 */}
        <span style={{ flex: 1 }} />
        <a href={api.exportCsvUrl(bridgeId)} download
           style={{ fontSize: 13 }}>변위 CSV</a>
        <button type="button" onClick={downloadVlm}
          style={{ fontSize: 13 }}>VLM 패키지(.zip)</button>
      </div>

      {/* 지도 — Bmaps 기존 GIS 라이브러리에 위임 */}
      {renderMap?.(points, {
        metric,
        colorOf: (p) => criColor(p.cri),
        onPick: pickPoint,
      })}

      {/* 점 클릭 시 시계열(차트 라이브러리는 Bmaps 것으로) */}
      {series && (
        <section>
          <h4>측점 #{series.point_id} ({series.member})</h4>
          <p>LOS {series.los_mm.length}개 시점 · {series.cri ? "CRI 포함" : "CRI 없음"}
            {series.EI != null && ` · EI ≈ ${series.EI.toExponential(2)}`}</p>
          {/* 예: <YourLineChart labels={series.dates}
                series={[series.los_mm, series.longitudinal_mm, series.cri]} /> */}
        </section>
      )}
    </div>
  );
}
