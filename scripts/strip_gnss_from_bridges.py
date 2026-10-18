#!/usr/bin/env python3
"""교량 결과물에서 **계측(GNSS) 대조와 품질 판정을 걷어낸다**.

왜 걷어내는가. 대조 자체가 틀린 계산은 아니지만, 지금 값으로는 아무것도 주장하지
못한다 — 추세가 다르고 R² 도 낮다. 그런 표를 자료에 붙여 두면 읽는 사람의 결론이
"이 데이터는 계측과 안 맞는다" 로 고정된다. 아직 완성 전이라 성능으로 다툴 자리가
아니므로, **주장하지 못하는 숫자는 싣지 않는다.** 원자료와 재현 스크립트는 그대로
두니 언제든 다시 만들 수 있다.

같이 걷어내는 것
  · `## GNSS 대조 판정` 절 전체
  · 표에서 계측과 맞대는 행 — 보고서 GNSS · 대조 · 계측 대조 · R² · 추세 · 연주기(위상차)
  · **감사 판정**(조건부 / 보고 가능 / 보고 불가) — 전송 보류 같은 게이트 문구.
    관측조건 사실 자체는 버리지 않고 "적어 둘 것" 으로 옮긴다.
  · README 의 집계 문구(16개소 중 n개소 …)
  · 집계 산출물 json/png

남기는 것: 보고서 판독 원자료(hangang_*.json)와 각 교량의 InSAR 자체 값.

    python scripts/strip_gnss_from_bridges.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BR = ROOT / "docs" / "bridges"
NL = chr(10)

# 표에서 지울 행 — 첫 칸이 이 중 하나로 시작하면 계측과 맞댄 행이다.
DROP_ROWS = ("보고서 GNSS", "대조", "**대조**", "계측 대조", "R²", "추세",
             "연주기 위상", "감사")
# MT-InSAR 절 안에서만 지울 행(교면 전용 절의 '연주기' 는 InSAR 자체 값이라 남긴다)
DROP_IN_MT = ("연주기",)

DROP_SECTIONS = ("## GNSS 대조 판정",)

DROP_FILES = ["gnss_verdict_all.json", "deck_point_match.json",
              "sensor_coverage.json", "climatology_compare.json",
              "deck_only_gnss.json", "gnss_compare.json"]
DROP_IMAGES = ["GNSS판정_전교량.png", "교면전용_GNSS대조.png", "교면점_계측일치.png",
               "월별기후값_대조.png", "센서자리_PS유무.png", "센서자리_PS유무_납작.png"]
DROP_JSON_KEYS = ("gnss", "gnss_sensors", "gnss_amp_mm", "gnss_peak_month",
                  "phase_diff_months", "same_seasonal_behaviour",
                  "r2", "r2_max", "r2_chance95", "verdict")


def first_cell(line: str) -> str:
    """'| 감사 | ... |' → '감사'."""
    if not line.startswith("|"):
        return ""
    return line.split("|")[1].strip().strip("*").strip()


def strip_md(text: str) -> tuple[str, list]:
    """결과.md 한 개를 훑어 절·행을 걷어내고, 무엇을 뺐는지 같이 돌려준다."""
    out, removed = [], []
    in_mt = False          # '## MT-InSAR 로 재도출' 안인가
    skip_section = False
    audit_facts = None

    for line in text.split(NL):
        if line.startswith("## "):
            skip_section = any(line.startswith(s) for s in DROP_SECTIONS)
            in_mt = line.startswith("## MT-InSAR")
            if skip_section:
                removed.append(line.strip())
                continue
        if skip_section:
            continue
        if line.startswith("![센서자리]"):
            removed.append("그림 센서자리_PS유무")
            continue

        cell = first_cell(line)
        if cell:
            hit = cell in DROP_ROWS or (in_mt and cell in DROP_IN_MT)
            if cell == "감사":
                # 판정 낱말(조건부·보고 가능·보고 불가)만 떼고 **사유는 지킨다**.
                # 동수원의 "30 m 내 0점" 처럼 판정보다 사유가 중요한 칸이 있다.
                body = line.split("|")[2] if line.count("|") >= 3 else ""
                body = re.sub(r"^\s*\*\*(조건부|보고 가능|보고 불가)\*\*\s*(·\s*)?",
                              "", body.strip())
                body = body.strip().strip("|").strip()
                if body:
                    audit_facts = body
                hit = True
            if hit:
                removed.append(line.strip()[:70])
                continue
        out.append(line)

    txt = NL.join(out)
    # 관측조건 사실을 '적어 둘 것' 으로 옮긴다 — 판정이 아니라 사실이라 버리지 않는다.
    if audit_facts and "## 적어 둘 것" in txt:
        note = "- " + audit_facts
        txt = txt.replace("## 적어 둘 것" + NL + NL, "## 적어 둘 것" + NL + NL + note + NL, 1)
    # 절이 통째로 빠지며 생긴 빈 줄 세 개 이상을 둘로 줄인다.
    txt = re.sub(NL + r"{3,}", NL * 2, txt).rstrip() + NL
    return txt, removed


def strip_json(path: Path) -> list:
    """계측 대조로 나온 키만 뺀다 — InSAR 자체 값은 건드리지 않는다."""
    d = json.loads(path.read_text(encoding="utf-8"))
    gone = []

    def clean(o):
        if isinstance(o, dict):
            for k in [k for k in o if k in DROP_JSON_KEYS]:
                o.pop(k)
                gone.append(k)
            for v in o.values():
                clean(v)
        elif isinstance(o, list):
            for v in o:
                clean(v)

    clean(d)
    if gone:
        path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return gone


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    log: dict = {"결과.md": {}, "json": {}, "삭제": []}

    for md in sorted(BR.glob("*/결과.md")):
        new, removed = strip_md(md.read_text(encoding="utf-8"))
        if removed:
            log["결과.md"][md.parent.name] = removed
            if not a.dry_run:
                md.write_text(new, encoding="utf-8")

    for jp in sorted(list(BR.glob("*/교면전용.json")) + list(BR.glob("*/재도출.json"))
                     + [BR / "deck_fix_all.json"]):
        if not jp.exists():
            continue
        if a.dry_run:
            gone = [k for k in DROP_JSON_KEYS
                    if k in jp.read_text(encoding="utf-8")]
        else:
            gone = strip_json(jp)
        if gone:
            log["json"][str(jp.relative_to(BR))] = sorted(set(gone))

    for name in DROP_FILES + ["*/GNSS판정.json"]:
        for p in (BR.glob(name) if "*" in name else [BR / name]):
            if p.exists():
                log["삭제"].append(str(p.relative_to(BR)))
                if not a.dry_run:
                    p.unlink()
    for name in DROP_IMAGES:
        p = ROOT / "docs" / "img" / "value" / name
        if p.exists():
            log["삭제"].append("docs/img/value/" + name)
            if not a.dry_run:
                p.unlink()

    print(f"결과.md {len(log['결과.md'])}개 · json {len(log['json'])}개 · "
          f"삭제 {len(log['삭제'])}개" + (" (dry-run)" if a.dry_run else ""))
    for k, v in list(log["결과.md"].items())[:3]:
        print(" ", k, "→", v[:3])
    if not a.dry_run:
        (BR / "strip_log.json").write_text(
            json.dumps({"_왜": "지금 값으로는 계측 대조가 아무것도 주장하지 못한다 — "
                              "주장하지 못하는 숫자는 싣지 않는다. 원자료와 스크립트는 남아 "
                              "있어 언제든 다시 만들 수 있다.", **log},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print("wrote", BR / "strip_log.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
