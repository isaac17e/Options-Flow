"""
src/scripts/test_connection.py
--------------------------------
Prueba end-to-end del Bloque 1:
  1. Conecta con Polygon.
  2. Trae el spot del subyacente (Capital.com).
  3. Trae la cadena de opciones para el vencimiento más próximo.
  4. Estima el Forward vía costo de acarreo.
  5. Imprime un resumen limpio.

Ejecutar desde la raíz del proyecto:
    python -m src.scripts.test_connection
"""

import sys
import os

# Esto le indica a Python dónde está la raíz del proyecto, para que
# los imports (config, src.data...) funcionen sin importar desde
# dónde ejecutes el script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.polygon_client import PolygonClient

TICKER = "SPY"  # Cambia a "NVDA" si prefieres


def main():
    client = PolygonClient()

    print(f"→ Consultando spot de {TICKER}...")
    underlying = client.get_underlying_snapshot(TICKER)
    print(f"  Spot: {underlying.spot_price} @ {underlying.snapshot_time}")

    nearest_expiration = client.get_available_expirations(TICKER, 1)[0]
    print(f"→ Descargando cadena de {TICKER} para el vencimiento más próximo ({nearest_expiration})...")
    chain = client.get_option_chain_snapshot(
        TICKER, expiration_date=nearest_expiration, underlying=underlying
    )
    df = chain.to_dataframe()
    print(f"  Contratos descargados: {len(df)}")

    print(f"→ Estimando Forward para vencimiento {nearest_expiration}...")

    # Tiempo a expiración en años, tomado del primer contrato de esa fecha
    sub_chain = chain.filter_by_expiration(nearest_expiration)
    tte_years = sub_chain.contracts[0].time_to_expiration_years

    forward = client.estimate_forward_price(
        spot_price=underlying.spot_price,
        time_to_expiration_years=tte_years,
    )
    print(f"  Forward estimado: {forward:.2f}  (Spot: {underlying.spot_price})")

    print("\n--- Muestra de la cadena (primeras 10 filas) ---")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
