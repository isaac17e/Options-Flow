import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np
import matplotlib.pyplot as plt

from src.data.polygon_client import PolygonClient
from src.models.svi_inputs import build_svi_inputs
from src.models.svi import calibrate_svi
from src.models.pdf_extraction import extract_risk_neutral_pdf

TICKER = "SPY"
EXPIRATION_INDEX = 10  # ~30-45 días a vencimiento (más "cuerpo" en la distribución)


def main():
    client = PolygonClient()

    print(f"→ Consultando vencimientos disponibles de {TICKER}...")
    expirations = client.get_available_expirations(TICKER)
    expiration = expirations[EXPIRATION_INDEX]
    print(f"→ Vencimiento elegido: {expiration} (de {len(expirations)} disponibles)")

    print(f"→ Descargando cadena completa SOLO para {expiration}...")
    underlying = client.get_underlying_snapshot(TICKER)
    chain = client.get_option_chain_snapshot(TICKER, expiration_date=expiration, max_contracts=1000)

    sub_chain = chain.filter_by_expiration(expiration)
    tte_years = sub_chain.contracts[0].time_to_expiration_years
    print(f"  Contratos descargados: {len(sub_chain.contracts)}  |  Días a vencimiento: {sub_chain.contracts[0].days_to_expiration}")

    forward = client.estimate_forward_price(
        spot_price=underlying.spot_price,
        time_to_expiration_years=tte_years,
    )
    print(f"→ Forward estimado: {forward:.2f}  (Spot: {underlying.spot_price:.2f})")

    print("→ Filtrando contratos líquidos y OTM, calibrando SVI...")
    svi_inputs = build_svi_inputs(chain, expiration, forward, min_open_interest=5, k_min=-0.35, k_max=0.20)
    calibration = calibrate_svi(svi_inputs.k, svi_inputs.w, weights=svi_inputs.open_interest)
    print(f"  SVI: a={calibration.params.a:.5f} b={calibration.params.b:.5f} "
          f"rho={calibration.params.rho:.3f} m={calibration.params.m:.5f} sigma={calibration.params.sigma:.5f}")
    print(f"  RMSE: {calibration.rmse:.6f}  |  Libre de arbitraje: {calibration.butterfly_arbitrage_free}")

    print("→ Extrayendo PDF neutral al riesgo (Breeden-Litzenberger)...")
    pdf = extract_risk_neutral_pdf(
        svi_params=calibration.params,
        forward=forward,
        time_to_expiration_years=tte_years,
        k_range=(-0.6, 0.6),
        n_points=3000,
    )

    print(f"\n=== Mapa de Destinos: {TICKER} @ {expiration} ===")
    print(f"  Integral total (control, debe ser ~1.0): {pdf.total_probability:.4f}")
    print(f"  Media implícita: {pdf.mean:.2f}  (Forward: {forward:.2f})")
    print(f"  Desviación estándar implícita: {pdf.std:.2f}")
    print(f"  Percentil  5%: {pdf.quantile(0.05):.2f}")
    print(f"  Percentil 25%: {pdf.quantile(0.25):.2f}")
    print(f"  Percentil 50%: {pdf.quantile(0.50):.2f}")
    print(f"  Percentil 75%: {pdf.quantile(0.75):.2f}")
    print(f"  Percentil 95%: {pdf.quantile(0.95):.2f}")

    # --- Gráfica ---
    plt.figure(figsize=(11, 6))
    plt.plot(pdf.strikes, pdf.density, color="tab:purple", linewidth=2, label="PDF neutral al riesgo")
    plt.fill_between(pdf.strikes, pdf.density, alpha=0.15, color="tab:purple")
    plt.axvline(forward, color="black", linestyle="--", linewidth=1, label=f"Forward ({forward:.2f})")
    plt.axvline(underlying.spot_price, color="gray", linestyle=":", linewidth=1, label=f"Spot ({underlying.spot_price:.2f})")

    for q, label in [(0.05, "5%"), (0.95, "95%")]:
        level = pdf.quantile(q)
        plt.axvline(level, color="tab:red", linestyle="-.", linewidth=0.8, alpha=0.6)
        plt.text(level, plt.ylim()[1] * 0.92, label, color="tab:red", ha="center", fontsize=9)

    # Recortar el eje X a una región razonable (la grilla completa cubre colas extremas
    # de baja probabilidad que no aportan a la visualización).
    plt.xlim(pdf.quantile(0.01), pdf.quantile(0.99))

    plt.xlabel("Precio al vencimiento (S_T)")
    plt.ylabel("Densidad de probabilidad")
    plt.title(f"{TICKER} — Mapa de Destinos — Vencimiento {expiration}")
    plt.legend()
    plt.grid(alpha=0.3)

    output_path = "pdf_map.png"
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"\n📊 Gráfica guardada en: {output_path}")
    plt.show()


if __name__ == "__main__":
    main()
