#!/usr/bin/env python3
"""데모 디지털 트윈 — **IFC 생성 → 부재 결합 → 3D 트윈**을 한 번에.

저장소에 "IFC 까지 붙은 트윈"의 실물이 없었다. 코드와 테스트는 있는데 열어 볼 파일이
없으니 확인할 방법도 없었다. 여기서 정자교(README 예시) 기준으로 통째로 만든다:

  1. 표준데이터 실측 제원 → 프록시 부재            (proxy_model)
  2. 부재 → **IFC4 파일** + IfcMapConversion       (ifc_write)
  3. 그 IFC 를 **되읽어** 부재 AABB·GlobalId 확보   (ifc_io)  ← 왕복 검증
  4. InSAR 점을 데크 레벨로 올려 부재에 결합        (gltf_export)
  5. .glb + 자립형 웹뷰어 + 3D Tiles

3번이 요점이다. 부재 테이블에서 바로 트윈을 만들면 IFC 는 장식이 된다. IFC 를 다시
읽어 그 결과로 결합해야 "IFC 로 결합했다"가 참이 된다.

    python scripts/make_demo_twin.py

산출: docs/twin/  (jeongjagyo_proxy.ifc · twin.glb · twin.viewer.html · tileset.json)
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from inframon.bim.georef import MapConversion                          # noqa: E402
from inframon.bim.ifc_io import read_elements, read_map_conversion     # noqa: E402
from inframon.bim.ifc_write import write_elements                      # noqa: E402
from inframon.bim.proxy_model import bridge_elements                   # noqa: E402
from inframon.insar.gltf_export import (export_insar_gltf,             # noqa: E402
                                        guid_map_from_alignment,
                                        write_3dtiles_tileset, write_web_viewer)

OUT = ROOT / "docs/twin"
PROJ = ROOT / "data/jeongjagyo_real.h5"

# 전국교량표준데이터(data.go.kr 15081953) 정자교 — OSM way 51473197 중심에서 8 m
NAME = "정자교"
LAT, LON = 37.36854, 127.10900
LENGTH_M, N_SPANS = 108.0, 5
WIDTH_M = 26.5           # OSM 양측 보도 간격 23.5 m + 보도 반폭 ×2
CLEARANCE_M = 6.0        # 탄천 위 형하고(표준데이터에 교량높이 없음 — 가정)
GROUND_M = 37.2          # DEM 지면 표고
DECK_AZ_DEG = 0.4        # OSM 차도 방위(동서 방향)
CRS = "EPSG:5186"


def build_ifc() -> tuple[Path, dict]:
    """① 실측 제원 → 프록시 부재  ② → IFC4 + 지오참조."""
    els = bridge_elements(length_m=LENGTH_M, width_m=WIDTH_M, n_spans=N_SPANS,
                          clearance_m=CLEARANCE_M, name=NAME)
    from pyproj import Transformer
    e0, n0 = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(LON, LAT)
    az = math.radians(DECK_AZ_DEG)
    mc = MapConversion(eastings=e0, northings=n0, orthogonal_height=GROUND_M,
                       x_axis_abscissa=math.cos(az), x_axis_ordinate=math.sin(az),
                       target_crs=CRS, source="proxy_placement",
                       fit={"note": "표준데이터 제원 + OSM 방위로 배치 — 측량 정합 아님"})
    ifc = OUT / "jeongjagyo_proxy.ifc"
    info = write_elements(els, ifc, map_conversion=mc, project_name=f"{NAME} 프록시 교량",
                          site_name=f"{NAME} (성남 분당)")
    return ifc, info


def roundtrip(ifc: Path) -> tuple[list, MapConversion, Path]:
    """③ IFC 를 **되읽는다** — 이게 있어야 'IFC 로 결합했다'가 참이 된다."""
    els = read_elements(ifc)
    mc = read_map_conversion(ifc)
    if mc is None:
        raise SystemExit("되읽은 IFC 에 IfcMapConversion 이 없습니다 — 배치를 알 수 없습니다.")
    ej = OUT / "jeongjagyo_elements.json"
    ej.write_text(json.dumps({
        "schema": "inframon.bim.elements/1",
        "source": f"read back from {ifc.name}",
        "elements": [{"guid": e.guid, "name": e.name, "ifc_type": e.ifc_type,
                      "member": e.member, "bbox_min": list(e.bbox_min),
                      "bbox_max": list(e.bbox_max), "extra": e.extra} for e in els],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return els, mc, ej


def prepare_points() -> Path:
    """④ 실 InSAR 점을 트윈용 프로젝트로 — 교량 주변만, 로컬 좌표 기준."""
    if not PROJ.exists():
        raise SystemExit(
            f"실데이터가 없습니다: {PROJ}\n"
            "  정자교 SARvey 산출물(2,661점 × 201에폭)이 필요합니다 — docs/실데이터_런북.md")
    with h5py.File(PROJ, "r") as h:
        g = h["insar"]
        xyz = np.asarray(g["xyz"][()], float)
        vel = np.asarray(g["velocity_mm_yr"][()], float)
        keys = [k for k in g.keys() if k != "xyz"]
        data = {k: g[k][()] for k in keys}
        attrs = dict(g.attrs)
    # 교량 중심 반경 80 m — 트윈은 이 교량 것이다
    dm = np.hypot((xyz[:, 0] - LON) * np.cos(np.radians(LAT)) * 111_320.0,
                  (xyz[:, 1] - LAT) * 111_320.0)
    m = dm <= 80.0
    if m.sum() < 3:
        raise SystemExit(f"교량 반경 80 m 안 점이 {int(m.sum())}개뿐입니다.")
    out = OUT / "_twin_points.h5"
    with h5py.File(out, "w") as o:
        # ProjectStore 규약(contracts.io.GROUPS): 네 그룹이 다 있어야 읽기로 열린다
        for g in ("cv", "pinn", "fram"):
            o.create_group(g)
        gi = o.create_group("insar")
        gi.create_dataset("xyz", data=xyz[m])
        for k, v in data.items():
            a = np.asarray(v)
            gi.create_dataset(k, data=(a[m] if a.shape[:1] == (len(m),) else a))
        for k, v in attrs.items():
            gi.attrs[k] = v
    print(f"  트윈 대상 점 {int(m.sum())} / {len(xyz)} (반경 80 m) · "
          f"속도 {np.nanmin(vel[m]):+.2f}~{np.nanmax(vel[m]):+.2f} mm/yr")
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"[1/5] 실측 제원 → 프록시 부재 → IFC4  ({LENGTH_M:.0f} m · {N_SPANS}경간)")
    ifc, info = build_ifc()
    print(f"      {ifc.relative_to(ROOT)} · 부재 {info['n_elements']} · "
          f"지오참조 {info['target_crs']}")

    print("[2/5] IFC 되읽기 — 왕복 검증")
    els, mc, ej = roundtrip(ifc)
    geom_n = sum(1 for e in els if e.extra.get("bbox_source") == "geometry")
    print(f"      부재 {len(els)} · 형상 AABB {geom_n} · "
          f"배치 E={mc.eastings:.1f} N={mc.northings:.1f} 회전 {mc.rotation_deg:.2f}°")

    print("[3/5] InSAR 점 준비")
    pts = prepare_points()

    print("[4/5] 점 → 부재 결합(GlobalId) + 데크 레벨")
    guids, ginfo = guid_map_from_alignment(pts, ej, map_conversion=mc, ifc_crs=CRS,
                                           max_dist_m=30.0)
    r = export_insar_gltf(pts, OUT / "twin.glb", value="velocity",
                          element_guids=guids, element_z=ginfo["element_z"],
                          z_source="deck", element_z_datum=GROUND_M)
    g = r["georef"]
    print(f"      점 {r['n_points']} · 결합 {r['bound']} · "
          f"고도 {g['z_source']} → {g.get('deck_z_median_m')} m")

    print("[5/5] 웹뷰어 + 3D Tiles")
    v = write_web_viewer(OUT / "twin.glb")
    t = write_3dtiles_tileset(OUT / "twin.glb")
    pts.unlink(missing_ok=True)                      # 중간 산출물은 남기지 않는다
    for p in (ifc, ej, OUT / "twin.glb", Path(v["viewer"]), Path(t["tileset"])):
        print(f"      {Path(p).relative_to(ROOT)}  {Path(p).stat().st_size:,} B")
    print(f"\n브라우저로 열기: {(OUT / 'twin.viewer.html')}")


if __name__ == "__main__":
    main()
