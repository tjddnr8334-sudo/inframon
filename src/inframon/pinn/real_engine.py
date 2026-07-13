"""모듈 2: PINN — 구조 건전성 모니터링 실구현 (Phase 4, PyTorch + Euler-Bernoulli + FEM).

교량 제원(`structure.BridgeProfile`)과 외생 데이터(온도·교통량)로 **교량별 맞춤형**으로
돈다. 제원 미지정·외생 미제공이면 강재 거더교 가정으로 폴백(기존 동작 유지).

InSAR 종방향 변위장 u(x,t)를 물리 성분으로 분해하고 보 거동을 추정한다.
  분해: u = thermal + load + settle + anomaly
    · thermal(x,t) = α·L_fixed(x)·ΔT(t)  (온도데이터) 또는 L_fixed·(a·sin+b·cos)(가정)
    · settle(x,t)  = s(x) · t                          (선형 침하)
    · load(x,t)    = traffic(t)·w_θ(x,t)  (교통량 변조) 또는 w_θ (보 PDE 지배)
    · anomaly(x,t) = a_θ(x,t)  (잔차 MLP, 정규화로 작게)
  외생(선택, [M] 정렬): `cfg.pinn_temperature`(°C)·`cfg.pinn_traffic`. 제원: `cfg.bridge_profile`.

  보 PDE (Euler-Bernoulli, 분포하중 가정):  EI·∂⁴w/∂x⁴ = q(x,t)
    자중 등 균일하중 가정 → ∂⁴w/∂x⁴ 이 x 에 대해 (시점별) 균일해야 함을 PDE 손실로 강제.
    autograd 4차 미분으로 잔차를 계산(진짜 PINN).

  구조응답: 처짐 = load, 곡률 κ=∂²w/∂x²(autograd), 변형률 = -y·κ, 응력 = E·strain.
  역산: **절대 EI 식별** — 비차원화 PDE 균형 `EI = q·L⁴/(w_scale·⟨|∂⁴ŵ/∂x̂⁴|⟩)`(가정
    자중 q=Q0, `_identify_EI_from_pde`)로 스케일 모호성을 해소한 뒤, 점별로 곡률이 큰
    곳(=손상 의심)을 저강성으로 변조. α(열팽창계수)도 thermal 진폭에서 역산.
  고유진동수: 식별 EI 로 **FEM(Euler-Bernoulli Hermite 보요소) 모달 해석**.

  가상센싱(virtual sensing): 학습된 연속장을 거더 종축의 촘촘한 가상센서 격자(V개)로
    재평가 → InSAR 관측점이 없는 위치까지 포함한 **상부거더 전체 변위장**을 도출한다.
    성분(처짐·열팽창·침하·이상)을 가상센서에서 복원하고, 종축(열팽창+이상)·연직(처짐+
    침하) 벡터합 크기를 전체 변위량[mm]으로 낸다. 가상센서 수: `cfg.pinn_virtual_sensors`
    (기본 200). 계약 필드 vsens_*_ds / n_virtual 로 저장(1.2, Optional).
    2D 상판(deck): 점 구름 PCA 로 상판 방향축·횡축을 잡아 격자(G=n_long×n_trans)를
    세우고, 각 격자점에 PINN 1D 전체변위장을 그 점 고정단거리 l 로 평가(물리 종축)한 뒤
    관측점 PINN 잔차를 IDW 로 2D 보간해 합침 → **상판 전체 면 변위 지도**. 격자
    `cfg.pinn_deck_long`(60)·`cfg.pinn_deck_trans`(9). 계약 deck_*_ds / n_deck(점<3 이면 None).

계약(PINNOutput)·V_func_series[4,M] 행순서(thermal,load,bearing,foundation)는 stub 과 동일.
torch 는 함수 내부에서 지연 import(코어 경량 유지). insar=real 핫스왑으로만 동작.
"""

from __future__ import annotations

import numpy as np

from ..config import PipelineConfig
from ..contracts.io import ProjectStore
from ..contracts.schema import FRAM_FUNCTIONS, InSAROutput, PINNOutput

E_MODULUS = 2.1e11        # 영률 [Pa] (강재 가정)
HALF_DEPTH = 0.5          # 단면 반높이 [m] (변형률 환산용 가정)
Q0_NOMINAL = 1.0e4        # 가정 분포하중 [N/m] (EI 스케일 해소용)
RHO_A = 1.0e4             # 단위길이 질량 [kg/m] (FEM 모달용 가정)


def _fem_beam_frequencies(EI: float, m_per_len: float, L: float,
                          n_elem: int = 12, n_modes: int = 3) -> np.ndarray:
    """Euler-Bernoulli 보 FEM 모달 해석 → 첫 n_modes 고유진동수 [Hz] (단순지지)."""
    L = float(np.clip(L, 5.0, 5000.0))
    le = L / n_elem
    ndof = 2 * (n_elem + 1)
    K = np.zeros((ndof, ndof))
    Mm = np.zeros((ndof, ndof))
    ke = EI / le ** 3 * np.array([
        [12, 6 * le, -12, 6 * le],
        [6 * le, 4 * le ** 2, -6 * le, 2 * le ** 2],
        [-12, -6 * le, 12, -6 * le],
        [6 * le, 2 * le ** 2, -6 * le, 4 * le ** 2]])
    me = m_per_len * le / 420 * np.array([
        [156, 22 * le, 54, -13 * le],
        [22 * le, 4 * le ** 2, 13 * le, -3 * le ** 2],
        [54, 13 * le, 156, -22 * le],
        [-13 * le, -3 * le ** 2, -22 * le, 4 * le ** 2]])
    for e in range(n_elem):
        d = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        K[np.ix_(d, d)] += ke
        Mm[np.ix_(d, d)] += me
    fixed = [0, 2 * n_elem]                       # 단순지지: 양단 처짐 w=0
    free = [i for i in range(ndof) if i not in fixed]
    Kf, Mf = K[np.ix_(free, free)], Mm[np.ix_(free, free)]
    w2 = np.linalg.eigvals(np.linalg.solve(Mf, Kf)).real
    w2 = np.sort(w2[w2 > 1e-6])
    freqs = np.sqrt(w2) / (2 * np.pi)
    return freqs[:n_modes].astype(float)


def _span_meters(xyz: np.ndarray) -> float:
    """xyz 에서 교량 길이[m] 추정 (lon/lat 로 보이면 degree→m)."""
    xy = xyz[:, :2]
    ext = float(max(np.ptp(xy[:, 0]), np.ptp(xy[:, 1])))
    return ext * 111000.0 if ext < 1.0 else ext


LIVE_LOAD_PER_LANE_N_M = 12.7e3   # 대표 도로 설계활하중 [N/m/차로] (KL-510 등 수준)
LANE_WIDTH_M = 3.5                # 차로 폭 [m]


def _effective_load_for_ei(prof, use_traffic: bool, traffic) -> tuple[float, str]:
    """EI 식별용 유효 분포하중 q — **교통데이터 있으면 교통 활하중 기반**, 없으면 자중 균일.

    InSAR 가 보는 load-deflection 변동은 (사하중은 상수라 상쇄되고) **활하중(교통) 구동**
    이다. 따라서 교통 시계열이 있으면 EI 를 자중 q0 가 아니라 활하중(차로수×설계활하중×
    교통 피크비)으로 식별해야 물리적으로 맞다. 차로수는 폭/3.5m 로 추정.
    """
    if use_traffic and traffic is not None:
        tr = np.asarray(traffic, dtype=float).ravel()
        n_lanes = max(1, round((float(prof.width_m) if prof.width_m else 2 * LANE_WIDTH_M)
                               / LANE_WIDTH_M))
        peak = float(tr.max() / (tr.mean() + 1e-9))          # 평균 대비 교통 피크비
        q = LIVE_LOAD_PER_LANE_N_M * n_lanes * peak
        return q, (f"교통 활하중({n_lanes}차로×{LIVE_LOAD_PER_LANE_N_M/1e3:.1f}kN/m"
                   f"×피크{peak:.2f}={q/1e3:.0f}kN/m)")
    return float(prof.load_per_len), f"자중 균일하중({prof.load_per_len/1e3:.0f}kN/m)"


def _identify_EI_from_pde(
    d4_hat: float, L_m: float, q: float = Q0_NOMINAL, w_scale_m: float = 1.0
) -> float:
    """비차원화 Euler-Bernoulli PDE 균형으로 **절대 EI[N·m²]** 식별.

    정규화 좌표 x̂∈[0,1](물리 x=L·x̂), 정규화 처짐 ŵ(물리 w=w_scale_m·ŵ[m])에서
      ∂⁴w/∂x⁴ = (w_scale_m / L⁴)·∂⁴ŵ/∂x̂⁴
    균일하중 PDE `EI·∂⁴w/∂x⁴ = q` 에 특성 4차도함수 크기 d4_hat=⟨|∂⁴ŵ/∂x̂⁴|⟩ 대입:
      EI = q·L⁴ / (w_scale_m·d4_hat)
    하중 q(가정 자중)와 측정 처짐형상으로부터 EI 를 절대적으로 얻는다(스케일 모호성 해소).
    물리 범위로 클립. (d4_hat→0: 거의 강체 → EI 매우 큼 → 상한.)
    """
    EI = q * L_m**4 / (w_scale_m * abs(d4_hat) + 1e-30)
    return float(np.clip(EI, 1e6, 1e14))


def run_pinn_real(store: ProjectStore, insar: InSAROutput, cfg: PipelineConfig) -> PINNOutput:
    import torch

    los = store.read_array(insar.longitudinal_ds).astype(np.float64)   # [N,M] 종축 수평(mm)
    dates = store.read_array(insar.dates_ds).astype(np.float64)        # [M]
    l_fixed = store.read_array(insar.l_from_fixed_ds).astype(np.float64)  # [N]
    xyz = store.read_array(insar.xyz_ds).astype(np.float64)
    N, M = los.shape

    # 연직 성분(asc+desc 융합 시) — 있으면 처짐/침하(물리적으로 연직)를 이 채널에 피팅.
    vert = None
    if getattr(insar, "vertical_ds", None):
        v = store.read_array(insar.vertical_ds).astype(np.float64)     # [N,M] 연직(mm)
        if v.shape == los.shape:
            vert = v
    use_vertical = vert is not None

    # 교량 구조 프로파일(제원) — 하드코딩 가정 대신 교량별 E/단면/질량/자중/스팬
    from ..structure import resolve_profile
    prof = resolve_profile(cfg, xyz)
    L_m = float(prof.length_m or _span_meters(xyz))                   # 교량 스팬 [m]

    # 외생 입력(선택, [M] 정렬): 온도 ΔT → 열팽창 구동, 교통량 → 하중 변조
    temp = getattr(cfg, "pinn_temperature", None)
    traffic = getattr(cfg, "pinn_traffic", None)
    use_temp = temp is not None and np.asarray(temp).ravel().shape[0] == M
    use_traffic = traffic is not None and np.asarray(traffic).ravel().shape[0] == M

    epochs = int(getattr(cfg, "pinn_epochs", 600))
    torch.manual_seed(getattr(cfg, "seed", 42))

    # 정규화
    t_year = (dates - dates[0]) / 365.0
    xn = (l_fixed - l_fixed.min()) / (np.ptp(l_fixed) + 1e-9)          # [N] in [0,1]
    Lf = l_fixed - l_fixed.min()                                       # 고정단 거리 [N]
    Lf_n = Lf / (Lf.max() + 1e-9)
    sin_s = np.sin(2 * np.pi * t_year)
    cos_s = np.cos(2 * np.pi * t_year)
    los_scale = np.abs(los).max() + 1e-9
    y = torch.tensor(los / los_scale, dtype=torch.float32)            # [N,M] 정규화 관측(종축)
    # 연직 관측(있으면) — 처짐/침하 채널. 자체 스케일로 정규화(종축과 진폭이 다를 수 있음).
    vert_scale = (np.abs(vert).max() + 1e-9) if use_vertical else los_scale
    y_v = torch.tensor(vert / vert_scale, dtype=torch.float32) if use_vertical else None
    w_scale_used = vert_scale if use_vertical else los_scale          # 처짐/침하 환산 스케일

    tx = torch.tensor(xn, dtype=torch.float32)
    tt = torch.tensor((dates - dates[0]) / (np.ptp(dates) + 1e-9), dtype=torch.float32)
    grid_x = tx[:, None].expand(N, M)                                  # [N,M]
    grid_t = tt[None, :].expand(N, M)
    sin_t = torch.tensor(sin_s, dtype=torch.float32)[None, :].expand(N, M)
    cos_t = torch.tensor(cos_s, dtype=torch.float32)[None, :].expand(N, M)
    Lf_t = torch.tensor(Lf_n, dtype=torch.float32)[:, None].expand(N, M)
    ty = torch.tensor(t_year, dtype=torch.float32)[None, :].expand(N, M)

    # 외생 텐서: 온도 ΔT(중앙화·정규화), 교통량(평균=1 곱셈 변조)
    dT_phys_max = 1.0
    dT_t = traffic_t = None
    if use_temp:
        dT = np.asarray(temp, dtype=np.float64).ravel() - float(np.mean(temp))
        dT_phys_max = float(np.abs(dT).max()) + 1e-9
        dT_t = torch.tensor(dT / dT_phys_max, dtype=torch.float32)[None, :].expand(N, M)
    if use_traffic:
        tr = np.asarray(traffic, dtype=np.float64).ravel()
        traffic_t = torch.tensor(tr / (tr.mean() + 1e-9), dtype=torch.float32)[None, :].expand(N, M)

    def mlp():
        return torch.nn.Sequential(
            torch.nn.Linear(2, 32), torch.nn.Tanh(),
            torch.nn.Linear(32, 32), torch.nn.Tanh(),
            torch.nn.Linear(32, 1))

    w_net = mlp()       # load 처짐 w(x,t)
    a_net = mlp()       # anomaly
    a_th = torch.nn.Parameter(torch.zeros(1))   # 열팽창 sin 계수(온도 미사용 시)
    b_th = torch.nn.Parameter(torch.zeros(1))   # 열팽창 cos 계수(온도 미사용 시)
    alpha_th = torch.nn.Parameter(torch.zeros(1))  # 온도 사용 시: thermal = αₜ·L·ΔT
    s_rate = torch.nn.Parameter(torch.zeros(N))  # 점별 침하율
    # EI 는 학습 파라미터가 아니라 비차원 PDE 균형으로 사후 식별(_identify_EI_from_pde).

    def thermal_field():
        """온도 데이터가 있으면 α·L·ΔT(물리), 없으면 계절 sin/cos(가정)."""
        if use_temp:
            return alpha_th * Lf_t * dT_t
        return Lf_t * (a_th * sin_t + b_th * cos_t)

    def load_field(w_raw):
        """교통량이 있으면 traffic(t)·w(영향선 변조), 없으면 자유 처짐 w."""
        return traffic_t * w_raw if use_traffic else w_raw

    # 교량 형식별 PDE 파라미터(사장교=탄성지지 p0, 아치·현수=축력 p2). 거더는 None.
    from .pde import make_pde_params, pde_loss
    p2_pde, p0_pde = make_pde_params(prof.bridge_type, torch)

    params = (list(w_net.parameters()) + list(a_net.parameters())
              + [a_th, b_th, alpha_th, s_rate]
              + [p for p in (p2_pde, p0_pde) if p is not None])
    opt = torch.optim.Adam(params, lr=5e-3)

    def feat(xx, tt_):
        return torch.stack([xx.reshape(-1), tt_.reshape(-1)], dim=1)

    # 콜로케이션 (PDE): x 격자 × 시점 부분집합
    n_col = 40
    xc0 = torch.linspace(0, 1, n_col)
    t_sub = tt[:: max(1, M // 12)]

    for ep in range(epochs):
        opt.zero_grad()
        w_raw = w_net(feat(grid_x, grid_t)).reshape(N, M)
        w = load_field(w_raw)                            # 교통량 변조(있으면)
        anom = a_net(feat(grid_x, grid_t)).reshape(N, M)
        thermal = thermal_field()                        # 온도 구동(있으면)
        settle = s_rate[:, None] * ty
        if use_vertical:
            # 종축(수평) 채널: 열팽창 + 수평 이상 ≈ longitudinal(y)
            # 연직 채널: 처짐(w) + 침하(settle) ≈ vertical(y_v) — 물리적으로 올바른 분리
            loss_data = torch.mean((thermal + anom - y) ** 2) + torch.mean((w + settle - y_v) ** 2)
        else:
            total = thermal + settle + w + anom
            loss_data = torch.mean((total - y) ** 2)

        # 형식별 지배 PDE 잔차(거더=w'''', 사장교=+탄성지지, 아치·현수=+축력) x-분산 패널티
        loss_pde = pde_loss(w_net, xc0, t_sub, n_col, p2_pde, p0_pde, prof.bridge_type, torch)

        loss_reg = 1e-2 * torch.mean(anom ** 2) + 1e-4 * torch.mean(w ** 2)
        loss = loss_data + 1e-3 * loss_pde + loss_reg
        loss.backward()
        opt.step()

    # ── 성분/응답 추출 (곡률은 autograd 2차) ──
    gx = grid_x.clone().requires_grad_(True)
    w_eval = w_net(torch.stack([gx.reshape(-1), grid_t.reshape(-1)], dim=1))
    wx = torch.autograd.grad(w_eval, gx, torch.ones_like(w_eval), create_graph=True)[0]
    wxx = torch.autograd.grad(wx.reshape(-1, 1), gx, torch.ones_like(wx.reshape(-1, 1)),
                              create_graph=True)[0]

    # 절대 EI 식별용 비차원 4차도함수 크기 d4_hat=⟨|∂⁴ŵ/∂x̂⁴|⟩ (콜로케이션×시점)
    d4_vals = []
    for tc in t_sub:
        xc = xc0.clone().requires_grad_(True)
        gg = w_net(torch.stack([xc, tc.expand(n_col)], dim=1))
        for _ in range(4):
            gg = torch.autograd.grad(gg, xc, torch.ones_like(gg), create_graph=True)[0]
        d4_vals.append(float(gg.detach().abs().mean().item()))
    d4_hat = float(np.mean(d4_vals))

    with torch.no_grad():
        w_raw = w_net(feat(grid_x, grid_t)).reshape(N, M)
        # 처짐·침하·곡률은 연직 채널 스케일(w_scale_used), 열팽창·이상은 종축 스케일(los_scale)
        w_load = load_field(w_raw).numpy() * w_scale_used
        anom = a_net(feat(grid_x, grid_t)).reshape(N, M).numpy() * los_scale
        thermal = thermal_field().numpy() * los_scale
        settle = (s_rate[:, None] * ty).numpy() * w_scale_used
        curvature = wxx.reshape(N, M).detach().numpy() * w_scale_used   # ∂²w/∂x²
        # 절대 EI: 비차원 PDE 균형 EI·∂⁴w/∂x⁴=q (프로파일 자중), 처짐 스케일[mm]→m.
        # 연직 관측이 있으면 실측 처짐으로부터 EI 식별(더 정확).
        q_eff, load_basis = _effective_load_for_ei(prof, use_traffic, traffic)
        EI_global = _identify_EI_from_pde(d4_hat, L_m, q_eff, w_scale_used * 1e-3)

    comp_thermal, comp_load = thermal, w_load
    comp_settle, comp_anomaly = settle, anom

    # 구조응답 (프로파일 단면·재료)
    deflection = comp_load
    strain = -prof.half_depth() * curvature
    stress = prof.youngs() * strain

    # 역산: 점별 EI — 곡률 큰 곳(=휨 집중/손상 의심) 저강성
    kappa = np.abs(curvature).mean(axis=1)                            # [N]
    EI = EI_global * (np.median(kappa) + 1e-9) / (kappa + 1e-9)
    EI = np.clip(EI, EI_global * 0.1, EI_global * 10)
    # 열팽창계수 α: 온도 데이터면 실측 ΔT 로 식별, 아니면 가정 ΔT(20℃)
    if use_temp:
        amp = abs(alpha_th.item()) * los_scale * 1e-3                 # 물리 thermal 진폭 [m]
        alpha = np.full(N, max(amp / ((Lf.max() + 1e-9) * dT_phys_max), 1e-7))
    else:
        amp = float(np.hypot(a_th.item(), b_th.item())) * los_scale * 1e-3
        alpha = np.full(N, max(amp / (20.0 * (Lf.max() + 1e-9)), 1e-7))

    # 고유진동수: FEM 모달 (식별 EI_global, 프로파일 ρA, 스팬)
    natural_freq = _fem_beam_frequencies(EI_global, prof.rho_a(), L_m)

    # ───────── 가상센싱(virtual sensing): 상부거더 전체 변위장 ─────────
    # InSAR 관측점(N개·희소·불규칙)에서 학습한 PINN 연속장 w(x,t)/anomaly(x,t) 와
    # 물리 성분(열팽창·침하)을 거더 종축을 따라 촘촘한 가상센서 격자(V개)로 재평가한다.
    # → 관측점이 없는 위치까지 포함한 **상부거더 전체 변위량**을 얻는다(가상 센싱).
    # 정규화 좌표 xv∈[0,1] 은 고정단거리 정규화 Lf_n(=xn)과 동일축 → thermal 의 L 항에 직접 사용.
    n_vsens = int(np.clip(int(getattr(cfg, "pinn_virtual_sensors", 200)), 8, 2000))
    xv = np.linspace(0.0, 1.0, n_vsens)                               # [V] 거더축 [0,1]
    xv_t = torch.tensor(xv, dtype=torch.float32)
    gxv = xv_t[:, None].expand(n_vsens, M)
    gtv = tt[None, :].expand(n_vsens, M)
    # 점별 침하율 s(x)[N] → 가상 격자로 보간(관측 x 정렬 기준). 그 외 장은 신경망이 임의 x 평가.
    order = np.argsort(xn)
    s_vsens = np.interp(xv, xn[order], s_rate.detach().numpy()[order])
    with torch.no_grad():
        wv = w_net(feat(gxv, gtv)).reshape(n_vsens, M)
        if use_traffic:
            tr = np.asarray(traffic, dtype=np.float64).ravel()
            tr_v = torch.tensor(tr / (tr.mean() + 1e-9), dtype=torch.float32)
            wv = tr_v[None, :].expand(n_vsens, M) * wv                # 교통량 변조(있으면)
        wv = wv.numpy()
        av = a_net(feat(gxv, gtv)).reshape(n_vsens, M).numpy()
        if use_temp:                                                 # α·L·ΔT (물리)
            dTv = (np.asarray(temp, dtype=np.float64).ravel() - float(np.mean(temp))) / dT_phys_max
            thv = float(alpha_th.item()) * xv[:, None] * dTv[None, :]
        else:                                                        # L·(a·sin+b·cos) (가정)
            thv = xv[:, None] * (float(a_th.item()) * sin_s[None, :] + float(b_th.item()) * cos_s[None, :])
    # 물리 스케일 환산[mm]: 처짐·침하는 연직 스케일, 열팽창·이상은 종축 스케일
    vsens_deflection = wv * w_scale_used
    vsens_settle = (s_vsens[:, None] * t_year[None, :]) * w_scale_used
    vsens_thermal = thv * los_scale
    vsens_anomaly = av * los_scale
    # 전체 변위량: 종축(열팽창+이상)·연직(처짐+침하) 벡터합 크기[mm] — 물리적 총 변위
    u_long = vsens_thermal + vsens_anomaly
    u_vert = vsens_deflection + vsens_settle
    vsens_total = np.hypot(u_long, u_vert)
    vsens_l = xv * float(Lf.max())                                    # [V] 고정단 거리[m]
    _pk = int(np.argmax(vsens_total))
    _pk_i, _pk_t = (_pk // M, _pk % M)

    # ───────── 가상센싱 2D: 교량 상판(deck) 전체 면 변위 지도 ─────────
    # 종축 1D 필드를 넘어 **상판 전체 면**(길이×폭)의 변위를 추정한다. 관측점 구름의
    # PCA 로 상판 방향축 u1(길이)·횡축 u2(폭)을 잡아 2D 격자를 세우고, 각 격자점에서
    #   ① PINN 1D 전체변위장(vsens_total)을 그 점의 고정단거리 l 에 평가(물리 종축 구조)
    #   ② 관측점의 PINN 총변위 잔차(관측 위치의 국소·횡방향 편차)를 IDW 로 2D 보간
    # 를 합쳐 상판 전역(관측 없는 위치 포함) 변위량을 낸다. 점<3 이면 생략(None).
    deck = None
    if N >= 3:
        n_dl = int(np.clip(int(getattr(cfg, "pinn_deck_long", 60)), 4, 400))
        n_dt = int(np.clip(int(getattr(cfg, "pinn_deck_trans", 9)), 1, 100))
        xy = xyz[:, :2].astype(np.float64)
        c_xy = xy.mean(axis=0)
        Xc = xy - c_xy
        cov = Xc.T @ Xc / max(N - 1, 1)
        evals, evecs = np.linalg.eigh(cov)               # 오름차순
        u1 = evecs[:, -1] if evals[-1] > 1e-12 else np.array([1.0, 0.0])  # 길이축
        u2 = evecs[:, 0] if evals[0] > 1e-12 else np.array([0.0, 1.0])    # 폭축
        s_i = Xc @ u1                                     # [N] 종축 투영
        w_i = Xc @ u2                                     # [N] 횡축 투영
        s_lo, s_hi = float(s_i.min()), float(s_i.max())
        w_lo, w_hi = float(w_i.min()), float(w_i.max())
        if (w_hi - w_lo) < 1e-6 * max(abs(s_hi - s_lo), 1.0):  # 거의 1D → 공칭 폭 부여
            half = 0.05 * max(abs(s_hi - s_lo), 1.0)
            w_lo, w_hi = -half, half
        sg = np.linspace(s_lo, s_hi, n_dl)
        wg = np.linspace(w_lo, w_hi, n_dt)
        SS, WW = np.meshgrid(sg, wg, indexing="ij")       # [n_dl,n_dt]
        s_node = SS.ravel(); w_node = WW.ravel()          # [G]
        G = s_node.size
        deck_xy = c_xy[None, :] + s_node[:, None] * u1[None, :] + w_node[:, None] * u2[None, :]  # [G,2]
        # 각 격자점의 고정단거리 l: 종축 투영 s → 관측 l(l_fixed) 보간(정렬). 정규화 xn.
        so = np.argsort(s_i)
        l_node = np.interp(s_node, s_i[so], l_fixed[so])
        xn_node = np.clip((l_node - l_fixed.min()) / (np.ptp(l_fixed) + 1e-9), 0.0, 1.0)
        xn_pt = np.clip(xn, 0.0, 1.0)

        def _axis_eval(field_vm, xn_q):                   # [V,M],[Q] → [Q,M] (축 필드 보간)
            return np.stack([np.interp(xn_q, xv, field_vm[:, m]) for m in range(M)], axis=1)

        # 관측점 PINN 총변위(성분 합) — 잔차 IDW 의 소스
        u_long_pt = comp_thermal + comp_anomaly
        u_vert_pt = deflection + comp_settle
        total_pt = np.hypot(u_long_pt, u_vert_pt)         # [N,M]
        beam_total_node = _axis_eval(vsens_total, xn_node)     # [G,M] 물리 종축장
        beam_defl_node = _axis_eval(vsens_deflection, xn_node)
        resid_total = total_pt - _axis_eval(vsens_total, xn_pt)      # [N,M]
        resid_defl = deflection - _axis_eval(vsens_deflection, xn_pt)
        # IDW 가중(2D 상판좌표 s,w). 근접 관측점 잔차가 격자점에 반영 → 국소·횡방향 편차.
        d2 = (s_node[:, None] - s_i[None, :]) ** 2 + (w_node[:, None] - w_i[None, :]) ** 2  # [G,N]
        eps = 1e-6 * ((s_hi - s_lo) ** 2 + (w_hi - w_lo) ** 2) + 1e-12
        wgt = 1.0 / (d2 + eps)
        wgt /= wgt.sum(axis=1, keepdims=True)
        deck_total = beam_total_node + wgt @ resid_total   # [G,M] 상판 전체 변위량[mm]
        deck_deflection = beam_defl_node + wgt @ resid_defl
        _dpk = int(np.argmax(deck_total))
        _dpk_g, _dpk_t = _dpk // M, _dpk % M
        deck = {
            "n_deck": G, "n_long": n_dl, "n_trans": n_dt,
            "xy": deck_xy, "s": s_node, "w": w_node,
            "total": deck_total, "deflection": deck_deflection,
            "peak_mm": float(deck_total[_dpk_g, _dpk_t]),
            "peak_xy": [float(deck_xy[_dpk_g, 0]), float(deck_xy[_dpk_g, 1])],
            "peak_date_index": int(_dpk_t),
            "footprint_m": [float(s_hi - s_lo), float(w_hi - w_lo)],
        }

    # ───────── 변동 V (FRAM 입력) ─────────
    ss_res = np.sum(comp_anomaly ** 2, axis=1)
    ss_tot = np.sum((los - los.mean(axis=1, keepdims=True)) ** 2, axis=1) + 1e-9
    V_thermal = np.clip(ss_res / ss_tot, 0, 1)
    V_settle = np.clip(np.abs(s_rate.detach().numpy()) /
                       (np.abs(s_rate.detach().numpy()).max() + 1e-9), 0, 1)
    V_anomaly = np.clip(comp_anomaly.std(axis=1) / (comp_anomaly.std(axis=1).max() + 1e-9), 0, 1)
    # V_load: PDE 이탈(곡률 4차 비균일성 대용) 점별 정규화 — 진짜 물리 이탈
    pde_dev = np.abs(curvature - curvature.mean(axis=0, keepdims=True)).mean(axis=1)
    V_load = np.clip(pde_dev / (pde_dev.max() + 1e-9), 0, 1)

    def series(comp: np.ndarray) -> np.ndarray:
        d = np.abs(np.gradient(comp, axis=1))
        s = d.mean(axis=0)
        return s / (s.max() + 1e-9)

    V_func_series = np.stack([
        series(comp_thermal),                       # thermal
        series(comp_load + comp_thermal * 0.3),     # load
        series(comp_anomaly),                       # bearing
        series(comp_settle),                        # foundation
    ], axis=0)

    g = "/pinn"
    paths = {}
    for name, arr in [
        ("comp_thermal", comp_thermal), ("comp_load", comp_load),
        ("comp_settle", comp_settle), ("comp_anomaly", comp_anomaly),
        ("strain", strain), ("stress", stress), ("deflection", deflection),
        ("natural_freq", natural_freq), ("EI", EI), ("alpha", alpha),
        ("V_thermal", V_thermal), ("V_load", V_load),
        ("V_settle", V_settle), ("V_anomaly", V_anomaly),
        ("V_func_series", V_func_series),
        # 가상센싱(상부거더 전체 변위장)
        ("vsens_x", xv), ("vsens_l_from_fixed", vsens_l),
        ("vsens_total", vsens_total), ("vsens_deflection", vsens_deflection),
        ("vsens_thermal", vsens_thermal), ("vsens_settle", vsens_settle),
        ("vsens_anomaly", vsens_anomaly),
    ]:
        paths[name] = store.write_array(f"{g}/{name}", np.asarray(arr))
    # 가상센싱 2D 상판 면(deck) — 점<3 이면 미기록(Optional 계약)
    if deck is not None:
        for name, arr in [
            ("deck_xy", deck["xy"]), ("deck_s", deck["s"]), ("deck_w", deck["w"]),
            ("deck_total", deck["total"]), ("deck_deflection", deck["deflection"]),
        ]:
            paths[name] = store.write_array(f"{g}/{name}", np.asarray(arr))

    out = PINNOutput(
        n_points=N, n_dates=M,
        comp_thermal_ds=paths["comp_thermal"], comp_load_ds=paths["comp_load"],
        comp_settle_ds=paths["comp_settle"], comp_anomaly_ds=paths["comp_anomaly"],
        strain_ds=paths["strain"], stress_ds=paths["stress"],
        deflection_ds=paths["deflection"], natural_freq_ds=paths["natural_freq"],
        EI_ds=paths["EI"], alpha_ds=paths["alpha"],
        V_thermal_ds=paths["V_thermal"], V_load_ds=paths["V_load"],
        V_settle_ds=paths["V_settle"], V_anomaly_ds=paths["V_anomaly"],
        V_func_series_ds=paths["V_func_series"],
        func_names=list(FRAM_FUNCTIONS),
        n_virtual=n_vsens,
        vsens_x_ds=paths["vsens_x"], vsens_l_from_fixed_ds=paths["vsens_l_from_fixed"],
        vsens_total_ds=paths["vsens_total"], vsens_deflection_ds=paths["vsens_deflection"],
        vsens_thermal_ds=paths["vsens_thermal"], vsens_settle_ds=paths["vsens_settle"],
        vsens_anomaly_ds=paths["vsens_anomaly"],
        n_deck=(deck["n_deck"] if deck is not None else None),
        deck_xy_ds=(paths["deck_xy"] if deck is not None else None),
        deck_s_ds=(paths["deck_s"] if deck is not None else None),
        deck_w_ds=(paths["deck_w"] if deck is not None else None),
        deck_total_ds=(paths["deck_total"] if deck is not None else None),
        deck_deflection_ds=(paths["deck_deflection"] if deck is not None else None),
    )
    store.write_meta("pinn", out)
    import torch.nn.functional as _F
    store.write_json_attr("pinn", "inputs", {
        "bridge_type": prof.bridge_type, "material": prof.material,
        "span_m": L_m, "youngs_Pa": prof.youngs(), "load_per_len": prof.load_per_len,
        "profile_source": prof.source,
        "temperature_driven": bool(use_temp), "traffic_driven": bool(use_traffic),
        "vertical_observed": bool(use_vertical),   # 연직 채널로 처짐/침하 분리 여부
        "EI_global": EI_global, "ei_load_basis": load_basis,   # EI 식별 하중(교통활하중 or 자중)
        "q_effective_N_m": q_eff,
        "pde_form": prof.bridge_type,
        "pde_axial_p2": None if p2_pde is None else float(p2_pde.item()),
        "pde_foundation_k": None if p0_pde is None else float(_F.softplus(p0_pde).item()),
    })
    # 가상센싱 요약(상부거더 전체 변위장) — 첨두 변위·위치·중앙경간 + 상판 2D 면
    store.write_json_attr("pinn", "virtual_sensing", {
        "n_virtual": int(n_vsens),
        "span_m": float(L_m),
        "vertical_separated": bool(use_vertical),
        "peak_total_mm": float(vsens_total[_pk_i, _pk_t]),
        "peak_l_from_fixed_m": float(vsens_l[_pk_i]),
        "peak_date_index": int(_pk_t),
        "midspan_total_mm_max": float(vsens_total[n_vsens // 2].max()),
        # 2D 상판 면(deck) 가상센싱 요약(점<3 이면 null)
        "deck": None if deck is None else {
            "n_deck": deck["n_deck"], "n_long": deck["n_long"], "n_trans": deck["n_trans"],
            "footprint_m": deck["footprint_m"],
            "peak_total_mm": deck["peak_mm"], "peak_xy": deck["peak_xy"],
            "peak_date_index": deck["peak_date_index"],
        },
    })
    return out
