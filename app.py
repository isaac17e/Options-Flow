import streamlit as st
import plotly.graph_objects as go

from config import SETTINGS
from src.data.polygon_client import PolygonClient
from src.data.models import OptionChainSnapshot
from src.models.svi_inputs import build_svi_inputs
from src.models.svi import calibrate_svi
from src.models.pdf_extraction import extract_risk_neutral_pdf
from src.models.dealer_exposure import aggregate_dealer_exposure, find_gamma_flip

st.set_page_config(page_title="Microestructura Cuantitativa", layout="wide")

st.title("Dashboard de Microestructura Cuantitativa")
st.caption(
    "⚠️ Opciones con retraso de ~15 minutos (plan Polygon.io delayed); spot en tiempo real "
    "de Capital.com — no aptos para ejecución de alta frecuencia"
)


@st.cache_resource
def get_client():
    return PolygonClient()


client = get_client()

with st.sidebar:
    ticker = st.text_input("Ticker", value="SPY").upper()
    expiration_index_pdf = st.slider("Vencimiento para PDF (índice)", 0, 30, 10)
    n_expirations_dealer = st.slider("Vencimientos para GEX/VEX/CEX", 2, 10, 6)
    dividend_yield_pct = st.number_input(
        "Dividend yield anual (%)",
        min_value=0.0, max_value=15.0,
        value=SETTINGS.default_dividend_yield * 100,
        step=0.1,
        help="Usado en F = S·e^((r-q)T). Un yield ignorado sesga el forward "
             "al alza y con él el centro del smile, la media del PDF y el gamma flip.",
    )
    dividend_yield = dividend_yield_pct / 100


@st.cache_data(ttl=60)
def load_underlying(_client, ticker):
    return _client.get_underlying_snapshot(ticker)


@st.cache_data(ttl=60)
def load_expirations(_client, ticker, max_expirations):
    return _client.get_available_expirations(ticker, max_expirations)


@st.cache_data(ttl=60)
def load_chain_for_expiration(_client, ticker, expiration, _underlying):
    return _client.get_option_chain_snapshot(ticker, expiration_date=expiration, underlying=_underlying)


@st.cache_data(ttl=60)
def load_dealer_chain(_client, ticker, expirations_tuple, _underlying):
    all_contracts = []
    for exp in expirations_tuple:
        chain = _client.get_option_chain_snapshot(ticker, expiration_date=exp, underlying=_underlying)
        all_contracts.extend(chain.filter_by_expiration(exp).contracts)
    return OptionChainSnapshot(underlying=_underlying, contracts=all_contracts)


try:
    underlying = load_underlying(client, ticker)
except Exception as e:
    st.error(f"Error obteniendo spot de Capital.com: {e}")
    st.stop()

st.metric("Spot", f"{underlying.spot_price:.2f}")
st.caption(
    f"Spot = punto medio bid/offer de Capital.com, actualizado: "
    f"{underlying.snapshot_time.strftime('%Y-%m-%d %H:%M:%S')}"
)

# Solo se piden los vencimientos que se van a usar (PDF y dealers).
expirations = load_expirations(client, ticker, max(expiration_index_pdf + 1, n_expirations_dealer))

if not expirations:
    st.error(f"No se encontraron vencimientos de opciones para {ticker}. Verifica el ticker.")
    st.stop()

col1, col2 = st.columns(2)

with col1:
    st.subheader("Mapa de Destinos (PDF neutral al riesgo)")
    idx = min(expiration_index_pdf, len(expirations) - 1)
    expiration_choice = expirations[idx]
    st.write(f"Vencimiento: **{expiration_choice}**")

    try:
        chain = load_chain_for_expiration(client, ticker, expiration_choice, underlying)
        sub_chain = chain.filter_by_expiration(expiration_choice)
        tte_years = sub_chain.contracts[0].time_to_expiration_years
        forward = client.estimate_forward_price(underlying.spot_price, tte_years, dividend_yield=dividend_yield)

        svi_inputs = build_svi_inputs(
            chain, expiration_choice, forward, min_open_interest=5, k_min=-0.35, k_max=0.20
        )
        calibration = calibrate_svi(svi_inputs.k, svi_inputs.w, weights=svi_inputs.open_interest)
        # El PDF solo se pide dentro de calibration.verified_k_range: es el único
        # rango de k donde efectivamente se comprobó ausencia de arbitraje butterfly
        # (ver src/models/svi.py). Pedir más ancho que eso sería extrapolar la
        # curva SVI a ciegas y recortar densidad negativa sin aviso.
        pdf = extract_risk_neutral_pdf(
            calibration.params, forward, tte_years, k_range=calibration.verified_k_range, n_points=2000
        )

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=pdf.strikes, y=pdf.density, fill="tozeroy", name="PDF", line=dict(color="purple")))
        fig.add_vline(x=forward, line_dash="dash", line_color="black", annotation_text="Forward")
        fig.add_vline(x=underlying.spot_price, line_dash="dot", line_color="gray", annotation_text="Spot")
        fig.update_layout(xaxis_title="Precio al vencimiento", yaxis_title="Densidad", height=450)
        fig.update_xaxes(range=[pdf.quantile(0.02), pdf.quantile(0.98)])
        st.plotly_chart(fig, width='stretch')

        c1, c2, c3 = st.columns(3)
        c1.metric("Media", f"{pdf.mean:.2f}")
        c2.metric("Desv. estándar", f"{pdf.std:.2f}")
        c3.metric("Libre de arbitraje", "Sí" if calibration.butterfly_arbitrage_free else "No")

        st.write(
            f"Percentiles — 5%: {pdf.quantile(0.05):.2f} | 25%: {pdf.quantile(0.25):.2f} | "
            f"50%: {pdf.quantile(0.50):.2f} | 75%: {pdf.quantile(0.75):.2f} | 95%: {pdf.quantile(0.95):.2f}"
        )
    except Exception as e:
        st.error(f"No se pudo calibrar este vencimiento: {e}")

with col2:
    st.subheader("Vectores de Fuerza de los Dealers")

    try:
        expirations_tuple = tuple(expirations[:n_expirations_dealer])
        dealer_chain = load_dealer_chain(client, ticker, expirations_tuple, underlying)
        profile = aggregate_dealer_exposure(dealer_chain, underlying.spot_price, dividend_yield=dividend_yield)
        flip = find_gamma_flip(profile, underlying.spot_price)

        window = profile[
            (profile["strike"] > underlying.spot_price * 0.85)
            & (profile["strike"] < underlying.spot_price * 1.15)
        ]

        fig_gex = go.Figure()
        colors_gex = ["green" if v >= 0 else "red" for v in window["GEX"]]
        fig_gex.add_trace(go.Bar(x=window["strike"], y=window["GEX"], marker_color=colors_gex, name="GEX"))
        fig_gex.add_vline(x=underlying.spot_price, line_dash="dash", line_color="black")
        fig_gex.add_vline(x=flip, line_dash="dot", line_color="blue")
        fig_gex.update_layout(title="GEX por strike", height=280)
        st.plotly_chart(fig_gex, width='stretch')

        regime = "Gamma Positivo (amortiguador)" if underlying.spot_price > flip else "Gamma Negativo (acelerador)"
        c1, c2 = st.columns(2)
        c1.metric("Régimen actual", regime)
        c2.metric("Gamma Flip", f"{flip:.2f}")

        fig_vex = go.Figure()
        colors_vex = ["steelblue" if v >= 0 else "orange" for v in window["VEX"]]
        fig_vex.add_trace(go.Bar(x=window["strike"], y=window["VEX"], marker_color=colors_vex, name="VEX"))
        fig_vex.add_vline(x=underlying.spot_price, line_dash="dash", line_color="black")
        fig_vex.update_layout(title="VEX por strike", height=230)
        st.plotly_chart(fig_vex, width='stretch')

        fig_cex = go.Figure()
        colors_cex = ["purple" if v >= 0 else "brown" for v in window["CEX"]]
        fig_cex.add_trace(go.Bar(x=window["strike"], y=window["CEX"], marker_color=colors_cex, name="CEX"))
        fig_cex.add_vline(x=underlying.spot_price, line_dash="dash", line_color="black")
        fig_cex.update_layout(title="CEX por strike", height=230)
        st.plotly_chart(fig_cex, width='stretch')

    except Exception as e:
        st.error(f"Error calculando exposición de dealers: {e}")

st.caption(
    "Los datos se guardan en caché 60 s: al recargar la página o cambiar un parámetro pasado "
    "ese tiempo se vuelven a pedir. Las opciones van ~15 min por detrás del mercado."
)
