"""OpenSees 독립 FE 교차검증 — PINN 의 EI 역산·진동수 예측을 검증한다.

**무엇을 검증하나(그리고 무엇이 아닌가).** 이 모듈은 PINN 이 푸는 구조 역문제(처짐형상
→ 절대 EI → 고유진동수)를 **독립 유한요소 엔진(OpenSees)** 의 정해와 대조한다. 이는
`fem_crosscheck.py`(닫힌해)가 못 다루는 영역 — **전단변형(Timoshenko)·다경간 연속·
비균일** — 까지 검증을 넓힌다. 결과는 **tier-② 물리·모델 일치**이지 tier-③ 실교량
검증이 아니다(실교량은 수준측량 등 지상실측의 몫). OpenSees 도 모델이므로 "PINN 이
물리를 옳게 푼다"를 보일 뿐 "실교량과 맞는다"는 아니다.

**순환논리 회피.** PINN 의 모달 해석은 내부적으로 Euler–Bernoulli(E-B) 요소를 쓴다.
OpenSees 를 같은 E-B·같은 EI 로 짜면 자명히 일치해 무의미하다. 그래서 forward 는
기본이 **Timoshenko(전단 포함)** 이고, shear=False(E-B) 는 두 FE 구현이 일치하는지
확인하는 **대조군**으로만 쓴다.

정/역 왕복(forward/inverse):
  1. forward — OpenSees 로 알려진 단면(→알려진 EI) 보에 등분포하중 → 처짐형상 w(x) +
     고유치해석 → 진동수. Timoshenko 는 전단을 포함한 "진짜" 응답.
  2. inverse — 그 처짐형상을 PINN 의 실제 역산(`_identify_EI_from_pde`)에 넣어 EI 를
     되찾고, `_fem_beam_frequencies`(E-B)로 진동수를 예측한다.
  3. 비교 — EI 회수오차, 진동수 예측오차. 세장비 L/h 를 스윕하면 E-B 가정의 모델오차
     곡선이 나온다(슬렌더=작음, 깊은 보=전단으로 커짐).

openseespy 는 선택 의존(`.[fem]`)이라 lazy import; 없으면 친절히 안내한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


def _require_openseespy():
    try:
        import openseespy.opensees as ops
    except Exception as exc:  # noqa: BLE001 — 미설치/Windows DLL 실패 모두 포함
        raise RuntimeError(
            "OpenSees 교차검증에는 openseespy 가 필요합니다(`pip install inframon[fem]` 또는 "
            "`pip install openseespy`). Windows 에서 DLL 로드가 실패하면 Visual C++ 재배포 "
            "런타임을 설치하거나 리눅스/WSL 에서 실행하세요."
        ) from exc
    return ops


def rect_section(b: float, h: float) -> tuple[float, float]:
    """직사각 단면 (폭 b, 높이 h) → (A, Iz). 세장비 L/h 스윕용."""
    return b * h, b * h ** 3 / 12.0


@dataclass
class BeamResponse:
    x_m: np.ndarray                 # [n_node] 절점 좌표
    w_m: np.ndarray                 # [n_node] 연직 처짐(하방 음수)
    freqs_hz: list                  # 첫 n_modes 고유진동수
    EI_true: float                  # E·Iz (참값)
    m_per_len: float                # 단위길이질량 ρ·A
    shear: bool
    boundary: str


def opensees_beam(*, L: float, E: float, Iz: float, A: float, rho: float,
                  boundary: str = "simply_supported", n_elem: int = 24,
                  shear: bool = True, nu: float = 0.2, kappa: float = 5.0 / 6.0,
                  q_N_m: float = 1.0e4, n_spans: int = 1,
                  n_modes: int = 3) -> BeamResponse:
    """OpenSees 2D 보: 등분포하중 정적 처짐 + 고유치해석.

    shear=True → ElasticTimoshenkoBeam(전단 포함), False → elasticBeamColumn(E-B).
    n_spans>1 → 등간격 중간 지점(연직구속)으로 연속보. 하중 q 는 왕복에서 상쇄되므로
    절대값은 무의미(형상만 중요).
    """
    ops = _require_openseespy()
    ops.wipe()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    nn = n_elem + 1
    dx = L / n_elem
    for i in range(nn):
        ops.node(i + 1, i * dx, 0.0)

    # 경계: 양단 + (연속보면) 중간 지점
    if boundary == "fixed":
        ops.fix(1, 1, 1, 1)
        ops.fix(nn, 1, 1, 1)
    elif boundary == "cantilever":
        ops.fix(1, 1, 1, 1)
    else:  # simply_supported
        ops.fix(1, 1, 1, 0)
        ops.fix(nn, 0, 1, 0)
    if n_spans > 1:
        for s in range(1, n_spans):
            # 경간 경계에 가장 가까운 절점을 연직 구속
            ni = int(round(s * n_elem / n_spans)) + 1
            if 1 < ni < nn:
                ops.fix(ni, 0, 1, 0)

    ops.geomTransf("Linear", 1)
    G = E / (2.0 * (1.0 + nu))
    Avy = kappa * A
    for e in range(n_elem):
        if shear:
            ops.element("ElasticTimoshenkoBeam", e + 1, e + 1, e + 2, E, G, A, Iz, Avy, 1)
        else:
            ops.element("elasticBeamColumn", e + 1, e + 1, e + 2, A, E, Iz, 1)

    # 집중질량(병진) — 고유치해석용
    m_node = rho * A * dx
    for i in range(nn):
        mi = m_node * (0.5 if i in (0, nn - 1) else 1.0)
        ops.mass(i + 1, mi, mi, 0.0)

    # 정적: 등분포하중(하방)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    for e in range(n_elem):
        ops.eleLoad("-ele", e + 1, "-type", "-beamUniform", -q_N_m)
    ops.system("BandGeneral")
    ops.numberer("RCM")
    ops.constraints("Plain")
    ops.integrator("LoadControl", 1.0)
    ops.algorithm("Linear")
    ops.analysis("Static")
    ops.analyze(1)
    x = np.array([i * dx for i in range(nn)], float)
    w = np.array([ops.nodeDisp(i + 1, 2) for i in range(nn)], float)

    # 모달(정적 결과 읽은 뒤)
    ops.wipeAnalysis()
    ops.setTime(0.0)
    try:
        lam = ops.eigen(n_modes)
    except Exception:  # noqa: BLE001 — 기본 솔버 실패 시 일반화 솔버 폴백
        lam = ops.eigen("-fullGenLapack", n_modes)
    freqs = [math.sqrt(abs(v)) / (2.0 * math.pi) for v in lam]
    ops.wipe()
    return BeamResponse(x_m=x, w_m=w, freqs_hz=freqs, EI_true=E * Iz,
                        m_per_len=rho * A, shear=shear, boundary=boundary)


def recover_EI_from_shape(x_m: np.ndarray, w_m: np.ndarray, L: float,
                          q_N_m: float) -> tuple[float, float, float]:
    """처짐형상 → PINN 의 실제 역산으로 절대 EI 회수.

    PINN 과 동일하게 비차원 4차도함수 크기 d4_hat=⟨|∂⁴ŵ/∂x̂⁴|⟩ 를 구해
    `_identify_EI_from_pde` 에 넣는다. 반환 (EI_rec, d4_hat, w_scale_m).
    """
    from .pinn.real_engine import _identify_EI_from_pde

    w = np.abs(w_m)
    w_scale = float(w.max())
    if w_scale <= 0:
        return float("nan"), float("nan"), 0.0
    what = w_m / w_scale                       # 정규화 처짐(부호 유지)
    n = what.size
    h = 1.0 / (n - 1)                          # x̂ 간격(균등절점 가정)
    # 5점 중심차분 4차도함수(내부 절점)
    d4 = (what[:-4] - 4 * what[1:-3] + 6 * what[2:-2]
          - 4 * what[3:-1] + what[4:]) / h ** 4
    d4_hat = float(np.mean(np.abs(d4)))
    EI_rec = _identify_EI_from_pde(d4_hat, L, q=q_N_m, w_scale_m=w_scale)
    return EI_rec, d4_hat, w_scale


@dataclass
class CrosscheckResult:
    L: float
    slenderness: float              # L/h
    boundary: str
    EI_true: float
    EI_recovered: float
    ei_err_pct: float               # |EI_rec-EI_true|/EI_true·100
    f1_opensees: float              # 참(Timoshenko) 1차 진동수
    f1_pinn: float                  # PINN(E-B, 회수EI) 예측 1차 진동수
    f1_err_pct: float
    shear: bool
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"L": self.L, "slenderness": round(self.slenderness, 2),
                "boundary": self.boundary, "EI_true": self.EI_true,
                "EI_recovered": self.EI_recovered,
                "ei_err_pct": round(self.ei_err_pct, 3),
                "f1_opensees": round(self.f1_opensees, 4),
                "f1_pinn": round(self.f1_pinn, 4),
                "f1_err_pct": round(self.f1_err_pct, 3),
                "shear": self.shear, **self.meta}


def crosscheck(*, L: float, b: float, h: float, E: float = 3.0e10,
               rho: float = 2400.0, boundary: str = "simply_supported",
               n_elem: int = 24, shear: bool = True, n_spans: int = 1,
               noise_mm: float = 0.0, q_N_m: float = 1.0e4,
               seed: int = 0) -> CrosscheckResult:
    """정/역 왕복 1케이스. OpenSees forward → PINN inverse → 오차.

    noise_mm>0 이면 처짐형상에 가우시안 잡음을 더해(실 InSAR 모사) EI 회수 강건성을 본다.
    """
    A, Iz = rect_section(b, h)
    resp = opensees_beam(L=L, E=E, Iz=Iz, A=A, rho=rho, boundary=boundary,
                         n_elem=n_elem, shear=shear, n_spans=n_spans, q_N_m=q_N_m)
    w = resp.w_m.copy()
    if noise_mm > 0:
        rng = np.random.default_rng(seed)
        w = w + rng.normal(0.0, noise_mm * 1e-3, w.shape)   # mm → m
    EI_rec, _d4, _ws = recover_EI_from_shape(resp.x_m, w, L, q_N_m)

    from .pinn.real_engine import _fem_beam_frequencies
    span = L / n_spans
    f_pinn = _fem_beam_frequencies(EI_rec, resp.m_per_len, span, boundary)[0]
    f_os = resp.freqs_hz[0]
    return CrosscheckResult(
        L=L, slenderness=L / h, boundary=boundary, EI_true=resp.EI_true,
        EI_recovered=EI_rec,
        ei_err_pct=abs(EI_rec - resp.EI_true) / resp.EI_true * 100.0,
        f1_opensees=f_os, f1_pinn=f_pinn,
        f1_err_pct=abs(f_pinn - f_os) / f_os * 100.0 if f_os > 0 else float("nan"),
        shear=shear, meta={"noise_mm": noise_mm, "n_spans": n_spans,
                           "section_bxh": f"{b}x{h}"})


def crosscheck_via_pinn(*, L: float, b: float, h: float, E: float = 3.0e10,
                        rho: float = 2400.0, boundary: str = "simply_supported",
                        n_elem: int = 40, n_points: int = 60, n_dates: int = 12,
                        noise_mm: float = 0.0, epochs: int = 400,
                        seed: int = 0, tmp_h5=None) -> CrosscheckResult:
    """Tier 2 — **실제 PINN 전체 파이프라인**을 OpenSees 형상에 돌려 검증(torch 필요).

    Tier 1(`crosscheck`)은 식별 공식만 원시 유한차분으로 테스트한다. 여기서는 OpenSees
    처짐을 합성 프로젝트의 연직 채널에 주입하고 진짜 `run_pinn_real`(신경망 피팅 →
    autograd 4차도함수 → EI 식별)을 돌려, **NN 스무딩을 포함한 전체 파이프라인**을 검증한다.
    잡음(noise_mm>0)을 줘도 NN 이 매끈하게 회귀하므로 원시 FD 보다 훨씬 강건하다.

    핵심: EI 식별은 가정 하중 q 에 선형 비례하므로, OpenSees forward 하중을 PINN 이
    가정하는 q(=prof.load_per_len)와 **동일**하게 맞춰야 EI_true 가 회수 목표가 된다.
    """
    import numpy as np

    from .config import PipelineConfig
    from .contracts.io import ProjectStore
    from .contracts.schema import InSAROutput
    from .pinn.real_engine import run_pinn_real

    A, Iz = rect_section(b, h)
    EI_true = E * Iz
    m_per_len = rho * A
    q = m_per_len * 9.81                       # 자중 [N/m] — PINN 가정 하중과 일치시킴
    resp = opensees_beam(L=L, E=E, Iz=Iz, A=A, rho=rho, boundary=boundary,
                         n_elem=n_elem, shear=True, q_N_m=q)

    # OpenSees 절점 처짐 → N 측정점으로 보간(mm), M 시점에 정적 반복 + 잡음
    x_pts = np.linspace(0.0, L, n_points)
    w_pts_m = np.interp(x_pts, resp.x_m, resp.w_m)
    w_mm = w_pts_m * 1000.0
    rng = np.random.default_rng(seed)
    vert = np.tile(w_mm[:, None], (1, n_dates))
    if noise_mm > 0:
        vert = vert + rng.normal(0.0, noise_mm, vert.shape)
    dates = np.arange(n_dates, dtype=float) * 30.0
    xyz = np.stack([x_pts, np.zeros(n_points), np.zeros(n_points)], axis=1)  # x=축, m 단위
    l_from_fixed = x_pts.copy()                # 고정단 x=0
    longitudinal = np.zeros((n_points, n_dates))   # 열팽창 없음
    los = vert.copy()

    import tempfile
    proj = tmp_h5 or (tempfile.mkdtemp() + "/os_pinn.h5")
    with ProjectStore(proj, mode="w") as store:
        g = "/insar"
        store.write_array(f"{g}/point_id", np.arange(n_points))
        store.write_array(f"{g}/xyz", xyz)
        store.write_array(f"{g}/member", np.zeros(n_points, np.int8))
        store.write_array(f"{g}/coherence", np.full(n_points, 0.9))
        store.write_array(f"{g}/l_from_fixed", l_from_fixed)
        store.write_array(f"{g}/los", los)
        store.write_array(f"{g}/longitudinal", longitudinal)
        store.write_array(f"{g}/vertical", vert)
        store.write_array(f"{g}/dates", dates)
        store.write_array(f"{g}/temporal_coherence", np.full(n_points, 0.9))
        insar = InSAROutput(
            n_points=n_points, n_dates=n_dates,
            point_id_ds=f"{g}/point_id", xyz_ds=f"{g}/xyz", member_ds=f"{g}/member",
            coherence_ds=f"{g}/coherence", l_from_fixed_ds=f"{g}/l_from_fixed",
            los_ds=f"{g}/los", longitudinal_ds=f"{g}/longitudinal",
            dates_ds=f"{g}/dates", temporal_coherence_ds=f"{g}/temporal_coherence",
            vertical_ds=f"{g}/vertical")
        store.write_meta("insar", insar)

        cfg = PipelineConfig(n_points=n_points, n_dates=n_dates)
        cfg.pinn_epochs = epochs
        cfg.seed = seed
        cfg.bridge_profile = {
            "bridge_type": "girder", "length_m": L, "youngs_modulus": E,
            "section_depth_m": h, "width_m": b, "load_per_len": q,
            "mass_per_len": m_per_len, "boundary": boundary}
        out = run_pinn_real(store, insar, cfg)
        EI_arr = np.asarray(store.read_array(out.EI_ds), float)
        EI_rec = float(np.median(EI_arr))
        f_pinn = float(np.asarray(store.read_array(out.natural_freq_ds), float)[0])

    f_os = resp.freqs_hz[0]
    return CrosscheckResult(
        L=L, slenderness=L / h, boundary=boundary, EI_true=EI_true,
        EI_recovered=EI_rec,
        ei_err_pct=abs(EI_rec - EI_true) / EI_true * 100.0,
        f1_opensees=f_os, f1_pinn=f_pinn,
        f1_err_pct=abs(f_pinn - f_os) / f_os * 100.0 if f_os > 0 else float("nan"),
        shear=True, meta={"noise_mm": noise_mm, "n_spans": 1,
                          "section_bxh": f"{b}x{h}", "via": "full_pinn",
                          "epochs": epochs})
