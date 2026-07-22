"""BIM / 디지털 트윈 정합 — 위성 관측을 IFC 부재에 붙인다.

핵심은 좌표 정합이다. 정합 코어(`georef`·`elements`·`psets`·`align`)는 IFC 파일이나
`ifcopenshell` 없이 동작·검증되고, `ifc_io` 만 실 IFC 를 읽고 쓴다.
"""

from .align import align_project_to_bim, load_control_points, write_result
from .elements import Element, associate, load_elements
from .georef import AlignmentError, MapConversion, fit_map_conversion, to_ifc_local

__all__ = [
    "AlignmentError", "Element", "MapConversion",
    "align_project_to_bim", "associate", "fit_map_conversion",
    "load_control_points", "load_elements", "to_ifc_local", "write_result",
]
