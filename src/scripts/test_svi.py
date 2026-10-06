import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np
import matplotlib.pyplot as plt

from src.data.polygon_client import PolygonClient
from src.models.svi_inputs import build_svi_inputs
from src.models.svi import calibrate_svi, svi_total_variance

TICKER = "SPY"
EXPIRATION_INDEX = 3  # índice dentro de las expiraciones disponibles (0 = la más próxima)


def main():
    client = PolygonClient()

    print(f"→ Consultando vencimientos disponibles de {TICKER}...")
    expirations = client.get_available_expirations(TICKER, EXPIRATION_INDEX + 1)
    expiration = expirations[EXPIRATION_INDEX]
    print(f"→ Vencimiento elegido: {expiration} (índice {EXPIRATION_INDEX})")

    print(f"→ Descargando cadena completa SOLO para {expiration}...")
    underlying = client.get_underlying_snapshot(TICKER)
    # Al filtrar por expiration_date en el servidor, evitamos que otras
    # fechas "roben" espacio del límite de contratos — aquí sí traemos
    # el rango completo de strikes (calls y puts) para este día exacto.
    chain = client.get_option_chain_snapshot(TICKER, expiration_date=expiration, max_contracts=1000)

    sub_chain = chain.filter_by_expiration(expiration)
    tte_years = sub_chain.contracts[0].time_to_expiration_years
    print(f"  Contratos descargados para este vencimiento: {len(sub_chain.contracts)}")

    forward = client.estimate_forward_price(
        spot_price=underlying.spot_price,
        time_to_expiration_years=tte_years,
    )
    print(f"→ Forward estimado: {forward:.2f}")

    print("→ Filtrando contratos líquidos y OTM...")
    inputs = build_svi_inputs(chain, expiration, forward, min_open_interest=5)
    print(f"  Puntos utilizables tras filtrado: {len(inputs.k)}")

    print("→ Calibrando SVI...")
    result = calibrate_svi(inputs.k, inputs.w, weights=inputs.open_interest)

    print("\n=== Resultado de calibración ===")
    print(f"  a     = {result.params.a:.6f}")
    print(f"  b     = {result.params.b:.6f}")
    print(f"  rho   = {result.params.rho:.6f}")
    print(f"  m     = {result.params.m:.6f}")
    print(f"  sigma = {result.params.sigma:.6f}")
    print(f"  RMSE (varianza total) = {result.rmse:.6f}")
    print(f"  Convergió: {result.converged}")
    print(f"  Libre de arbitraje butterfly: {result.butterfly_arbitrage_free} "
          f"(min g(k) = {result.min_g_value:.6f})")

    # --- Gráfica: puntos de mercado vs curva SVI ajustada ---
    k_grid = np.linspace(inputs.k.min() - 0.05, inputs.k.max() + 0.05, 300)
    w_fit = svi_total_variance(k_grid, *result.params.as_tuple())

    iv_market = np.sqrt(inputs.w / tte_years)
    iv_fit = np.sqrt(w_fit / tte_years)

    plt.figure(figsize=(10, 6))
    plt.scatter(inputs.k, iv_market, label="IV de mercado (OTM, filtrado)", color="tab:blue", zorder=3)
    plt.plot(k_grid, iv_fit, label="Curva SVI calibrada", color="tab:red", linewidth=2)
    plt.axvline(0, color="gray", linestyle="--", linewidth=1, label="ATM (k=0)")
    plt.xlabel("k = ln(K/F)")
    plt.ylabel("Volatilidad implícita")
    plt.title(f"{TICKER} — Smile SVI calibrado — Vencimiento {expiration}")
    plt.legend()
    plt.grid(alpha=0.3)

    output_path = "svi_fit.png"
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"\n📊 Gráfica guardada en: {output_path}")
    plt.show()


if __name__ == "__main__":
    main()
