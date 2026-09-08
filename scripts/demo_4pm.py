#!/usr/bin/env python3
"""4시 시연 — 좌표 하나 → 트윈·CRI·브리프까지, 화면에 순서대로 띄운다.

    python scripts/demo_4pm.py                # 정자교(기본) 실행 + 결과 자동 열기
    python scripts/demo_4pm.py --bridge 청양교
    python scripts/demo_4pm.py --open-only    # 이미 만든 결과만 연다(실행 없이)

순서(각 창을 차례로 연다 — 발표자가 Alt+Tab 로 넘긴다):
  1. 3D 트윈(속도)          twin.viewer.html
  2. 3D 트윈(CRI 위험도)    twin_cri.viewer.html
  3. 건기연 형식 4단 그림    brief.png
  4. 결과 문서              결과.md
트랙이 이미 있는 교량이라 1~2분 안에 끝난다. 실 SLC 부터는 시간이 걸려 시연에는 안 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BATCH = json.loads((ROOT / "docs/bridges/batch.json").read_text(encoding="utf-8"))
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _open(p: Path) -> None:
    if os.name == "nt":
        os.startfile(str(p))                                   # noqa: S606 — 시연용
    else:
        subprocess.Popen(["xdg-open", str(p)])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="정자교")
    ap.add_argument("--open-only", action="store_true")
    a = ap.parse_args()
    it = next((b for b in BATCH if b["name"] == a.bridge), None)
    if it is None:
        raise SystemExit(f"batch.json 에 없는 교량: {a.bridge} — {[b['name'] for b in BATCH]}")
    out = ROOT / it["out"]

    print("=" * 64)
    print(f"  inframon 시연 — {it['name']}  ({it['lat']}, {it['lon']})")
    print("  좌표 하나 → 제원 → 쉬프트 보정 → 잔차고도 → IFC 트윈 → PINN → CRI → 브리프")
    print("=" * 64)
    if not a.open_only:
        args = [sys.executable, str(ROOT / "scripts/bridge_run.py"),
                "--name", it["name"], "--lat", str(it["lat"]), "--lon", str(it["lon"]),
                "--track", it["track"], "--out", it["out"]]
        for k in ("proc", "master", "baselines"):
            if it.get(k):
                args += [f"--{k}", it[k]]
        t0 = time.time()
        r = subprocess.run(args, env=ENV)
        print(f"\n  실행 {time.time() - t0:.0f}초 · rc={r.returncode}")
        if r.returncode != 0:
            raise SystemExit("실행 실패 — 위 로그 확인")

    print("\n  결과를 엽니다 (Alt+Tab 로 넘기세요):")
    for name, delay in (("twin.viewer.html", 2.0), ("twin_cri.viewer.html", 2.0),
                        ("brief.png", 1.0), ("결과.md", 0.0)):
        p = out / name
        if p.exists():
            print(f"    · {p.relative_to(ROOT)}")
            _open(p)
            time.sleep(delay)
        else:
            print(f"    · (없음) {name}")
    print("\n  끝. 질문 예상: 결과.md 아래 '이 파이프라인이 원리상 못 하는 것' 참조.")


if __name__ == "__main__":
    main()
