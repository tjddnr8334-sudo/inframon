#!/usr/bin/env python3
"""연구개발 7p 발표자료의 **현장 검증 장을 지금 값으로** 고친다.

`docs/KICT_Bmaps_Inframon_연구개발_7p_1.pptx` 는 손으로 만든 자료라 생성 스크립트가
없다. 그래서 pptx 를 직접 열어 **틀린 숫자만 골라 바꾸고**, 근거 그림을 한 칸 넣는다.
바꾼 내역은 `docs/bridges/deck_patch_log.json` 에 남긴다 — 무엇이 왜 바뀌었는지
나중에 따라갈 수 있어야 한다.

무엇이 틀렸었나
  · "위성 판정은 현장 보고서와 **16곳 중 14곳에서 일치**" — 이 값은 '데크 ±30 m' 로
    고른 점에서 나왔다. 그 점들은 교면이 아니라 강변 지반이었다
    (`make_deck_vs_ground.py` — 15개소 중 11개소에서 교량 위 점의 계절 위상이
    100~200 m 떨어진 맨땅과 1.5개월 안으로 같다). 근거가 없어진 숫자다.
  · "월별 수치 상관 R² 0.13~0.88 (6개소)" — 같은 점에서 나온 값이고, 게다가 보고서
    계측을 시계열로 놓고 잰 것이다. 계측은 해마다 같은 달의 평균(계절 기후값)이라
    그렇게 재면 안 된다(`make_climatology_compare.py`).

무엇으로 바꾸나 — 교면 전용 재선별(MT-InSAR) 뒤의 값이다.
  · 계측 곡선이 있는 5개소 중 **1개소(가양대교)만 R² ≥ 0.80** 으로 일치
  · 나머지 4개소는 **계측 지점에서 PS 를 도출하지 못함** — 주경간 중앙 ±50 m 안
    PS 가 0~2점이고 가장 가까운 점이 22~656 m 떨어져 있다

    python scripts/patch_research_deck.py

산출: 대상 pptx 를 제자리에서 갱신 · docs/bridges/deck_patch_log.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu, Pt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kaia_theme import C, _typeface                                   # noqa: E402

IN = 914400
DECK = ROOT / "docs" / "KICT_Bmaps_Inframon_연구개발_7p_1.pptx"
FIG = ROOT / "docs" / "img" / "value" / "센서자리_PS유무_납작.png"


def set_text(shape, paras) -> str:
    """**있는 run 의 글자만** 바꾼다 — XML 구조를 건드리지 않는다.

    처음에는 run 을 지우고 새로 달았는데 파일이 깨져 PowerPoint 가 못 열었다. 원래
    서식(크기·글꼴·정렬)을 그대로 두는 편이 안전하고, 자료의 생김새도 지켜진다.
    남는 run 은 빈 글자로 비우고, 모자라면 마지막 run 에 이어 붙인다.

    paras: [[(글, '16진색' 또는 None), ...], ...]  — 문단마다 run 목록
    """
    old = shape.text_frame.text
    tf = shape.text_frame
    for pi, pruns in enumerate(paras):
        if pi >= len(tf.paragraphs):
            break
        runs = tf.paragraphs[pi].runs
        if not runs:
            continue
        for ri, r in enumerate(runs):
            if ri < len(pruns):
                t, col = pruns[ri]
                # run 이 모자라면 남은 글을 마지막 run 에 몰아 넣는다.
                if ri == len(runs) - 1 and len(pruns) > len(runs):
                    t = t + "".join(x for x, _ in pruns[ri + 1:])
                r.text = t
                if col:
                    r.font.color.rgb = C(col)
            else:
                r.text = ""
    # 남는 문단은 비운다(지우지 않는다 — 구조를 건드리면 파일이 깨진다).
    for pi in range(len(paras), len(tf.paragraphs)):
        for r in tf.paragraphs[pi].runs:
            r.text = ""
    return old


def find(slide, needle: str):
    for sh in slide.shapes:
        if sh.has_text_frame and needle in sh.text_frame.text:
            return sh
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", default=str(DECK))
    ap.add_argument("--verdicts", default="docs/bridges/gnss_verdict_all.json")
    ap.add_argument("--log", default="docs/bridges/deck_patch_log.json")
    ap.add_argument("--figure", action="store_true",
                    help="슬라이드 6 오른쪽 열에 근거 그림을 넣는다(연동 접점 표를 덮을 수 있다)")
    a = ap.parse_args()

    V = json.loads(Path(a.verdicts).read_text(encoding="utf-8"))["bridges"]
    got = [v for v in V if v.get("r2") is not None]
    ok = [v for v in got if v["verdict"] == "계측과 일치"]
    no = [v for v in got if v["verdict"] != "계측과 일치"]
    zero = [v for v in no if v.get("n_ps_within_50m_of_mid", 1) == 0]
    best = max(got, key=lambda v: v["r2"])

    prs = Presentation(a.deck)
    s = prs.slides[5]                       # ⑥ 연구수행 내용 ③ — B-Maps 연동·현장 검증
    log = []

    def swap(needle, runs, why):
        sh = find(s, needle)
        if sh is None:
            log.append({"찾음": False, "찾던 글": needle})
            return
        old = set_text(sh, runs)
        log.append({"이전": old.replace("\n", " / "),
                    "이후": " / ".join("".join(t for t, _ in pr) for pr in runs),
                    "이유": why})

    swap("16곳 중 14곳",
         [[(f"B-Maps 연동은 구현했다. 현장 대조는 계측 곡선이 있는 {len(got)}개소 중 "
            f"{len(ok)}개소만 일치 — 나머지는 계측 지점에 PS 가 없었다", None)]],
         "‘16곳 중 14곳 일치’ 는 데크 ±30 m 로 고른 강변 지반 점에서 나온 값이라 폐기")

    swap("14/16",
         [[(f"{len(ok)}/{len(got)}", "1E7A54"), ("  개소", None)]],
         "교면 전용 재선별 뒤 계측과 일치한 교량 수")
    swap("위성 판정이 현장 보고서",
         [[("교면 PS 가 계측과 일치", None)],
          [(f"{best['name']} R² {best['r2']:.2f} · 우연 {best['r2_chance95']:.2f}",
            None)]],
         "판정 근거를 R² 와 우연 기준선으로 바꿈")

    swap("15/16",
         [[(f"{len(no)}/{len(got)}", "ED6C00"), ("  개소", None)]],
         "계측 지점에서 PS 를 도출하지 못한 교량 수")
    lo = min(v.get("n_ps_within_50m_of_mid", 0) for v in no)
    hi = max(v.get("n_ps_within_50m_of_mid", 0) for v in no)
    swap("유의한 거동 없음",
         [[("계측 지점에서 PS 도출 못함", None)],
          [(f"주경간 중앙 ±50 m 안 PS {lo}~{hi}점 ({len(zero)}개소는 0점)", None)]],
         "‘관리기준 이내와 부합’ 은 근거가 없어져 위치 근거로 교체")

    swap("보완 과제",
         [[("원인  ", "ED6C00"),
           ("계측기는 주경간 중앙·주탑에 있는데 강 위 주경간은 산란체가 없어 PS 가 "
            "교대·접속부에 몰린다. 방법이 아니라 자료가 그 자리에 없는 것이다 — "
            "주경간에 코너리플렉터를 놓으면 바로 채워진다 (Ⅳ 참조)", None)]],
         "보완 과제를 ‘원인 + 해결책’ 으로 바꿈(R² 0.13~0.88 은 옛 점에서 나온 값)")

    swap("※ 판정 일치",
         [[("※ 대조는 교면 전용 재선별(MT-InSAR: 기선망·점별 DEM 오차·열팽창·APS) 뒤의 "
            "교면 PS 를 쓴다. 계측은 해마다 같은 달의 평균(계절 기후값)이라 월별로 접어 "
            "맞대고, 점 N개 중 최대 R² 의 우연 기준선을 같이 낸다. 계측기 정확 위치는 "
            "보고서에 없어 계측 종류로부터 주경간 중앙을 가정했다. "
            "근거 그림: docs/img/value/센서자리_PS유무.png", None)]],
         "근거 문장을 새 방법으로 교체")

    # 그림은 기본으로 넣지 않는다 — 이 자료는 두 장 다 빈 칸이 없어서, 넣으면 아래
    # '연동 접점' 표를 덮는다. 남의 배치를 망가뜨리는 것보다 그림을 따로 두는 편이 낫다.
    # 자리를 비워 두고 넣고 싶으면 --figure 로 켠다.
    if a.figure and FIG.exists():
        s.shapes.add_picture(str(FIG), Emu(round(7.70 * IN)), Emu(round(5.68 * IN)),
                             width=Emu(round(5.18 * IN)))
        log.append({"추가": FIG.name, "자리": "슬라이드 6 오른쪽 열 x7.70 y5.68 w5.18",
                    "이유": "계측 지점에 PS 가 없다는 것을 위치로 보이는 그림"})
        cap = s.shapes.add_textbox(Emu(round(7.70 * IN)), Emu(round(6.66 * IN)),
                                   Emu(round(5.18 * IN)), Emu(round(0.24 * IN)))
        r = cap.text_frame.paragraphs[0].add_run()
        r.text = "붉은 ▽ = 주경간 중앙(계측 지점) · 점 = 교면 PS, 색은 계측과의 |r|"
        r.font.size = Pt(8)
        r.font.color.rgb = C("6B7480")
        _typeface(r, "Paperlogy 5 Medium")

    prs.save(a.deck)
    Path(a.log).write_text(json.dumps(
        {"_대상": str(Path(a.deck).name),
         "_왜": "‘16곳 중 14곳 일치’ 등은 데크 ±30 m 로 고른 강변 지반 점에서 나온 값이다. "
              "교면 전용 재선별 뒤의 값으로 바꾼다",
         "_판정기준": "교면 PS 중 최대 R² ≥ 0.80 → 일치, 그 아래 → 계측 지점에서 PS 도출 못함",
         "바꾼 것": log}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"고쳤다: {a.deck}")
    for e in log:
        if "이전" in e:
            print(f"  · {e['이전'][:46]:<48} → {e['이후'][:46]}")
        elif e.get("찾음") is False:
            print(f"  ⚠ 못 찾음: {e['찾던 글']}")
        else:
            print(f"  + {e['추가']} ({e['자리']})")
    print("wrote", a.log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
