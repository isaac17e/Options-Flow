import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import matplotlib.pyplot as plt

from src.data.polygon_client import PolygonClient
from src.data.models import OptionChainSnapshot
from src.models.dealer_exposure import aggregate_dealer_exposure, find_gamma_flip

TICKER = "SPY"
N_EXPIRATIONS = 6


def main():
    client = PolygonClient()

    expirations = client.get_available_expirations(TICKER, N_EXPIRATIONS)
    target_expirations = expirations[:N_EXPIRATIONS]
    underlying = client.get_underlying_snapshot(TICKER)

    all_contracts = []
    for exp in target_expirations:
        chain = client.get_option_chain_snapshot(TICKER, expiration_date=exp, max_contracts=1000)
        all_contracts.extend(chain.filter_by_expiration(exp).contracts)
        print(f"  {exp}: {len(chain.filter_by_expiration(exp).contracts)} contratos")

    full_chain = OptionChainSnapshot(underlying=underlying, contracts=all_contracts)

    profile = aggregate_dealer_exposure(full_chain, spot_price=underlying.spot_price)
    flip = find_gamma_flip(profile, underlying.spot_price)

    print(f"\nSpot: {underlying.spot_price:.2f}")
    print(f"Gamma flip: {flip:.2f}")
    print(f"GEX total: {profile['GEX'].sum():,.0f}")
    print(f"VEX total: {profile['VEX'].sum():,.0f}")
    print(f"CEX total: {profile['CEX'].sum():,.0f}")

    window = profile[(profile["strike"] > underlying.spot_price * 0.85) &
                      (profile["strike"] < underlying.spot_price * 1.15)]

    fig, axes = plt.subplots(3, 1, figsize=(12, 12), sharex=True)

    colors_gex = ["tab:green" if v >= 0 else "tab:red" for v in window["GEX"]]
    axes[0].bar(window["strike"], window["GEX"], width=3, color=colors_gex)
    axes[0].axvline(underlying.spot_price, color="black", linestyle="--", label=f"Spot ({underlying.spot_price:.2f})")
    axes[0].axvline(flip, color="blue", linestyle=":", label=f"Gamma Flip ({flip:.2f})")
    axes[0].set_title(f"{TICKER} — GEX por strike")
    axes[0].legend()
    axes[0].grid(alpha=0.3)

    colors_vex = ["tab:blue" if v >= 0 else "tab:orange" for v in window["VEX"]]
    axes[1].bar(window["strike"], window["VEX"], width=3, color=colors_vex)
    axes[1].axvline(underlying.spot_price, color="black", linestyle="--")
    axes[1].set_title(f"{TICKER} — VEX por strike")
    axes[1].grid(alpha=0.3)

    colors_cex = ["tab:purple" if v >= 0 else "tab:brown" for v in window["CEX"]]
    axes[2].bar(window["strike"], window["CEX"], width=3, color=colors_cex)
    axes[2].axvline(underlying.spot_price, color="black", linestyle="--")
    axes[2].set_title(f"{TICKER} — CEX por strike")
    axes[2].set_xlabel("Strike")
    axes[2].grid(alpha=0.3)

    plt.tight_layout()
    output_path = "dealer_exposure.png"
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"\nGráfica guardada en: {output_path}")
    plt.show()


if __name__ == "__main__":
    main()
