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

datas += collect_data_files("inframon", include_py_files=True)
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
              "h5netcdf", "dask"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="inframon_full",
    console=True,
    icon="assets/inframon.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, name="inframon_full")
