"""교량 형식별 지배 PDE — PINN 손실에 형식에 맞는 물리방정식을 넣는다.

균일하중 가정에서 지배식은 x 에 대해 균일(상수)해야 하므로, 일반형
    R(x,t) = w'''' + p2·w'' + p0·w
의 **x-분산**을 손실로 쓴다(R 이 x 에 균일하면 분산 0). 형식별 활성 항:

  girder/continuous_girder/truss : w''''            (Euler-Bernoulli 보)
  cable_stayed                   : w'''' + p0·w     (탄성지지 보 — 케이블=분포 스프링 k≥0)
  arch / suspension              : w'''' + p2·w''    (축력 — 추력 H / 케이블 장력 T)

p2(축력항)·p0(탄성지지항)는 학습 파라미터로, 해당 형식에서만 활성화된다. 미지원/거더
계열은 보(w'''')로 폴백. autograd 로 w·w''·w'''' 를 한 번에 구한다.
"""

from __future__ import annotations

from typing import Any

# bridge_type -> (use_w2 축력항, use_w0 탄성지지항, w0_nonneg 강성≥0)
PDE_TERMS: dict[str, tuple[bool, bool, bool]] = {
    "girder": (False, False, False),
    "continuous_girder": (False, False, False),
    "truss": (False, False, False),
    "cable_stayed": (False, True, True),
    "arch": (True, False, False),
    "suspension": (True, False, False),
}


def make_pde_params(bridge_type: str, torch: Any) -> tuple[Any, Any]:
    """형식에 필요한 학습 파라미터(p2 축력, p0 탄성지지)를 만든다(없으면 None)."""
    use_w2, use_w0, _ = PDE_TERMS.get(bridge_type, (False, False, False))
    p2 = torch.nn.Parameter(torch.zeros(1)) if use_w2 else None
    p0 = torch.nn.Parameter(torch.zeros(1)) if use_w0 else None
    return p2, p0


def pde_loss(w_net, xc0, t_sub, n_col, p2, p0, bridge_type: str, torch: Any):
    """형식별 PDE 잔차의 x-분산 손실(시점 평균). w_net: load 처짐 MLP."""
    use_w2, use_w0, w0_nonneg = PDE_TERMS.get(bridge_type, (False, False, False))
    loss = torch.zeros(())
    for tc in t_sub:
        xc = xc0.clone().requires_grad_(True)
        inp = torch.stack([xc, tc.expand(n_col)], dim=1)
        w = w_net(inp)
        g = w
        derivs = [w]
        for _ in range(4):
            g = torch.autograd.grad(g, xc, torch.ones_like(g), create_graph=True)[0]
            derivs.append(g)
        residual = derivs[4]                                  # w''''
        if use_w2 and p2 is not None:
            residual = residual + p2 * derivs[2]              # + (추력/장력)·w''
        if use_w0 and p0 is not None:
            k = torch.nn.functional.softplus(p0) if w0_nonneg else p0
            residual = residual + k * derivs[0]               # + (탄성지지 k)·w
        loss = loss + torch.var(residual)
    return loss / len(t_sub)
