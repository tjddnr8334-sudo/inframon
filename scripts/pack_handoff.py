#!/usr/bin/env python3
"""타 컴퓨터에 **저장소 밖의 것**만 묶어 준다 — 코드는 GitHub 에 다 있다.

공개 저장소라 못 올리는 것(파트너 CSV)과 크기·재현 문제로 안 올리는 것(처리 결과 h5)만
zip 하나로. 받는 쪽은 clone 뒤 `python scripts/pack_handoff.py --unpack <zip>` 으로 제자리에.

    python scripts/pack_handoff.py                 → inframon_handoff_YYYYMMDD.zip
    python scripts/pack_handoff.py --unpack X.zip  → 이 clone 안에 풀기

넣는 것(있는 것만):
  data/national_bridge_standard*.csv   전국교량표준데이터 — 교량명 검색·제원. data.go.kr 수동 다운로드라
                                       새 PC 마다 다시 받기 번거롭다
  data/bridges_load.csv, bridges_specs.csv   파트너 실측 제원(비공개)
  data/bridge_registry.json            교량 등록부(있으면)
  docs/bridges/<교량>/project.h5 · track_deck.h5 · track_rh_full.h5   처리 결과 — 재처리(수 시간) 없이 바로 보기
넣지 않는 것:
  Earthdata 토큰(개인 자격 — 받는 사람이 자기 것을 붙여넣는다), SLC zip(수백 GB — 폴더째 복사 후
  --slc-dir 로 등록), Pontifex 산출물(파트너 소유).
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = [
    "data/national_bridge_standard*.csv",
    "data/bridges_load.csv",
    "data/bridges_specs.csv",
    "data/bridge_registry.json",
    "docs/bridges/*/project.h5",
    "docs/bridges/*/track_deck.h5",
    "docs/bridges/*/track_rh_full.h5",
]
NEVER = ("earthdata_token", ".zip", "pontifex")


def collect() -> list[Path]:
    out: list[Path] = []
    for pat in PATTERNS:
        out += [p for p in ROOT.glob(pat) if p.is_file() and not any(n in p.name.lower() for n in NEVER)]
    return sorted(set(out))


def pack(dest: Path | None) -> Path:
    files = collect()
    if not files:
        sys.exit("묶을 파일이 없습니다 (data/*.csv, docs/bridges/*/ *.h5).")
    dest = dest or ROOT / f"inframon_handoff_{date.today():%Y%m%d}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(ROOT).as_posix())
        z.writestr("HANDOFF_README.txt",
                   "inframon 인계 묶음 — 코드는 https://github.com/tjddnr8334-sudo/inframon\n"
                   "받는 쪽:\n"
                   "  irm https://raw.githubusercontent.com/tjddnr8334-sudo/inframon/main/install.ps1 | iex\n"
                   "  cd $HOME\\inframon\n"
                   "  python scripts\\pack_handoff.py --unpack <이 zip>\n"
                   "Earthdata 토큰은 각자: 대시보드 🔧 패널 또는 python start.py --tools\n"
                   "SLC zip 폴더를 복사해 왔다면: python -m inframon --slc-dir <그 폴더>\n")
    total = sum(f.stat().st_size for f in files)
    print(f"묶음: {dest}  ({len(files)}개, {total / 1e6:.1f} MB)")
    for f in files:
        print(f"  {f.relative_to(ROOT).as_posix()}  {f.stat().st_size / 1e6:.1f} MB")
    return dest


def unpack(src: Path) -> None:
    with zipfile.ZipFile(src) as z:
        names = [n for n in z.namelist() if n != "HANDOFF_README.txt"]
        bad = [n for n in names if n.startswith(("/", "..")) or ":" in n]
        if bad:
            sys.exit(f"이상한 경로가 들어 있어 풀지 않습니다: {bad[:3]}")
        for n in names:
            (ROOT / n).parent.mkdir(parents=True, exist_ok=True)
            z.extract(n, ROOT)
            print(f"  ✓ {n}")
    print(f"풀기 완료 → {ROOT}. 확인: python -m inframon --doctor")


def main() -> None:
    ap = argparse.ArgumentParser(description="타 컴퓨터 인계용 — 저장소 밖 파일만 zip")
    ap.add_argument("--out", default=None, help="zip 경로(기본 inframon_handoff_날짜.zip)")
    ap.add_argument("--unpack", default=None, metavar="ZIP", help="이 clone 안에 풀기")
    a = ap.parse_args()
    if a.unpack:
        unpack(Path(a.unpack))
    else:
        pack(Path(a.out) if a.out else None)


if __name__ == "__main__":
    main()
