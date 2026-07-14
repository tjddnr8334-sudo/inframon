"""SNAP(ESA SNAP) 기반 **Windows 네이티브** InSAR 백엔드 — ISCE2/WSL 불필요.

교량 좌표만 주면 임의의 한국 교량에 재사용된다:
  1. 기준 SLC 주석(annotation)에서 교량을 덮는 **subswath·burst 자동판별**
  2. `gpt`(SNAP)로 S1 TOPS 코레지스트레이션+간섭도+지오코딩 — **스타 네트워크**(기준 vs 각 보조)
  3. 지오코딩 결과 → inframon **Track H5 계약**(pixel_lonlat/epochs/los_mm/coh/incidenceAngle)

full-frame ISCE2 방식과 달리 교량 주변 1 burst 만 처리 → 빠르고 병렬화 쉬움. ISCE2 는
리눅스 전용이라 WSL 을 강제했지만, SNAP 은 Java 기반 네이티브 Windows 라 이 백엔드는
Windows 에서 그대로 돈다(교체 가능한 상류 백엔드의 Windows 기본값).

네트워크/외부프로세스(gpt)는 `run_pair` 한 곳으로 격리(테스트에서 monkeypatch).
"""

from __future__ import annotations

import math
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

WAVELENGTH_M = 0.05546576  # Sentinel-1 C-band
_GRAPH_TC = "coreg_ifg_tc.xml"  # scripts/snap/ 의 코레지+간섭도+TC 그래프

# gpt 실행파일 후보(Windows 우선, 리눅스/PATH 폴백).
_GPT_CANDIDATES = (
    r"C:\Program Files\esa-snap\bin\gpt.exe",
    r"C:\Program Files\snap\bin\gpt.exe",
    "/opt/snap/bin/gpt",
    "gpt",
)


class SnapError(RuntimeError):
    """SNAP 백엔드 오류(gpt 미탐지·처리 실패·커버리지 없음)."""


@dataclass
class BurstLoc:
    """교량을 덮는(또는 가장 가까운) subswath·burst."""

    subswath: str          # 'IW1'|'IW2'|'IW3'
    burst_index: int       # 1-based (SNAP firstBurstIndex/lastBurstIndex)
    distance_km: float     # contained=True: 가장자리서 안쪽 margin[km] / False: burst중심 거리
    grid_lat: float        # burst 중심(또는 최근접) lat
    grid_lon: float
    contained: bool = True  # 교량이 이 burst footprint 안에 있는가

    @property
    def covered(self) -> bool:
        return self.contained


@dataclass
class SnapPairResult:
    ref_date: str
    sec_date: str
    product: str           # 지오코딩 산출(.tif/.dim)
    ok: bool
    detail: str = ""


@dataclass
class SnapRunResult:
    reference: str
    burst: BurstLoc
    pairs: list[SnapPairResult] = field(default_factory=list)
    track_h5: str | None = None
    n_points: int = 0
    weather: object = None            # ⑤ ERA5 MasterSelection(사용시) 또는 사유 문자열
    rejected_slaves: list = field(default_factory=list)   # baseline·도플러 사전필터 제거

    def as_dict(self) -> dict:
        w = self.weather
        wsum = None
        if w is not None and hasattr(w, "selected_master"):
            wsum = {"master": w.selected_master, "n_excluded": getattr(w, "n_excluded", 0)}
        elif isinstance(w, str):
            wsum = w
        return {
            "reference": self.reference,
            "burst": {"subswath": self.burst.subswath, "index": self.burst.burst_index,
                      "distance_km": round(self.burst.distance_km, 2)},
            "pairs": [{"ref": p.ref_date, "sec": p.sec_date, "ok": p.ok} for p in self.pairs],
            "track_h5": self.track_h5, "n_points": self.n_points, "weather": wsum,
            "rejected_slaves": self.rejected_slaves,
        }


# ── gpt 탐지 ──────────────────────────────────────────────────────────────
def find_gpt(explicit: str | None = None) -> str:
    """gpt 실행파일 경로. 없으면 SnapError."""
    import shutil

    for c in ([explicit] if explicit else []) + list(_GPT_CANDIDATES):
        if not c:
            continue
        if Path(c).exists():
            return c
        w = shutil.which(c)
        if w:
            return w
    raise SnapError(
        "SNAP gpt 를 찾지 못했습니다. ESA SNAP 설치 후 경로를 지정하세요 "
        "(예: C:\\Program Files\\esa-snap\\bin\\gpt.exe). https://step.esa.int/main/download/snap-download/")


# ── SLC 날짜/주석 파싱 ────────────────────────────────────────────────────
def scene_date(slc_zip: str | Path) -> str:
    """granule 이름에서 YYYYMMDD (첫 취득일)."""
    m = re.search(r"_(\d{8})T\d{6}_", Path(slc_zip).name)
    if not m:
        raise SnapError(f"SLC 이름에서 날짜를 못 읽음: {slc_zip}")
    return m.group(1)


def _annotation_xml(z: zipfile.ZipFile, subswath: str) -> ET.Element:
    sw = subswath.lower()
    names = [n for n in z.namelist()
             if f"annotation/s1a-{sw}-slc-vv" in n.lower() and n.endswith(".xml")]
    if not names:
        names = [n for n in z.namelist()
                 if f"-{sw}-slc-vv" in n.lower() and n.endswith(".xml") and "calibration" not in n.lower()]
    if not names:
        raise SnapError(f"{subswath} VV 주석을 SLC 에서 못 찾음")
    return ET.fromstring(z.read(names[0]))


def _km(dlon: float, dlat: float, lat: float) -> float:
    return math.hypot(dlat, dlon * math.cos(math.radians(lat))) * 111.0


def _edge_margin_km(lon: float, lat: float, poly: list[tuple[float, float]]) -> float:
    """점에서 다각형 변까지 최단거리[km](얼마나 안쪽인지)."""
    best = float("inf")
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]; bx, by = poly[(i + 1) % n]
        vx, vy = bx - ax, by - ay
        wx, wy = lon - ax, lat - ay
        seg2 = vx * vx + vy * vy
        u = 0.0 if seg2 == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / seg2))
        px, py = ax + u * vx, ay + u * vy
        best = min(best, _km(lon - px, lat - py, lat))
    return best


def _point_in_poly(lon: float, lat: float, poly: list[tuple[float, float]]) -> bool:
    """ray-casting 점-다각형 포함(poly: [(lon,lat),...]). shapely 의존 회피."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > lat) != (yj > lat)) and \
           (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def doppler_centroid_hz(slc_zip: str | Path, subswath: str) -> float | None:
    """SLC 애노테이션의 대표 도플러 중심 주파수 fDC[Hz] — dataDcPolynomial 상수항 평균.

    ASF 검색 API 는 fDC 를 안 주므로(다운로드 후 애노테이션에만 존재) 여기서 파싱.
    슬레이브-마스터 |ΔfDC| 가 크면 방위 스펙트럼 비간섭 → slave 품질 저하 판정에 사용.
    """
    try:
        with zipfile.ZipFile(str(slc_zip)) as z:
            root = _annotation_xml(z, subswath)
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        return None
    vals = []
    for dc in root.iter("dcEstimate"):
        poly = dc.find("dataDcPolynomial")
        if poly is None or not (poly.text and poly.text.strip()):
            poly = dc.find("geometryDcPolynomial")
        if poly is not None and poly.text and poly.text.strip():
            try:
                vals.append(float(poly.text.split()[0]))       # 상수항 = fDC 기준값
            except (ValueError, IndexError):
                continue
    if not vals:
        return None
    import numpy as np
    return float(np.mean(vals))


def doppler_centroids(slc_zips: list, subswath: str) -> dict:
    """장면들의 fDC[Hz] → {YYYYMMDD: fDC}. 파싱 실패 장면은 생략."""
    out: dict = {}
    for s in slc_zips:
        f = doppler_centroid_hz(s, subswath)
        if f is not None:
            out[scene_date(str(s))] = f
    return out


def find_bridge_burst(slc_zip: str | Path, lat: float, lon: float) -> BurstLoc:
    """기준 SLC 에서 교량(lat,lon)을 **포함하는** subswath·burst(1-based) 판별.

    각 subswath·burst 의 4모서리 geolocationGridPoint 로 footprint 다각형을 만들어 점
    포함을 검사한다(단순 최근접 격자점은 IW 경계 overlap 에서 오판 → 포함검사가 정확).
    포함 burst 가 여럿이면(인접 overlap) 중심에 가장 가까운 것을, 없으면 최근접 격자점
    burst 로 폴백(거리로 커버리지 밖임을 알림).
    """
    contain: list[BurstLoc] = []
    nearest: BurstLoc | None = None
    with zipfile.ZipFile(str(slc_zip)) as z:
        for sw in ("IW1", "IW2", "IW3"):
            try:
                root = _annotation_xml(z, sw)
            except SnapError:
                continue
            st = root.find(".//swathTiming")
            lpb = int(st.find("linesPerBurst").text)
            nb = len(st.find("burstList").findall("burst"))
            pts = [(int(p.find("line").text), int(p.find("pixel").text),
                    float(p.find("latitude").text), float(p.find("longitude").text))
                   for p in root.findall(".//geolocationGridPoint")]
            if not pts:
                continue
            pixels = sorted({q[1] for q in pts})
            px0, px1 = pixels[0], pixels[-1]

            def nearest_pt(line: int, pix: int) -> tuple[float, float]:
                q = min(pts, key=lambda t: (abs(t[0] - line), abs(t[1] - pix)))
                return q[3], q[2]      # lon, lat

            for i in range(nb):
                l0, l1 = i * lpb, (i + 1) * lpb
                poly = [nearest_pt(l0, px0), nearest_pt(l0, px1),
                        nearest_pt(l1, px1), nearest_pt(l1, px0)]
                clon = sum(p[0] for p in poly) / 4.0
                clat = sum(p[1] for p in poly) / 4.0
                dc = _km(clon - lon, clat - lat, lat)
                if nearest is None or dc < nearest.distance_km:
                    nearest = BurstLoc(sw, i + 1, dc, clat, clon, contained=False)
                if _point_in_poly(lon, lat, poly):
                    margin = _edge_margin_km(lon, lat, poly)
                    contain.append(BurstLoc(sw, i + 1, margin, clat, clon, contained=True))
    if contain:
        return max(contain, key=lambda b: b.distance_km)   # 가장 깊이 포함(가장자리 여유 큰)
    if nearest is None:
        raise SnapError("SLC 에서 subswath 주석을 읽지 못했습니다.")
    return nearest      # 커버리지 밖 — contained=False, distance_km=중심거리


def platform_heading(slc_zip: str | Path, subswath: str) -> float | None:
    """플랫폼 heading(도) — asc/desc 연직분해용. 못 읽으면 None."""
    try:
        with zipfile.ZipFile(str(slc_zip)) as z:
            root = _annotation_xml(z, subswath)
        el = root.find(".//platformHeading")
        return float(el.text) if el is not None else None
    except Exception:  # noqa: BLE001
        return None


# ── gpt 실행(격리 지점) ───────────────────────────────────────────────────
def _snap_date(yyyymmdd: str) -> str:
    """YYYYMMDD → SNAP 밴드 접미 날짜('20240107'→'07Jan2024')."""
    from datetime import datetime
    return datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%d%b%Y")


def ifg_band_names(subswath: str, ref_date: str, sec_date: str) -> tuple[str, str, str]:
    """(i_ifg, q_ifg, coh) 밴드명 — SNAP Interferogram 명명규칙."""
    suf = f"{subswath}_VV_{_snap_date(ref_date)}_{_snap_date(sec_date)}"
    return f"i_ifg_{suf}", f"q_ifg_{suf}", f"coh_{suf}"


def run_pair(gpt: str, graph: str, ref: str, sec: str, burst: BurstLoc,
             dem: str, out_file: str, log_file: str | None = None,
             timeout: int = 3600) -> int:
    """gpt 그래프로 한 쌍(ref,sec) 코레지+간섭도+위상+TC 실행 → rc. 프로세스 격리 지점."""
    iband, qband, cohband = ifg_band_names(burst.subswath, scene_date(ref), scene_date(sec))
    args = [gpt, graph,
            f"-PrefFile={ref}", f"-PsecFile={sec}",
            f"-Psubswath={burst.subswath}",
            f"-PfirstBurst={burst.burst_index}", f"-PlastBurst={burst.burst_index}",
            f"-PiBand={iband}", f"-PqBand={qband}", f"-PcohBand={cohband}",
            f"-PdemName={dem}", f"-PoutFile={out_file}"]
    if log_file:
        with open(log_file, "w", encoding="utf-8") as lf:
            p = subprocess.run(args, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout)
        return p.returncode
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return p.returncode


def process_star_network(
    scenes: list[str | Path], lat: float, lon: float, out_dir: str | Path,
    *, reference: str | Path | None = None, dem: str = "SRTM 1Sec HGT",
    graph_dir: str | Path | None = None, gpt: str | None = None,
    burst: BurstLoc | None = None, skip_existing: bool = True,
    filter_baseline: bool = True, bperp: dict | None = None,
    max_temporal_days: float = 72.0, max_perp_m: float = 150.0,
    max_doppler_hz: float = 500.0, min_keep: int = 3,
) -> SnapRunResult:
    """스타 네트워크(기준 vs 각 보조) 코레지+간섭도+TC. reference 기본=최이른 날짜.

    burst 를 주면 자동판별을 건너뛴다(배치에서 같은 burst 재사용). skip_existing 이면
    이미 만든 지오코딩 산출물(.tif)은 gpt 를 다시 돌리지 않고 재사용(멱등 재개).
    filter_baseline 이면 처리 전에 **시공간 baseline·도플러** 초과 slave 를 제거
    (도플러는 SLC 애노테이션 로컬 파싱, B⊥ 는 bperp 주면 사용). min_keep 유지.
    """
    scenes = [str(s) for s in scenes]
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    gpt = gpt or find_gpt()
    graph = str((Path(graph_dir) if graph_dir else _default_graph_dir()) / _GRAPH_TC)

    ref = str(reference) if reference else min(scenes, key=scene_date)
    secs = [s for s in scenes if str(s) != str(ref)]
    if burst is None:
        burst = find_bridge_burst(ref, lat, lon)

    res = SnapRunResult(reference=scene_date(ref), burst=burst)

    # ── 사전 필터: 시공간 baseline·도플러 초과 slave 제거(비싼 간섭처리 전) ──
    if filter_baseline and len(secs) >= 2:
        from .slc_search import filter_slaves_by_baseline
        by_date = {scene_date(s): s for s in [ref, *secs]}
        dop = doppler_centroids([ref, *secs], burst.subswath)
        keep_dates, rejected = filter_slaves_by_baseline(
            list(by_date), scene_date(ref), bperp=bperp, doppler=dop,
            max_temporal_days=max_temporal_days, max_perp_m=max_perp_m,
            max_doppler_hz=max_doppler_hz, min_keep=min_keep)
        secs = [by_date[d] for d in keep_dates if d != scene_date(ref)]
        res.rejected_slaves = rejected
    for sec in sorted(secs, key=scene_date):
        rd, sd = scene_date(ref), scene_date(sec)
        tif = str(out / f"tc_{rd}_{sd}.tif")
        log = str(out / f"tc_{rd}_{sd}.log")
        try:
            if skip_existing and Path(tif).exists() and Path(tif).stat().st_size > 0:
                res.pairs.append(SnapPairResult(rd, sd, tif, True, "재사용(skip_existing)"))
                continue
            rc = run_pair(gpt, graph, ref, sec, burst, dem, tif, log_file=log)
            ok = rc == 0 and Path(tif).exists()
            res.pairs.append(SnapPairResult(rd, sd, tif, ok,
                                            "" if ok else f"gpt rc={rc}"))
        except (OSError, subprocess.SubprocessError) as e:
            res.pairs.append(SnapPairResult(rd, sd, tif, False, str(e)))
    return res


def _default_graph_dir() -> Path:
    # 리포 루트 추정: src/inframon/insar/snap_backend.py → 리포/scripts/snap
    return Path(__file__).resolve().parents[3] / "scripts" / "snap"


# ── 지오코딩 결과 → Track H5 계약 ────────────────────────────────────────
def build_track_h5(
    pairs: list[SnapPairResult], ref_date: str, out_h5: str | Path,
    *, lat: float, lon: float, coh_min: float = 0.3, radius_km: float = 3.0,
    heading: float | None = None, max_points: int = 20000,
) -> int:
    """스타 네트워크 지오코딩 산출(각 [phase, coh, incidence]) → inframon Track H5.

    각 쌍 tif 의 band1=phase(rad)·band2=coherence·band3=incidence(deg). 교량 반경 내
    coh≥coh_min 점을 골라 los_mm = −λ/4π·phase·1000, 기준일 변위=0 으로 시계열 구성.
    점이 부족하면 반경을 2배씩 넓혀 재시도. 반환: 점 수 N.
    """
    import h5py
    import numpy as np
    import rasterio

    ok_pairs = [p for p in pairs if p.ok and Path(p.product).exists()]
    if not ok_pairs:
        raise SnapError("성공한 간섭도 쌍이 없어 Track 을 만들 수 없습니다.")

    # 기준 격자 = 첫 쌍
    with rasterio.open(ok_pairs[0].product) as ds0:
        ph0 = ds0.read(1).astype(np.float64)
        coh0 = ds0.read(2).astype(np.float64)
        inc0 = ds0.read(3).astype(np.float64) if ds0.count >= 3 else np.full(ph0.shape, np.nan)
        H, W = ph0.shape
        rows, cols = np.mgrid[0:H, 0:W]
        xs, ys = rasterio.transform.xy(ds0.transform, rows.ravel(), cols.ravel())
        glon = np.asarray(xs).reshape(H, W)
        glat = np.asarray(ys).reshape(H, W)

    dist_km = np.hypot(glat - lat, (glon - lon) * math.cos(math.radians(lat))) * 111.0
    valid = np.isfinite(ph0) & (ph0 != 0.0) & (coh0 >= coh_min)

    r = radius_km
    for _ in range(5):
        sel = valid & (dist_km <= r)
        if sel.sum() >= 20:
            break
        r *= 2.0
    idx = np.where(sel.ravel())[0]
    if idx.size == 0:
        raise SnapError(f"교량 반경 {r:.0f}km 내 coh≥{coh_min} 점이 없습니다(커버리지 부족).")
    if idx.size > max_points:      # 너무 많으면 coherence 상위로 제한
        order = np.argsort(-coh0.ravel()[idx])[:max_points]
        idx = idx[order]

    pt_lon = glon.ravel()[idx]; pt_lat = glat.ravel()[idx]
    N = idx.size
    M = 1 + len(ok_pairs)                       # 기준일 + 보조일들
    los = np.zeros((N, M), dtype=np.float64)    # col0 = 기준(0)
    coh_acc = coh0.ravel()[idx].copy()
    scale = -WAVELENGTH_M / (4.0 * math.pi) * 1000.0   # phase(rad) → mm

    dates = [ref_date]
    for k, p in enumerate(ok_pairs, start=1):
        dates.append(p.sec_date)
        with rasterio.open(p.product) as ds:
            phk = ds.read(1).astype(np.float64)
            cohk = ds.read(2).astype(np.float64)
        if phk.shape == (H, W):                 # 동일 격자 → 직접 인덱스
            los[:, k] = phk.ravel()[idx] * scale
            coh_acc += cohk.ravel()[idx]
        else:                                    # 다르면 좌표 샘플
            with rasterio.open(p.product) as ds:
                samp = np.array([v[0] for v in ds.sample(zip(pt_lon, pt_lat), indexes=1)],
                                dtype=np.float64)
            los[:, k] = samp * scale
    coh_mean = (coh_acc / M).astype(np.float32)
    incidence = inc0.ravel()[idx].astype(np.float32)
    epochs = np.array([int(d) for d in dates], dtype=np.int32)

    out_h5 = Path(out_h5); out_h5.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(out_h5, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack([pt_lon, pt_lat]).astype(np.float64))
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=los.astype(np.float32))
        f.create_dataset("coh", data=coh_mean)
        if np.isfinite(incidence).any():
            f.create_dataset("incidenceAngle", data=incidence)
        if heading is not None and math.isfinite(heading):
            f.attrs["HEADING"] = float(heading)
        f.attrs["source"] = "SNAP(Windows) star-network wrapped-phase → LOS"
        f.attrs["RADAR_WAVELENGTH"] = WAVELENGTH_M
    return N


def _stack_graph_xml(scene_files: list[str], subswath: str, burst: int, dem: str) -> str:
    """N장면 코레지스트레이션(Back-Geocoding) → Deburst → TC → 지오코딩 진폭 스택 GeoTIFF.

    첫 장면이 마스터(기준). 각 장면 TOPSAR-Split(같은 subswath·burst) → Apply-Orbit →
    Back-Geocoding(전부) → Deburst → Terrain-Correction. TC 가 복소 i/q 를 Intensity 로
    지오코딩하므로 날짜별 Intensity 밴드가 나온다(√Intensity=진폭 → ADI).
    """
    reads, splits, orbits, srcs = [], [], [], []
    for i, f in enumerate(scene_files):
        reads.append(f'  <node id="Read_{i}"><operator>Read</operator><sources/>'
                     f'<parameters class="com.bc.ceres.binding.dom.XppDomElement">'
                     f'<file>{f}</file></parameters></node>')
        splits.append(
            f'  <node id="Split_{i}"><operator>TOPSAR-Split</operator>'
            f'<sources><sourceProduct refid="Read_{i}"/></sources>'
            f'<parameters class="com.bc.ceres.binding.dom.XppDomElement">'
            f'<subswath>{subswath}</subswath><selectedPolarisations>VV</selectedPolarisations>'
            f'<firstBurstIndex>{burst}</firstBurstIndex><lastBurstIndex>{burst}</lastBurstIndex>'
            f'</parameters></node>')
        orbits.append(
            f'  <node id="Orbit_{i}"><operator>Apply-Orbit-File</operator>'
            f'<sources><sourceProduct refid="Split_{i}"/></sources>'
            f'<parameters class="com.bc.ceres.binding.dom.XppDomElement">'
            f'<orbitType>Sentinel Precise (Auto Download)</orbitType>'
            f'<continueOnFail>true</continueOnFail></parameters></node>')
        tag = "sourceProduct" if i == 0 else f"sourceProduct.{i}"
        srcs.append(f'    <{tag} refid="Orbit_{i}"/>')
    nodes = "\n".join(reads + splits + orbits)
    return f"""<graph id="Graph">
  <version>1.0</version>
{nodes}
  <node id="BackGeo"><operator>Back-Geocoding</operator>
    <sources>
{chr(10).join(srcs)}
    </sources>
    <parameters class="com.bc.ceres.binding.dom.XppDomElement">
      <demName>{dem}</demName>
      <demResamplingMethod>BICUBIC_INTERPOLATION</demResamplingMethod>
      <resamplingType>BISINC_5_POINT_INTERPOLATION</resamplingType>
      <maskOutAreaWithoutElevation>false</maskOutAreaWithoutElevation>
    </parameters>
  </node>
  <node id="Deburst"><operator>TOPSAR-Deburst</operator>
    <sources><sourceProduct refid="BackGeo"/></sources>
    <parameters class="com.bc.ceres.binding.dom.XppDomElement">
      <selectedPolarisations>VV</selectedPolarisations></parameters>
  </node>
  <node id="TC"><operator>Terrain-Correction</operator>
    <sources><sourceProduct refid="Deburst"/></sources>
    <parameters class="com.bc.ceres.binding.dom.XppDomElement">
      <demName>{dem}</demName>
      <imgResamplingMethod>NEAREST_NEIGHBOUR</imgResamplingMethod>
      <pixelSpacingInMeter>14.0</pixelSpacingInMeter>
      <mapProjection>WGS84(DD)</mapProjection>
    </parameters>
  </node>
  <node id="Write"><operator>Write</operator>
    <sources><sourceProduct refid="TC"/></sources>
    <parameters class="com.bc.ceres.binding.dom.XppDomElement">
      <file>${{outFile}}</file><formatName>GeoTIFF</formatName></parameters>
  </node>
</graph>
"""


def amplitude_stack(scenes: list[str | Path], lat: float, lon: float, out_file: str | Path,
                    *, dem: str = "SRTM 1Sec HGT", gpt: str | None = None,
                    burst: BurstLoc | None = None, timeout: int = 7200) -> dict:
    """전 장면 코레지스트레이션 지오코딩 진폭 스택(GeoTIFF) 산출. 반환: 밴드↔날짜 매핑 등."""
    scenes = [str(s) for s in sorted(scenes, key=scene_date)]
    gpt = gpt or find_gpt()
    ref = scenes[0]
    if burst is None:
        burst = find_bridge_burst(ref, lat, lon)
    dates = [scene_date(s) for s in scenes]
    graph_xml = _stack_graph_xml(scenes, burst.subswath, burst.burst_index, dem)
    gpath = Path(out_file).with_suffix(".stackgraph.xml")
    gpath.write_text(graph_xml, encoding="utf-8")
    args = [gpt, str(gpath), f"-PoutFile={out_file}"]
    log = str(Path(out_file).with_suffix(".log"))
    with open(log, "w", encoding="utf-8") as lf:
        p = subprocess.run(args, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout)
    ok = p.returncode == 0 and Path(out_file).exists()
    return {"ok": ok, "rc": p.returncode, "out": str(out_file), "dates": dates,
            "burst": f"{burst.subswath}#{burst.burst_index}", "log": log}


def compute_adi(amp_stack_tif: str | Path, n_dates: int,
                out_adi_tif: str | Path | None = None):
    """진폭 스택(날짜별 Intensity 또는 i/q) → **ADI = σ_amp/μ_amp** 단일밴드.

    밴드수가 n_dates 면 각 밴드=Intensity(√ → 진폭), 2·n_dates 면 i/q 쌍(hypot → 진폭).
    반환: (adi[H,W], transform, crs). out_adi_tif 주면 단일밴드 GeoTIFF 저장.
    """
    import numpy as np
    import rasterio

    with rasterio.open(str(amp_stack_tif)) as ds:
        nb = ds.count
        arr = ds.read().astype(np.float64)          # (bands, H, W)
        transform, crs = ds.transform, ds.crs
        prof = ds.profile
    if nb == 2 * n_dates:
        amp = np.hypot(arr[0::2], arr[1::2])         # i/q per date
    else:
        amp = np.sqrt(np.clip(arr, 0.0, None))       # intensity → amplitude
    mean = amp.mean(axis=0)
    std = amp.std(axis=0)
    adi = np.where(mean > 0, std / mean, np.nan).astype(np.float32)
    if out_adi_tif:
        prof.update(count=1, dtype="float32")
        keys = ("driver", "height", "width", "count", "dtype", "crs", "transform")
        with rasterio.open(str(out_adi_tif), "w", **{k: prof[k] for k in keys}) as dst:
            dst.write(adi, 1)
    return adi, transform, crs


def _sample_raster(tif: str | Path, lons, lats, band: int = 1):
    """지오코딩 래스터를 점 좌표(lon,lat)에서 band 샘플(최근접)."""
    import numpy as np
    import rasterio

    with rasterio.open(str(tif)) as ds:
        return np.array([v[0] for v in ds.sample(zip(lons, lats), indexes=band)],
                        dtype=np.float64)


def adi_at_points(amp_pair_tifs: list[str | Path], lons, lats):
    """쌍별 진폭 tif(band1=기준강도·band2=보조강도) → 각 점의 **ADI = σ_amp/μ_amp**.

    기준(마스터) 진폭은 모든 쌍에서 동일하므로 첫 쌍 band1 에서, 보조 진폭은 각 쌍
    band2 에서 샘플. amp=√Intensity. 반환: adi[N](진폭시계열 8날짜 기준).
    """
    import numpy as np

    ref_amp = np.sqrt(np.clip(_sample_raster(amp_pair_tifs[0], lons, lats, band=1), 0, None))
    cols = [ref_amp]
    for t in amp_pair_tifs:
        cols.append(np.sqrt(np.clip(_sample_raster(t, lons, lats, band=2), 0, None)))
    amp = np.column_stack(cols)                     # (N, 1+n_pairs)
    mean = amp.mean(axis=1); std = amp.std(axis=1)
    return np.where(mean > 0, std / mean, np.nan).astype(np.float32)


def _seg_dist_km(plon, plat, a, b):
    """점 배열(plon,plat) ~ 선분 a-b(각 (lon,lat)) 최단거리[km] (벡터화)."""
    import numpy as np
    ax, ay = a; bx, by = b
    vx, vy = bx - ax, by - ay
    seg2 = vx * vx + vy * vy or 1e-12
    t = np.clip(((plon - ax) * vx + (plat - ay) * vy) / seg2, 0.0, 1.0)
    px, py = ax + t * vx, ay + t * vy
    return np.hypot((plon - px) * math.cos(math.radians(ay)), plat - py) * 111.0


def _polyline_dist_km(plon, plat, geom_lonlat):
    """점 배열 ~ 폴리라인(데크) 최단거리[km] — 세그먼트별 최소."""
    import numpy as np
    best = None
    for a, b in zip(geom_lonlat, geom_lonlat[1:]):
        d = _seg_dist_km(plon, plat, a, b)
        best = d if best is None else np.minimum(best, d)
    return best


def find_reference_point(
    pairs: list, *, roi_bbox: tuple | None = None, deck_geometry: list | None = None,
    deck_buffer_m: float = 60.0, min_coh: float = 0.98,
) -> dict | None:
    """간섭도에서 **교량 밖·ROI 내 초안정 기준점**(시간평균 coherence ≥ min_coh) 선정.

    reference point 는 데크(움직임) 밖 안정 지반·건물 PS(coh≥0.98)여야 상대변위 기준이 된다.
    데크 점은 열·하중으로 coh 가 낮아(≈0.85) 기준점 부적합 → **도심밀도 ROI 의 건물 PS**에서
    고른다. roi_bbox=(w,s,e,n) 안, deck 폴리라인 버퍼 밖에서 coh 최대 픽셀.
    반환: {lon,lat,coherence,n_candidates,meets_threshold} 또는 None(간섭도 없음).
    """
    import numpy as np
    import rasterio

    ok = [p for p in pairs if p.ok and Path(p.product).exists()]
    if not ok:
        return None
    with rasterio.open(ok[0].product) as ds0:
        coh_stack = [ds0.read(2).astype(np.float64)]
        H, W = coh_stack[0].shape
        rows, cols = np.mgrid[0:H, 0:W]
        xs, ys = rasterio.transform.xy(ds0.transform, rows.ravel(), cols.ravel())
        glon = np.asarray(xs).reshape(H, W); glat = np.asarray(ys).reshape(H, W)
    for p in ok[1:]:
        with rasterio.open(p.product) as ds:
            coh_stack.append(ds.read(2).astype(np.float64))
    coh_mean = np.mean(coh_stack, axis=0)

    mask = np.isfinite(coh_mean) & (coh_mean > 0.0)
    if roi_bbox:                                       # ROI(도심) 범위로 제한
        w, s, e, n = roi_bbox
        mask &= (glon >= w) & (glon <= e) & (glat >= s) & (glat <= n)
    if deck_geometry:                                  # 데크 버퍼 밖만(교량 위 제외)
        geom = [(float(lon), float(lat)) for lat, lon in deck_geometry]
        d = np.full((H, W), np.inf)
        d.ravel()[mask.ravel()] = _polyline_dist_km(
            glon.ravel()[mask.ravel()], glat.ravel()[mask.ravel()], geom)
        mask &= d > (deck_buffer_m / 1000.0)
    pool = np.where(mask.ravel())[0]
    if pool.size == 0:
        return None
    cm = coh_mean.ravel()
    cand = pool[cm[pool] >= min_coh]
    if cand.size:
        bi = int(cand[np.argmax(cm[cand])]); met = True
    else:
        bi = int(pool[np.argmax(cm[pool])]); met = False   # 폴백: 데크 밖 최고 coh
    return {"lon": float(glon.ravel()[bi]), "lat": float(glat.ravel()[bi]),
            "coherence": float(cm[bi]), "n_candidates": int(cand.size),
            "meets_threshold": met, "min_coh": float(min_coh)}


def build_bridge_track_ps_ds(
    pairs: list[SnapPairResult], ref_date: str, out_h5: str | Path,
    *, geometry_latlon: list, buffer_m: float = 30.0, coh_min: float = 0.35,
    ps_coh: float = 0.7, heading: float | None = None,
    adi_tif: str | Path | None = None, adi_max: float = 0.25,
    amp_pairs: list | None = None,
    apply_reference: bool = True, roi_bbox: tuple | None = None,
    ref_min_coh: float = 0.98,
    reject_outliers: bool = True, outlier_k: float = 3.0,
    coh_floor: float = 0.2, min_keep: int = 3,
) -> dict:
    """**교량 데크(폴리라인) buffer_m 이내**의 PS/DS 점 선별 → Track H5.

    각 쌍 tif band1=phase·2=coherence·3=incidence. 데크 30m 버퍼 안에서 시간평균
    시간평균 코히런스(temporal coherence 근사)로 응집산란체를 고르고, γ̄≥ps_coh 는 PS,
    coh_min≤γ̄<ps_coh 는 DS 로 1차 분류(진폭 ADI 산출 시 PS 정밀화 가능).
    LOS 속도(mm/yr)도 계산해 저장(대시보드 속도점군용). 반환: 통계 dict.
    """
    import h5py
    import numpy as np
    import rasterio

    ok = [p for p in pairs if p.ok and Path(p.product).exists()]
    if not ok:
        raise SnapError("성공한 간섭도 쌍이 없어 Track 을 만들 수 없습니다.")
    geom = [(float(lon), float(lat)) for lat, lon in geometry_latlon]   # (lon,lat)
    lons = [g[0] for g in geom]; lats = [g[1] for g in geom]
    mlon, Mlon, mlat, Mlat = min(lons), max(lons), min(lats), max(lats)
    marg = buffer_m / 111000.0 * 3 + 0.001    # 데크 bbox + 여유(버퍼계산 대상 축소)

    with rasterio.open(ok[0].product) as ds0:
        ph0 = ds0.read(1); coh0 = ds0.read(2)
        inc0 = ds0.read(3) if ds0.count >= 3 else np.full(ph0.shape, np.nan, np.float32)
        H, W = ph0.shape
        rows, cols = np.mgrid[0:H, 0:W]
        xs, ys = rasterio.transform.xy(ds0.transform, rows.ravel(), cols.ravel())
        glon = np.asarray(xs).reshape(H, W); glat = np.asarray(ys).reshape(H, W)

    # 데크 bbox 근방만 폴리라인 거리 계산(전체 격자 스캔 회피)
    near = ((glon >= mlon - marg) & (glon <= Mlon + marg) &
            (glat >= mlat - marg) & (glat <= Mlat + marg))
    dist_km = np.full((H, W), np.inf)
    dist_km[near] = _polyline_dist_km(glon[near], glat[near], geom)

    # 각 쌍(master-slave) coherence·위상 (coh_stack[i]↔ph_list[i]↔ok[i])
    coh_stack = [coh0.astype(np.float64)]
    ph_list = [ph0.astype(np.float64)]
    for p in ok[1:]:
        with rasterio.open(p.product) as ds:
            coh_stack.append(ds.read(2).astype(np.float64))
            ph_list.append(ds.read(1).astype(np.float64))

    # ── 튀는 슬레이브 자동 제거: master 대비 (데크 근방) 평균 coherence 로버스트 이상치 ──
    # 대기교란·비간섭·궤도이상 등으로 간섭도가 급락한 slave 는 시계열을 오염 → MAD 하한
    # (중앙값−k·MAD) 또는 절대 floor 미만이면 제거. 과다제거 방지로 최소 min_keep 유지.
    rejected_slaves: list = []
    if reject_outliers and len(ok) >= max(4, min_keep + 1):
        region = dist_km <= (buffer_m / 1000.0 * 3.0)      # 데크 근방(없으면 전역)
        if not region.any():
            region = np.isfinite(coh_stack[0])
        pcoh = np.array([float(np.nanmean(np.where(region, c, np.nan))) for c in coh_stack])
        med = float(np.median(pcoh)); mad = float(np.median(np.abs(pcoh - med)))
        lo = max(med - outlier_k * 1.4826 * mad, coh_floor)
        keep = pcoh >= lo
        if int(keep.sum()) >= min_keep and int((~keep).sum()) > 0:
            rejected_slaves = [{"date": ok[i].sec_date, "coh": round(float(pcoh[i]), 3),
                                "reason": f"deck coh {pcoh[i]:.2f} < {lo:.2f}"}
                               for i in range(len(ok)) if not keep[i]]
            ok = [ok[i] for i in range(len(ok)) if keep[i]]
            coh_stack = [coh_stack[i] for i in range(len(coh_stack)) if keep[i]]
            ph_list = [ph_list[i] for i in range(len(ph_list)) if keep[i]]
    coh_mean = np.mean(coh_stack, axis=0)

    within = dist_km <= (buffer_m / 1000.0)
    finite = np.isfinite(ph0) & (ph0 != 0.0)
    sel = within & finite & (coh_mean >= coh_min)
    idx = np.where(sel.ravel())[0]
    if idx.size == 0:
        raise SnapError(f"데크 {buffer_m:.0f}m 이내 평균코히런스>={coh_min} 점이 없습니다"
                        f"(coh_min 완화 또는 버퍼 확대 필요).")

    pt_lon = glon.ravel()[idx]; pt_lat = glat.ravel()[idx]
    N = idx.size; M = 1 + len(ok)
    scale = -WAVELENGTH_M / (4.0 * math.pi) * 1000.0
    los = np.zeros((N, M))
    dates = [ref_date]
    for k, phk in enumerate(ph_list, start=1):
        dates.append(ok[k - 1].sec_date)
        los[:, k] = phk.ravel()[idx] * scale

    # ── 튀는 슬레이브 2차: 변위 시계열 이상치 epoch(대기교란 등) 자동 제거 ──
    # 각 시점 공간중앙 변위가 로버스트 선형추세에서 크게 벗어나면(대기 스파이크) 그 slave 제거.
    if reject_outliers and M >= max(6, min_keep + 2):
        from datetime import datetime as _dt
        dd = np.array([(_dt.strptime(d, "%Y%m%d") - _dt.strptime(ref_date, "%Y%m%d")).days
                       for d in dates], float)
        emed = np.median(los, axis=0)                  # [M] 시점별 공간중앙 변위
        A0 = np.vstack([dd, np.ones_like(dd)]).T
        resid = emed - A0 @ np.linalg.lstsq(A0, emed, rcond=None)[0]
        rmad = float(np.median(np.abs(resid - np.median(resid))))
        thr = outlier_k * 1.4826 * rmad
        bad = np.abs(resid) > thr
        bad[0] = False                                 # master(기준시점) 제외
        if thr > 0 and int((~bad).sum()) >= min_keep and int(bad.sum()) > 0:
            for i in range(1, M):
                if bad[i]:
                    rejected_slaves.append({"date": dates[i], "coh": None,
                                            "reason": f"LOS 이상 {resid[i]:+.1f}mm(>{thr:.1f})"})
            keepc = ~bad
            los = los[:, keepc]
            dates = [dates[i] for i in range(M) if keepc[i]]
            ok = [ok[i - 1] for i in range(1, M) if keepc[i]]
            ph_list = [ph_list[i - 1] for i in range(1, M) if keepc[i]]
            M = len(dates)
    gbar = coh_mean.ravel()[idx].astype(np.float32)
    incidence = inc0.ravel()[idx].astype(np.float32)
    deck_dist_m = (dist_km.ravel()[idx] * 1000.0).astype(np.float32)

    # ── 기준점 자동 선정·적용: ROI 내·데크 밖 초안정 PS(coh≥ref_min_coh)로 상대변위화 ──
    # 데크 점은 열·하중으로 coh 낮아 기준 부적합 → 교량 밖 안정 지반/건물 PS 를 기준점으로.
    ref_meta = {"applied": False}
    if apply_reference:
        rmask = np.isfinite(coh_mean) & (coh_mean > 0.0)
        if roi_bbox:                                    # ROI(도심) 범위로 제한
            w, s, e, no = roi_bbox
            rmask &= (glon >= w) & (glon <= e) & (glat >= s) & (glat <= no)
        rmask &= ~(dist_km <= max(buffer_m, 60.0) / 1000.0)   # 데크 버퍼 밖(inf 포함)
        rpool = np.where(rmask.ravel())[0]
        cm = coh_mean.ravel()
        if rpool.size:
            rcand = rpool[cm[rpool] >= ref_min_coh]
            rbi = int(rcand[np.argmax(cm[rcand])]) if rcand.size \
                else int(rpool[np.argmax(cm[rpool])])
            ref_los = np.array([0.0] + [ph.ravel()[rbi] * scale for ph in ph_list])
            los = los - ref_los[None, :]                # 데크 LOS − 기준점 LOS = 상대변위
            r_lon, r_lat = float(glon.ravel()[rbi]), float(glat.ravel()[rbi])
            r_dist_m = float(_polyline_dist_km(
                np.array([r_lon]), np.array([r_lat]), geom)[0] * 1000.0)
            ref_meta = {"applied": True, "lon": r_lon, "lat": r_lat,
                        "coherence": float(cm[rbi]), "meets_098": bool(cm[rbi] >= ref_min_coh),
                        "n_candidates": int(rcand.size), "min_coh": float(ref_min_coh),
                        "deck_dist_m": r_dist_m}

    # PS/DS 분류: ADI 가 있으면 **진폭분산 ADI<adi_max=PS**(엄밀, Ferretti 2001),
    # 없으면 코히런스(γ̄≥ps_coh=PS) 1차 분류.
    if amp_pairs:                                   # 쌍별 진폭 → 점별 ADI
        adi_pt = adi_at_points(amp_pairs, pt_lon, pt_lat)
        adi_pt[~np.isfinite(adi_pt)] = 9.99
        scatter_class = np.where(adi_pt < adi_max, 1, 0).astype(np.int8)
        adi_method = f"ADI<{adi_max}"
    elif adi_tif is not None and Path(adi_tif).exists():
        adi_pt = _sample_raster(adi_tif, pt_lon, pt_lat).astype(np.float32)
        adi_pt[~np.isfinite(adi_pt)] = 9.99
        scatter_class = np.where(adi_pt < adi_max, 1, 0).astype(np.int8)  # 1=PS,0=DS
        adi_method = f"ADI<{adi_max}"
    else:
        adi_pt = np.full(N, np.nan, np.float32)
        scatter_class = np.where(gbar >= ps_coh, 1, 0).astype(np.int8)
        adi_method = f"coherence>={ps_coh}(1차)"

    # LOS 속도(mm/yr): los vs 경과일 선형회귀
    from datetime import datetime
    days = np.array([(datetime.strptime(d, "%Y%m%d") -
                      datetime.strptime(ref_date, "%Y%m%d")).days for d in dates], float)
    yr = days / 365.25
    A = np.vstack([yr, np.ones_like(yr)]).T
    vel = np.linalg.lstsq(A, los.T, rcond=None)[0][0].astype(np.float32)   # mm/yr

    epochs = np.array([int(d) for d in dates], dtype=np.int32)
    out_h5 = Path(out_h5); out_h5.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(out_h5, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack([pt_lon, pt_lat]).astype(np.float64))
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=los.astype(np.float32))
        f.create_dataset("coh", data=gbar)
        f.create_dataset("incidenceAngle", data=incidence)
        f.create_dataset("los_velocity_mm_yr", data=vel)
        f.create_dataset("scatterer_class", data=scatter_class)   # 1=PS,0=DS
        f.create_dataset("amplitude_dispersion", data=adi_pt)     # ADI(없으면 NaN)
        f.create_dataset("deck_distance_m", data=deck_dist_m)
        if heading is not None and math.isfinite(heading):
            f.attrs["HEADING"] = float(heading)
        f.attrs["source"] = "SNAP bridge-deck PS/DS (deck buffer, temporal-coherence)"
        f.attrs["RADAR_WAVELENGTH"] = WAVELENGTH_M
        f.attrs["deck_buffer_m"] = float(buffer_m)
        if ref_meta.get("applied"):                     # 기준점(상대변위 기준) 메타
            f.attrs["reference_lonlat"] = [ref_meta["lon"], ref_meta["lat"]]
            f.attrs["reference_coherence"] = ref_meta["coherence"]
            f.attrs["reference_meets_098"] = ref_meta["meets_098"]
            f.attrs["reference_applied"] = True
    n_ps = int((scatter_class == 1).sum())
    return {"n_points": N, "n_ps": n_ps, "n_ds": N - n_ps,
            "buffer_m": buffer_m, "deck_dist_max_m": float(deck_dist_m.max()),
            "coh_mean": float(gbar.mean()), "class_method": adi_method,
            "adi_median": (float(np.nanmedian(adi_pt)) if np.isfinite(adi_pt).any() else None),
            "reference": ref_meta, "n_epochs_used": len(ok),
            "rejected_slaves": rejected_slaves, "out": str(out_h5)}


def select_master_era5(
    scenes: list[str | Path], lat: float, lon: float, *,
    precip_max_mm: float | None = 8.0, humidity_max_pct: float | None = None,
    temp_max_c: float | None = None, temp_min_c: float | None = None,
    select_fn=None,
) -> tuple[str, list[str], object]:
    """⑤ ERA5(강수·습도·온도)로 **master 선정 + 악천후 씬 소거**.

    era5_master.select_master 로 대기 안정도×baseline coherence 최대 master 를 고르고,
    임계 초과(강수/습도/온도) 씬을 제거. 반환: (master_scene 경로, 유지 scenes, MasterSelection).
    네트워크는 select_fn 주입으로 격리(테스트).
    """
    paths = [str(s) for s in scenes]
    by_date = {scene_date(p): p for p in paths}
    dates = sorted(by_date)
    if select_fn is None:
        from .era5_master import select_master as select_fn
    ms = select_fn(lat, lon, dates, scene_names=dates,
                   precip_max_mm=precip_max_mm, humidity_max_pct=humidity_max_pct,
                   temp_max_c=temp_max_c, temp_min_c=temp_min_c)
    excluded = {sw.date for sw in ms.scenes if getattr(sw, "excluded", False)}
    kept = [by_date[d] for d in dates if d not in excluded]
    master = by_date.get(ms.selected_master, kept[0] if kept else paths[0])
    if master not in kept:
        kept = [master] + kept
    return master, kept, ms


def run(scenes: list[str | Path], lat: float, lon: float, out_dir: str | Path,
        *, out_h5: str | Path | None = None, reference: str | Path | None = None,
        dem: str = "SRTM 1Sec HGT", coh_min: float = 0.3, radius_km: float = 3.0,
        graph_dir: str | Path | None = None, gpt: str | None = None,
        era5_master: bool = False, precip_max_mm: float | None = 8.0,
        humidity_max_pct: float | None = None, temp_max_c: float | None = None,
        temp_min_c: float | None = None) -> SnapRunResult:
    """전체: 스타 네트워크 처리 → Track H5. 임의 한국 교량 재사용 진입점.

    era5_master=True 면 ⑤ ERA5(강수·습도·온도)로 master(reference) 선정 + 악천후 씬 소거
    후 처리. reference 를 명시하면 그 값이 우선.
    """
    scenes = [str(s) for s in scenes]
    weather = None
    if era5_master and reference is None:
        try:
            reference, scenes, weather = select_master_era5(
                scenes, lat, lon, precip_max_mm=precip_max_mm,
                humidity_max_pct=humidity_max_pct, temp_max_c=temp_max_c, temp_min_c=temp_min_c)
        except (ValueError, OSError) as e:            # ERA5 실패 → 최이른 폴백
            weather = f"ERA5 master 실패(최이른 폴백): {e}"

    res = process_star_network(scenes, lat, lon, out_dir, reference=reference,
                               dem=dem, graph_dir=graph_dir, gpt=gpt)
    res.weather = weather
    ref = str(reference) if reference else min([str(s) for s in scenes], key=scene_date)
    hd = platform_heading(ref, res.burst.subswath)
    out_h5 = str(out_h5) if out_h5 else str(Path(out_dir) / f"track_snap_{res.reference}.h5")
    res.n_points = build_track_h5(res.pairs, res.reference, out_h5, lat=lat, lon=lon,
                                  coh_min=coh_min, radius_km=radius_km, heading=hd)
    res.track_h5 = out_h5
    return res


@dataclass
class BridgeResult:
    name: str
    lat: float
    lon: float
    track_h5: str | None
    n_points: int
    burst: BurstLoc | None
    error: str | None = None

    def as_dict(self) -> dict:
        return {"name": self.name, "lat": self.lat, "lon": self.lon,
                "track_h5": self.track_h5, "n_points": self.n_points,
                "burst": None if not self.burst else
                f"{self.burst.subswath}#{self.burst.burst_index}",
                "error": self.error}


_GRAPH_AMP = "coreg_amp_tc.xml"    # 2장 코레지→지오코딩 진폭(band1=기준·band2=보조)


def amplitude_pairs(
    scenes: list[str | Path], lat: float, lon: float, out_dir: str | Path,
    *, reference: str | Path | None = None, burst: BurstLoc | None = None,
    dem: str = "SRTM 1Sec HGT", gpt: str | None = None,
    graph_dir: str | Path | None = None, skip_existing: bool = True,
    timeout: int = 3600,
) -> list[str]:
    """ADI 용 **쌍별 지오코딩 진폭** 산출 — 각 (기준,보조) 2장 코레지→진폭 GeoTIFF.

    8장 단일 Back-Geocoding 은 메모리 스래싱(멈춤) → 쌍별 경량(~3분/쌍). 반환: amp tif
    경로들(band1=기준강도·band2=보조강도). adi_at_points 로 점별 ADI 계산에 쓴다.
    """
    scenes = [str(s) for s in scenes]
    gpt = gpt or find_gpt()
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    ref = str(reference) if reference else min(scenes, key=scene_date)
    if burst is None:
        burst = find_bridge_burst(ref, lat, lon)
    graph = str((Path(graph_dir) if graph_dir else _default_graph_dir()) / _GRAPH_AMP)
    rd = scene_date(ref)
    amps: list[str] = []
    for sec in sorted([s for s in scenes if str(s) != ref], key=scene_date):
        tif = str(out / f"amp_{rd}_{scene_date(sec)}.tif")
        if not (skip_existing and Path(tif).exists() and Path(tif).stat().st_size > 0):
            args = [gpt, graph, f"-PrefFile={ref}", f"-PsecFile={sec}",
                    f"-Psubswath={burst.subswath}", f"-PfirstBurst={burst.burst_index}",
                    f"-PlastBurst={burst.burst_index}", f"-PdemName={dem}", f"-PoutFile={tif}"]
            try:
                subprocess.run(args, capture_output=True, timeout=timeout)
            except (OSError, subprocess.SubprocessError):
                continue
        if Path(tif).exists():
            amps.append(tif)
    return amps


def fuse_snap_asc_desc(asc_h5: str | Path, desc_h5: str | Path,
                       out_h5: str | Path | None = None,
                       *, min_desc_epochs: int = 5) -> dict:
    """⑦ 상승·하강 SNAP Track → **연직(U)·수평(H) 분해**. 불가/부족 시 **단일 궤도 폴백**.

    desc 시점이 min_desc_epochs 미만이면 융합 시도 없이 단일(asc). fuse_asc_desc 가
    FusionError(입사각·heading·정합·기하 부족)면 단일 폴백. 성공 시 los_mm=연직 U 로,
    horizontal_mm=종축 H 로 out_h5 기록.
    """
    import h5py
    import numpy as np

    from .fusion import FusionError, fuse_asc_desc
    from .track_reader import read_track_h5

    asc = read_track_h5(str(asc_h5))
    desc = read_track_h5(str(desc_h5))
    n_desc = int(desc.los.shape[1])
    if n_desc < min_desc_epochs:
        return {"mode": "single", "reason": f"하강 시점 부족({n_desc}<{min_desc_epochs}) → 단일(asc)",
                "out": str(asc_h5)}
    try:
        fr = fuse_asc_desc(asc, desc)
    except FusionError as e:
        return {"mode": "single", "reason": f"{e} → 단일(asc)", "out": str(asc_h5)}

    out_h5 = str(out_h5) if out_h5 else str(Path(asc_h5).with_name("track_vertical.h5"))
    U, H, t = fr.vertical, fr.longitudinal, fr.track
    with h5py.File(out_h5, "w") as f:
        f.create_dataset("pixel_lonlat", data=t.lonlat.astype(np.float64))
        f.create_dataset("epochs", data=np.array([int(d) for d in
                         [s.decode() if isinstance(s, bytes) else str(s) for s in t.date_labels]],
                         dtype=np.int32))
        f.create_dataset("los_mm", data=U.astype(np.float32))          # 연직 U 를 주 변위로
        f.create_dataset("vertical_mm", data=U.astype(np.float32))
        f.create_dataset("horizontal_mm", data=H.astype(np.float32))
        f.create_dataset("coh", data=t.coherence.astype(np.float32))
        if t.incidence is not None:
            f.create_dataset("incidenceAngle", data=t.incidence.astype(np.float32))
        f.attrs["source"] = "SNAP asc+desc 연직분해(fuse_asc_desc)"
    return {"mode": "fused", "n_points": int(U.shape[0]), "n_epochs": int(U.shape[1]),
            "vertical_mm_range": [float(np.nanmin(U)), float(np.nanmax(U))], "out": out_h5}


def run_batch(
    scenes: list[str | Path], bridges: list[dict], out_dir: str | Path,
    *, reference: str | Path | None = None, dem: str = "SRTM 1Sec HGT",
    graph_dir: str | Path | None = None, gpt: str | None = None,
    coh_min: float = 0.3, radius_km: float = 3.0,
) -> list[BridgeResult]:
    """여러 교량 배치 처리. 같은 (subswath,burst) 교량은 코레지+간섭도를 **1번만** 하고
    Track H5 만 교량별로 생성(전체 한국 스케일에서 재처리 회피). bridges: [{name,lat,lon}].
    """
    scenes = [str(s) for s in scenes]
    gpt = gpt or find_gpt()
    ref = str(reference) if reference else min(scenes, key=scene_date)
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)

    groups: dict[tuple[str, int], list[dict]] = {}
    detected: dict[tuple[str, int], BurstLoc] = {}
    results: list[BridgeResult] = []
    for b in bridges:
        try:
            la, lo = float(b["lat"]), float(b["lon"])
        except (KeyError, ValueError, TypeError):
            results.append(BridgeResult(str(b.get("name", "?")), 0.0, 0.0, None, 0, None,
                                        "lat/lon 누락/오류"))
            continue
        bl = find_bridge_burst(ref, la, lo)
        key = (bl.subswath, bl.burst_index)
        groups.setdefault(key, []).append(b)
        detected.setdefault(key, bl)

    for key, members in groups.items():
        bl = detected[key]
        burst_dir = out / f"{bl.subswath}_b{bl.burst_index}"
        first = members[0]
        run_res = process_star_network(
            scenes, float(first["lat"]), float(first["lon"]), burst_dir,
            reference=ref, dem=dem, graph_dir=graph_dir, gpt=gpt, burst=bl)
        hd = platform_heading(ref, bl.subswath)
        for b in members:
            name = str(b.get("name") or f"{b['lat']}_{b['lon']}")
            safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", name)
            th5 = str(out / f"track_{safe}.h5")
            try:
                n = build_track_h5(run_res.pairs, run_res.reference, th5,
                                   lat=float(b["lat"]), lon=float(b["lon"]),
                                   coh_min=coh_min, radius_km=radius_km, heading=hd)
                results.append(BridgeResult(name, float(b["lat"]), float(b["lon"]), th5, n, bl))
            except SnapError as e:
                results.append(BridgeResult(name, float(b["lat"]), float(b["lon"]),
                                            None, 0, bl, str(e)))
    return results
