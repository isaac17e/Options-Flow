"""
src/models/svi.py
-------------------
Calibración del modelo SVI (Stochastic Volatility Inspired) de Jim Gatheral,
parametrización "raw":

    w(k) = a + b * [ rho * (k - m) + sqrt((k - m)^2 + sigma^2) ]

donde:
    k = ln(K / F)          log-strike forward (moneyness)
    w = sigma_impl^2 * T   varianza total (no "volatilidad", VARIANZA)

Parámetros:
    a     -> nivel general de varianza (traslada la curva verticalmente)
    b     -> pendiente / "ancho" del smile (b >= 0)
    rho   -> asimetría / skew, en (-1, 1). Para equities normalmente < 0
             (puts caras respecto a calls -> "smirk" hacia la izquierda)
    m     -> traslación horizontal del mínimo de la parábola
    sigma -> curvatura cerca del mínimo (ATM), sigma > 0

Este módulo NO depende de Polygon ni de pandas — solo de numpy/scipy,
para que sea reutilizable y testeable de forma aislada.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import minimize


@dataclass
class SVIParams:
    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def as_tuple(self) -> tuple:
        return (self.a, self.b, self.rho, self.m, self.sigma)


@dataclass
class SVICalibrationResult:
    params: SVIParams
    rmse: float                  # error cuadrático medio en varianza total
    converged: bool
    n_points: int
    butterfly_arbitrage_free: bool
    min_g_value: float           # valor mínimo de g(k) en la grilla; debe ser >= 0
    verified_k_range: tuple[float, float]  # rango de k donde se verificó (o exigió)
                                            # ausencia de arbitraje butterfly. Cualquier
                                            # consumidor (ej. extract_risk_neutral_pdf)
                                            # debe pedir su PDF dentro de este rango —
                                            # fuera de él la curva SVI es pura
                                            # extrapolación sin garantía de no-arbitraje.


# ----------------------------------------------------------------------
# Función SVI y sus derivadas analíticas (necesarias para chequear
# arbitraje y, más adelante en el Bloque 3, para Black-Scholes/Breeden-Litzenberger)
# ----------------------------------------------------------------------

def svi_total_variance(k: np.ndarray, a: float, b: float, rho: float, m: float, sigma: float) -> np.ndarray:
    """w(k): varianza total según SVI raw."""
    x = k - m
    return a + b * (rho * x + np.sqrt(x ** 2 + sigma ** 2))


def svi_first_derivative(k: np.ndarray, a: float, b: float, rho: float, m: float, sigma: float) -> np.ndarray:
    """w'(k)"""
    x = k - m
    return b * (rho + x / np.sqrt(x ** 2 + sigma ** 2))


def svi_second_derivative(k: np.ndarray, a: float, b: float, rho: float, m: float, sigma: float) -> np.ndarray:
    """w''(k)"""
    x = k - m
    return b * sigma ** 2 / np.power(x ** 2 + sigma ** 2, 1.5)


# ----------------------------------------------------------------------
# Calibración
# ----------------------------------------------------------------------

def calibrate_svi(
    k: np.ndarray,
    w_market: np.ndarray,
    weights: Optional[np.ndarray] = None,
    initial_guess: Optional[SVIParams] = None,
    enforce_no_arbitrage: bool = True,
    n_arbitrage_constraints: int = 150,
    arbitrage_margin: float = 0.25,
    n_starts: int = 6,
) -> SVICalibrationResult:
    """
    arbitrage_margin : margen (en unidades de k) más allá del rango de
              datos observado donde se exige/verifica ausencia de
              arbitraje butterfly. IMPORTANTE: este mismo margen se usa
              tanto para las restricciones DENTRO del optimizador como
              para la verificación final — si no coincidieran, quedaría
              una franja "ciega" (exigida en la verificación pero nunca
              impuesta durante la optimización) donde podría colarse
              una violación residual.
    """
    k = np.asarray(k, dtype=float)
    w_market = np.asarray(w_market, dtype=float)

    if len(k) < 6:
        raise ValueError(
            f"Se necesitan al menos 6 puntos para calibrar 5 parámetros de forma estable "
            f"(se recibieron {len(k)})."
        )

    if weights is None:
        weights = np.ones_like(k)
    else:
        weights = np.asarray(weights, dtype=float)
        weights = np.sqrt(np.maximum(weights, 0))
        weights = weights / weights.mean()

    def objective(params: np.ndarray) -> float:
        a, b, rho, m, sigma = params
        w_model = svi_total_variance(k, a, b, rho, m, sigma)
        residuals = (w_model - w_market) * weights
        return float(np.sum(residuals ** 2))

    def min_variance_constraint(params: np.ndarray) -> float:
        a, b, rho, m, sigma = params
        return a + b * sigma * np.sqrt(max(1 - rho ** 2, 0))

    # sigma_min: un piso realista. sigma es la curvatura ATM en unidades de
    # k = ln(K/F); valores por debajo de ~0.02-0.03 producen pliegues casi
    # verticales sin sentido económico, sin importar qué tan "bien" ajusten
    # unos pocos puntos ruidosos.
    bounds = [
        (1e-8, None),
        (1e-6, None),
        (-0.95, 0.95),
        (None, None),
        (0.02, 2.0),
    ]

    constraints = [{"type": "ineq", "fun": min_variance_constraint}]

    if enforce_no_arbitrage:
        k_constraint_points = np.linspace(
            k.min() - arbitrage_margin, k.max() + arbitrage_margin, n_arbitrage_constraints
        )

        def arbitrage_constraint(params: np.ndarray) -> np.ndarray:
            a, b, rho, m, sigma = params
            w_c = svi_total_variance(k_constraint_points, a, b, rho, m, sigma)
            w1_c = svi_first_derivative(k_constraint_points, a, b, rho, m, sigma)
            w2_c = svi_second_derivative(k_constraint_points, a, b, rho, m, sigma)
            w_safe = np.maximum(w_c, 1e-8)

            term1 = (1 - (k_constraint_points * w1_c) / (2 * w_safe)) ** 2
            term2 = (w1_c ** 2 / 4) * (1 / w_safe + 0.25)
            term3 = w2_c / 2
            return term1 - term2 + term3

        constraints.append({"type": "ineq", "fun": arbitrage_constraint})

    # --- Multi-start: generamos varios puntos de partida razonables ---
    if initial_guess is not None:
        starting_points = [initial_guess]
    else:
        a0 = max(w_market.min() * 0.5, 1e-4)
        # Variamos rho, b y sigma juntos (no solo rho) para cubrir tanto
        # smiles asimétricos marcados como slices con poca curvatura ATM
        # (ej. vencimientos muy cortos/largos), donde b=0.15/sigma=0.2 fijos
        # podrían converger siempre al mismo mínimo local subóptimo.
        candidate_rhos = np.linspace(-0.6, -0.1, n_starts)
        candidate_sigmas = np.linspace(0.08, 0.35, n_starts)
        candidate_bs = np.linspace(0.08, 0.25, n_starts)
        starting_points = [
            SVIParams(a=a0, b=b0, rho=rho0, m=0.0, sigma=sigma0)
            for rho0, sigma0, b0 in zip(candidate_rhos, candidate_sigmas, candidate_bs)
        ]

    best_result = None
    best_score = np.inf

    for start in starting_points:
        result = minimize(
            objective,
            x0=start.as_tuple(),
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 3000, "ftol": 1e-12},
        )

        if not result.success:
            continue

        a, b, rho, m, sigma = result.x
        candidate = SVIParams(a=a, b=b, rho=rho, m=m, sigma=sigma)
        arb_free, min_g = check_butterfly_arbitrage(candidate, k_range=(k.min() - arbitrage_margin, k.max() + arbitrage_margin))

        # Preferimos SIEMPRE una solución libre de arbitraje sobre una que
        # ajuste "mejor" pero sea inválida. Penalizamos fuertemente las
        # soluciones con arbitraje para que casi nunca ganen frente a una
        # alternativa válida, aunque su RMSE numérico sea algo menor.
        penalty = 0.0 if arb_free else 1e6 * max(0.0, -min_g)
        score = result.fun + penalty

        if score < best_score:
            best_score = score
            best_result = (result, candidate, arb_free, min_g)

    if best_result is None:
        raise RuntimeError("Ningún punto de partida convergió a una solución válida.")

    result, fitted, arb_free, min_g = best_result

    w_fitted = svi_total_variance(k, *fitted.as_tuple())
    rmse = float(np.sqrt(np.mean((w_fitted - w_market) ** 2)))

    verified_k_range = (float(k.min() - arbitrage_margin), float(k.max() + arbitrage_margin))

    return SVICalibrationResult(
        params=fitted,
        rmse=rmse,
        converged=bool(result.success),
        n_points=len(k),
        butterfly_arbitrage_free=arb_free,
        min_g_value=min_g,
        verified_k_range=verified_k_range,
    )


# ----------------------------------------------------------------------
# Chequeo de arbitraje "butterfly" (Gatheral, condición de densidad no-negativa)
# ----------------------------------------------------------------------

def check_butterfly_arbitrage(params: SVIParams, k_range: tuple, n_grid: int = 500) -> tuple[bool, float]:
    """
    Evalúa la función g(k) de Gatheral sobre una grilla fina. Si g(k) >= 0
    para todo k, la curva está libre de arbitraje "butterfly" (la densidad
    de probabilidad implícita nunca es negativa).

    g(k) = (1 - k*w'(k)/(2*w(k)))^2 - (w'(k)^2/4)*(1/w(k) + 1/4) + w''(k)/2

    Retorna (esta_libre_de_arbitraje, valor_minimo_de_g).
    """
    k_grid = np.linspace(k_range[0], k_range[1], n_grid)
    a, b, rho, m, sigma = params.as_tuple()

    w = svi_total_variance(k_grid, a, b, rho, m, sigma)
    w1 = svi_first_derivative(k_grid, a, b, rho, m, sigma)
    w2 = svi_second_derivative(k_grid, a, b, rho, m, sigma)

    # Evitar división por cero si w es extremadamente pequeño
    w_safe = np.maximum(w, 1e-10)

    term1 = (1 - (k_grid * w1) / (2 * w_safe)) ** 2
    term2 = (w1 ** 2 / 4) * (1 / w_safe + 0.25)
    term3 = w2 / 2

    g = term1 - term2 + term3
    min_g = float(np.min(g))

    return (min_g >= -1e-6), min_g  # pequeña tolerancia numérica
