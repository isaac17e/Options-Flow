import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np

from src.data.polygon_client import PolygonClient
from src.models.svi_inputs import build_svi_inputs

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
    w = svi_inputs.w
    strikes = svi_inputs.strikes
    oi = svi_inputs.open_interest
    iv = np.sqrt(w / tte_years)

    # Ordenar por k y calcular la variación de IV punto a punto para detectar saltos
    order = np.argsort(k)
    k_sorted = k[order]
    iv_sorted = iv[order]
    strikes_sorted = strikes[order]
    oi_sorted = oi[order]

    iv_diff = np.diff(iv_sorted)

    print(f"{'strike':>8} {'k':>8} {'IV':>8} {'OI':>8} {'ΔIV vs anterior':>16}")
    print("-" * 55)
    for i in range(len(k_sorted)):
        delta_str = f"{iv_diff[i-1]:+.4f}" if i > 0 else "   ---"
        marker = "  <<< SALTO GRANDE" if i > 0 and abs(iv_diff[i-1]) > 0.03 else ""
        print(f"{strikes_sorted[i]:8.1f} {k_sorted[i]:8.4f} {iv_sorted[i]:8.4f} {oi_sorted[i]:8.0f} {delta_str:>16}{marker}")


if __name__ == "__main__":
    main()
