"""
src/models/black76.py
------------------------
Pricing de opciones europeas usando el modelo Black-76 (variante de
Black-Scholes que usa el precio Forward en vez de Spot + dividendos).

Elegimos Black-76 porque ya trabajamos con F (Bloque 1) y con
k = ln(K/F) (Bloque 2) — es la forma más limpia y estándar en la
industria de tasas/commodities/index options para conectar SVI con
precios de opciones.

Fórmulas (sin descuento, "precio forward" del call/put):
    d1 = (ln(F/K) + 0.5*sigma^2*T) / (sigma*sqrt(T))
    d2 = d1 - sigma*sqrt(T)

    C_forward = F*N(d1) - K*N(d2)
    P_forward = K*N(-d2) - F*N(-d1)

Precio "spot" (con descuento a valor presente):
    C = e^(-rT) * C_forward
    P = e^(-rT) * P_forward
"""

from __future__ import annotations
import numpy as np
from scipy.stats import norm


def black76_call_price(
    forward: np.ndarray,
    strike: np.ndarray,
    time_to_expiration: float,
    volatility: np.ndarray,
    risk_free_rate: float = 0.0,
) -> np.ndarray:
    """
    Precio de un Call europeo bajo Black-76. Todos los inputs pueden ser
    escalares o arrays de numpy (vectorizado).
    """
    forward = np.asarray(forward, dtype=float)
    strike = np.asarray(strike, dtype=float)
    volatility = np.asarray(volatility, dtype=float)
    T = time_to_expiration

    sqrt_T = np.sqrt(T)
    vol_sqrt_T = volatility * sqrt_T

    # Protección numérica: evitar división por cero si T o vol son ~0
    vol_sqrt_T_safe = np.maximum(vol_sqrt_T, 1e-10)

    d1 = (np.log(forward / strike) + 0.5 * volatility ** 2 * T) / vol_sqrt_T_safe
    d2 = d1 - vol_sqrt_T_safe

    call_forward_price = forward * norm.cdf(d1) - strike * norm.cdf(d2)
    discount_factor = np.exp(-risk_free_rate * T)

    return discount_factor * call_forward_price
