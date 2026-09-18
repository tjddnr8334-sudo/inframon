"""StaMPS 네이티브 출력 읽기 — SARvey 대신 쓸 수 있는 두 번째 길.

SARvey 는 좋은 도구지만 논문으로 인용하기가 마땅치 않다. StaMPS 는 Hooper 2004·2007
로 널리 인용되고 구현도 공개돼 있어, **같은 자리에 갈아 끼울 수 있는** 경로가 있어야
한다. 이 모듈은 StaMPS 가 처리폴더에 남기는 .mat 을 그대로 읽는다 — `ps_plot` 으로
따로 내보내지 않아도 된다.

읽는 파일(있는 것만 쓴다)
  ps2.mat    lonlat[N,2] · day[M] (MATLAB datenum) · master_day · bperp · mean_incidence
  phuw2.mat  ph_uw[N,M]  언래핑 위상[rad] — 여기서 LOS[mm] 로 바꾼다
  pm2.mat    coh_ps[N]   PS 품질
  la2.mat    la[N]       시선각[rad] — 점별 입사각
  hgt2.mat   hgt[N]      고도[m]
  bp2.mat    bperp_mat[N,M]  점별 수직기선 — 있으면 ps2 의 값보다 이걸 쓴다
  parms.mat  lambda      파장[m] — 없으면 Sentinel-1 0.0555 m 로 본다

**위상 → 변위** 는 `d[m] = −λ/(4π)·φ` 다. 부호 규약은 리포 전체와 같다 —
**양수 = 위성 접근**(hyp3_backend·snap_backend 와 동일).

**함정 세 가지를 막아 둔다.**
  ① MATLAB v7.3 은 HDF5 이고 **열우선**이라 h5py 로 읽으면 전치돼 나온다.
  ② `day` 는 MATLAB datenum(0000-01-01 기준)이라 719529 를 빼야 유닉스 날짜가 된다.
  ③ 단일마스터(PS) 모드는 마스터 열이 있기도 없기도 하다. 점 수·시점 수로 맞춰 보고
     안 맞으면 **이유를 적고 멈춘다** — 조용히 자르지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np

RADAR_WAVELENGTH_M = 0.05546576          # Sentinel-1 C-band
MATLAB_EPOCH_OFFSET = 719529             # datenum(1970-01-01)


class StampsError(RuntimeError):
    """StaMPS 폴더를 읽지 못했을 때 — 왜 못 읽는지 메시지에 담는다."""


@dataclass
class StampsStack:
    lonlat: np.ndarray                   # [N,2]
    epochs: list                         # [M] 'YYYYMMDD'
    los_mm: np.ndarray                   # [N,M] 양수=위성 접근
    bperp_m: np.ndarray                  # [M] 또는 [N,M]
    incidence_deg: np.ndarray            # [N]
    coherence: np.ndarray                # [N]
    height_m: np.ndarray | None = None
    slant_range_m: float | None = None   # ps2.mat mean_range — 잔차고도 환산에 쓴다
    master: str | None = None
    wavelength_m: float = RADAR_WAVELENGTH_M
    sources: dict = field(default_factory=dict)

    @property
    def n_points(self) -> int:
        return int(self.lonlat.shape[0])

    @property
    def n_epochs(self) -> int:
        return len(self.epochs)


def _load_mat(path: Path) -> dict:
    """v5 는 scipy, v7.3(HDF5)은 h5py — 둘 다 dict[str, ndarray] 로."""
    try:
        from scipy.io import loadmat

        m = loadmat(str(path), squeeze_me=True, struct_as_record=False)
        return {k: v for k, v in m.items() if not k.startswith("__")}
    except NotImplementedError:
        import h5py

        out: dict = {}
        with h5py.File(path, "r") as f:
            for k in f:
                try:
                    a = np.asarray(f[k][()])
                except (OSError, TypeError):
                    continue
                # MATLAB v7.3 은 열우선이라 2차원은 전치해야 [N,M] 이 된다.
                out[k] = a.T if a.ndim == 2 else a
        return out
    except (ValueError, OSError) as exc:
        raise StampsError(f"{path.name} 을 읽지 못했다: {exc}") from exc


def _pick(d: dict, *names):
    for n in names:
        if n in d:
            return np.asarray(d[n])
    return None


def datenum_to_ymd(dn) -> list:
    """MATLAB datenum → 'YYYYMMDD'. 이미 YYYYMMDD 꼴이면 그대로 둔다."""
    out = []
    for v in np.atleast_1d(np.asarray(dn)).ravel():
        f = float(v)
        if f > 19000000:                      # 이미 YYYYMMDD
            out.append(f"{int(round(f)):08d}")
            continue
        d = date(1970, 1, 1) + timedelta(days=f - MATLAB_EPOCH_OFFSET)
        out.append(d.strftime("%Y%m%d"))
    return out


def phase_to_los_mm(ph_rad: np.ndarray, wavelength_m: float) -> np.ndarray:
    """φ[rad] → LOS[mm], 양수 = 위성 접근. snap/hyp3 백엔드와 같은 부호 규약."""
    return -wavelength_m / (4.0 * np.pi) * np.asarray(ph_rad, float) * 1000.0


def read_stamps(folder: str | Path, *, wavelength_m: float | None = None,
                patch: str | None = None) -> StampsStack:
    """StaMPS 처리폴더 → StampsStack. 없는 파일은 건너뛰되 필수는 이유를 적고 멈춘다.

    `patch` 를 주면 그 하위 폴더(PATCH_1 …)를 본다. 안 주면 폴더에 바로 있는 .mat 을
    쓰고, 없으면 PATCH_* 중 첫 번째를 쓴다(그 사실을 sources 에 적는다).
    """
    root = Path(folder)
    if not root.is_dir():
        raise StampsError(f"StaMPS 폴더가 없다: {root}")
    here = root / patch if patch else root
    if not (here / "ps2.mat").exists():
        cand = sorted(p for p in root.glob("PATCH_*") if (p / "ps2.mat").exists())
        if not cand:
            raise StampsError(
                f"{here} 에 ps2.mat 이 없다 — StaMPS 처리폴더(또는 PATCH_*)를 가리켜야 한다")
        here = cand[0]

    src = {"dir": str(here)}
    ps = _load_mat(here / "ps2.mat")
    src["ps2"] = sorted(ps)

    lonlat = _pick(ps, "lonlat")
    if lonlat is None or lonlat.ndim != 2 or lonlat.shape[1] != 2:
        raise StampsError("ps2.mat 에 lonlat[N,2] 가 없다")
    lonlat = np.asarray(lonlat, float)
    n = lonlat.shape[0]

    day = _pick(ps, "day")
    if day is None:
        raise StampsError("ps2.mat 에 day 가 없다 — 취득일을 알 수 없다")
    epochs = datenum_to_ymd(day)
    m = len(epochs)

    master = None
    md = _pick(ps, "master_day")
    if md is not None and np.size(md):
        master = datenum_to_ymd(md)[0]

    # 파장 — parms.mat 에 있으면 그것이 우선이다(센티넬이 아닐 수 있다).
    lam = wavelength_m
    if lam is None:
        pm = here / "parms.mat"
        if pm.exists():
            try:
                lam = float(np.ravel(_pick(_load_mat(pm), "lambda"))[0])
                src["lambda"] = "parms.mat"
            except (StampsError, TypeError, IndexError, ValueError):
                lam = None
    if lam is None:
        lam = RADAR_WAVELENGTH_M
        src.setdefault("lambda", "기본값(Sentinel-1 0.05547 m) — parms.mat 없음")

    # 언래핑 위상 → LOS
    puw = here / "phuw2.mat"
    if not puw.exists():
        raise StampsError(f"{here} 에 phuw2.mat 이 없다 — 언래핑 결과가 있어야 한다")
    ph = _pick(_load_mat(puw), "ph_uw", "ph_uw_sb", "ph")
    if ph is None:
        raise StampsError("phuw2.mat 에 ph_uw 가 없다")
    ph = np.atleast_2d(np.asarray(ph, float))
    if ph.shape[0] != n and ph.shape[1] == n:
        ph = ph.T                                     # 열우선 흔적 — 되돌린다
    if ph.shape[0] != n:
        raise StampsError(f"ph_uw 점 수 {ph.shape[0]} 가 lonlat {n} 과 다르다")
    if ph.shape[1] != m:
        raise StampsError(
            f"ph_uw 시점 수 {ph.shape[1]} 가 day {m} 과 다르다 — 단일마스터/소기선 "
            "모드가 섞였거나 마스터 열이 빠졌다. 자르지 않고 멈춘다")
    los = phase_to_los_mm(ph, lam)
    src["phuw2"] = "ph_uw → LOS[mm] (양수=위성 접근)"

    # 수직기선 — 점별(bp2)이 있으면 그것을 쓴다.
    bperp = None
    bp = here / "bp2.mat"
    if bp.exists():
        b = _pick(_load_mat(bp), "bperp_mat")
        if b is not None:
            b = np.atleast_2d(np.asarray(b, float))
            if b.shape[0] != n and b.shape[1] == n:
                b = b.T
            if b.shape == (n, m):
                bperp = b
                src["bperp"] = "bp2.mat bperp_mat[N,M] (점별)"
    if bperp is None:
        b = _pick(ps, "bperp")
        if b is not None:
            b = np.asarray(b, float).ravel()
            if b.size == m:
                bperp = b
                src["bperp"] = "ps2.mat bperp[M]"
    if bperp is None:
        bperp = np.zeros(m)
        src["bperp"] = "없음 — 0 으로 둔다(잔차고도·열팽창을 못 푼다)"

    # 입사각 — la2.mat 의 시선각[rad]. 없으면 ps2 의 평균 입사각.
    inc = None
    la = here / "la2.mat"
    if la.exists():
        v = _pick(_load_mat(la), "la")
        if v is not None and np.size(v) == n:
            inc = np.degrees(np.asarray(v, float).ravel())
            src["incidence"] = "la2.mat la[N] (rad→deg)"
    if inc is None:
        v = _pick(ps, "mean_incidence")
        val = (float(np.ravel(v)[0]) if v is not None and np.size(v) else 39.0)
        if val < 1.6:                                  # 라디안으로 들어온 경우
            val = float(np.degrees(val))
        inc = np.full(n, val)
        src["incidence"] = (f"ps2.mat mean_incidence {val:.1f}°"
                            if v is not None else "없음 — 39° 로 둔다")

    coh = None
    pmf = here / "pm2.mat"
    if pmf.exists():
        v = _pick(_load_mat(pmf), "coh_ps")
        if v is not None and np.size(v) == n:
            coh = np.asarray(v, float).ravel()
            src["coherence"] = "pm2.mat coh_ps[N]"
    if coh is None:
        coh = np.ones(n)
        src["coherence"] = "없음 — 1 로 둔다"

    # 슬랜트 거리 — 잔차고도 K = 1000·B⊥/(R·sinθ) 에 필요하다.
    rng_m = None
    v = _pick(ps, "mean_range")
    if v is not None and np.size(v):
        rng_m = float(np.ravel(v)[0])
        src["slant_range"] = "ps2.mat mean_range"
    else:
        src["slant_range"] = "없음 — 쓰는 쪽에서 기본값을 정해야 한다"

    hgt = None
    hf = here / "hgt2.mat"
    if hf.exists():
        v = _pick(_load_mat(hf), "hgt")
        if v is not None and np.size(v) == n:
            hgt = np.asarray(v, float).ravel()
            src["height"] = "hgt2.mat hgt[N]"

    return StampsStack(lonlat=lonlat, epochs=epochs, los_mm=los, bperp_m=bperp,
                       incidence_deg=inc, coherence=coh, height_m=hgt,
                       slant_range_m=rng_m, master=master,
                       wavelength_m=float(lam), sources=src)


def write_track_h5(stack: StampsStack, out: str | Path) -> Path:
    """StampsStack → inframon Track H5 — SNAP/SARvey 경로와 같은 형식으로.

    같은 형식으로 내보내야 뒤 단계(import_track_h5 → 트윈 → PINN → FRAM)가 그대로
    돌아간다. 어디서 온 자료인지는 attrs['source'] 에 적어 둔다.
    """
    import h5py

    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    bperp = stack.bperp_m
    with h5py.File(p, "w") as h:
        h.create_dataset("pixel_lonlat", data=stack.lonlat.astype(np.float64))
        h.create_dataset("los_mm", data=stack.los_mm.astype(np.float32))
        h.create_dataset("coh", data=stack.coherence.astype(np.float32))
        h.create_dataset("incidenceAngle", data=stack.incidence_deg.astype(np.float32))
        h.create_dataset("epochs", data=np.array(stack.epochs, dtype="S8"))
        # 점별이면 시점 평균을 같이 둔다 — 시점별 값이 뒤 단계에서 필요하다.
        h.create_dataset("bperp_m", data=(bperp.mean(axis=0) if bperp.ndim == 2
                                          else bperp).astype(np.float32))
        if bperp.ndim == 2:
            h.create_dataset("bperp_mat", data=bperp.astype(np.float32))
        if stack.height_m is not None:
            h.create_dataset("height", data=stack.height_m.astype(np.float32))
        h.attrs["RADAR_WAVELENGTH"] = str(stack.wavelength_m)
        h.attrs["unwrapped"] = "True"
        h.attrs["source"] = f"StaMPS {Path(stack.sources.get('dir', '?')).name} — ph_uw → LOS"
        if stack.master:
            h.attrs["master"] = stack.master
        if stack.slant_range_m:
            h.attrs["slant_range_m"] = str(stack.slant_range_m)
        for k, v in stack.sources.items():
            h.attrs[f"stamps_{k}"] = str(v)
    return p
