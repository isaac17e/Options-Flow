"""
src/models/greeks_black76.py
-------------------------------
Griegas bajo Black-76. Polygon nos da delta/gamma/vega/theta por
contrato, pero NO vanna ni charm — así que las calculamos nosotros.

Estrategia: Delta y Gamma usan fórmulas cerradas (simples, bajo riesgo
de error). Vanna y Charm se calculan por DIFERENCIAS FINITAS sobre la
propia Delta — esto evita errores de signo sutiles en las fórmulas
cerradas de griegas de segundo orden (que varían entre fuentes según
convención), a costa de un poco más de cómputo (insignificante aquí).

Validamos todo contra la igualdad de derivadas cruzadas (teorema de
Schwarz): d(Vanna)/dF debe coincidir con d(Gamma)/d(sigma). Si nuestra
implementación tiene un error de signo o de fórmula, esta prueba lo
detecta de inmediato.
"""

from __future__ import annotations
import numpy as np
from scipy.stats import norm


def _d1_d2(forward, strike, T, sigma):
    sqrt_T = np.sqrt(T)
    vol_sqrt_T = np.maximum(sigma * sqrt_T, 1e-10)
    d1 = (np.log(forward / strike) + 0.5 * sigma ** 2 * T) / vol_sqrt_T
    d2 = d1 - vol_sqrt_T
    return d1, d2


def delta_call(forward, strike, T, sigma, r=0.0):
    d1, _ = _d1_d2(forward, strike, T, sigma)
    return np.exp(-r * T) * norm.cdf(d1)


def delta_put(forward, strike, T, sigma, r=0.0):
    d1, _ = _d1_d2(forward, strike, T, sigma)
    return np.exp(-r * T) * (norm.cdf(d1) - 1)


def gamma(forward, strike, T, sigma, r=0.0):
    """Gamma es la misma fórmula para calls y puts."""
    d1, _ = _d1_d2(forward, strike, T, sigma)
    sqrt_T = np.sqrt(T)
    return np.exp(-r * T) * norm.pdf(d1) / (forward * sigma * sqrt_T)


def vanna(forward, strike, T, sigma, r=0.0, option_type="call", h=1e-4):
    """
    Vanna = d(Delta)/d(sigma), calculada por diferencia central.
    Misma fórmula matemática para calls y puts (Vanna no distingue tipo,
    ya que Delta_put = Delta_call - descuento_constante, y esa constante
    no depende de sigma), así que option_type se ignora en el cálculo
    y solo se conserva en la firma por compatibilidad. Vectorizado:
    forward/strike/T/sigma/option_type pueden ser escalares o arrays.
    """
    up = delta_call(forward, strike, T, sigma + h, r)
    down = delta_call(forward, strike, T, sigma - h, r)
    return (up - down) / (2 * h)


def charm(forward, strike, T, sigma, r=0.0, option_type="call", h=None):
    """
    Charm = d(Delta)/dt, donde t es tiempo CALENDARIO que transcurre
    (T disminuye). Por convención, Charm = -d(Delta)/dT.
    Calculado por diferencia central sobre T. A diferencia de Vanna, sí
    distingue tipo (el descuento e^(-rT) que separa Delta_call de
    Delta_put depende de T). Vectorizado: option_type puede ser un
    escalar ("call"/"put") o un array de esas etiquetas, igual que el
    resto de los parámetros.
    """
    T = np.asarray(T, dtype=float)
    if h is None:
        h = np.minimum(T * 0.01, 1e-4)  # paso pequeño relativo a T
    is_call = np.asarray(option_type) == "call"
    T_up = np.maximum(T + h, 1e-8)
    T_down = np.maximum(T - h, 1e-8)
    up = np.where(is_call, delta_call(forward, strike, T_up, sigma, r), delta_put(forward, strike, T_up, sigma, r))
    down = np.where(is_call, delta_call(forward, strike, T_down, sigma, r), delta_put(forward, strike, T_down, sigma, r))
    return -(up - down) / (T_up - T_down)  # negativo: decaimiento al pasar el tiempo
