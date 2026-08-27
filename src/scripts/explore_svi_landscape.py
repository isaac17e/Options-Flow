import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np
from scipy.optimize import minimize

from src.data.polygon_client import PolygonClient
from src.models.svi_inputs import build_svi_inputs
from src.models.svi import (
    SVIParams,
    svi_total_variance,
    svi_first_derivative,
    svi_second_derivative,
    check_butterfly_arbitrage,
)

TICKER = "SPY"
EXPIRATION_INDEX = 10


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
    k = svi_inputs.k
    w_market = svi_inputs.w
    weights = np.sqrt(np.maximum(svi_inputs.open_interest, 0))
    weights = weights / weights.mean()

    print(f"Puntos de mercado: {len(k)}  |  Rango k: [{k.min():.3f}, {k.max():.3f}]\n")

    margin = 0.25
    n_constraints = 150
    k_constraint_points = np.linspace(k.min() - margin, k.max() + margin, n_constraints)

    def objective(params):
        a, b, rho, m, sigma = params
        w_model = svi_total_variance(k, a, b, rho, m, sigma)
        residuals = (w_model - w_market) * weights
        return float(np.sum(residuals ** 2))

    def min_variance_constraint(params):
        a, b, rho, m, sigma = params
        return a + b * sigma * np.sqrt(max(1 - rho ** 2, 0))

    def arbitrage_constraint(params):
        a, b, rho, m, sigma = params
        w_c = svi_total_variance(k_constraint_points, a, b, rho, m, sigma)
        w1_c = svi_first_derivative(k_constraint_points, a, b, rho, m, sigma)
        w2_c = svi_second_derivative(k_constraint_points, a, b, rho, m, sigma)
        w_safe = np.maximum(w_c, 1e-8)
        term1 = (1 - (k_constraint_points * w1_c) / (2 * w_safe)) ** 2
        term2 = (w1_c ** 2 / 4) * (1 / w_safe + 0.25)
        term3 = w2_c / 2
        return term1 - term2 + term3

    bounds = [(1e-8, None), (1e-6, None), (-0.95, 0.95), (None, None), (0.02, 2.0)]
    constraints = [
        {"type": "ineq", "fun": min_variance_constraint},
        {"type": "ineq", "fun": arbitrage_constraint},
    ]

    # Grilla amplia de puntos de partida: variando rho, m y sigma iniciales
    a0 = max(w_market.min() * 0.5, 1e-4)
    rhos0 = [-0.8, -0.6, -0.4, -0.2, -0.1]
    ms0 = [-0.15, -0.05, 0.0, 0.05, 0.15]
    sigmas0 = [0.1, 0.2, 0.3]

    print(f"{'rho0':>6} {'m0':>6} {'sigma0':>7} | {'RMSE':>10} {'rho':>7} {'m':>8} {'sigma':>7} {'arb_free':>9}")
    print("-" * 75)

    results = []
    for rho0 in rhos0:
        for m0 in ms0:
            for sigma0 in sigmas0:
                start = [a0, 0.15, rho0, m0, sigma0]
                result = minimize(
                    objective, x0=start, method="SLSQP", bounds=bounds,
                    constraints=constraints, options={"maxiter": 3000, "ftol": 1e-12},
                )
                if not result.success:
                    continue
                a, b, rho, m, sigma = result.x
                candidate = SVIParams(a=a, b=b, rho=rho, m=m, sigma=sigma)
                arb_free, min_g = check_butterfly_arbitrage(candidate, k_range=(k.min() - margin, k.max() + margin))
                rmse = np.sqrt(result.fun / len(k))  # aprox, sin ponderar por weights exactos
                results.append((rho0, m0, sigma0, rmse, rho, m, sigma, arb_free, min_g))
                print(f"{rho0:6.2f} {m0:6.2f} {sigma0:7.2f} | {rmse:10.6f} {rho:7.3f} {m:8.3f} {sigma:7.4f} {str(arb_free):>9}")

    # Mejor resultado VÁLIDO (libre de arbitraje) por RMSE
    valid = [r for r in results if r[7]]
    if valid:
        best = min(valid, key=lambda r: r[3])
        print(f"\n✅ Mejor solución VÁLIDA encontrada: RMSE={best[3]:.6f}  rho={best[4]:.3f}  m={best[5]:.3f}  sigma={best[6]:.4f}")
    else:
        print("\n⚠️ Ninguna solución fue libre de arbitraje en esta grilla de puntos de partida.")


if __name__ == "__main__":
    main()
