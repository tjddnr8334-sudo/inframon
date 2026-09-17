#!/usr/bin/env python3
"""`2026 KAIA 자문회의 ppt_v2.pptx` 13쪽 — **어려운 말을 풀고, 다 됐다는 말을 뺀다**.

자문회의에서 두 가지가 걸렸다.
  ① "FRAM 공명" 이 어렵다. 공명은 물리 공진이 아니라 기능공명분석(FRAM)의 비유인데,
     구조 하는 사람이 들으면 고유진동수 이야기로 읽힌다. 말을 풀어 쓰고 학술 용어는
     괄호로 내린다.
  ② "실증 데이터 검증 완료 (한강 교량 16개소)" — 검증이 끝났다고 읽힌다. 끝나지
     않았다. 계측 대조는 지금 값으로 아무것도 주장하지 못한다
     (docs/bridges/strip_log.json). **앞으로 하겠다**로 바꾼다.

②의 취지를 같은 쪽 다른 자리에도 맞춘다 — 한 쪽 안에서 어떤 칸은 "완료", 어떤 칸은
"향후" 면 그게 더 애매하다.
  · "순수 유효 산란체(103점) 100% 분리" · "103점 100% 매핑, 전체 94%" — 비율은
    검증 성적처럼 읽힌다
  · "'보고 가능/조건부/불가' 및 잠정 경보를 스스로 통제" — 게이트 낱말은 구조 등급으로
    오독된다(교량 결과물·7p 자료에서도 같은 이유로 걷어냈다)

대상 파일은 250 MB 제3자 자료라 리포에 담지 않는다(.gitignore). 이 스크립트만 남겨
무엇을 왜 바꿨는지 따라갈 수 있게 한다.

    python scripts/patch_kaia_v2_slide13.py

산출: 대상 pptx 를 제자리에서 갱신 · docs/bridges/kaia_v2_slide13_log.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pptx import Presentation

ROOT = Path(__file__).resolve().parent.parent
DECK = ROOT / "docs" / "2026 KAIA 자문회의 ppt_v2.pptx"
LOG: list = []

# (도형 번호, 문단 번호, 바꾸기 전 글의 일부, 바꾼 뒤 글, 이유)
EDITS = [
    (7, 9, "FRAM 공명 위험 지수 산출",
     "위험 지수(CRI) 산출 (Risk Evaluation)",
     "'FRAM 공명' 은 물리 공진으로 오독된다 — 목록에서는 이름만 남기고 설명은 기능 03 으로"),
    (7, 15, "실증 데이터 검증 완료",
     "한강 교량 시범 적용 (계측 대조 검증은 향후 과제)",
     "검증이 끝났다고 읽힌다 — 끝나지 않았다. 앞으로 하겠다로"),
    (16, 4, "100% 분리",
     "성수대교 시범: OSM 데크선 쉬프트 보정과 DEM 잔차고도 분석으로 교면 위 측점을 "
     "가려내는 절차를 적용(103점). 측점 밀도 확보는 향후 과제",
     "'100% 분리' 는 비율이라 검증 성적처럼 읽힌다 — 절차를 적용했다로"),
    (17, 6, "기능 03. FRAM 공명 위험 지수(CRI)",
     "기능 03. 위험 지수(CRI) 평가 & 자가 점검",
     "제목에서 어려운 말을 뺀다"),
    (17, 8, "기능공명분석(FRAM) 기반 기능별 변동",
     "CRI 지수 도출: 여러 지표의 흔들림이 서로 맞물려 커지는 정도를 0~1 지수로 환산 — "
     "하나가 기준을 넘었는지가 아니라 여럿이 겹치는지를 본다 (기능공명분석 FRAM 기반)",
     "무엇을 보는 지수인지 평이한 말로 먼저 적고, 학술 용어는 괄호로 내린다"),
    (17, 10, "'보고 가능/조건부/불가'",
     "신뢰성 자가 점검: 관측 잡음(σ)과 95% 신뢰구간(CI)을 매번 함께 계산해, 지금 "
     "자료로 말할 수 있는 범위를 결과에 같이 적는다",
     "'보고 가능/조건부/불가' 는 구조 등급으로 오독된다 — 하는 일만 적는다"),
    (18, 2, "103점 100% 매핑, 전체 94%",
     "부재 결합: 실측 제원 기반 IFC4 프록시 구조물의 부재 고유식별자(GUID)에 변위 "
     "시계열을 결합 (성수대교 103점)",
     "비율은 검증 성적처럼 읽힌다 — 무엇을 결합하는지만"),
]


def set_para(shape, idx: int, text: str) -> str:
    """문단 하나의 글자만 바꾼다 — 첫 run 에 몰아넣고 나머지는 비운다.

    run 을 지우고 새로 달면 파일이 깨져 PowerPoint 가 못 연다(전에 겪었다).
    첫 run 의 서식(크기·글꼴·색)을 그대로 쓰므로 생김새도 지켜진다.
    """
    pa = shape.text_frame.paragraphs[idx]
    old = "".join(r.text for r in pa.runs)
    if not pa.runs:
        raise ValueError(f"문단 {idx} 에 run 이 없다")
    pa.runs[0].text = text
    for r in pa.runs[1:]:
        r.text = ""
    return old


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", default=str(DECK))
    ap.add_argument("--slide", type=int, default=13)
    ap.add_argument("--log", default="docs/bridges/kaia_v2_slide13_log.json")
    ap.add_argument("--out", default=None,
                    help="다른 곳에 저장한다(원본이 PowerPoint 에 열려 있어 잠겼을 때)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    prs = Presentation(a.deck)
    s = prs.slides[a.slide - 1]
    ok = True
    for shi, pi, needle, new, why in EDITS:
        sh = s.shapes[shi]
        cur = "".join(r.text for r in sh.text_frame.paragraphs[pi].runs)
        if needle not in cur:
            # 자리가 밀렸으면 엉뚱한 문단을 덮어쓴다 — 멈추고 사람이 본다.
            LOG.append({"찾음": False, "도형": shi, "문단": pi,
                        "찾던 글": needle, "그 자리 글": cur[:90]})
            ok = False
            continue
        old = cur if a.dry_run else set_para(sh, pi, new)
        LOG.append({"도형": shi, "문단": pi, "이전": old, "이후": new, "이유": why})

    for e in LOG:
        if e.get("찾음") is False:
            print(f"  ⚠ [{e['도형']}]p{e['문단']} 못 찾음: {e['찾던 글']}")
            print(f"       그 자리에는: {e['그 자리 글']}")
        else:
            print(f"  · [{e['도형']}]p{e['문단']} {e['이전'][:52]}")
            print(f"       → {e['이후'][:52]}")
    if not ok:
        print("!! 문단 자리가 안 맞는다 — 아무것도 저장하지 않는다")
        return 1
    if a.dry_run:
        print("(dry-run — 저장하지 않았다)")
        return 0

    dest = a.out or a.deck
    try:
        prs.save(dest)
    except PermissionError:
        print(f"!! 저장이 막혔다 — PowerPoint 에서 열려 있는 듯하다: {dest}")
        print("   닫은 뒤 다시 돌리거나, --out 으로 다른 곳에 저장한다.")
        return 1
    Path(a.log).write_text(json.dumps(
        {"_대상": Path(a.deck).name + " (250 MB · 리포에 담지 않는다)",
         "_슬라이드": a.slide,
         "_왜": "① 'FRAM 공명' 이 어렵다 — 물리 공진으로 오독된다. ② '검증 완료' 는 "
              "끝났다고 읽히는데 끝나지 않았다. 애매한 표현을 없애고 향후 과제로 적는다.",
         "바꾼 것": LOG}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"고쳤다: {dest}")
    print("wrote", a.log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
