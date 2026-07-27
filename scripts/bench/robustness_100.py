"""100 교량 로버스트니스 벤치 — 다양한 형식·규모·좌표·데이터 조합에서 문제를 잡는다.

실 데이터 100개는 없다. 대신 실제로 마주칠 조합을 100 케이스로 만들어 파이프라인을
돌리고, 케이스별로 (a) 크래시 (b) 비물리적 출력 (c) 미처리 경계를 카탈로그한다.
목표는 실 교량 진단이 아니라 **소프트웨어 결함 발견**이다.

Tier A (100 케이스, 빠름): stub 파이프라인 + 프로파일·좌표·지오로케이션·잔존수명 로직.
Tier B (형식×규모 대표, 느림): real PINN/FRAM 물리 sanity.
"""
import itertools
import json
import math
import os
import traceback

import numpy as np

T = os.environ.get("INFRAMON_BENCH_TMP", "data/bench"); os.makedirs(T, exist_ok=True)
from inframon.config import PipelineConfig
from inframon.contracts.io import ProjectStore
from inframon.contracts.schema import InSAROutput
from inframon.orchestrator.pipeline import run_pipeline
from inframon.structure import BRIDGE_TYPES, resolve_profile
from inframon.korea_crs import KOREA_ORIGINS, to_wgs84
from inframon.insar.geolocation import apply_correction, diagnose
from inframon.life import estimate_remaining_life

PROBLEMS = []          # (case_id, severity, category, detail)
CASES = []             # 케이스 요약


def problem(cid, sev, cat, detail):
    PROBLEMS.append({"case": cid, "sev": sev, "cat": cat, "detail": detail})


# ── 케이스 매트릭스 (100개) ────────────────────────────────────────────
def build_matrix():
    cases = []
    spans = [12, 18, 25, 35, 50, 70, 90, 120, 200, 400, 800, 1500]   # 소~초대형
    origins = list(KOREA_ORIGINS)
    # 1) 형식 × 규모 (8형식 × 대표 span 몇 개) — 프로파일/PINN
    for i, (typ, span) in enumerate(itertools.product(
            BRIDGE_TYPES, [18, 40, 90, 200, 800])):
        cases.append({"id": f"A{i:02d}", "kind": "profile", "type": typ, "span": span})
    # 2) 좌표 원점계 × 위치 (4원점 × 위치 여럿) — korea_crs
    kr_pts = [(37.5, 127.0), (37.6, 126.9), (37.75, 128.9), (37.5, 130.9),
              (35.1, 129.0), (33.4, 126.5), (38.2, 127.5), (36.3, 127.4)]
    for i, (org, (lat, lon)) in enumerate(itertools.product(origins, kr_pts)):
        cases.append({"id": f"C{i:02d}", "kind": "coord", "origin": org, "lat": lat, "lon": lon})
    # 3) 지오로케이션 — 입사각 × dem_error 분포
    for i, (inc, dhmax) in enumerate(itertools.product(
            [29, 33, 39, 43, 46], [2, 8, 15, 25])):
        cases.append({"id": f"G{i:02d}", "kind": "geoloc", "inc": inc, "dhmax": dhmax})
    # 4) 잔존수명 — 침하율 × 한계 × 관측기간
    for i, (rate, settle, years) in enumerate(itertools.product(
            [0.0, 0.5, 2.0, 8.0], [10, 25, 50], [1.5, 3.0, 8.0])):
        cases.append({"id": f"L{i:02d}", "kind": "life", "rate": rate, "settle": settle, "years": years})
    return cases


# ── Tier A 실행기 ──────────────────────────────────────────────────────
def run_profile(c):
    cfg = PipelineConfig()
    cfg.bridge_profile = {"bridge_type": c["type"], "length_m": float(c["span"])}
    p = resolve_profile(cfg, None)
    rec = {"depth": p.section_depth_m, "boundary": p.boundary, "material": p.material,
           "q": p.load_per_len, "gEI": p.geometric_EI()}
    # sanity: 단면높이 물리 범위(0.3~10m), 재료 유효
    if not (0.3 <= p.section_depth_m <= 10.0):
        problem(c["id"], "med", "profile", f"단면높이 {p.section_depth_m}m 비물리(형식 {c['type']}, span {c['span']})")
    if p.material not in ("steel", "concrete", "reinforced_concrete", "prestressed_concrete"):
        problem(c["id"], "high", "profile", f"미지 재료 {p.material}")
    if c["type"] == "rahmen" and p.boundary != "fixed":
        problem(c["id"], "low", "profile", f"라멘인데 경계 {p.boundary}(고정단 기대)")
    return rec


def run_coord(c):
    x, y = _to_tm(c["lon"], c["lat"], KOREA_ORIGINS[c["origin"]][0])
    r = to_wgs84(x, y, c["origin"])
    err = math.hypot((r.lat - c["lat"]), (r.lon - c["lon"])) * 111000
    rec = {"err_m": round(err, 4), "in_korea": r.in_korea, "note": bool(r.note)}
    if err > 0.1:
        problem(c["id"], "high", "coord", f"왕복 오차 {err:.2f}m ({c['origin']} {c['lat']},{c['lon']})")
    if not r.in_korea:
        problem(c["id"], "med", "coord", f"한국 범위 밖 판정 ({c['lat']},{c['lon']} {c['origin']})")
    return rec


def run_geoloc(c):
    rng = np.random.default_rng(hash(c["id"]) % 2**32)
    n = 80
    dh = rng.uniform(-1, c["dhmax"], n)
    xyz = np.column_stack([np.full(n, 127.1) + rng.normal(0, 1e-4, n),
                           np.full(n, 37.5) + rng.normal(0, 1e-4, n), np.zeros(n)])
    gc = apply_correction(xyz, dh, float(c["inc"]), -13.0, crs_is_lonlat=True, base_height=np.zeros(n))
    d = diagnose(dh, float(c["inc"]), -13.0)
    rec = {"shift_p95": gc["meta"]["shift_p95_abs_m"], "needs": d["needs_correction"]}
    # sanity: 보정 결과에 NaN/inf 없어야
    if not np.isfinite(gc["xyz"]).all():
        problem(c["id"], "high", "geoloc", f"보정 결과 NaN/inf (inc {c['inc']}, dhmax {c['dhmax']})")
    # 큰 δh + 작은 입사각이면 큰 쉬프트 — 경고 나와야
    exp_big = c["dhmax"] >= 15 and c["inc"] <= 33
    if exp_big and not d["needs_correction"]:
        problem(c["id"], "low", "geoloc", f"큰 쉬프트인데 보정불필요 판정 (inc {c['inc']}, dhmax {c['dhmax']})")
    return rec


def run_life(c, project):
    with ProjectStore(project) as s:
        ins = s.read_meta("insar", InSAROutput)
        days = s.read_array(ins.dates_ds)
        base = float(days[0])
        # 관측기간을 years 로 맞춘 날짜축
        m = ins.n_dates
        newdays = base + np.linspace(0, c["years"] * 365.25, m)
        s.write_array(ins.dates_ds, newdays)
        # 침하율 주입 (열성분 제거되도록 comp_thermal 0)
        n = ins.n_points
        t = (newdays - newdays[0]) / 365.25
        los = (-c["rate"] * t)[None, :] * np.ones((n, 1)) + \
            np.random.default_rng(0).normal(0, 0.05, (n, m))
        s.write_array(ins.los_ds, los)
        from inframon.contracts.schema import PINNOutput
        pn = s.read_meta("pinn", PINNOutput)
        s.write_array(pn.comp_thermal_ds, np.zeros_like(los))
        out = estimate_remaining_life(s, user_limits={"settlement_mm": float(c["settle"])},
                                      write=False)
    rec = {"rsl": out.rsl_lower_years, "gov": out.governing, "cens": round(out.censored_fraction, 2),
           "conf": out.confidence}
    # sanity
    if out.rsl_lower_years is not None and out.rsl_lower_years < 0:
        problem(c["id"], "high", "life", f"음수 잔존수명 {out.rsl_lower_years}")
    # 침하율 0 이면 검열되어야(열화 없음)
    if c["rate"] == 0.0 and out.rsl_lower_years is not None:
        problem(c["id"], "med", "life", f"열화 0인데 유한 잔존수명 {out.rsl_lower_years} (검열 기대)")
    # 침하율 큰데 관측 충분(≥3년)이면 유한값 나와야
    if c["rate"] >= 2.0 and c["years"] >= 3.0 and out.rsl_lower_years is None and out.censored_fraction > 0.5:
        problem(c["id"], "low", "life", f"큰 침하({c['rate']}mm/yr)·관측 {c['years']}년인데 전부 검열")
    return rec


def _to_tm(lon, lat, epsg):
    from pyproj import Transformer
    return Transformer.from_crs("EPSG:4326", epsg, always_xy=True).transform(lon, lat)


def main():
    cases = build_matrix()
    print(f"케이스 {len(cases)}개 생성")
    # life 용 공용 project 하나(빠르게)
    proj = T + "/bench_life.h5"
    run_pipeline(proj, PipelineConfig(n_points=60, n_dates=24))

    for c in cases:
        try:
            if c["kind"] == "profile":
                c["result"] = run_profile(c)
            elif c["kind"] == "coord":
                c["result"] = run_coord(c)
            elif c["kind"] == "geoloc":
                c["result"] = run_geoloc(c)
            elif c["kind"] == "life":
                c["result"] = run_life(c, proj)
            c["ok"] = True
        except Exception as exc:  # noqa: BLE001 — 크래시 자체가 문제
            c["ok"] = False
            problem(c["id"], "crash", c["kind"], f"{type(exc).__name__}: {str(exc)[:120]}")
            c["result"] = {"error": f"{type(exc).__name__}"}
            traceback.print_exc()
        CASES.append(c)

    # 요약
    by_kind = {}
    for c in CASES:
        by_kind.setdefault(c["kind"], [0, 0])
        by_kind[c["kind"]][0] += 1
        by_kind[c["kind"]][1] += int(c["ok"])
    print("\n=== Tier A 완료 ===")
    for k, (tot, ok) in by_kind.items():
        print(f"  {k:8} {ok}/{tot} 정상 실행")
    sev_order = {"crash": 0, "high": 1, "med": 2, "low": 3}
    PROBLEMS.sort(key=lambda p: sev_order.get(p["sev"], 9))
    print(f"\n=== 문제 {len(PROBLEMS)}건 ===")
    for p in PROBLEMS:
        print(f"  [{p['sev']:5}] {p['case']} {p['cat']}: {p['detail']}")
    with open(T + "/bench100_result.json", "w", encoding="utf-8") as fh:
        json.dump({"cases": CASES, "problems": PROBLEMS}, fh, ensure_ascii=False, indent=1, default=str)
    print(f"\n저장: {T}/bench100_result.json")


if __name__ == "__main__":
    main()
