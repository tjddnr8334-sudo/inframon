"""테스트 공통 설정.

**선택 의존성(extras) 미설치 → 실패가 아니라 스킵.**
`pip install -e .` (코어만) 로 clone-and-run 하는 사용자가 `pytest` 를 돌리면, 선택 기능
(pyproj/torch/matplotlib/pandas/rasterio…)에 의존하는 테스트가 ImportError 로 **실패**해
프로그램이 깨진 것처럼 보였다. 이 훅은 그런 실패를 **skip 으로 재분류**해 코어가 온전함을
정확히 드러낸다. 전체 스위트를 초록으로 보려면 해당 extras 를 설치한다:

    pip install -e ".[dev,cv,insar,bim,dashboard,report]"

코어 의존성(numpy/h5py/pydantic)은 allowlist 에 없으므로, 그 import 실패는 **여전히 실패**로
남아 진짜 회귀를 가린다."""

from __future__ import annotations

import pytest

# extras(pyproject.toml)에서 오는 선택 의존성 — 이들의 부재는 '설치 안 함'이지 버그가 아니다.
_OPTIONAL_DEPS = frozenset({
    "torch", "pyproj", "matplotlib", "pandas", "rasterio", "gdal",
    "ifcopenshell", "transformers", "timm", "scipy", "skimage",
    "mintpy", "streamlit", "plotly", "folium", "streamlit_folium",
    "openseespy", "openseespywin", "fastapi", "uvicorn", "networkx",
    "prefect", "asf_search", "imageio", "PIL",
})


def _is_optional_dep_import_error(exc: BaseException) -> str | None:
    """예외 사슬을 훑어 '선택 의존성 부재' ImportError 면 그 의존성명을 반환."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ImportError):
            name = (getattr(cur, "name", "") or "").split(".")[0]
            if name in _OPTIONAL_DEPS:
                return name
            msg = str(cur)
            for dep in _OPTIONAL_DEPS:
                if dep in msg:
                    return dep
        cur = cur.__cause__ or cur.__context__
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed or call.excinfo is None:
        return
    dep = _is_optional_dep_import_error(call.excinfo.value)
    if dep is not None:
        report.outcome = "skipped"
        report.longrepr = f"선택 의존성 '{dep}' 미설치 → 스킵 (pip install -e '.[extras]')"
