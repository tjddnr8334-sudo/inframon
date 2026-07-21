# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 명세 — inframon **풀** exe(대시보드 + 처리엔진 CLI 겸용).

기본 inframon.spec(경량 뷰어)와 달리 torch(PINN·CV)·rasterio(InSAR·SNAP)를 포함해
`inframon_full.exe --snap-auto …` / `--custom-pinn …` 같은 처리 파이프라인을 frozen 에서
그대로 돌린다(무인자 더블클릭이면 대시보드). ⚠️ torch 포함으로 산출이 크다(~1.5–2.5GB).
SNAP(gpt) 자체는 라이선스·용량상 번들 불가 — exe 가 설치된 gpt 를 감지·호출한다.

빌드:  pyinstaller inframon_full.spec --noconfirm
산출:  dist/inframon_full/inframon_full.exe
"""
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

datas, binaries, hiddenimports = [], [], []

for pkg in ("streamlit", "plotly", "folium", "streamlit_folium", "altair",
            "pyarrow", "pydeck", "webview", "uvicorn", "starlette",
            "jsonschema", "jsonschema_specifications", "referencing",
            "rfc3987_syntax", "lark",
            "asf_search", "shapely", "dateparser", "dateparser_data", "networkx",
            "tzlocal", "requests", "urllib3", "idna", "charset_normalizer",
            # 풀 exe 추가: 처리엔진 — PINN/CV(torch), InSAR/SNAP(rasterio+GDAL)
            "torch", "rasterio", "scipy"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

for pkg in ("streamlit", "numpy", "pandas", "pyarrow", "plotly", "altair",
            "packaging", "tornado", "watchdog", "gitpython", "pydeck",
            "tenacity", "toml", "click", "blinker", "cachetools", "rich",
            "protobuf", "pillow", "jsonschema", "narwhals", "uvicorn", "starlette",
            "jsonschema_specifications", "referencing", "rfc3987_syntax", "lark",
            "asf_search", "shapely", "dateparser", "networkx", "tzlocal", "regex",
            "requests", "certifi", "torch", "rasterio", "scipy"):
    try:
        datas += copy_metadata(pkg)
    except Exception:  # noqa: BLE001
        pass

# collect_data_files("inframon") 는 **설치된** 패키지를 보므로, editable 설치가 다른
# 체크아웃을 가리키면 엉뚱한 소스를 번들한다. 항상 이 spec 옆의 src/ 를 쓴다.
from pathlib import Path as _Path

_PKG = _Path(SPECPATH) / "src" / "inframon"
if not _PKG.is_dir():
    raise SystemExit(f"소스를 찾을 수 없습니다: {_PKG}")
for _f in _PKG.rglob("*"):
    if _f.is_file() and "__pycache__" not in _f.parts:
        datas.append((str(_f), str(_Path("inframon") / _f.relative_to(_PKG).parent)))

try:
    datas += copy_metadata("inframon")
except Exception:  # noqa: BLE001
    pass

import os as _os
if _os.path.exists("data/project.h5"):
    datas += [("data/project.h5", "data")]

a = Analysis(
    ["src/inframon/_app_entry.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        "inframon", "inframon.__main__", "inframon.desktop",
        "inframon.dashboard.app", "inframon.dashboard.data",
        "inframon.contracts.schema",
        # 처리엔진 서브모듈(지연 import 라 정적 분석에 안 잡힘 → 명시)
        "inframon.custom_pinn", "inframon.pinn.real_engine",
        "inframon.cv.real_engine", "inframon.insar.snap_backend",
        "inframon.insar.snap_acquire", "inframon.insar.dem",
        "inframon.insar.track_reader", "inframon.insar.slc_download",
        "torch", "torch.nn.functional", "rasterio", "rasterio.sample",
    ],
    hookspath=[],
    runtime_hooks=[],
    # mintpy 는 SNAP 백엔드로 대체되어 불필요, 무거운 다운로드 전용 의존만 제외.
    excludes=["transformers", "timm", "mintpy", "prefect",
              "zarr", "s3fs", "rioxarray", "xarray", "fsspec", "remotezip",
              "h5netcdf", "dask",
              # 풀 빌드는 torch/rasterio 가 필요하지만, 아래는 여전히 참조 0건이다.
              "polars", "geopandas", "fiona", "pyogrio",
              "IPython", "notebook", "jupyter", "pytest", "setuptools",
              ],
    noarchive=False,
)

# 훅이 끌어온 바이너리 중 런타임에 안 쓰이는 것 제거(inframon.spec 과 동일 규칙).
_DROP_BINARIES = ("arrow_flight", "arrow_substrait", "arrow_dataset", "arrow_acero")


def _keep(entry) -> bool:
    name = entry[0].lower().replace("\\", "/").split("/")[-1]
    if name.endswith(".lib"):
        return False  # 링크타임 import 라이브러리 — 런타임엔 죽은 무게
    return not any(d in name for d in _DROP_BINARIES)


_before = len(a.binaries)
a.binaries = [b for b in a.binaries if _keep(b)]
print(f"[inframon_full.spec] 불필요 바이너리 {_before - len(a.binaries)}개 제외")

pyz = PYZ(a.pure)

import sys as _sys
_icon = "assets/inframon.ico" if _sys.platform in ("win32", "darwin") else None

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="inframon_full",
    console=True,
    icon=_icon,
)
coll = COLLECT(exe, a.binaries, a.datas, name="inframon_full")
