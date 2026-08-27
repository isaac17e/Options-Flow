import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np
import matplotlib.pyplot as plt

from src.data.polygon_client import PolygonClient
from src.models.svi_inputs import build_svi_inputs
from src.models.svi import (
    calibrate_svi,
    check_butterfly_arbitrage,
    svi_total_variance,
    svi_first_derivative,
    svi_second_derivative,
)

TICKER = "SPY"
EXPIRATION_INDEX = 10


def g_function(k_grid, params):
    a, b, rho, m, sigma = params.as_tuple()
    w = svi_total_variance(k_grid, a, b, rho, m, sigma)
    w1 = svi_first_derivative(k_grid, a, b, rho, m, sigma)
    w2 = svi_second_derivative(k_grid, a, b, rho, m, sigma)
    w_safe = np.maximum(w, 1e-10)
    term1 = (1 - (k_grid * w1) / (2 * w_safe)) ** 2
    term2 = (w1 ** 2 / 4) * (1 / w_safe + 0.25)
    term3 = w2 / 2
    return term1 - term2 + term3


def main():
    client = PolygonClient()
    expirations = client.get_available_expirations(TICKER)
    expiration = expirations[EXPIRATION_INDEX]
    underlying = client.get_underlying_snapshot(TICKER)
    chain = client.get_option_chain_snapshot(TICKER, expiration_date=expiration, max_contracts=1000)

    sub_chain = chain.filter_by_expiration(expiration)
    tte_years = sub_chain.contracts[0].time_to_expiration_years
    forward = client.estimate_forward_price(underlying.spot_price, tte_years)

    svi_inputs = build_svi_inputs(chain, expiration, forward, min_open_interest=5)
    print(f"Rango real de k en los datos: [{svi_inputs.k.min():.4f}, {svi_inputs.k.max():.4f}]")
    print(f"Número de puntos de mercado: {len(svi_inputs.k)}")

    margin = 0.25

    # --- Intento 1: igual que en test_pdf.py (40 puntos de restricción) ---
    print("\n=== Calibración con 40 puntos de restricción (como test_pdf.py) ===")
    result_40 = calibrate_svi(svi_inputs.k, svi_inputs.w, weights=svi_inputs.open_interest,
                                n_arbitrage_constraints=40, arbitrage_margin=margin)
    print(f"Libre de arbitraje: {result_40.butterfly_arbitrage_free} | min_g: {result_40.min_g_value:.6f}")

    # --- Intento 2: mucho más denso (150 puntos) ---
    print("\n=== Calibración con 150 puntos de restricción (más denso) ===")
    result_150 = calibrate_svi(svi_inputs.k, svi_inputs.w, weights=svi_inputs.open_interest,
                                 n_arbitrage_constraints=150, arbitrage_margin=margin)
    print(f"Libre de arbitraje: {result_150.butterfly_arbitrage_free} | min_g: {result_150.min_g_value:.6f}")

    # --- Visualización: g(k) sobre grilla continua fina, marcando los 40 puntos usados ---
    k_range = (svi_inputs.k.min() - margin, svi_inputs.k.max() + margin)
    k_fine = np.linspace(k_range[0], k_range[1], 3000)
    k_constraints_40 = np.linspace(k_range[0], k_range[1], 40)

    g_fine_40 = g_function(k_fine, result_40.params)
    g_at_constraints_40 = g_function(k_constraints_40, result_40.params)

    plt.figure(figsize=(11, 6))
    plt.plot(k_fine, g_fine_40, label="g(k) — curva continua (calibración 40 pts)", color="tab:blue")
    plt.scatter(k_constraints_40, g_at_constraints_40, color="tab:orange", s=30, zorder=5,
                label="Puntos donde SÍ exigimos g(k)>=0 (40 pts)")
    plt.axhline(0, color="black", linewidth=1, linestyle="--")
    plt.axvspan(svi_inputs.k.min(), svi_inputs.k.max(), alpha=0.1, color="green", label="Rango real de datos de mercado")
    plt.xlabel("k = ln(K/F)")
    plt.ylabel("g(k)  (debe ser >= 0)")
    plt.title(f"Diagnóstico de arbitraje butterfly — {TICKER} {expiration}")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.savefig("arbitrage_diagnosis.png", dpi=150, bbox_inches="tight")
    print("\n📊 Diagnóstico guardado en: arbitrage_diagnosis.png")
    plt.show()


if __name__ == "__main__":
    main()
