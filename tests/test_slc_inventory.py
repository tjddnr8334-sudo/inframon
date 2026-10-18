"""SLC 보관 현황 — 파일명 해석·궤도 계산·.SAFE/조각 판정·중복 제거."""
import json
import zipfile

from inframon.insar.slc_store import INDEX_NAME, inventory, parse_scene

A = "S1A_IW_SLC__1SDV_{d}T212441_{d}T212508_{o:06d}_055E11_3B40"


def test_parse_scene_relative_orbit():
    s = parse_scene("S1A_IW_SLC__1SDV_20220909T212441_20220909T212508_044933_055E11_3B40.zip")
    assert s["date"] == "2022-09-09" and s["track"] == 61          # 각동 하강 61
    assert parse_scene("not_a_slc.zip") is None
    assert parse_scene("S1C_IW_SLC__1SDV_20250911T000000_20250911T000027_004000_00ABCD_1234")["track"] is None


def _zip(p):
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("x.txt", "x")


def test_inventory_counts_zip_safe_partial_and_dedup(tmp_path):
    r1, r2 = tmp_path / "a", tmp_path / "b"
    (r1 / "frame").mkdir(parents=True)
    r2.mkdir()
    _zip(r1 / "frame" / (A.format(d="20220909", o=44933) + ".zip"))
    _zip(r2 / (A.format(d="20220909", o=44933) + ".zip"))                # 같은 장면 — 한 번만
    (r1 / (A.format(d="20221015", o=45458) + ".zip")).write_bytes(b"PK\x03\x04broken")  # 조각
    safe = r1 / (A.format(d="20230718", o=49483) + ".SAFE")
    (safe / "measurement").mkdir(parents=True)
    (safe / "manifest.safe").write_text("<xml/>")
    inv = inventory([r1, r2])
    assert inv["n_scenes"] == 3 and inv["n_files"] == 4
    assert inv["incomplete"] == 1
    t = inv["tracks"][0]
    assert t["track"] == 61 and t["first"] == "2022-09-09" and t["last"] == "2023-07-18"
    assert t["gaps_over_24d"] == 2
    idx = json.loads((r1 / INDEX_NAME).read_text(encoding="utf-8"))
    assert len(idx["scenes"]) == 3
