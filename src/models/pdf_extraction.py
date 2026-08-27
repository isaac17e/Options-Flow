"""
src/models/pdf_extraction.py
-------------------------------
Implementa Breeden-Litzenberger: toma la curva SVI continua, genera una
grilla ultra-fina de strikes teóricos, calcula precios de Call vía
Black-76, y deriva dos veces respecto al strike para obtener la
densidad de probabilidad neutral al riesgo (el "Mapa de Destinos").

    f(K) = e^(rT) * d^2C/dK^2
"""

from __future__ import annotations
from dataclasses import dataclass

import numpy as np
from scipy.integrate import cumulative_trapezoid

# Compatibilidad numpy 1.x / 2.x: trapz fue renombrado a trapezoid en NumPy 2.0
_trapz = getattr(np, "trapezoid", None) or np.trapz

from src.models.svi import SVIParams, svi_total_variance
from src.models.black76 import black76_call_price


@dataclass
class RiskNeutralPDF:
    strikes: np.ndarray        # grilla de strikes (dominio de la densidad)
    density: np.ndarray        # f(K), densidad de probabilidad
    forward: float
    time_to_expiration_years: float

    @property
    def mean(self) -> float:
        """Media de la distribución (debería estar cerca del Forward)."""
        return float(_trapz(self.strikes * self.density, self.strikes))

    @property
    def std(self) -> float:
        m = self.mean
        variance = _trapz(((self.strikes - m) ** 2) * self.density, self.strikes)
        return float(np.sqrt(max(variance, 0)))

    @property
    def total_probability(self) -> float:
        """Debe integrar ~1.0 si la densidad está bien construida."""
        return float(_trapz(self.density, self.strikes))

    def quantile(self, q: float) -> float:
        """
        Strike aproximado tal que P(S_T <= K) = q, integrando la densidad
        acumulada (CDF) con la regla trapezoidal (correcta para grillas
        no uniformes, como la nuestra que crece exponencialmente en K).
        """
        cdf = cumulative_trapezoid(self.density, self.strikes, initial=0.0)
        cdf = cdf / cdf[-1]  # normalizar por si el área total no es exactamente 1
        return float(np.interp(q, cdf, self.strikes))


def extract_risk_neutral_pdf(
    svi_params: SVIParams,
    forward: float,
    time_to_expiration_years: float,
    risk_free_rate: float = 0.0,
    k_range: tuple = (-1.2, 1.2),
    n_points: int = 2000,
) -> RiskNeutralPDF:
    """
    Genera la PDF neutral al riesgo a partir de una curva SVI ya calibrada.

    Parameters
    ----------
    k_range : rango de log-moneyness a cubrir (en unidades de k = ln(K/F)).
              Por defecto cubre un rango amplio; para vencimientos muy
              cortos esto puede corresponder a strikes irrealmente
              extremos, pero no afecta el resultado cerca del centro.
    n_points : resolución de la grilla. Más puntos = derivada segunda
               más precisa, pero también más sensible a ruido numérico
               si se usa una curva de mercado sin suavizar (por eso
               usamos SVI: la curva es analítica y suave).
    """
    T = time_to_expiration_years
    r = risk_free_rate

    # --- 1. Grilla ultra-fina de log-moneyness, convertida a strikes ---
    k_grid = np.linspace(k_range[0], k_range[1], n_points)
    strikes = forward * np.exp(k_grid)

    # --- 2. Volatilidad implícita suavizada por SVI en cada strike ---
    w_grid = svi_total_variance(k_grid, *svi_params.as_tuple())
    w_grid = np.maximum(w_grid, 1e-10)  # protección: varianza no negativa
    iv_grid = np.sqrt(w_grid / T)

    # --- 3. Precios teóricos de Call vía Black-76 ---
    call_prices = black76_call_price(
        forward=forward,
        strike=strikes,
        time_to_expiration=T,
        volatility=iv_grid,
        risk_free_rate=r,
    )

    # --- 4. Segunda derivada numérica respecto al strike ---
    dK = np.diff(strikes)
    # La grilla en k es uniforme, pero en K (strike) no lo es (es exponencial).
    # Usamos derivada segunda con espaciado no uniforme (más preciso que
    # asumir dK constante).
    d2C_dK2 = _second_derivative_nonuniform(strikes, call_prices)

    # --- 5. Densidad de probabilidad ---
    discount_factor = np.exp(r * T)
    density = discount_factor * d2C_dK2
    density = np.maximum(density, 0.0)  # la densidad no puede ser negativa;
    # valores negativos pequeños son ruido numérico en los extremos de la grilla.

    return RiskNeutralPDF(
        strikes=strikes,
        density=density,
        forward=forward,
        time_to_expiration_years=T,
    )


def _second_derivative_nonuniform(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """
    Segunda derivada numérica para una grilla NO necesariamente uniforme,
    usando diferencias finitas centradas de 3 puntos con espaciado variable.
    Los extremos (primer y último punto) se rellenan repitiendo el valor
    vecino más cercano (no se pueden calcular ahí con este método).
    """
    n = len(x)
    d2y = np.zeros(n)

    for i in range(1, n - 1):
        h1 = x[i] - x[i - 1]
        h2 = x[i + 1] - x[i]
        # Fórmula de diferencias finitas centradas para espaciado desigual
        d2y[i] = 2 * (
            y[i - 1] / (h1 * (h1 + h2))
            - y[i] / (h1 * h2)
            + y[i + 1] / (h2 * (h1 + h2))
        )

    d2y[0] = d2y[1]
    d2y[-1] = d2y[-2]
    return d2y
