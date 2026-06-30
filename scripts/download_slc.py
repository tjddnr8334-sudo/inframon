"""실 Sentinel-1 SLC 다운로드 — Windows(WSL2 불필요, 순수 Python HTTPS).

선별된 레시피의 정확한 granule 이름으로 ASF 에서 SLC 를 받는다. 처리(ISCE2/SARvey)는
별도 WSL2 단계지만, **다운로드 자체는 여기서** 된다.

사용:
    # 1) Earthdata 자격증명(무료: urs.earthdata.nasa.gov) → ~/.netrc 한 줄:
    #    machine urs.earthdata.nasa.gov login <아이디> password <비번>
    # 2) 실행(처음 N장만 테스트 다운로드):
    python scripts/download_slc.py data/jeongjagyo_real <out_dir> --limit 5

자격증명이 없으면 명확히 안내하고 종료(네트워크 인증 우회 불가).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows 콘솔(cp949)에서 한글·기호 출력이 깨지지 않게 UTF-8 로 재설정.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _session():
    """~/.netrc 의 Earthdata 자격으로 ASF 세션. 없으면 None."""
    import netrc

    import asf_search as asf
    try:
        auth = netrc.netrc().authenticators("urs.earthdata.nasa.gov")
    except (FileNotFoundError, netrc.NetrcParseError):
        auth = None
    if not auth:
        return None
    try:
        return asf.ASFSession().auth_with_creds(auth[0], auth[2])
    except Exception as exc:  # noqa: BLE001
        print(f"인증 실패: {exc}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("recipe_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--limit", type=int, default=5, help="처음 N장만(기본 5, 0=전체)")
    args = ap.parse_args()

    import asf_search as asf

    manifest = Path(args.recipe_dir) / "processing_manifest.json"
    names = json.loads(manifest.read_text(encoding="utf-8"))["stack"]["scene_names"]
    if args.limit:
        names = names[: args.limit]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    session = _session()
    if session is None:
        print("=" * 60)
        print("  ❌ Earthdata 자격증명이 없습니다 — 다운로드 불가")
        print("=" * 60)
        print("  1) 무료 가입: https://urs.earthdata.nasa.gov")
        print("  2) ~/.netrc 에 한 줄 추가:")
        print("     machine urs.earthdata.nasa.gov login <아이디> password <비번>")
        print("  3) 다시 실행: python scripts/download_slc.py "
              f"{args.recipe_dir} {args.out_dir} --limit {args.limit}")
        print("=" * 60)
        return 2

    print(f">> {len(names)}장 다운로드 → {out} (1장 ~4GB)")
    results = asf.granule_search(names)
    slc = [r for r in results if r.properties.get("processingLevel") == "SLC"]
    for i, r in enumerate(slc, 1):
        name = r.properties["sceneName"]
        dest = out / f"{name}.zip"
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  [{i}/{len(slc)}] 이미 있음: {name}")
            continue
        print(f"  [{i}/{len(slc)}] 다운로드: {name} …")
        r.download(path=str(out), session=session)
    print(f"완료: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
