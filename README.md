# Options Flow — Quantitative Options Microstructure

A Python project that turns the live options chain from **Polygon.io** into two views of market positioning, shown side by side in a **Streamlit** dashboard:

1. **Risk-neutral probability map**: a no-arbitrage SVI volatility smile, converted into the market-implied distribution of the price at expiry (Breeden-Litzenberger).
2. **Dealer force vectors**: dealers' net Gamma, Vanna and Charm exposure by strike (GEX / VEX / CEX), plus the gamma flip level that separates stabilizing and accelerating regimes.

> Code comments, UI labels and console output are in Spanish.

---

## How it works

The project was built in five blocks, each with its own module and test script.

### Block 1 · Data (`src/data/`)
- `polygon_client.py`: REST client for Polygon.io with retries and transparent pagination.
  - Spot price delegated to `capital_client.py` (the Polygon plan has no same-day stock bars).
  - Retries on HTTP 429 (honoring `Retry-After`) and transient 5xx, with exponential backoff and jitter.
  - The nearest N expirations from the contracts reference endpoint (stops paging once N are found).
  - Full options chain snapshot (IV, Greeks, open interest) for each expiration.
  - Forward price by cost of carry, `F = S·e^((r−q)T)`. Put-call parity isn't used because the data plan has no option bid/ask.
- `capital_client.py`: real-time spot from Capital.com, mid of bid/offer from `GET /markets/{epic}`.
- `models.py`: `OptionContract`, `OptionChainSnapshot` and `UnderlyingSnapshot` data classes.

### Block 2 · SVI smile calibration (`src/models/svi.py`, `svi_inputs.py`)
- Filters each expiration to liquid out-of-the-money options (open interest ≥ 5, sensible IV) and converts them to log-moneyness `k = ln(K/F)` and total variance `w = σ²T`.
- Fits Gatheral's raw SVI, `w(k) = a + b·[ρ(k−m) + √((k−m)² + σ²)]`, with SLSQP. The fit:
  - weights points by open interest;
  - tries several starting points;
  - imposes **butterfly no-arbitrage** constraints (Gatheral's `g(k) ≥ 0`) over the data range plus a margin.
- Returns the range of `k` where no-arbitrage was verified, so later steps never extrapolate outside it.

### Block 3 · Risk-neutral density (`src/models/black76.py`, `pdf_extraction.py`)
- Prices calls with **Black-76** on a fine strike grid, using the SVI-smoothed volatility.
- Takes the second derivative with respect to the strike (finite differences on a non-uniform grid): `f(K) = e^(rT)·∂²C/∂K²`.
- Reports the density's mean, standard deviation, total probability and quantiles.

### Block 4 · Dealer exposure (`src/models/greeks_black76.py`, `dealer_exposure.py`)
- Gamma comes from Polygon. **Vanna** and **Charm** are not included in the data plan, so they are computed with Black-76 by finite differences on delta. Validated with the symmetry of cross-derivatives.
- Uses the standard industry convention: dealers are long calls and short puts.

  ```
  GEX = sign × Gamma × OI × 100 × Spot² × 0.01
  VEX = sign × Vanna × OI × 100 × Spot × 0.01
  CEX = sign × Charm × OI × 100 × Spot
  ```

- Aggregates by strike across several expirations and locates the **gamma flip**: the zero crossing of the GEX profile closest to spot.

### Block 5 · Dashboard (`app.py`)
A Streamlit app with a sidebar to choose the ticker (default `SPY`), the expiration used for the density, how many expirations enter the dealer profile, and the dividend yield. It shows:
- **Left column**: the risk-neutral density with forward and spot markers, its mean, standard deviation, percentiles and an arbitrage-free flag.
- **Right column**: GEX, VEX and CEX bars by strike (±15% around spot), the current gamma regime and the gamma flip level.

Data is cached for 60 seconds.

---

## Project structure

```
.
├── app.py                    # Streamlit dashboard (Block 5)
├── config.py                 # Central settings: API key, risk-free rate, dividend yield
├── .env.template             # Template for the .env file with the API key
├── requirements.txt          # Runtime dependencies
├── requirements-dev.txt      # + pytest
├── pytest.ini
├── src/
│   ├── data/
│   │   ├── models.py         # Option and underlying data classes
│   │   ├── capital_client.py # Capital.com spot price client
│   │   └── polygon_client.py # Polygon.io REST client
│   ├── models/
│   │   ├── svi.py            # SVI calibration + no-arbitrage check
│   │   ├── svi_inputs.py     # Liquidity filters and (k, w) transform
│   │   ├── black76.py        # Black-76 pricing
│   │   ├── pdf_extraction.py # Breeden-Litzenberger density
│   │   ├── greeks_black76.py # Delta, Gamma, Vanna, Charm
│   │   └── dealer_exposure.py# GEX / VEX / CEX aggregation and gamma flip
│   └── scripts/              # Block-by-block check and diagnostic scripts
└── tests/                    # pytest tests for SVI and density extraction
```

`quant_microstructure_final.zip` is an archived snapshot of the project.

## Setup

Python 3.10 or later.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt        # or requirements-dev.txt to also run tests

cp .env.template .env                  # then set POLYGON_API_KEY and the CAPITAL_* credentials in .env
```

`config.py` loads the keys from `.env` and stops with a clear error if `POLYGON_API_KEY` is missing. The Capital.com credentials (`CAPITAL_API_KEY`, `CAPITAL_IDENTIFIER`, `CAPITAL_API_PASSWORD`, plus optional `CAPITAL_API_URL` and `CAPITAL_EPIC`) are checked when the spot is first requested. The default risk-free rate (4.5%) and dividend yield (1.3%) are also defined there.

## Usage

Launch the dashboard:

```bash
python3 -m streamlit run app.py
```

Check scripts, one per block:

```bash
python3 src/scripts/test_connection.py        # Block 1: Polygon connection
python3 src/scripts/test_svi.py               # Block 2: SVI calibration
python3 src/scripts/test_pdf.py               # Block 3: risk-neutral density
python3 src/scripts/test_greeks_black76.py    # Block 4: Greeks engine
python3 src/scripts/test_dealer_exposure.py   # Block 4: full dealer profile + chart
```

Diagnostic scripts:

```bash
python3 src/scripts/diagnose_arbitrage.py     # plots g(k) and finds arbitrage gaps
python3 src/scripts/explore_svi_landscape.py  # compares several SVI starting points
python3 src/scripts/inspect_raw_data.py       # prints the raw chain (strike, IV, OI)
```

Unit tests:

```bash
pytest
```

## Notes

- With a delayed Polygon plan, options data lags the market by **about 15 minutes**. The spot comes from Capital.com in real time.
- Use `python3 -m streamlit` so the app runs in the same virtual environment as the dependencies.
- If you rotate your Polygon API key, only `.env` needs to change.

## Disclaimer

This code is for research and educational purposes only and does not constitute investment advice. The delayed data is not suitable for high-frequency execution.
