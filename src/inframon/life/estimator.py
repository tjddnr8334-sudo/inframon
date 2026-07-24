"""잔존수명 추정 오케스트레이션 — project.h5 → `/life`.

`--remaining-life` 로만 동작하는 **후처리 스테이지**다. 4엔진(ENGINE_NAMES)에
5번째를 추가하지 않으므로 미사용 시 기존 파이프라인 수치는 완전히 불변이다.

P1 은 사용성 채널만 활성이고 나머지 채널은 **사유와 함께 비활성**으로 기록한다
(침묵하면 사용자는 그 한계상태가 검토된 줄 안다). 설계: docs/잔존수명_설계.md
"""

from __future__ import annotations

from datetime import date

import numpy as np

from ..contracts.io import LIFE_GROUP, ProjectStore
from ..contracts.schema import (
    InSAROutput,
    RemainingLifeOutput,
    RSLChannel,
)
from . import limits as limits_mod
from .aggregate import cohesive_min
from .channels import serviceability, stiffness
from .geometry import DEFAULT_INCIDENCE_DEG, los_to_vertical

# 잔존수명이 이 값을 넘으면 표시 의미가 없다(검열 상한의 하한선).
MIN_HORIZON_YEARS = 10.0


def _xy_meters(xyz: np.ndarray) -> tuple[np.ndarray, str]:
    """[N,3] 좌표 → 거리 계산용 평면 좌표[m]. 경위도면 국소 평면으로 환산.

    Track 좌표계는 투영(EPSG:5179 등)일 수도, 경위도(EPSG:4326)일 수도 있다.
    경위도를 그대로 미터로 쓰면 이웃 반경·각변위가 5자리 틀린다.
    """
    xy = np.asarray(xyz, dtype=np.float64)[:, :2]
    if np.abs(xy[:, 0]).max() <= 180.0 and np.abs(xy[:, 1]).max() <= 90.0:
        lat0 = float(np.median(xy[:, 1]))
        mx = 111_320.0 * np.cos(np.deg2rad(lat0))
        return np.stack([(xy[:, 0] - np.median(xy[:, 0])) * mx,
                         (xy[:, 1] - lat0) * 110_540.0], axis=1), "lonlat→국소평면"
    return xy - np.median(xy, axis=0), "투영좌표"


def _resolve_displacement(store: ProjectStore, ins: InSAROutput,
                          *, default_incidence_deg: float = DEFAULT_INCIDENCE_DEG) -> dict:
    """외삽에 쓸 **연직** 변위 시계열을 고르고, 어떤 변환·가정을 거쳤는지 기록한다.

    두 가지를 반드시 처리한다.
    - **기하**: 사용성 한계(침하·각변위)는 연직량 규정인데 단일 궤도 관측은 LOS 다.
      LOS 를 그대로 쓰면 1/cos θ ≈ 1.29 배 낙관적이 된다(`geometry.los_to_vertical`).
    - **열성분**: 계절 성분이 남은 신호를 외삽하면 관측창의 계절 위상이 속도를 바꾼다.
    """
    fused = bool(ins.vertical_ds and store.has_array(ins.vertical_ds))
    if fused:
        d = np.asarray(store.read_array(ins.vertical_ds), dtype=np.float64)
        why = "asc+desc 융합 연직(/insar/vertical)"
    else:
        d = np.asarray(store.read_array(ins.los_ds), dtype=np.float64)
        why = "LOS(/insar/los)"

    # ── 열성분 제거는 **투영 전에** 한다 ────────────────────────────────
    # PINN comp_thermal 은 LOS 기하로 산출된 값이다. 먼저 투영하고 나서 빼면 서로 다른
    # 기하의 양을 빼게 된다. (투영은 점별 스칼라 배라 순서를 바꿔도 대수적으로는 같지만,
    # 융합 연직 경로에서는 투영이 없으므로 뺄셈을 한 곳으로 모아야 분기가 안 갈린다.)
    thermal_removed, how = False, "없음"
    # ① 인제스트에서 이미 분리했는가(--insar-thermal). 출처 attr 이름이 경로마다 다르다 —
    #    run_insar_real 은 `insar_source`, import_track_h5 는 `track_source` 로 쓴다.
    #    한쪽만 보면 --import-track-h5 경로에서 열보정이 통째로 무시된다(실제로 그랬다).
    for attr in ("insar_source", "track_source"):
        try:
            corr = (store.read_json_attr("insar", attr) or {}).get("corrections") or {}
        except (KeyError, ValueError):
            continue
        if "thermal" in (corr.get("applied") or []):
            thermal_removed, how = True, f"인제스트 열팽창 보정(--insar-thermal, {attr})"
            break
    if not thermal_removed and store.has_meta("pinn"):     # ② 아니면 PINN 열성분 차감
        try:
            from ..contracts.schema import PINNOutput
            pn = store.read_meta("pinn", PINNOutput)
            th = np.asarray(store.read_array(pn.comp_thermal_ds), dtype=np.float64)
            if th.shape == d.shape:
                d = d - th
                thermal_removed, how = True, "PINN 성분분해 열성분 차감(/pinn/comp_thermal)"
        except (KeyError, ValueError, AttributeError):
            pass

    # ── 기하 투영: LOS → 연직 (융합 연직이면 이미 연직이라 건너뛴다) ──
    projection = None
    if not fused:
        inc = None
        if ins.incidence_ds and store.has_array(ins.incidence_ds):
            inc = np.asarray(store.read_array(ins.incidence_ds), dtype=np.float64)
        d, projection = los_to_vertical(d, inc, default_deg=default_incidence_deg)
        why += (" → 연직 투영"
                + ("(관측 입사각)" if not projection["incidence_assumed"] else "(가정 입사각)"))

    return {"disp": d, "source": why, "thermal_removed": thermal_removed, "thermal_how": how,
            "projection": projection}


def _span_m(store: ProjectStore, xy_m: np.ndarray) -> tuple[float, str, bool]:
    """지간[m]과 (값, 출처, **실제로 아는 값인가**).

    세 번째 값이 중요하다. 점군 최대 신장은 지간이 아니다 — 실 SARvey 결과에서는
    주변 건물까지 점이 잡혀 신장이 교량 지간의 10배를 넘기도 한다(정자교: 점군
    1519m vs 교량 ~120m). 그 값으로 L/800 을 만들면 한계가 1.9m 가 되어 상판에서
    아무것도 검출되지 않는다. 그래서 추정치일 때는 span_known=False 로 알린다.
    """
    try:
        from ..contracts.schema import CVOutput
        cv = store.read_meta("cv", CVOutput)
        if cv.geometry.length_unit == "m" and cv.geometry.bridge_length > 0:
            return float(cv.geometry.bridge_length), "CV 기하(bridge_length)", True
    except (KeyError, ValueError, AttributeError):
        pass
    if xy_m.shape[0] >= 2:
        ext = xy_m.max(axis=0) - xy_m.min(axis=0)
        return float(max(ext.max(), 1.0)), "점군 최대 신장 — 지간이 아님(참고용)", False
    return 30.0, "기본가정 30m", False


def _build_year(cfg) -> int | None:
    digits = "".join(ch for ch in str(getattr(cfg, "bridge_build_year", "") or "") if ch.isdigit())
    if len(digits) >= 4:
        y = int(digits[:4])
        if 1800 <= y <= 2200:
            return y
    return None


def _stiffness_channel(store: ProjectStore, observed_years: float,
                       r_limit: float, alpha: float) -> RSLChannel:
    """PINN 시간분해 EI 로 강성열화 채널을 만든다. 없으면 사유를 남기고 비활성."""
    ch = RSLChannel(name="stiffness", kind="measured", active=False)
    if not store.has_meta("pinn"):
        ch.inactive_reason = "PINN 결과가 없음 — 강성 추세를 낼 근거가 없다"
        return ch
    try:
        from ..contracts.schema import PINNOutput
        pn = store.read_meta("pinn", PINNOutput)
    except (KeyError, ValueError):
        ch.inactive_reason = "PINN 메타를 읽지 못함"
        return ch
    if not (pn.EI_series_ds and store.has_array(pn.EI_series_ds)
            and pn.EI_series_t_ds and store.has_array(pn.EI_series_t_ds)):
        ch.inactive_reason = ("시간분해 EI(EI_series)가 없음 — pinn=real 로 다시 돌리거나 "
                              "구버전 project.h5 다(EI_ds[N] 은 관측창 전체 한 값이라 추세 불가)")
        return ch
    geo = None
    try:
        geo = (store.read_json_attr("pinn", "inputs") or {}).get("geometric_EI_Nm2")
    except (KeyError, ValueError):
        pass
    r = stiffness(store.read_array(pn.EI_series_t_ds), store.read_array(pn.EI_series_ds),
                  observed_years=observed_years, r_limit=r_limit, alpha=alpha,
                  geometric_ei=geo)
    return RSLChannel(name="stiffness", kind="measured", active=r["active"],
                      inactive_reason=r["inactive_reason"], rsl_years=r["rsl_years"],
                      rsl_lower_years=r["rsl_lower_years"], censored=r["censored"],
                      detail=r["detail"])


def _hotspot(members, xyz: np.ndarray, xy_m: np.ndarray, res: dict) -> dict | None:
    """지배 열화 군집의 위치·규모·속도 — 점검 대상을 지목하기 위한 블록."""
    if not members:
        return None
    idx = np.asarray(members, dtype=int)
    pts = xy_m[idx]
    ext = pts.max(axis=0) - pts.min(axis=0) if idx.size > 1 else np.zeros(2)
    return {
        "n_points": int(idx.size),
        "point_index": [int(i) for i in idx[:20]],       # 상위 20개면 현장 확인엔 충분
        "centroid_xy": [round(float(np.mean(xyz[idx, 0])), 6),
                        round(float(np.mean(xyz[idx, 1])), 6)],
        "extent_m": [round(float(ext[0]), 1), round(float(ext[1]), 1)],
        "rate_max_mm_yr": round(float(res["rate"][idx].max()), 3),
        "rate_median_mm_yr": round(float(np.median(res["rate"][idx])), 3),
        "rsl_min_years": round(float(np.min(res["rsl"][idx])), 2),
    }


def estimate_remaining_life(
    store: ProjectStore,
    cfg=None,
    *,
    user_limits: dict | None = None,
    consumed_mm: float = 0.0,
    min_cluster: int = 3,
    alpha: float = 0.05,
    as_of: str | None = None,
    default_incidence_deg: float = DEFAULT_INCIDENCE_DEG,
    write: bool = True,
) -> RemainingLifeOutput:
    """`/life` 를 계산해 project.h5 에 기록하고 계약 객체를 돌려준다.

    `write=False` 면 파일을 건드리지 않고 계산만 한다 — 대시보드에서 한계값을
    바꿔 가며 결과가 어떻게 달라지는지 미리보기할 때 쓴다(project.h5 를 슬라이더마다
    덮어쓰지 않게). 이때 `store` 는 읽기 모드('r')로 열어도 된다.
    미저장 시 배열 `_ds` 경로는 계산된 값을 담은 in-memory 키로 채워지고,
    반환 객체의 `assumptions["_arrays"]` 에 실제 배열이 들어간다(계약 검증은 생략).
    """
    ins = store.read_meta("insar", InSAROutput)
    xyz = np.asarray(store.read_array(ins.xyz_ds), dtype=np.float64)
    member = np.asarray(store.read_array(ins.member_ds)).ravel()
    days = np.asarray(store.read_array(ins.dates_ds), dtype=np.float64).ravel()
    t_years = (days - days[0]) / 365.25
    observed_years = float(t_years[-1] - t_years[0])

    xy_m, coord_kind = _xy_meters(xyz)
    span_m, span_src, span_known = _span_m(store, xy_m)
    lim_vals, lim_srcs = limits_mod.resolve(user_limits)
    point_limit, limit_basis = limits_mod.point_limits(
        member, span_m, lim_vals, span_known=span_known)

    dsrc = _resolve_displacement(store, ins, default_incidence_deg=default_incidence_deg)
    disp = dsrc["disp"]

    # 관측이 너무 짧으면 어떤 추세도 유의하지 않다 — 계산하지 않고 사유를 남긴다.
    too_short = observed_years < 1.0 or disp.shape[1] < 4
    if too_short:
        reason = (f"관측 {observed_years:.2f}년 / {disp.shape[1]}시점 — "
                  "추세 추정 최소요건(1년·4시점) 미달")
        n = int(ins.n_points)
        res = {"rsl": np.full(n, np.inf), "rsl_lower": np.full(n, np.inf),
               "rate": np.zeros(n), "sigma": np.zeros(n),
               "sublimit": np.zeros(n, dtype=np.int16), "meta": {"skipped": reason}}
        channels = [RSLChannel(name="serviceability", kind="measured", active=False,
                               inactive_reason=reason)]
        bridge_rsl = bridge_lo = None
        conf, conf_why = "low", reason
        censored_frac = 1.0
    else:
        res = serviceability(
            t_years, disp, xy=xy_m, point_limit_mm=point_limit,
            angular_limit=lim_vals["angular_distortion"],
            consumed_mm=consumed_mm, alpha=alpha,
        )
        radius = float(res["meta"]["neighbor_radius_m"])
        bridge_rsl, agg_pt = cohesive_min(res["rsl"], xy_m, radius_m=radius,
                                          min_cluster=min_cluster)
        bridge_lo, agg_lo = cohesive_min(res["rsl_lower"], xy_m, radius_m=radius,
                                         min_cluster=min_cluster)
        censored_frac = float(np.mean(~np.isfinite(res["rsl"])))
        # 지배 군집의 위치·규모 — 숫자 하나만 주면 어디를 점검할지 알 수 없다.
        res["meta"]["hotspot"] = _hotspot(agg_pt.pop("members", None), xyz, xy_m, res)
        agg_lo.pop("members", None)
        res["meta"]["aggregate_point"] = agg_pt
        res["meta"]["aggregate_lower"] = agg_lo
        # 극값만 보면 오해한다 — 유한 잔존수명의 분포도 같이 남긴다.
        fin = res["rsl"][np.isfinite(res["rsl"])]
        if fin.size:
            res["meta"]["rsl_percentiles_years"] = {
                "p1": round(float(np.percentile(fin, 1)), 2),
                "p5": round(float(np.percentile(fin, 5)), 2),
                "p50": round(float(np.median(fin)), 2),
                "p95": round(float(np.percentile(fin, 95)), 2),
            }
        channels = [RSLChannel(
            name="serviceability", kind="measured", active=True,
            rsl_years=bridge_rsl, rsl_lower_years=bridge_lo,
            censored=bridge_lo is None,
            detail=res["meta"],
        )]
        # 신뢰도 — 관측 길이·열성분 제거·연직 관측 여부로 정한다(과신 방지)
        if not dsrc["thermal_removed"]:
            conf, conf_why = "low", ("열성분 미분리 — 계절 열팽창이 속도에 섞여 있습니다. "
                                     "--insar-thermal 또는 PINN 성분분해를 먼저 적용하세요.")
        elif observed_years < 2.0:
            conf, conf_why = "low", f"관측 {observed_years:.1f}년 — 2년 미만은 추세 신뢰 곤란"
        elif observed_years < 3.0:
            conf, conf_why = "medium", f"관측 {observed_years:.1f}년 — 3년 이상 권장"
        elif dsrc["projection"] is not None:
            pj = dsrc["projection"]
            conf, conf_why = "medium", (
                f"단일 궤도 — LOS 를 연직으로 투영함(×{pj['scale_1_over_cos']['median']}, "
                f"{pj['incidence_source']}). 변위가 주로 연직이라는 가정이며, "
                "수평 이동이 크면 과대평가된다. asc+desc 융합이면 가정이 사라진다.")
        else:
            conf, conf_why = "high", (f"관측 {observed_years:.1f}년 · asc+desc 실측 연직 · 열성분 제거")

    # 잔존수명 표시 상한 — 설계공용수명에서 공용연수를 뺀 값
    by = _build_year(cfg) if cfg is not None else None
    ref_year = int((as_of or date.today().isoformat())[:4])
    age = max(0, ref_year - by) if by else None
    horizon = max(MIN_HORIZON_YEARS, float(lim_vals["design_life_years"]) - (age or 0))

    arrays = {
        "rsl_point": np.asarray(res["rsl"], dtype=np.float64),
        "rsl_lower": np.asarray(res["rsl_lower"], dtype=np.float64),
        "rate": np.asarray(res["rate"], dtype=np.float64),
        "rate_sigma": np.asarray(res["sigma"], dtype=np.float64),
        "sublimit": np.asarray(res["sublimit"], dtype=np.int16),
    }
    paths = {}
    for key, arr in arrays.items():
        paths[key] = (store.write_array(f"/{LIFE_GROUP}/{key}", arr) if write
                      else f"/{LIFE_GROUP}/{key}")

    assumptions = limits_mod.describe(lim_vals, lim_srcs, span_m=span_m,
                                      span_known=span_known, extra={
        "span_source": span_src,
        "span_known": span_known,
        "limit_basis": limit_basis,
        "coordinate_handling": coord_kind,
        "displacement_source": dsrc["source"],
        "thermal_removed": dsrc["thermal_removed"],
        "thermal_removal": dsrc["thermal_how"],
        # 기하 투영 — 사용성 한계는 연직 규정이므로 LOS 는 반드시 되돌려야 한다.
        "vertical_projection": dsrc["projection"],
        "consumed_mm": float(consumed_mm),
        "consumed_note": ("관측 시작 이전 누적 변위를 0 으로 가정(낙관적). 수준측량 등 실측 "
                          "누적치가 있으면 지정해야 한다." if consumed_mm == 0 else "사용자 지정"),
        "min_cluster": int(min_cluster),
        "build_year": by, "age_years": age,
        "observed_years": round(observed_years, 3),
    })

    # 강성열화 채널(P2) — PINN 시간분해 EI 가 있을 때만. 게이트는 channels.stiffness 참조.
    channels.append(_stiffness_channel(store, observed_years, lim_vals["ei_limit_ratio"], alpha))

    # 미구현 채널도 사유와 함께 남긴다 — 침묵하면 검토된 것으로 오해된다.
    channels += [
        RSLChannel(name="fatigue", kind="model_based", active=False,
                   inactive_reason="P3 미구현 — 피로상세등급·대형차 혼입률 입력 필요(설계코드 추정)"),
        RSLChannel(name="durability", kind="model_based", active=False,
                   inactive_reason="P4 미구현 — 피복두께·노출등급·w/c 점검자료 필요(위성 무관)"),
    ]

    # 교량 대표값 = **활성 채널 중 최소**. 한 채널만 보면 그 채널이 검열됐을 때 다른
    # 한계상태가 이미 임박했어도 "> horizon" 으로 보인다.
    usable = [c for c in channels
              if c.active and not c.censored and c.rsl_lower_years is not None]
    if usable:
        gov = min(usable, key=lambda c: c.rsl_lower_years)
        governing, b_rsl, b_lo = gov.name, gov.rsl_years, gov.rsl_lower_years
    else:
        governing, b_rsl, b_lo = None, None, None

    out = RemainingLifeOutput(
        n_points=int(ins.n_points),
        as_of=as_of or date.today().isoformat(),
        observed_years=round(observed_years, 3),
        horizon_years=round(horizon, 1),
        rsl_point_ds=paths["rsl_point"], rsl_lower_ds=paths["rsl_lower"],
        rate_ds=paths["rate"], rate_sigma_ds=paths["rate_sigma"],
        sublimit_ds=paths["sublimit"],
        channels=channels,
        rsl_years=(None if b_rsl is None else round(float(b_rsl), 2)),
        rsl_lower_years=(None if b_lo is None else round(float(b_lo), 2)),
        governing=governing,
        censored_fraction=round(float(censored_frac), 4),
        confidence=conf, confidence_reason=conf_why,
        assumptions=assumptions,
    )
    if write:
        store.write_meta(LIFE_GROUP, out)
        store.validate(LIFE_GROUP, out)
    else:
        # 미저장 미리보기 — 계산된 배열을 객체에 실어 대시보드가 파일 없이 그린다.
        out.assumptions["_arrays"] = {k: v.tolist() for k, v in arrays.items()}
    return out


def summarize(out: RemainingLifeOutput) -> str:
    """CLI 한 줄 요약 — 하한과 지배 채널을 항상 같이 보여준다."""
    if out.rsl_lower_years is None:
        return (f"잔존수명 > {out.horizon_years:.0f}년 (관측 구간에서 유의한 열화 군집 없음, "
                f"검열 {out.censored_fraction * 100:.0f}%)")
    return (f"잔존수명 ≥ {out.rsl_lower_years:.1f}년 (하한, {out.governing} 지배, "
            f"점추정 {out.rsl_years:.1f}년, 신뢰도 {out.confidence})")
