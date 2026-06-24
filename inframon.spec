# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 명세 — inframon 데스크톱 앱(.exe).

빌드:  pyinstaller inframon.spec --noconfirm
산출:  dist/inframon/inframon.exe  (onedir — 폴더째 배포, 더블클릭 실행)

Streamlit/plotly/folium 은 런타임에 데이터파일·패키지 메타데이터를 importlib 로
조회하므로, collect_all + copy_metadata 로 모두 동봉해야 frozen 에서 동작한다.
"""
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

datas, binaries, hiddenimports = [], [], []

# 1) 런타임 데이터/서브모듈을 통째로 끌어오는 패키지(정적 자산·하위 import 多)
#    Streamlit 1.5x 는 내부 서버로 starlette+uvicorn 을 동적 import 하므로 반드시 포함.
#    altair 차트 검증 → jsonschema IRI 포맷검사 → rfc3987_syntax(.lark 문법파일) 까지 필요.
#    asf_search(C·D SLC 검색)는 shapely(GEOS)·dateparser(언어데이터)·networkx 를 끌어온다.
for pkg in ("streamlit", "plotly", "folium", "streamlit_folium", "altair",
            "pyarrow", "pydeck", "webview", "uvicorn", "starlette",
            "jsonschema", "jsonschema_specifications", "referencing",
            "rfc3987_syntax", "lark",
            "asf_search", "shapely", "dateparser", "dateparser_data", "networkx",
            "tzlocal", "requests", "urllib3", "idna", "charset_normalizer"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 2) streamlit 이 importlib.metadata 로 버전을 조회하는 패키지들 — 메타데이터 동봉 필수
for pkg in ("streamlit", "numpy", "pandas", "pyarrow", "plotly", "altair",
            "packaging", "tornado", "watchdog", "gitpython", "pydeck",
            "tenacity", "toml", "click", "blinker", "cachetools", "rich",
            "protobuf", "pillow", "jsonschema", "narwhals", "uvicorn", "starlette",
            "jsonschema_specifications", "referencing", "rfc3987_syntax", "lark",
            "asf_search", "shapely", "dateparser", "networkx", "tzlocal", "regex",
            "requests", "certifi"):
    try:
        datas += copy_metadata(pkg)
    except Exception:  # noqa: BLE001 — 설치 안 된 선택 의존성은 건너뜀
        pass

# 3) 우리 패키지(.py 포함) — streamlit 이 app.py 를 '스크립트'로 실행하므로 .py 도 데이터로 동봉
datas += collect_data_files("inframon", include_py_files=True)
try:
    datas += copy_metadata("inframon")
except Exception:  # noqa: BLE001
    pass

# 4) 데모 데이터 — 더블클릭 첫 실행에서 바로 채워진 대시보드를 보여주는 시드(앱이 exe 옆으로 복사)
import os as _os
if _os.path.exists("data/project.h5"):
    datas += [("data/project.h5", "data")]

a = Analysis(
    ["src/inframon/_app_entry.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        "inframon", "inframon.desktop", "inframon.dashboard.app",
        "inframon.dashboard.data", "inframon.contracts.schema",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "transformers", "timm", "mintpy", "rasterio", "osgeo",
              "prefect",
              # asf_search 의 '다운로드 전용' 무거운 의존(검색엔 불필요·지연로드) → 제외해 용량 절감
              "zarr", "s3fs", "rioxarray", "xarray", "fsspec", "remotezip",
              "h5netcdf", "dask"],  # (uvicorn·h5py 는 필요하므로 제외 금지!)
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="inframon",
    console=True,          # 첫 빌드는 콘솔 유지(오류 확인). 안정화 후 False 로.
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="inframon")
