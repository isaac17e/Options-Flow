"""
tests/test_svi.py
------------------
Pruebas de regresión para la calibración SVI (src/models/svi.py) usando
datos SINTÉTICOS (sin llamar a Polygon), para poder correr en CI sin
API key y sin depender del estado del mercado en el momento del run.
"""

import numpy as np
import pytest

from src.models.svi import (
    SVIParams,
    calibrate_svi,
    check_butterfly_arbitrage,
    svi_total_variance,
)

# Parámetros "verdaderos" de un smile de equity típico (skew negativo,
# curvatura moderada). Elegidos para ser libres de arbitraje por
# construcción -- se verifica en test_true_params_are_arbitrage_free.
TRUE_PARAMS = SVIParams(a=0.04, b=0.10, rho=-0.40, m=0.0, sigma=0.15)


def _synthetic_market(n=25, k_lo=-0.35, k_hi=0.20, noise_std=0.0005, seed=7):
    rng = np.random.default_rng(seed)
    k = np.linspace(k_lo, k_hi, n)
    w_true = svi_total_variance(k, *TRUE_PARAMS.as_tuple())
    w_noisy = w_true + rng.normal(0, noise_std, size=n)
    open_interest = rng.uniform(10, 500, size=n)
    return k, w_noisy, open_interest


def test_true_params_are_arbitrage_free():
    arb_free, min_g = check_butterfly_arbitrage(TRUE_PARAMS, k_range=(-0.6, 0.6))
    assert arb_free
    assert min_g >= -1e-6


def test_check_butterfly_arbitrage_detects_violation():
    # b muy grande + rho casi 1 + sigma minúsculo: pliegue casi vertical,
    # violación clásica de la condición g(k) >= 0 de Gatheral.
    bad_params = SVIParams(a=0.01, b=5.0, rho=0.94, m=0.0, sigma=0.02)
    arb_free, min_g = check_butterfly_arbitrage(bad_params, k_range=(-0.6, 0.6))
    assert not arb_free
    assert min_g < 0


def test_calibrate_svi_recovers_known_params():
    k, w, oi = _synthetic_market()
    result = calibrate_svi(k, w, weights=oi)

    assert result.converged
    assert result.butterfly_arbitrage_free
    # RMSE en varianza total debe ser pequeño y del orden del ruido inyectado
    assert result.rmse < 0.005

    fitted = result.params
    w_fitted = svi_total_variance(k, *fitted.as_tuple())
    w_true = svi_total_variance(k, *TRUE_PARAMS.as_tuple())
    # El ajuste debe reproducir la curva verdadera casi punto a punto,
    # no solo "converger" a cualquier mínimo local arbitrario.
    assert np.max(np.abs(w_fitted - w_true)) < 0.01


def test_verified_k_range_matches_arbitrage_margin():
    k, w, oi = _synthetic_market()
    margin = 0.3
    result = calibrate_svi(k, w, weights=oi, arbitrage_margin=margin)

    expected = (float(k.min() - margin), float(k.max() + margin))
    assert result.verified_k_range == pytest.approx(expected)


def test_raises_with_too_few_points():
    k = np.array([-0.1, 0.0, 0.1])
    w = np.array([0.04, 0.03, 0.04])
    with pytest.raises(ValueError):
        calibrate_svi(k, w)


def test_multistart_diversifies_sigma_and_b():
    # Con datos casi planos (curvatura ATM muy baja) un único punto de
    # partida fijo (b=0.15, sigma=0.2) podía quedar lejos del óptimo.
    # Verificamos que la calibración igual converge a un ajuste razonable.
    k = np.linspace(-0.35, 0.20, 20)
    flat_params = SVIParams(a=0.01, b=0.02, rho=-0.2, m=0.0, sigma=0.3)
    w = svi_total_variance(k, *flat_params.as_tuple())

    result = calibrate_svi(k, w)
    assert result.converged
    assert result.rmse < 0.001
