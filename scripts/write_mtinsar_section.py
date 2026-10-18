#!/usr/bin/env python3
"""MT-InSAR 의 **기선망과 시간결맞음(γ)** 을 교량 `결과.md` 에 올린다.

값은 이미 다 나와 있었다 — `mtinsar.json` 에 기선망·기선 범위·γ 중앙값이,
`mtinsar_baselines.csv` 에 시점별 B⊥·시간기선이, `mtinsar_baseline.png` 에 그림이
있다. 그런데 **문서에는 한 줄도 없었다.** 가양대교 γ 중앙값이 0.155 라는 것은
"점 3,189개 중 382개만 문턱을 넘었다" 는 뜻이고, 이건 결과를 읽는 방식을 바꾸는
숫자다. 그런 값이 json 안에만 있으면 없는 것과 같다.

올리는 것
  · **기선** — B⊥ 범위·표준편차, 시간기선 폭, 쌍 수·연결성, 100 m 기준 높이 모호성
  · **γ(시간결맞음)** — 중앙값, 문턱과 통과 점수, 그리고 **그 점들이 어디 있는지**
    (교면 0~10 m 안에 몇 점이 남았는가). γ 는 값보다 자리가 중요하다.
  · 잔차고도 불확실도 σΔh — 형하고보다 크면 높이로 교면을 못 가린다는 뜻이다.
  · 어느 도구 사슬로 나온 값인지(SNAP / StaMPS)

`run_mtinsar.py` 가 교량을 처리할 때마다 같은 함수를 불러 절을 갱신하므로, 이
스크립트는 **이미 처리해 둔 교량의 문서를 뒤늦게 채우는 용도**다.

    python scripts/write_mtinsar_section.py

산출: docs/bridges/<교량>/결과.md 의 `## 기선망과 시간결맞음` 절
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SECTION = "## 기선망과 시간결맞음"
NL = chr(10)


def _fmt(v, spec="", dash="—"):
    return dash if v is None else format(v, spec)


def section(rec: dict) -> str:
    """mtinsar.json 한 건 → 결과.md 에 넣을 절 문자열."""
    net = rec.get("network") or {}
    bl = rec.get("baseline") or {}
    org = rec.get("origin") or {}
    chain = org.get("toolchain", "SNAP")

    n_pts, n_kept = rec.get("n_points"), rec.get("n_kept")
    frac = (f" ({100.0 * n_kept / n_pts:.0f} %)"
            if n_pts and n_kept is not None and n_pts > 0 else "")

    L = [SECTION, "",
         "점마다 `los = c + v·t + K·Δh (+ α·ΔT)` 를 같이 푼다. K 는 수직기선이 정하므로",
         "**기선이 얼마나 벌어져 있는지가 잔차고도를 가를 수 있는지를 정한다.** γ 는 그 모형이",
         "그 점을 얼마나 설명하는지이고, 값보다 **어디 있는 점이 살아남았는지**가 중요하다.", "",
         "| 항목 | 값 |", "|---|---|",
         f"| 처리 사슬 | {chain}"
         + (f" · `{org['dir']}`" if org.get("dir") else "") + " |",
         f"| 시점 · 마스터 | {_fmt(rec.get('n_epochs'))}시점 · "
         f"{rec.get('master') or '—'} |",
         f"| 수직기선 B⊥ | {_fmt(bl.get('bperp_min_m'), '+.1f')} ~ "
         f"{_fmt(bl.get('bperp_max_m'), '+.1f')} m "
         f"(폭 {_fmt(bl.get('bperp_span_m'), '.0f')} m · "
         f"σ {_fmt(bl.get('bperp_std_m'), '.1f')} m) |",
         f"| 시간기선 | {_fmt(bl.get('btemp_span_days'), '.0f')} 일 |",
         f"| 기선망 | 쌍 {_fmt(net.get('n_pairs'))}개 · 차수 최소 "
         f"{_fmt(net.get('degree_min'))} · 평균 "
         f"{_fmt(net.get('degree_mean'), '.1f')} · "
         + ("**연결됨**" if net.get("connected") else "**끊김**")
         + f" (문턱 B⊥ ≤ {_fmt(net.get('max_bperp_m'), '.0f')} m · "
           f"Bt ≤ {_fmt(net.get('max_btemp_days'), '.0f')} 일) |",
         f"| 높이 모호성 | B⊥=100 m 에서 "
         f"{_fmt(rec.get('height_ambiguity_m_at_100m'), '.0f')} m |",
         f"| **γ 중앙값** | **{_fmt(rec.get('gamma_median'), '.3f')}** "
         f"(문턱 {_fmt(rec.get('gamma_min'), '.2f')} → "
         f"{_fmt(n_kept)}/{_fmt(n_pts)}점 통과{frac}) |"]

    nd, ndk = rec.get("n_deck_10m"), rec.get("n_deck_10m_kept")
    if nd is not None:
        L.append(f"| γ 통과 점이 어디 있나 | 교량 위(0~10 m) {_fmt(ndk)}/{_fmt(nd)}점 "
                 f"· 먼 맨땅(100~200 m) {_fmt(rec.get('n_ground_100_200m'))}점 |")
    sdh = rec.get("sigma_dh_median_m")
    if sdh is not None:
        L.append(f"| 잔차고도 σΔh | {sdh:.1f} m — 형하고(8~25 m)보다 "
                 + ("크다: 높이로 교면 점을 가려낼 수 없다" if sdh > 25
                    else "작다: 높이로 가려낼 여지가 있다") + " |")
    if rec.get("aps_rms_mm") is not None:
        L.append(f"| APS 제거량 | RMS {rec['aps_rms_mm']:.2f} mm · 잔차 "
                 f"{_fmt(rec.get('resid_rms_before_mm'), '.1f')} → "
                 f"{_fmt(rec.get('resid_rms_after_mm'), '.1f')} mm |")
    if org.get("slant_range"):
        L.append(f"| 슬랜트 거리 | {rec.get('slant_range_m'):,.0f} m — "
                 f"{org['slant_range']} |")

    L += ["", "![기선망](mtinsar_baseline.png)", "",
          "시점별 B⊥·시간기선은 `mtinsar_baselines.csv`, 점별 속도·Δh·열팽창·γ 는",
          "`mtinsar_points.csv` 에 있다(SARPROZ 가 내는 표와 같은 꼴).", ""]
    return NL.join(L)


def update_md(folder: Path, rec: dict) -> bool:
    """결과.md 의 절을 바꿔 넣는다(없으면 끝에 붙인다). 바뀌었으면 True."""
    md = folder / "결과.md"
    if not md.exists():
        return False
    block = section(rec)
    txt = md.read_text(encoding="utf-8")
    pat = re.compile(re.escape(SECTION) + r".*?(?=" + NL + r"## |\Z)", re.S)
    new = pat.sub(lambda _: block, txt) if pat.search(txt) else (
        txt.rstrip() + NL * 2 + block)
    if new == txt:
        return False
    md.write_text(new, encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs/bridges")
    a = ap.parse_args()
    n = 0
    for j in sorted(Path(a.root).glob("*/mtinsar.json")):
        rec = json.loads(j.read_text(encoding="utf-8"))
        if rec.get("skipped"):
            continue
        if update_md(j.parent, rec):
            n += 1
            print(f"  {j.parent.name:<12} γ {_fmt(rec.get('gamma_median'), '.3f')} · "
                  f"B⊥ {_fmt((rec.get('baseline') or {}).get('bperp_span_m'), '.0f')} m "
                  f"· 쌍 {_fmt((rec.get('network') or {}).get('n_pairs'))}")
    print(f"{n}개 교량 결과.md 에 '{SECTION[3:]}' 절을 넣었다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
