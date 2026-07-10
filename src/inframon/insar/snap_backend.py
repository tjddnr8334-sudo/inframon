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
    distance_km: float     # 교량 ~ 최근접 격자점 거리
    grid_lat: float
    grid_lon: float


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

    def as_dict(self) -> dict:
        return {
            "reference": self.reference,
            "burst": {"subswath": self.burst.subswath, "index": self.burst.burst_index,
                      "distance_km": round(self.burst.distance_km, 2)},
            "pairs": [{"ref": p.ref_date, "sec": p.sec_date, "ok": p.ok} for p in self.pairs],
            "track_h5": self.track_h5, "n_points": self.n_points,
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


def find_bridge_burst(slc_zip: str | Path, lat: float, lon: float) -> BurstLoc:
    """기준 SLC 에서 교량(lat,lon)에 가장 가까운 subswath·burst(1-based) 판별.

    각 subswath 주석의 geolocationGridPoint 중 교량 최근접 점을 찾고, 그 점의 line 을
    linesPerBurst 로 나눠 burst 인덱스를 얻는다. 세 subswath 중 최근접을 채택.
    """
    best: BurstLoc | None = None
    with zipfile.ZipFile(str(slc_zip)) as z:
        for sw in ("IW1", "IW2", "IW3"):
            try:
                root = _annotation_xml(z, sw)
            except SnapError:
                continue
            st = root.find(".//swathTiming")
            lpb = int(st.find("linesPerBurst").text)
            nb = len(st.find("burstList").findall("burst"))
            for p in root.findall(".//geolocationGridPoint"):
                gla = float(p.find("latitude").text)
                glo = float(p.find("longitude").text)
                ln = int(p.find("line").text)
                d = math.hypot(gla - lat, (glo - lon) * math.cos(math.radians(lat))) * 111.0
                if best is None or d < best.distance_km:
                    bi = min(max(ln // lpb + 1, 1), nb)   # 1-based, clamp
                    best = BurstLoc(sw, bi, d, gla, glo)
    if best is None:
        raise SnapError("SLC 에서 subswath 주석을 읽지 못했습니다.")
    return best


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
) -> SnapRunResult:
    """스타 네트워크(기준 vs 각 보조) 코레지+간섭도+TC. reference 기본=최이른 날짜.

    burst 를 주면 자동판별을 건너뛴다(배치에서 같은 burst 재사용). skip_existing 이면
    이미 만든 지오코딩 산출물(.tif)은 gpt 를 다시 돌리지 않고 재사용(멱등 재개).
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


def run(scenes: list[str | Path], lat: float, lon: float, out_dir: str | Path,
        *, out_h5: str | Path | None = None, reference: str | Path | None = None,
        dem: str = "SRTM 1Sec HGT", coh_min: float = 0.3, radius_km: float = 3.0,
        graph_dir: str | Path | None = None, gpt: str | None = None) -> SnapRunResult:
    """전체: 스타 네트워크 처리 → Track H5. 임의 한국 교량 재사용 진입점."""
    res = process_star_network(scenes, lat, lon, out_dir, reference=reference,
                               dem=dem, graph_dir=graph_dir, gpt=gpt)
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
