"""SNAP 레인 위상 언래핑(snaphu) — 래핑 위상으로 변위를 내던 것을 끊는다.

지금까지 SNAP 레인은 `Interferogram → Deburst → atan2 → TC` 로 끝나, **래핑된 위상**에
−λ/4π 를 곱해 그대로 LOS[mm] 로 썼다. 그러면 변위가 λ/4(≈13.87mm)를 넘는 순간 값이
접혀 들어와 하류(PINN·CRI·트윈)가 물리적으로 무의미한 수를 계산한다.

언래핑은 gpt 그래프 하나로 끝나지 않는다 — snaphu 는 외부 실행파일이라 3단계다:
  ① `coreg_ifg_snaphu_export.xml` : 코레지→간섭도→Goldstein→**SnaphuExport**(레이더 기하)
  ② `snaphu` 실행 : 내보낸 폴더의 snaphu.conf 가 시키는 명령 그대로
  ③ `snaphu_import_tc.xml` : **SnaphuImport** → 언래핑 위상 → Terrain-Correction

**이식성**: snaphu 는 Windows 네이티브 빌드가 흔치 않다. 이 PC 처럼 WSL 에만 있으면
`wsl` 로 건너가 실행한다(경로는 /mnt/<드라이브> 로 변환). 둘 다 없으면 "무엇을 어떻게
설치하면 되는지"를 말하고 멈춘다 — 조용히 래핑 산출을 내놓지 않는다.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

GRAPH_EXPORT = "coreg_ifg_snaphu_export.xml"
GRAPH_IMPORT = "snaphu_import_tc.xml"


class UnwrapError(RuntimeError):
    """언래핑 준비·실행 실패. 메시지에 다음 행동이 들어 있어야 한다."""


@dataclass(frozen=True)
class SnaphuTool:
    kind: str          # "native"(현 OS 실행파일) | "wsl"(WSL 안의 실행파일)
    path: str
    distro: str | None = None

    def describe(self) -> str:
        return f"{self.path} ({'WSL:' + (self.distro or 'default') if self.kind == 'wsl' else '네이티브'})"


def find_snaphu(distro: str | None = None) -> SnaphuTool | None:
    """snaphu 를 찾는다 — 현 OS 우선, 없으면 WSL. 없으면 None."""
    native = shutil.which("snaphu")
    if native:
        return SnaphuTool(kind="native", path=native)
    if not shutil.which("wsl"):
        return None
    args = ["wsl"] + (["-d", distro] if distro else []) + ["--", "bash", "-lc", "command -v snaphu"]
    try:
        r = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or "").strip().splitlines()
    if r.returncode != 0 or not out:
        return None
    return SnaphuTool(kind="wsl", path=out[-1].strip(), distro=distro)


def install_hint() -> str:
    """snaphu 가 없을 때 사용자가 실제로 칠 수 있는 명령."""
    return ("snaphu 를 찾지 못했습니다. 위상 언래핑 없이는 LOS 가 ±λ/4 에 갇혀 물리적 "
            "의미가 없으므로 진행하지 않습니다.\n"
            "  · WSL(Ubuntu) : wsl -- sudo apt-get install -y snaphu\n"
            "  · conda       : conda install -c conda-forge snaphu\n"
            "  · 소스        : https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/")


def to_wsl_path(path: str | Path) -> str:
    """E:\\프로그램\\x → /mnt/e/프로그램/x (WSL 로 건너갈 때만 쓴다).

    문자열을 먼저 본다 — 호스트 OS 로 resolve() 부터 하면 리눅스에서 "E:\\..." 가
    상대경로로 취급돼 cwd 가 앞에 붙는다(리눅스 CI 에서 드러났다). 드라이브 문자가
    없을 때만 현재 OS 기준으로 절대화해 다시 본다.
    """
    s = str(path).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    if m is None:
        s = str(Path(path).resolve()).replace("\\", "/")
        m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/mnt/{m.group(1).lower()}/{m.group(2)}" if m else s


def parse_snaphu_command(conf: str | Path) -> list[str]:
    """snaphu.conf 헤더가 적어 주는 실행 명령을 그대로 쓴다.

    SNAP 이 conf 안에 `#    snaphu -f snaphu.conf Phase_ifg_... 1234` 형태로 실제
    명령줄을 적어 둔다. 파일 이름·너비를 직접 추측하면 SNAP 버전이 바뀔 때 틀리므로
    **적혀 있는 것을 읽는다**.
    """
    text = Path(conf).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if s.startswith("snaphu ") and "-f" in s:
            return s.split()
    raise UnwrapError(
        f"{conf} 에서 snaphu 실행 명령을 찾지 못했습니다 — SnaphuExport 산출이 온전한지 "
        f"확인하세요(파일 목록: {[p.name for p in Path(conf).parent.iterdir()][:8]}).")


def run_snaphu(export_dir: str | Path, *, tool: SnaphuTool | None = None,
               timeout: int = 7200, log_file: str | Path | None = None) -> Path:
    """내보낸 폴더에서 snaphu 를 돌려 언래핑 위상(.hdr)을 만든다. 산출 .hdr 경로 반환."""
    export_dir = Path(export_dir)
    conf = export_dir / "snaphu.conf"
    if not conf.exists():
        raise UnwrapError(f"snaphu.conf 가 없습니다: {conf} — ① SnaphuExport 가 실패했습니다.")
    tool = tool or find_snaphu()
    if tool is None:
        raise UnwrapError(install_hint())

    cmd = parse_snaphu_command(conf)
    if tool.kind == "wsl":
        inner = " ".join(["cd", _sh_quote(to_wsl_path(export_dir)), "&&", *cmd])
        args = ["wsl"] + (["-d", tool.distro] if tool.distro else []) + ["--", "bash", "-lc", inner]
        cwd = None
    else:
        args, cwd = cmd, str(export_dir)

    if log_file:
        with open(log_file, "w", encoding="utf-8") as lf:
            r = subprocess.run(args, cwd=cwd, stdout=lf, stderr=subprocess.STDOUT,
                               timeout=timeout)
        rc, tail = r.returncode, f"로그: {log_file}"
    else:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           errors="replace", timeout=timeout)
        rc, tail = r.returncode, (r.stdout or "")[-400:]
    if rc != 0:
        raise UnwrapError(f"snaphu 실행 실패(rc={rc}) — {tail}")

    # SnaphuExport 가 .hdr 를 **미리** 깔아두므로 hdr 존재만으로는 성공이 아니다.
    # 실제 언래핑 결과는 .img 다 — 그것으로 판정한다(실패를 성공으로 읽지 않게).
    hdrs = [h for h in sorted(export_dir.glob("UnwPhase*.hdr"))
            if h.with_suffix(".img").exists()]
    if not hdrs:
        raise UnwrapError(
            f"snaphu 가 언래핑 산출(UnwPhase*.img)을 만들지 않았습니다: {export_dir} — {tail}")
    return hdrs[0]


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def is_available(distro: str | None = None) -> tuple[bool, str]:
    """(가능한가, 사람이 읽을 상태 한 줄) — ⓪ 시작 탭·--insar-tools 가 쓴다."""
    tool = find_snaphu(distro)
    if tool is None:
        return False, "snaphu 없음 — SNAP 레인은 래핑 위상까지만 (언래핑 필요)"
    return True, f"snaphu {tool.describe()}"


def geo_region(lat: float, lon: float, half_km: float = 2.0) -> str:
    """교량 주변 정사각 WKT — Subset 의 geoRegion 으로 쓴다."""
    import math
    d = half_km / 111.32
    dl = half_km / (111.32 * max(math.cos(math.radians(lat)), 1e-6))
    pts = [(lon - dl, lat - d), (lon + dl, lat - d), (lon + dl, lat + d),
           (lon - dl, lat + d), (lon - dl, lat - d)]
    return "POLYGON ((" + ", ".join(f"{x:.6f} {y:.6f}" for x, y in pts) + "))"


def unwrap_pair(gpt: str, ref: str, sec: str, burst, dem: str, out_tif: str | Path, *,
                target: tuple[float, float] | None = None, half_km: float = 2.0,
                work_dir: str | Path | None = None, graph_dir: str | Path | None = None,
                tool: SnaphuTool | None = None, timeout: int = 7200,
                log_file: str | Path | None = None) -> dict:
    """한 쌍을 **언래핑까지** 처리해 지오코딩 GeoTIFF 를 낸다.

    ① SnaphuExport → ② snaphu → ③ SnaphuImport+TC. 어느 단계에서 실패했는지 알 수 있게
    단계 이름과 로그 경로를 UnwrapError 에 담는다.

    `target=(lat, lon)` 을 주면 교량 주변 ±`half_km` 만 잘라 언래핑한다 — 전 버스트를
    풀면 화소가 3700만이라 수십 분~시간이 걸리고, 교량 밖은 어차피 ⑨에서 버린다.
    """
    from .snap_backend import ifg_band_names, scene_date

    out_tif = Path(out_tif)
    work = Path(work_dir) if work_dir else out_tif.parent / f"snaphu_{out_tif.stem}"
    work.mkdir(parents=True, exist_ok=True)
    gdir = Path(graph_dir) if graph_dir else _graph_dir()
    rd, sd = scene_date(ref), scene_date(sec)
    _, _, cohband = ifg_band_names(burst.subswath, rd, sd)
    logs = Path(log_file).parent if log_file else work

    # ① 내보내기 (+ coherence 별도 ENVI)
    coh_folder = work / "coh_envi"
    rc = _gpt(gpt, str(gdir / GRAPH_EXPORT), [
        f"-PrefFile={ref}", f"-PsecFile={sec}", f"-Psubswath={burst.subswath}",
        f"-PfirstBurst={burst.burst_index}", f"-PlastBurst={burst.burst_index}",
        f"-PdemName={dem}", f"-PtargetFolder={work}", f"-PcohBand={cohband}",
        f"-PcohFolder={coh_folder}",
        f"-PgeoRegion={geo_region(*target, half_km) if target else ''}",
        f"-PwrappedDim={work / 'wrapped.dim'}",
    ], timeout=timeout, log_file=logs / f"unwrap_{rd}_{sd}_export.log")
    export_dir = _find_export_dir(work)
    if rc != 0 or export_dir is None:
        raise UnwrapError(
            f"① SnaphuExport 실패(rc={rc}) — 로그 {logs / f'unwrap_{rd}_{sd}_export.log'}")
    _attach_coherence(export_dir, coh_folder)
    prepare_snaphu_inputs(export_dir)

    # ② snaphu
    hdr = run_snaphu(export_dir, tool=tool, timeout=timeout,
                     log_file=logs / f"unwrap_{rd}_{sd}_snaphu.log")

    # ③ 되가져오기 + 지오코딩
    dim = work / "wrapped.dim"
    if not dim.exists():                          # 구 산출물 호환
        dim = next((p for p in export_dir.glob("*.dim")), None)
    if dim is None or not Path(dim).exists():
        raise UnwrapError(
            f"③ SnaphuImport 입력(래핑 .dim)을 찾지 못했습니다: {work} — ① 로그를 확인하세요.")
    rc = _gpt(gpt, str(gdir / GRAPH_IMPORT), [
        f"-PwrappedDim={dim}", f"-PunwrappedHdr={hdr}",
        f"-PdemName={dem}", f"-PoutFile={out_tif}",
    ], timeout=timeout, log_file=logs / f"unwrap_{rd}_{sd}_import.log")
    if rc != 0 or not out_tif.exists():
        raise UnwrapError(
            f"③ SnaphuImport/TC 실패(rc={rc}) — 로그 {logs / f'unwrap_{rd}_{sd}_import.log'}")
    roles = label_bands(out_tif)          # 밴드 순서를 외우지 않게 이름을 새긴다
    return {"tif": str(out_tif), "export_dir": str(export_dir), "unwrapped_hdr": str(hdr),
            "unwrapped": True, "bands": roles}


def _attach_coherence(export_dir: Path, coh_folder: Path) -> None:
    """conf 가 요구하는 coh_*.snaphu.img 를 별도 ENVI 산출에서 채워 넣는다.

    SnaphuExport 가 CORRFILE 이름만 적고 이미지를 쓰지 않아 snaphu 가 그 자리에서
    죽는다. 이미 있으면 아무것도 하지 않는다(SNAP 이 고쳐지면 자동으로 무해해진다).
    """
    want = _conf_value(export_dir / "snaphu.conf", "CORRFILE")
    if want is None or (export_dir / want).exists():
        return
    src = next((p for p in coh_folder.rglob("*.img") if p.name.startswith("coh")), None)
    if src is None:
        raise UnwrapError(
            f"coherence 이미지를 찾지 못했습니다({coh_folder}) — snaphu 는 CORRFILE 없이는 "
            f"돌지 않습니다. ① 단계 로그를 확인하세요.")
    shutil.copyfile(src, export_dir / want)
    hdr = src.with_suffix(".hdr")
    if hdr.exists():
        shutil.copyfile(hdr, (export_dir / want).with_suffix(".hdr"))


# 물리적으로 가능한 범위 — 엔디안 판정의 근거. 래핑 위상은 (−π, π], coherence 는 [0,1].
_RANGE = {"INFILE": 3.1416, "CORRFILE": 1.0}


def prepare_snaphu_inputs(export_dir: str | Path) -> list[str]:
    """snaphu 입력(위상·coherence)을 snaphu 가 실제로 읽을 수 있는 상태로 만든다.

    두 가지를 고친다 — 둘 다 실행으로 겪은 것이다:
      ① **엔디안**: SNAP 은 ENVI 를 빅엔디안으로 쓰는데 snaphu 는 native(x86=리틀엔디안)
         로 읽는다. 게다가 **.hdr 의 byte order 표기를 믿을 수 없다** — Subset 을 거친
         산출은 `byte order = 0` 이라고 적어놓고 데이터는 빅엔디안이었다. 그래서 표기가
         아니라 **데이터의 물리 범위**로 판정한다(위상 |φ|≤π, coherence 0≤c≤1).
         coherence 는 NaN 으로 죽어서 드러나지만, **위상은 죽지 않고 조용히 쓰레기 값으로
         언래핑된다**(실제로 max flow 817 → 오버플로 abort 로만 겨우 드러났다).
      ② **비유한값**: nodata 의 NaN/Inf 를 snaphu 가 거부한다. 위상은 0, coherence 는
         0(=신뢰 없음)으로 낮춘다. 뒤에서 coherence 임계가 이 점들을 어차피 버린다.

    반환: 손본 파일 설명 목록.
    """
    import numpy as np

    export_dir = Path(export_dir)
    conf = export_dir / "snaphu.conf"
    # 위상 파일은 conf 의 INFILE 이 아니라 **명령줄 위치인자**로 온다(SNAP 이 그렇게 적는다).
    targets: list[tuple[str | None, str]] = [(_conf_value(conf, "INFILE"), "INFILE"),
                                             (_conf_value(conf, "CORRFILE"), "CORRFILE")]
    try:
        cmd = parse_snaphu_command(conf)
        targets += [(a, "INFILE") for a in cmd[1:] if a.endswith(".img")]
    except UnwrapError:
        pass

    fixed: list[str] = []
    seen: set[str] = set()
    for name, key in targets:
        if not name or name in seen:
            continue
        seen.add(name)
        img = export_dir / name
        if not img.exists():
            continue
        raw = np.fromfile(img, dtype="<f4")
        big = _looks_big_endian(raw, _RANGE.get(key, 3.1416))
        arr = raw.byteswap() if big else raw
        nonfinite = int((~np.isfinite(arr)).sum())
        if not big and not nonfinite:
            continue                                   # 손댈 것 없음
        arr = np.nan_to_num(arr.astype("<f4"), nan=0.0, posinf=0.0, neginf=0.0)
        if key == "CORRFILE":
            arr = arr.clip(0.0, 1.0)                   # coherence 는 [0,1]
        arr.tofile(img)
        _set_hdr_little_endian(img.with_suffix(".hdr"))
        why = "·".join((["엔디안"] if big else []) + ([f"비유한 {nonfinite}"] if nonfinite else []))
        fixed.append(f"{name}({why})")
    return fixed


def _looks_big_endian(le: "object", limit: float) -> bool:
    """리틀엔디안으로 읽어 물리 범위를 벗어나고, 뒤집으면 들어맞으면 빅엔디안이다."""
    import numpy as np

    le = np.asarray(le)
    if le.size == 0:
        return False
    be = le.byteswap()

    def _fit(a) -> float:
        """물리적으로 그럴듯한 값의 비율 — 0 이거나 [1e-12, limit] 안.

        엔디안이 틀린 float 는 천문학적으로 크거나(1e38) 비정규수로 작아진다(1e-41).
        '작으니 범위 안' 으로 세면 NaN 이 섞인 정상 데이터가 뒤집힌 쪽에 지는 일이
        생긴다 — 아래끝도 함께 본다.
        """
        f = np.isfinite(a)
        if not f.any():
            return 0.0
        v = np.abs(a[f])
        plausible = (v == 0.0) | ((v >= 1e-12) & (v <= limit))
        return float(plausible.mean() * f.mean())

    return _fit(be) > _fit(le) + 0.01


def _set_hdr_little_endian(hdr: Path) -> None:
    if not hdr.exists():
        return
    text = hdr.read_text(encoding="utf-8", errors="replace")
    hdr.write_text(re.sub(r"(?im)^(\s*byte\s+order\s*=\s*)\d(\s*)$", r"\g<1>0", text),
                   encoding="utf-8")


def _conf_value(conf: Path, key: str) -> str | None:
    if not conf.exists():
        return None
    for line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].upper() == key.upper():
            return parts[1]
    return None


def _graph_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "scripts" / "snap"


def _find_export_dir(work: Path) -> Path | None:
    """SnaphuExport 는 <targetFolder>/<제품명>/ 아래에 conf 를 쓴다."""
    if (work / "snaphu.conf").exists():
        return work
    return next((d for d in sorted(work.iterdir()) if d.is_dir()
                 and (d / "snaphu.conf").exists()), None)


def _gpt(gpt: str, graph: str, params: list[str], *, timeout: int,
         log_file: str | Path) -> int:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w", encoding="utf-8") as lf:
        p = subprocess.run([gpt, graph, *params], stdout=lf, stderr=subprocess.STDOUT,
                           timeout=timeout)
    return p.returncode


# TC 산출 GeoTIFF 는 밴드 이름을 잃는다. 순서도 그래프 구성에 따라 바뀐다
# (언래핑 레인은 [coh, 위상, 입사각], 기존 래핑 레인은 [위상, coh, 입사각]).
# 순서를 외워 쓰면 조용히 틀리므로, **물리 범위로 식별해 이름을 새겨** 자기기술로 만든다.
BAND_ROLES = ("phase", "coherence", "incidence")


def label_bands(tif: str | Path) -> list[str]:
    """산출 GeoTIFF 의 밴드에 역할 이름을 새기고 그 순서를 돌려준다.

    coherence 는 [0,1], 입사각은 도(度) 단위로 좁게 모인 값(TC 가 덧붙임), 나머지가 위상.
    구분이 안 되면 추측하지 않고 UnwrapError 로 알린다.
    """
    import numpy as np
    import rasterio

    with rasterio.open(tif, "r+") as ds:
        roles: list[str] = []
        for i in range(1, ds.count + 1):
            a = ds.read(i).astype(float)
            a = a[np.isfinite(a) & (a != 0)]
            if a.size == 0:
                roles.append("unknown")
                continue
            lo, hi, sd = float(a.min()), float(a.max()), float(a.std())
            if 0.0 <= lo and hi <= 1.0:
                roles.append("coherence")
            elif 5.0 <= lo and hi <= 80.0 and sd < 5.0:      # 입사각(도) — 좁게 모인다
                roles.append("incidence")
            else:
                roles.append("phase")
        if roles.count("phase") != 1 or roles.count("coherence") != 1:
            raise UnwrapError(
                f"산출 밴드 역할을 확정하지 못했습니다({roles}) — {tif} 를 직접 확인하세요.")
        ds.descriptions = tuple(roles)
    return roles


def band_index(tif: str | Path, role: str) -> int | None:
    """새겨둔 이름으로 밴드 번호(1-base)를 찾는다. 이름이 없으면 None."""
    import rasterio

    with rasterio.open(tif) as ds:
        for i, d in enumerate(ds.descriptions or (), start=1):
            if d == role:
                return i
    return None
