/**
 * VWorldInsarMap — InsarTab 의 renderMap prop 에 끼우는 VWorld 지도 컴포넌트.
 *
 * ol.Map 은 명령형이라 ref+effect 로 1회 생성하고, points 변경 시 레이어만 갱신한다.
 * 전역 `ol`(OpenLayers/VWorld 2.0) 이 로드돼 있어야 한다.
 *
 *   <InsarTab apiBase={API} bridgeId={id}
 *     renderMap={(points, { colorBy, onPick }) =>
 *       <VWorldInsarMap apiKey={VW_KEY} points={points} colorBy={colorBy} onPick={onPick} />} />
 *
 * 단, InsarTab 예제의 renderMap 콜백 시그니처는 (points, { metric, colorOf, onPick }) 이므로
 * 아래처럼 colorBy 만 넘기면 된다(색 계산은 어댑터가 criColor 로 처리):
 *   renderMap={(points, { onPick }) =>
 *     <VWorldInsarMap apiKey={VW_KEY} points={points} colorBy="cri" onPick={onPick} />}
 */
import { useEffect, useRef } from "react";
import { createVWorldMap, InsarPointLayer } from "./vworld-adapter.js";

export default function VWorldInsarMap({
  apiKey, layer = "base", points = [], colorBy = "cri", onPick,
  center = [127.8, 36.5], zoom = 7, style,
}) {
  const elRef = useRef(null);
  const mapRef = useRef(null);
  const layerRef = useRef(null);
  const pickRef = useRef(onPick);
  pickRef.current = onPick; // 최신 콜백 유지(맵 재생성 없이)

  // 지도 1회 생성
  useEffect(() => {
    if (!elRef.current) return undefined;
    const map = createVWorldMap(elRef.current, { apiKey, layer, center, zoom });
    const ptLayer = new InsarPointLayer(map, { onPick: (id) => pickRef.current?.(id) });
    mapRef.current = map;
    layerRef.current = ptLayer;
    return () => { ptLayer.dispose(); map.setTarget(null); mapRef.current = null; layerRef.current = null; };
    // apiKey/layer 변경 시에만 재생성.
  }, [apiKey, layer]); // eslint-disable-line react-hooks/exhaustive-deps

  // points/색상 변경 시 레이어만 갱신
  useEffect(() => {
    const ptLayer = layerRef.current;
    if (!ptLayer) return;
    const { rendered } = ptLayer.update(points, { colorBy });
    if (rendered > 0) ptLayer.fit();
  }, [points, colorBy]);

  return <div ref={elRef} style={{ width: "100%", height: "100%", minHeight: 360, ...style }} />;
}
