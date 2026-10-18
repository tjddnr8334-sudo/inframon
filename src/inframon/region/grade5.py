"""변위 5단계 등급(우수·양호·정상·주의·위험) — 형식·경간·종별마다 다른 허용값으로 판정.

**법이 주는 것과 주지 않는 것**을 섞지 않는다.

  · 법·지침이 주는 것 — 등급의 **단계와 뜻**, 그리고 어떤 변위가 d·e 인지에 대한 **정성 기준**:
      「시설물의 안전 및 유지관리에 관한 특별법 시행령」 별표 8: A 우수 · B 양호 · C 보통 · D 미흡 · E 불량.
      「시설물의 안전 및 유지관리 실시 세부지침(안전점검·진단 편)」(2021.12) 교량 편
        [표 1.20] 콘크리트 교각 · [표 1.21] 기초: "기초의 부등침하로 교대/교각이 기울음" = d,
        "…기울음으로 상부구조의 단차 및 파손 발생" = e. '교량 교각의 부등침하'는 중대한 결함.
      종별(1종·2종·3종)은 같은 법의 시설물 구분.
  · 법·지침이 주지 **않는** 것 — 몇 mm·몇 rad 부터 d·e 인가. 지침의 표에는 수치가 없다.
    그래서 수치는 설계기준·문헌에서 가져오고, 값마다 출처를 붙여 돌려준다:
      구조 한계 각변위  단순교 0.008 · 연속교 0.004 rad — AASHTO LRFD C10.5.2.2 (Moulton et al. 1985)
      관리 기준         각변위 1/500 · 침하 25 mm — inframon ``life.limits`` 기본 가정(보수적 조기 기준)

등급을 정하는 두 눈금:
  관리 비율 r_m = max(|침하| / 25 mm, 각변위 / (1/500))        ← 조기 경보(A~D 를 가른다)
  구조 비율 r_s = 각변위 / 구조 한계(형식별)                     ← 위험(E)을 가른다

  위험(E)  r_s ≥ 1            형식별 구조 한계 초과 — 지침의 e(상부구조 단차·파손 우려)에 대응
  주의(D)  r_m ≥ 종별 셋째 값  관리 기준에 다가섬·넘음 — 지침의 d(부등침하 발생)를 놓치지 않게 일찍
  정상(C)  r_m ≥ 종별 둘째 값
  양호(B)  r_m ≥ 종별 첫째 값
  우수(A)  그 밖

종별 경계는 엔진(criteria.yaml ``ratio_thresholds``)의 관심·주의·경고 경계와 **같은 값**이다. 그래서
기존 4단계와 어긋나지 않는다: 정상→우수 · 관심→양호 · 주의→정상 · 경고→주의, 그리고 형식별 구조
한계를 넘은 것만 새로 '위험'이 된다.

이 등급은 **위성 변위만으로 본 등급**이다. 법정 안전등급(A~E)은 균열·부식·내하력을 포함한
점검·진단으로 정하며, 이 값이 그것을 대신하지 않는다 — 단계 이름만 맞춰 읽기 쉽게 한 것이다.
"""

from __future__ import annotations

from ..life.limits import DEFAULTS

GRADES = ("우수", "양호", "정상", "주의", "위험")
LETTERS = "ABCDE"                       # 시행령 별표 8 의 단계와 같은 순서(뜻이 같다는 주장은 아니다)

# 연속 구조로 보는 형식 — 부정정이라 부등침하에 민감하다(엔진 criteria.yaml 과 같은 목록 + 같은 계열)
CONTINUOUS_TYPES = frozenset({
    "라멘교", "PSC박스거더교", "강박스거더교", "RC박스거더교", "사장교", "현수교", "아치교", "트러스교",
    "엑스트라도즈드교"})
STRUCT_BETA = {"continuous": 0.004, "simple": 0.008}
# 경간을 대장에서 못 얻었을 때의 형식별 대표 경간[m] — 교량 연장을 넘지 않게 자른다
TYPICAL_SPAN_M = {
    "RC슬래브교": 10, "RC중공슬래브교": 15, "PSC슬래브교": 20, "PSC중공슬래브교": 20, "라멘교": 15,
    "RCT거더교": 15, "PSCI거더교": 30, "PSC박스거더교": 50, "RC박스거더교": 30, "강박스거더교": 45,
    "프리플렉스거더교": 35, "강I거더교": 35, "강플레이트거더교": 30, "사장교": 150, "현수교": 200,
    "엑스트라도즈드교": 100, "아치교": 80, "트러스교": 60}
DEFAULT_SPAN_M = 25
# 종별 관리 비율 경계 (양호, 정상, 주의) — 중요한 시설일수록 일찍 올린다. 엔진 ratio_thresholds 와 같은 값.
CLASS_CUTS = {"1종": (0.3, 0.5, 0.7), "2종": (0.4, 0.6, 0.8), "3종": (0.5, 0.7, 0.9), "기타": (0.5, 0.7, 0.9)}

SOURCES = {
    "grades": "시설물안전법 시행령 별표 8 (A 우수 · B 양호 · C 보통 · D 미흡 · E 불량) — 단계만 차용",
    "qualitative": "세부지침(안전점검·진단 편, 2021.12) 교량 [표 1.20]·[표 1.21] — 부등침하로 기울음 d, 상부구조 단차·파손 e (수치 없음)",
    "struct_beta": "AASHTO LRFD C10.5.2.2 (Moulton et al. 1985) — 단순교 0.008 · 연속교 0.004 rad",
    "mgmt_beta": "관리 기준 1/500 — inframon life.limits 기본 가정(법정 수치 아님)",
    "mgmt_settlement": "관리 기준 25 mm — inframon life.limits 기본 가정(지반조건 의존, 법정 수치 아님)",
    "class_cuts": "종별 경계 — 시설물안전법의 1·2·3종 구분에 맞춘 운영 설정(법정 수치 아님)",
}


def span_of(typ: str | None, span_m=None, length_m=None) -> float:
    """판정에 쓸 경간[m]: 주어진 값 > 형식별 대표값(연장 이하)."""
    if span_m:
        return float(span_m)
    s = float(TYPICAL_SPAN_M.get(typ or "", DEFAULT_SPAN_M))
    return min(s, float(length_m)) if length_m else s


def allowables(typ: str | None, cls: str | None, span_m=None, length_m=None, *,
               overrides: dict | None = None) -> dict:
    """교량 하나의 허용값 — 형식(연속/단순)·크기(경간)·종별에 따라 달라지는 것을 한데 모은다.

    ``overrides`` (region.json 의 ``grade5``): struct_beta{continuous,simple} · mgmt_beta ·
    mgmt_settlement_mm · class_cuts{종별: [양호, 정상, 주의]} — 바꾼 값은 출처가 '사용자 지정'이 된다.
    """
    o = overrides or {}
    kind = "continuous" if (typ or "") in CONTINUOUS_TYPES else "simple"
    span = span_of(typ, span_m, length_m)
    sb = {**STRUCT_BETA, **(o.get("struct_beta") or {})}[kind]
    mb = float(o.get("mgmt_beta") or DEFAULTS["angular_distortion"])
    ms = float(o.get("mgmt_settlement_mm") or DEFAULTS["settlement_mm"])
    cuts_all = {**CLASS_CUTS, **{k: tuple(v) for k, v in (o.get("class_cuts") or {}).items()}}
    key = cls if cls in cuts_all else "기타"
    src = dict(SOURCES)
    for k, name in (("struct_beta", "struct_beta"), ("mgmt_beta", "mgmt_beta"),
                    ("mgmt_settlement_mm", "mgmt_settlement"), ("class_cuts", "class_cuts")):
        if o.get(k):
            src[name] = "사용자 지정"
    return {
        "structure": kind, "type": typ or "미상", "class": key, "span_m": span,
        "struct_beta": sb,                                  # 형식별
        "struct_diff_mm": round(sb * span * 1000.0, 1),     # 크기별: 인접 지점 간 허용 부등침하량
        "mgmt_beta": mb,
        "mgmt_diff_mm": round(mb * span * 1000.0, 1),
        "mgmt_settlement_mm": ms,
        "cuts": tuple(float(x) for x in cuts_all[key]),     # 종별
        "sources": src,
    }


def _index(d_mm, beta, a: dict) -> tuple[int, float | None, float | None]:
    r_m = None
    if d_mm is not None or beta is not None:
        r_m = max(abs(d_mm or 0.0) / a["mgmt_settlement_mm"], abs(beta or 0.0) / a["mgmt_beta"])
    r_s = None if beta is None else abs(beta) / a["struct_beta"]
    if r_m is None:
        return -1, None, None
    b, c, d = a["cuts"]
    if r_s is not None and r_s >= 1.0:
        return 4, r_m, r_s
    return (3 if r_m >= d else 2 if r_m >= c else 1 if r_m >= b else 0), r_m, r_s


def grade(d_mm, beta, *, typ=None, cls=None, span_m=None, length_m=None,
          d_proj_mm=None, beta_proj=None, overrides: dict | None = None) -> dict:
    """측정값(누적 침하 mm · 각변위 rad) → 5단계 등급과 근거.

    10년 예측이 더 나쁘면 **한 단계만** 올린다(엔진과 같은 규칙). 다만 예측만으로 '위험'을
    만들지는 않는다 — 위험은 지금 잰 값이 구조 한계를 넘었을 때만이다.
    값이 없으면 idx=-1(판정 불가).
    """
    a = allowables(typ, cls, span_m, length_m, overrides=overrides)
    idx, r_m, r_s = _index(d_mm, beta, a)
    if idx < 0:
        return {"idx": -1, "grade": "판정 불가", "letter": "", "allow": a, "basis": ["측정값 없음"],
                "ratio_mgmt": None, "ratio_struct": None, "raised_by_projection": False}
    raised = False
    if d_proj_mm is not None or beta_proj is not None:
        pidx, _, _ = _index(d_proj_mm, beta_proj, a)
        if pidx > idx and idx < 3:
            idx, raised = idx + 1, True
    b, c, d = a["cuts"]
    basis = [
        f"관리 비율 {r_m:.0%} = max(침하 {abs(d_mm or 0):.1f}/{a['mgmt_settlement_mm']:.0f} mm, "
        f"각변위 {abs(beta or 0):.5f}/{a['mgmt_beta']:.4f})",
        f"{a['class']} 경계: 양호 {b:.0%} · 정상 {c:.0%} · 주의 {d:.0%}",
    ]
    if r_s is not None:
        basis.append(f"구조 비율 {r_s:.0%} = 각변위/{a['struct_beta']:.3f} "
                     f"({'연속' if a['structure'] == 'continuous' else '단순'}교 · 경간 {a['span_m']:.0f} m → "
                     f"허용 부등침하 {a['struct_diff_mm']:.0f} mm) — 100% 이상이면 위험")
    if raised:
        basis.append("10년 예측이 더 나빠 한 단계 올림")
    return {"idx": idx, "grade": GRADES[idx], "letter": LETTERS[idx], "allow": a, "basis": basis,
            "ratio_mgmt": round(r_m, 3), "ratio_struct": None if r_s is None else round(r_s, 3),
            "raised_by_projection": raised}


def grade_bridge(b: dict, *, overrides: dict | None = None) -> dict | None:
    """엔진 state.json 의 교량 한 줄 → 5단계 등급. 미처리·판정 불가는 None."""
    r = b.get("r")
    if not r or (r.get("lv") is None) or r["lv"] < 0:
        return None
    g = grade(r.get("dn"), r.get("bn"), typ=r.get("typ") or b.get("t"), cls=r.get("cls") or b.get("cls"),
              span_m=r.get("sp"), length_m=b.get("len"), d_proj_mm=r.get("d10"), beta_proj=r.get("b10"),
              overrides=overrides)
    g["review"] = bool(r.get("rv"))
    return g


def summarize(state: dict, *, overrides: dict | None = None) -> dict:
    """지역 전체 분포 → {counts{등급: n}, review{등급: n}, rows[...]}. rows 는 CSV 로 내보낼 수 있는 형태."""
    counts = {g: 0 for g in GRADES}
    review = {g: 0 for g in GRADES}
    rows = []
    for b in state.get("bridges", []):
        g = grade_bridge(b, overrides=overrides)
        if g is None or g["idx"] < 0:
            continue
        counts[g["grade"]] += 1
        if g["review"]:
            review[g["grade"]] += 1
        a, r = g["allow"], b["r"]
        rows.append({
            "id": b["id"], "교량명": b["n"], "시군": b.get("c"), "형식": a["type"], "종별": a["class"],
            "구조": "연속" if a["structure"] == "continuous" else "단순", "경간_m": a["span_m"],
            "등급": g["grade"], "기호": g["letter"], "확인필요": "예" if g["review"] else "",
            "침하_mm": r.get("dn"), "각변위_rad": r.get("bn"),
            "관리비율": g["ratio_mgmt"], "구조비율": g["ratio_struct"],
            "허용부등침하_구조_mm": a["struct_diff_mm"], "허용부등침하_관리_mm": a["mgmt_diff_mm"],
            "허용침하_관리_mm": a["mgmt_settlement_mm"], "예측으로올림": "예" if g["raised_by_projection"] else "",
            "기존판정": {0: "정상", 1: "관심", 2: "주의", 3: "경고"}.get(r.get("lv"), ""),
        })
    return {"counts": counts, "review": review, "rows": rows}
