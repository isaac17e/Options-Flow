# Quantitative Options Microstructure & 0DTE Analytics Hub

A collection of quantitative scripts and interactive dashboards for options market microstructure analysis, implied volatility modeling, risk-neutral probability density extraction, and real-time dealer exposure tracking.

---

## 🚀 Key Modules

### 🖥️ `app.py` — Quantitative Microstructure Dashboard (Streamlit)
Interactive web application designed to visualize market expectations and dealer positional mechanics:
* **Risk-Neutral Density (PDF)**: Calibrates the **SVI (Stochastic Volatility Inspired)** surface over option chains to extract clean, arbitrage-free risk-neutral probability distributions and price percentiles.
* **Dealer Exposure Profiling**: Aggregates multi-expiration **GEX** (Gamma Exposure), **VEX** (Vanna Exposure), and **CEX** (Charm Exposure) across strike prices to identify market regimes and key structural flip levels.

---

### ⚡ `0DTE_Analytics.py` — Intradía & 0DTE Institutional Flow Engine
Automated terminal script and continuous HTTP dashboard server for real-time option flow analysis:
* **Dealer Gamma & Flip Detection**: Calculates Black-Scholes-Merton (BSM) Greeks dynamically to pinpoint the **Zero Gamma Level**, Call Walls (resistance), and Put Walls (support).
* **Order Flow & Smart Money Tracking**: Evaluates intraday Volume/OI ratios and premium positioning to classify order flows (New Position, Unwinding, Rotation) and detect institutional activity.
* **Consensus & Auto-Refresh**: Generates dynamic directional consensus scores and auto-serves an inline, self-updating Plotly dashboard via a lightweight local HTTP server.

---

## 🛠️ Data & Infrastructure
* **Data Provider**: Powered by [Polygon.io API](https://polygon.io/) (supports delayed and real-time options chain snapshots).
* **Robust Fallbacks**: Built-in Bisection BSM Inverse Volatility solver for missing IV data and multi-tier spot price resolution (underlying close, live snapshot, or zero-discount Put-Call Parity estimation).
