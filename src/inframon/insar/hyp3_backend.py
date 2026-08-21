"""ASF HyP3 클라우드 InSAR 백엔드 — 로컬 SAR 연산·WSL·SNAP 전부 불필요.

교량 좌표만 주면 처리는 ASF 클라우드(HyP3)가 한다:
  1. asf_search 로 교량을 덮는 **S1 burst 시계열** 조회(SLC-BURST 데이터셋)
  2. HyP3 `INSAR_ISCE_BURST` 잡을 **스타 네트워크**(기준 vs 각 보조)로 제출·폴링·다운로드
  3. 산출 GeoTIFF(unw_phase/corr/lv_theta/dem) → inframon **Track H5 계약**
     (pixel_lonlat/epochs/los_mm/coh/incidenceAngle/height)

SLC 원본(장당 4GB+) 다운로드도, 간섭계 연산도 로컬에서 하지 않으므로 무료 Earthdata
계정과 노트북만 있으면 어느 OS 에서든 돈다. snap_backend(Windows 네이티브)와 같은
스타 네트워크·burst 철학이라 하류(track_reader→4대 엔진)는 그대로 재사용된다.
품질은 SARvey full PSI(WSL) > SNAP/HyP3 실용 경로 — 자리매김은 docs 참고.

네트워크/외부서비스는 `_search_bursts`(asf_search 조회)와 `_run_jobs`(hyp3_sdk
제출·폴링·다운로드) 두 곳으로 격리한다(테스트에서 monkeypatch). 변환기
`products_to_track_h5` 는 완전 오프라인이라 이미 받아둔 산출물 폴더에도 쓴다
(`--hyp3-import`).

주의: HyP3 는 월간 크레딧 쿼터가 있다(스타 N쌍 = 잡 N개 — 교량 1곳 연 8~12쌍이면 소량).
"""

from __future__ import annotations

import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .snap_backend import WAVELENGTH_M


class Hyp3Error(RuntimeError):
    """HyP3 백엔드 실패(조회/잡/산출물/변환)."""


@dataclass
class Hyp3PairProduct:
    """간섭도 쌍 1개의 산출물 폴더(오프라인 변환 입력)."""

    product_dir: Path
    date1: str            # 산출물 이름의 이른 날짜(YYYYMMDD) = HyP3 기준(reference)
    date2: str            # 늦은 날짜 = HyP3 보조(secondary)
    ok: bool = True
    error: str | None = None


@dataclass
class Hyp3RunResult:
    """run() 결과 요약."""

    track_h5: str
    n_points: int
    ref_date: str
    epochs: list[str]
    n_ok: int
    n_fail: int
    burst_id: str | None = None
    failures: list[str] = field(default_factory=list)


# ── 순수 로직(네트워크 불필요) ────────────────────────────────────────────────

_DATE_RE = re.compile(r"(?<!\d)(20\d{6})(?!\d)")


def two_dates(name: str) -> tuple[str, str]:
    """산출물 폴더/파일 이름에서 취득일 2개(YYYYMMDD, 오름차순)를 뽑는다.

    HyP3 이름 관례(burst: S1_136231_IW2_20200604_20200616_VV_INT80_XXXX,
    GAMMA: S1AA_20200604T…_20200616T…_…)를 모두 8자리 날짜 정규식으로 처리한다.
    """
    seen: list[str] = []
    for d in _DATE_RE.findall(name):
        if d not in seen:
            seen.append(d)
    if len(seen) < 2:
        raise Hyp3Error(f"이름에서 취득일 쌍을 찾지 못함: {name}")
    a, b = seen[0], seen[1]
    return (a, b) if a <= b else (b, a)


def find_product_dirs(root: str | Path) -> list[Hyp3PairProduct]:
    """root 아래에서 unw_phase GeoTIFF 를 가진 산출물 폴더들을 찾는다(오프라인)."""
    root = Path(root)
    dirs: list[Path] = []
    if list(root.glob("*_unw_phase.tif")):
        dirs = [root]
    else:
        dirs = sorted({p.parent for p in root.rglob("*_unw_phase.tif")})
    out = []
    for d in dirs:
        d1, d2 = two_dates(d.name if _DATE_RE.search(d.name) else
                           next(d.glob("*_unw_phase.tif")).name)
        out.append(Hyp3PairProduct(product_dir=d, date1=d1, date2=d2))
    if not out:
        raise Hyp3Error(f"HyP3 산출물(*_unw_phase.tif)을 찾지 못함: {root}")
    return out


def infer_ref_date(pairs: list[Hyp3PairProduct]) -> str:
    """스타 네트워크의 기준일 = 모든 쌍에 공통으로 나타나는 날짜."""
    from collections import Counter
    cnt = Counter()
    for p in pairs:
        cnt[p.date1] += 1
        cnt[p.date2] += 1
    date, n = cnt.most_common(1)[0]
    if len(pairs) > 1 and n < len(pairs):
        raise Hyp3Error("스타 네트워크가 아님 — 모든 쌍에 공통인 기준일이 없습니다.")
    return date


def plan_star_pairs(dates_granules: list[tuple[str, str]], count: int = 8,
                    ref_date: str | None = None) -> tuple[str, list[tuple[str, str]]]:
    """(date, granule) 시계열 → (기준 granule, [(granule1, granule2), …]) 스타 쌍.

    최신 count 장을 취해 기준=중간 날짜(계절 대칭). HyP3 는 이른 쪽이 reference 이므로
    각 쌍은 날짜 오름차순으로 배열한다(부호는 변환기에서 기준 대비로 복원).
    """
    if len(dates_granules) < 2:
        raise Hyp3Error(f"burst 시계열이 부족합니다({len(dates_granules)}장) — 최소 2장 필요")
    sel = sorted(dates_granules)[-max(2, int(count)):]
    by_date = dict(sel)
    if ref_date is not None:
        if ref_date not in by_date:
            raise Hyp3Error(f"기준일 {ref_date} 이 선택된 시계열에 없습니다.")
    else:
        ref_date = sel[len(sel) // 2][0]
    ref_g = by_date[ref_date]
    pairs = []
    for d, g in sel:
        if d == ref_date:
            continue
        pairs.append((g, ref_g) if d < ref_date else (ref_g, g))
    return ref_g, pairs


def products_to_track_h5(
    pairs: list[Hyp3PairProduct], out_h5: str | Path, *,
    lat: float, lon: float, ref_date: str | None = None,
    coh_min: float = 0.3, radius_km: float = 3.0, max_points: int = 20000,
) -> int:
    """HyP3 산출 GeoTIFF 들 → inframon Track H5 (완전 오프라인). 반환: 점 수 N.

    산출물별 unw_phase(rad)·corr 을 읽고, lv_theta(look 고도각, rad)→입사각(deg),
    dem→점별 고도(m)로 변환한다. los_mm = −λ/4π·Δφ·1000 (snap_backend 와 동일 부호,
    양수=위성 접근). 쌍이 (보조, 기준) 순서(보조가 더 이른 날)면 부호를 뒤집어
    항상 '기준→보조' 변위로 맞춘다. 교량 반경 내 coh≥coh_min 점을 고르고 부족하면
    반경을 2배씩 넓힌다(snap_backend.build_track_h5 와 동일 정책).
    """
    import h5py
    import numpy as np
    import rasterio

    pairs = [p for p in pairs if p.ok]
    if not pairs:
        raise Hyp3Error("성공한 간섭도 쌍이 없어 Track 을 만들 수 없습니다.")
    if ref_date is None:
        ref_date = infer_ref_date(pairs)

    def _band(d: Path, suffix: str) -> Path | None:
        hits = sorted(d.glob(f"*_{suffix}.tif"))
        return hits[0] if hits else None

    # 기준 격자 = 첫 쌍의 unw_phase
    first = _band(pairs[0].product_dir, "unw_phase")
    with rasterio.open(first) as ds0:
        ph0 = ds0.read(1).astype(np.float64)
        H, W = ph0.shape
        rows, cols = np.mgrid[0:H, 0:W]
        xs, ys = rasterio.transform.xy(ds0.transform, rows.ravel(), cols.ravel())
        glon = np.asarray(xs).reshape(H, W)
        glat = np.asarray(ys).reshape(H, W)
        crs = ds0.crs
    if crs is not None and not crs.to_epsg() == 4326:
        # HyP3 는 UTM 산출이 기본 — 경위도로 역투영해 계약(pixel_lonlat=WGS84)에 맞춘다.
        from pyproj import Transformer
        tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        glon, glat = tr.transform(glon, glat)
        glon = np.asarray(glon).reshape(H, W)
        glat = np.asarray(glat).reshape(H, W)

    corr0_p = _band(pairs[0].product_dir, "corr")
    with rasterio.open(corr0_p) as ds:
        coh0 = ds.read(1).astype(np.float64)

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
        raise Hyp3Error(f"교량 반경 {r:.0f}km 내 coh≥{coh_min} 점이 없습니다(커버리지 부족).")
    if idx.size > max_points:
        order = np.argsort(-coh0.ravel()[idx])[:max_points]
        idx = idx[order]

    pt_lon = glon.ravel()[idx]
    pt_lat = glat.ravel()[idx]
    N = idx.size
    M = 1 + len(pairs)
    los = np.zeros((N, M), dtype=np.float64)      # col0 = 기준(0)
    coh_acc = coh0.ravel()[idx].copy()
    scale = -WAVELENGTH_M / (4.0 * math.pi) * 1000.0

    dates = [ref_date]
    for k, p in enumerate(pairs, start=1):
        sec = p.date2 if p.date1 == ref_date else p.date1
        sign = 1.0 if p.date1 == ref_date else -1.0   # (보조,기준) 순서면 부호 반전
        dates.append(sec)
        with rasterio.open(_band(p.product_dir, "unw_phase")) as ds:
            phk = ds.read(1).astype(np.float64)
        cp = _band(p.product_dir, "corr")
        if cp is not None:
            with rasterio.open(cp) as ds:
                coh_acc += ds.read(1).astype(np.float64).ravel()[idx]
        los[:, k] = sign * phk.ravel()[idx] * scale
    coh_mean = (coh_acc / M).astype(np.float32)

    # 입사각: lv_theta(수평 기준 look 고도각, rad) → incidence(deg) = 90 − el
    incidence = None
    lv = _band(pairs[0].product_dir, "lv_theta")
    if lv is not None:
        with rasterio.open(lv) as ds:
            el = ds.read(1).astype(np.float64).ravel()[idx]
        inc = 90.0 - np.degrees(el)
        if np.isfinite(inc).any():
            incidence = inc.astype(np.float32)

    # 점별 고도: 산출물 동봉 DEM → track height (import 때 z 로 쓰여 z=0 경고가 사라진다)
    height = None
    demp = _band(pairs[0].product_dir, "dem")
    if demp is not None:
        with rasterio.open(demp) as ds:
            hv = ds.read(1).astype(np.float64).ravel()[idx]
        if np.isfinite(hv).any():
            height = hv.astype(np.float32)

    epochs = np.array([int(d) for d in dates], dtype=np.int32)
    out_h5 = Path(out_h5)
    out_h5.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(out_h5, "w") as f:
        f.create_dataset("pixel_lonlat", data=np.column_stack([pt_lon, pt_lat]).astype(np.float64))
        f.create_dataset("epochs", data=epochs)
        f.create_dataset("los_mm", data=los.astype(np.float32))
        f.create_dataset("coh", data=coh_mean)
        if incidence is not None:
            f.create_dataset("incidenceAngle", data=incidence)
        if height is not None:
            f.create_dataset("height", data=height)
        f.attrs["source"] = "HyP3(cloud) INSAR_ISCE_BURST star-network unw-phase → LOS"
        f.attrs["RADAR_WAVELENGTH"] = WAVELENGTH_M
    return N


# ── 네트워크 격리 지점(테스트에서 monkeypatch) ────────────────────────────────

def _search_bursts(lat: float, lon: float, start: str, end: str,
                   polarization: str = "VV") -> tuple[str | None, list[tuple[str, str]]]:
    """ASF SLC-BURST 조회 → (최다 시계열 fullBurstID, [(YYYYMMDD, granule), …])."""
    import asf_search as asf

    results = asf.search(intersectsWith=f"POINT({lon} {lat})", dataset=asf.DATASET.SLC_BURST,
                         polarization=polarization, start=start, end=end)
    by_id: dict[str, dict[str, str]] = {}
    for r in results:
        props = r.properties
        bid = (props.get("burst") or {}).get("fullBurstID")
        name = props.get("sceneName") or props.get("fileID")
        date = (props.get("startTime") or "")[:10].replace("-", "")
        if bid and name and len(date) == 8:
            by_id.setdefault(bid, {})[date] = name
    if not by_id:
        return None, []
    bid = max(by_id, key=lambda k: len(by_id[k]))
    return bid, sorted(by_id[bid].items())


def _run_jobs(pairs: list[tuple[str, str]], out_dir: Path, *, name: str,
              username: str | None, password: str | None, token: str | None,
              ) -> list[tuple[tuple[str, str], Path | None, str | None]]:
    """HyP3 잡 제출→폴링→다운로드→해제. 반환: [(쌍, 산출폴더|None, 오류|None), …]."""
    import hyp3_sdk as sdk

    if token:
        hyp3 = sdk.HyP3(prompt=False, username=None, password=None)  # 토큰은 netrc 미지원 —
        hyp3.session.headers["Authorization"] = f"Bearer {token}"     # 세션 헤더로 직접
    else:
        hyp3 = sdk.HyP3(username=username, password=password, prompt=False)
    batch = sdk.Batch()
    for g1, g2 in pairs:
        batch += hyp3.submit_insar_isce_burst_job(g1, g2, name=name)
    batch = hyp3.watch(batch)
    out: list[tuple[tuple[str, str], Path | None, str | None]] = []
    for pair, job in zip(pairs, batch.jobs):
        if not job.succeeded():
            out.append((pair, None, f"HyP3 잡 실패: {getattr(job, 'status_code', '?')}"))
            continue
        try:
            files = job.download_files(str(out_dir))
            pdir = None
            for fp in files:
                fp = Path(fp)
                if fp.suffix == ".zip":
                    with zipfile.ZipFile(fp) as z:
                        z.extractall(out_dir)
                    pdir = out_dir / fp.stem
                    fp.unlink(missing_ok=True)
            out.append((pair, pdir, None))
        except OSError as exc:
            out.append((pair, None, f"다운로드 실패: {exc}"))
    return out


# ── 오케스트레이터 ───────────────────────────────────────────────────────────

def run(lat: float, lon: float, out_dir: str | Path, out_h5: str | Path, *,
        count: int = 8, start: str = "2024-01-01", end: str = "2025-07-01",
        username: str | None = None, password: str | None = None,
        token: str | None = None, coh_min: float = 0.3) -> Hyp3RunResult:
    """교량 좌표 → burst 조회 → 스타 잡 제출·수집 → Track H5. (조회·잡은 격리 함수 경유)"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    bid, series = _search_bursts(lat, lon, start, end)
    if not series:
        raise Hyp3Error(f"교량({lat},{lon})을 덮는 S1 burst 를 찾지 못함 — 기간/좌표 확인")
    ref_g, pairs = plan_star_pairs(series, count=count)
    date_of = {g: d for d, g in series}

    done = _run_jobs(pairs, out_dir, name=f"inframon_{lat:.3f}_{lon:.3f}",
                     username=username, password=password, token=token)
    products: list[Hyp3PairProduct] = []
    failures: list[str] = []
    for (g1, g2), pdir, err in done:
        d1, d2 = sorted((date_of[g1], date_of[g2]))
        if pdir is None:
            failures.append(f"{d1}-{d2}: {err}")
            products.append(Hyp3PairProduct(Path("."), d1, d2, ok=False, error=err))
        else:
            products.append(Hyp3PairProduct(pdir, d1, d2))

    ref_date = date_of[ref_g]
    n = products_to_track_h5(products, out_h5, lat=lat, lon=lon,
                             ref_date=ref_date, coh_min=coh_min)
    epochs = [ref_date] + [p.date1 if p.date2 == ref_date else p.date2
                           for p in products if p.ok]
    return Hyp3RunResult(track_h5=str(out_h5), n_points=n, ref_date=ref_date,
                         epochs=sorted(epochs), n_ok=sum(p.ok for p in products),
                         n_fail=len(failures), burst_id=bid, failures=failures)
