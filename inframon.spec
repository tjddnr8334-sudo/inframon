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

# 3) 우리 패키지(.py 포함) — streamlit 이 app.py 를 '스크립트'로 실행하므로 .py 도 데이터로 동봉.
#    collect_data_files("inframon") 는 **설치된** 패키지를 보므로, editable 설치가 다른
#    체크아웃을 가리키면 엉뚱한 소스를 번들한다. 항상 이 spec 옆의 src/ 를 쓴다.
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
              "h5netcdf", "dask",
              # ── 아래는 inframon 이 import 하지 않는데도 빌드환경에 깔려 있다는 이유만으로
              #    PyInstaller 훅이 쓸어담던 것들(뷰어 용량의 절반가량). 코드 근거:
              #    cv2·polars·geopandas·fiona·pyogrio·sklearn·skimage → src 전체에 참조 0건.
              #    scipy → gnss_ngl.py 의 theilslopes(try/except + 순수 파이썬 폴백)와
              #            doctor.py 의 가용성 '이름 조회'뿐이라 없어도 동작한다.
              #    실 CV/PINN 기능은 어차피 torch 를 제외한 뷰어 빌드의 범위 밖이다.
              "cv2", "polars", "geopandas", "fiona", "pyogrio", "sklearn",
              "skimage", "scipy", "sympy", "numba", "llvmlite",
              "IPython", "notebook", "jupyter", "pytest", "setuptools",
              ],  # (uvicorn·h5py·pyarrow·pandas 는 streamlit 이 쓰므로 제외 금지!)
    noarchive=False,
)

# 5) 훅이 끌어온 바이너리 중 **런타임에 절대 안 쓰이는 것**을 덜어낸다.
#    excludes 는 파이썬 모듈 단위라 이런 동반 DLL 은 걸러지지 않는다.
_DROP_BINARIES = (
    "arrow_flight",      # Arrow Flight = gRPC 원격전송. st.dataframe 직렬화와 무관.
    "arrow_substrait",   # Substrait 쿼리계획 IR. 미사용.
    "arrow_dataset",     # 파일시스템 데이터셋 스캔. 미사용.
    "arrow_acero",       # 스트리밍 실행엔진. 미사용.
)


def _keep(entry) -> bool:
    name = entry[0].lower().replace("\\", "/").split("/")[-1]
    if name.endswith(".lib"):
        return False  # 링크타임 import 라이브러리 — 런타임엔 죽은 무게
    return not any(d in name for d in _DROP_BINARIES)


_before = len(a.binaries)
a.binaries = [b for b in a.binaries if _keep(b)]
print(f"[inframon.spec] 불필요 바이너리 {_before - len(a.binaries)}개 제외")

pyz = PYZ(a.pure)

# 아이콘: Windows 는 .ico, macOS 는 .icns, 리눅스 ELF 는 아이콘 임베드 개념이 없다
# (.desktop 파일이 .png 를 가리킨다) → 리눅스에선 넘기지 않는다.
import sys as _sys
_icon = "assets/inframon.ico" if _sys.platform in ("win32", "darwin") else None

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="inframon",
    console=False,         # 뷰어 = windowed(더블클릭 시 콘솔창 없이 앱 창만).
    icon=_icon,
)
coll = COLLECT(exe, a.binaries, a.datas, name="inframon")
