"""
tests/test_pdf_extraction.py
-----------------------------
Pruebas de regresión para Breeden-Litzenberger (src/models/pdf_extraction.py)
usando una curva SVI fija (sin llamar a Polygon ni pasar por la calibración).
"""

import numpy as np
import pytest

from src.models.svi import SVIParams, calibrate_svi, svi_total_variance
from src.models.pdf_extraction import extract_risk_neutral_pdf

FORWARD = 500.0
TTE = 0.25  # ~3 meses
PARAMS = SVIParams(a=0.02, b=0.12, rho=-0.35, m=0.0, sigma=0.18)


def _pdf(k_range=(-0.8, 0.8), n_points=4000):
    return extract_risk_neutral_pdf(PARAMS, FORWARD, TTE, k_range=k_range, n_points=n_points)


def test_density_is_nonnegative():
    pdf = _pdf()
    assert np.all(pdf.density >= 0)


def test_total_probability_close_to_one():
    pdf = _pdf()
    # Con un rango de k suficientemente ancho y grilla fina, la densidad
    # extraída vía Breeden-Litzenberger debe integrar ~1.
    assert pdf.total_probability == pytest.approx(1.0, abs=0.02)


def test_mean_close_to_forward():
    # Para una curva con skew moderado, la media de la RN-PDF debe quedar
    # razonablemente cerca del forward (no exactamente igual: el skew la
    # desplaza, pero no debería dispararse a un múltiplo del forward).
    pdf = _pdf()
    assert pdf.mean == pytest.approx(FORWARD, rel=0.05)


def test_quantiles_are_monotonic():
    pdf = _pdf()
    q25 = pdf.quantile(0.25)
    q50 = pdf.quantile(0.50)
    q75 = pdf.quantile(0.75)
    assert q25 < q50 < q75


def test_pdf_domain_matches_calibration_verified_k_range():
    # Regresión del bug de desalineación: el dominio pedido a
    # extract_risk_neutral_pdf debe coincidir con lo que calibrate_svi
    # verificó como libre de arbitraje, no un rango arbitrario más ancho.
    k = np.linspace(-0.35, 0.20, 20)
    w = svi_total_variance(k, *PARAMS.as_tuple())
    calibration = calibrate_svi(k, w)

    pdf = extract_risk_neutral_pdf(
        calibration.params, FORWARD, TTE, k_range=calibration.verified_k_range, n_points=2000
    )

    lo, hi = calibration.verified_k_range
    assert pdf.strikes.min() == pytest.approx(FORWARD * np.exp(lo), rel=1e-6)
    assert pdf.strikes.max() == pytest.approx(FORWARD * np.exp(hi), rel=1e-6)


def test_narrower_k_range_raises_no_error_but_shrinks_domain():
    pdf_wide = _pdf(k_range=(-0.8, 0.8))
    pdf_narrow = _pdf(k_range=(-0.2, 0.2))
    assert pdf_narrow.strikes.min() > pdf_wide.strikes.min()
    assert pdf_narrow.strikes.max() < pdf_wide.strikes.max()
