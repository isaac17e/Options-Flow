import requests
import pandas as pd
import numpy as np
import math
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import warnings
import time
import sys
import os
import threading
import http.server
import socketserver
import webbrowser

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
#  CONFIGURACIÓN POLYGON.IO
# ─────────────────────────────────────────────

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "").strip()
POLYGON_BASE_URL = "https://api.polygon.io"


def _polygon_get(path, params=None, full_url=None):
    """
    Hace un GET contra la API de Polygon. Si se pasa full_url (p.ej. el
    'next_url' de una respuesta paginada), lo usa directamente y solo
    añade el apiKey. Lanza excepción si la respuesta no es 200.
    """
    params = dict(params or {})
    params["apiKey"] = POLYGON_API_KEY
    url = full_url if full_url else f"{POLYGON_BASE_URL}{path}"
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") not in ("OK", "DELAYED"):
        raise RuntimeError(f"Polygon respondió status={data.get('status')}: {data.get('error', data)}")
    return data


def _polygon_get_all_pages(path, params=None, max_pages=20):
    """
    Sigue next_url hasta agotar páginas o llegar a max_pages.
    Devuelve la lista concatenada de 'results'.
    """
    resultados = []
    data = _polygon_get(path, params=params)
    resultados.extend(data.get("results", []) or [])
    paginas = 1
    while data.get("next_url") and paginas < max_pages:
        data = _polygon_get(None, full_url=data["next_url"])
        resultados.extend(data.get("results", []) or [])
        paginas += 1
    return resultados

# ─────────────────────────────────────────────
#  MATH HELPERS
# ─────────────────────────────────────────────

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ─────────────────────────────────────────────
#  PRECIO SPOT — fallback agresivo
# ─────────────────────────────────────────────

def _precio_via_prev_close(ticker):
    """
    /v2/aggs/ticker/{ticker}/prev es parte del nivel gratuito de datos de
    acciones que trae CUALQUIER cuenta de Polygon (5 llamadas/min, EOD),
    incluso si solo pagas el plan de opciones. Es la fuente más confiable
    de precio spot cuando no tienes un plan de stocks de pago.
    """
    data = _polygon_get(f"/v2/aggs/ticker/{ticker.upper()}/prev", params={"adjusted": "true"})
    resultados = data.get("results", []) or []
    if resultados and resultados[0].get("c"):
        return float(resultados[0]["c"])
    return None


def _precio_via_underlying_asset(ticker):
    """
    underlying_asset.price viene incluido en el snapshot de opciones,
    pero SOLO si tu cuenta también tiene un plan de acciones de pago.
    Con solo el plan de opciones Starter, este campo normalmente viene
    vacío — se deja como bonus/fallback, no como fuente principal.
    """
    data = _polygon_get(f"/v3/snapshot/options/{ticker.upper()}", params={"limit": 1})
    resultados = data.get("results", []) or []
    if not resultados:
        return None
    val = resultados[0].get("underlying_asset", {}).get("price")
    return float(val) if val else None


def _precio_via_paridad_put_call(ticker):
    """
    Último recurso: si no hay acceso a NINGÚN dato de acciones, se estima
    el spot con paridad put-call sobre el vencimiento 0DTE/más próximo:
        S ≈ K + C - P   (aproximación sin descuento, válida para el corto plazo)
    usando el strike donde call y put tienen precios más parecidos (ATM).
    """
    try:
        exp, _ = seleccionar_vencimiento_0dte(ticker, silencioso=True)
        if not exp:
            return None
        snap = _polygon_get_all_pages(
            f"/v3/snapshot/options/{ticker.upper()}",
            params={"expiration_date": exp, "limit": 250},
        )
        calls = _snapshot_a_dataframe(snap, "call")
        puts  = _snapshot_a_dataframe(snap, "put")
        if calls.empty or puts.empty:
            return None

        merged = pd.merge(calls[["strike", "lastPrice"]], puts[["strike", "lastPrice"]],
                           on="strike", suffixes=("_c", "_p")).dropna()
        if merged.empty:
            return None
        merged["diff"] = (merged["lastPrice_c"] - merged["lastPrice_p"]).abs()
        fila = merged.loc[merged["diff"].idxmin()]
        S_est = fila["strike"] + fila["lastPrice_c"] - fila["lastPrice_p"]
        return float(S_est) if S_est > 0 else None
    except Exception:
        return None


def obtener_precio(ticker):
    """
    Intenta varias fuentes en orden de fiabilidad para obtener el spot,
    porque el campo underlying_asset.price requiere plan de acciones y
    con el plan Starter de OPCIONES solamente suele venir vacío.
    """
    metodos = [
        ("prev close (nivel gratuito de acciones)", _precio_via_prev_close),
        ("underlying_asset.price (snapshot de opciones)", _precio_via_underlying_asset),
        ("paridad put-call (estimado desde la cadena 0DTE)", _precio_via_paridad_put_call),
    ]
    for nombre, fn in metodos:
        try:
            val = fn(ticker)
            if val and val > 0:
                print(f"💰 Precio obtenido: ${val:.2f}  (vía {nombre})")
                return val
        except Exception as e:
            print(f"   ⚠️  {nombre} falló: {e}")

    print("❌ Todos los métodos de precio fallaron.")
    return None


# ─────────────────────────────────────────────
#  BSM GREEKS
# ─────────────────────────────────────────────

def bsm_greeks(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0.0, 0.0, 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    delta_call = norm_cdf(d1)
    delta_put  = norm_cdf(d1) - 1
    gamma      = norm_pdf(d1) / (S * sigma * np.sqrt(T))
    return delta_call, delta_put, gamma


def calcular_greeks_cadena(df, S, T, r=0.045, tipo="call"):
    results = df.apply(
        lambda row: bsm_greeks(S, row["strike"], T, r, row["impliedVolatility"])
        if pd.notna(row["impliedVolatility"]) and row["impliedVolatility"] > 0
        else (0.0, 0.0, 0.0),
        axis=1,
        result_type="expand"
    )
    results.columns = ["delta_call_raw", "delta_put_raw", "gamma_bsm"]
    df = df.copy()
    df["delta"]     = results["delta_call_raw"] if tipo == "call" else results["delta_put_raw"]
    df["gamma_bsm"] = results["gamma_bsm"]
    signo = 1 if tipo == "call" else -1
    df["gex"] = signo * df["openInterest"] * df["gamma_bsm"] * 100 * S
    return df


# ─────────────────────────────────────────────
#  IV IMPLÍCITA — FALLBACK BSM INVERSO
# ─────────────────────────────────────────────
#
# El campo implied_volatility del snapshot de Polygon lo calcula su motor
# de greeks en vivo, y puede venir vacío para TODA la cadena cuando no hay
# NBBO en tiempo real (fin de semana, feriado, o directamente para ciertos
# subyacentes/planes como opciones de índice). Sin IV no hay gamma BSM ni
# GEX. En vez de abortar el análisis, si Polygon no trae IV pero sí hay un
# lastPrice (último trade o el cierre del día anterior), se infiere la IV
# invirtiendo Black-Scholes por bisección sobre ese precio.

def _bs_price(S, K, T, r, sigma, tipo):
    if T <= 0 or sigma <= 0 or K <= 0:
        return max(0.0, (S - K) if tipo == "call" else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if tipo == "call":
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def _implied_vol_bisection(price, S, K, T, r, tipo, tol=1e-4, max_iter=60):
    """
    Resuelve sigma tal que el precio BSM coincida con `price`, por
    bisección en [0.0001, 5.0] (0.01% a 500% de vol anualizada).
    Devuelve None si el precio está fuera del rango posible (arbitraje,
    dato corrupto, o strike/precio incoherentes).
    """
    if price is None or price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    lo, hi = 1e-4, 5.0
    p_lo, p_hi = _bs_price(S, K, T, r, lo, tipo), _bs_price(S, K, T, r, hi, tipo)
    if price < p_lo - tol or price > p_hi + tol:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        p_mid = _bs_price(S, K, T, r, mid, tipo)
        if abs(p_mid - price) < tol:
            return mid
        if p_mid < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def inferir_iv_faltante(df, S, T, r, tipo):
    """
    Rellena impliedVolatility con IV inferida (BSM inverso) en las filas
    donde Polygon no la trajo pero sí hay lastPrice utilizable. Devuelve
    (df_actualizado, n_inferidas).
    """
    df = df.copy()
    df["impliedVolatility"] = df["impliedVolatility"].astype(float)
    mask = (df["impliedVolatility"] <= 0) & (df["lastPrice"] > 0) & (df["strike"] > 0)
    n = int(mask.sum())
    if n == 0:
        return df, 0

    df.loc[mask, "impliedVolatility"] = df.loc[mask].apply(
        lambda row: _implied_vol_bisection(row["lastPrice"], S, row["strike"], T, r, tipo) or 0.0,
        axis=1
    )
    return df, n


def clasificar_flujo(df, tipo="call"):
    """
    Clasifica cada strike según el ratio Volumen/OI (la métrica reina en
    0DTE, porque el OI de la mañana es del día anterior y no refleja la
    actividad intradía). Si el mercado está cerrado y no hay volumen,
    cae de respaldo al percentil de OI.
    """
    df = df.copy()

    oi_p75 = df["openInterest"].quantile(0.75) if df["openInterest"].sum() > 0 else 0
    modo_oi = df["volume"].sum() == 0 and df["openInterest"].sum() > 0

    def tipo_flujo(row):
        vol = row.get("volume", 0)
        oi  = row.get("openInterest", 0)

        if modo_oi:
            # Mercado cerrado / sin actividad todavía: clasificar por OI acumulado
            if oi == 0:
                return "SIN DATOS", "NEUTRAL"
            if oi >= oi_p75:
                señal = "ALCISTA 🟢" if tipo == "call" else "BAJISTA 🔴"
                return "OI ALTO", señal
            else:
                return "OI BAJO", "NEUTRAL"

        # Mercado abierto: clasificar por ratio vol/OI (prioridad 0DTE)
        if oi == 0 and vol == 0:
            return "SIN DATOS", "NEUTRAL"
        ratio = vol / oi if oi > 0 else float("inf")
        if ratio > 1.5:
            señal = "ALCISTA 🟢" if tipo == "call" else "BAJISTA 🔴"
            return "APERTURA NUEVA", señal
        elif ratio > 0.5:
            señal = "BAJISTA ⚠️" if tipo == "call" else "ALCISTA ⚠️"
            return "CIERRE/ROTACIÓN", señal
        else:
            return "LIQUIDACIÓN", "NEUTRAL"

    flujos = df.apply(tipo_flujo, axis=1, result_type="expand")
    flujos.columns = ["tipo_flujo", "señal_direccional"]
    df["tipo_flujo"]        = flujos["tipo_flujo"]
    df["señal_direccional"] = flujos["señal_direccional"]
    return df


# ─────────────────────────────────────────────
#  SELECCIÓN DE VENCIMIENTO — SOLO 0DTE
# ─────────────────────────────────────────────

def seleccionar_vencimiento_0dte(ticker, silencioso=False):
    """
    Selecciona ÚNICAMENTE el vencimiento más próximo disponible:
    - Si hoy hay un contrato que vence hoy → ese es el 0DTE real.
    - Si no (fin de semana, feriado, o el ticker no lista 0DTE), cae al
      vencimiento más próximo (normalmente el día hábil siguiente).
    Devuelve (fecha_str, dias) o (None, None) si no hay nada disponible.
    """
    try:
        contratos = _polygon_get_all_pages(
            "/v3/reference/options/contracts",
            params={
                "underlying_ticker": ticker.upper(),
                "expired": "false",
                "order": "asc",
                "sort": "expiration_date",
                "limit": 1000,
            },
        )
    except Exception as e:
        if not silencioso:
            print(f"❌ No se pudieron obtener vencimientos desde Polygon: {e}")
        return None, None

    exps = sorted({c["expiration_date"] for c in contratos if c.get("expiration_date")})
    if not exps:
        if not silencioso:
            print("❌ La lista de vencimientos está vacía.")
        return None, None

    hoy = datetime.now().date()
    candidatos = []
    for e in exps:
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
            dias = (d - hoy).days
            if dias >= 0:      # incluye HOY → 0DTE real
                candidatos.append((e, dias))
        except Exception:
            continue

    if not candidatos:
        if not silencioso:
            print("❌ No hay vencimientos vigentes (ni siquiera 0DTE).")
        return None, None

    candidatos.sort(key=lambda x: x[1])
    exp, dias = candidatos[0]

    if not silencioso:
        if dias == 0:
            print(f"   🎯 Vencimiento 0DTE real detectado: {exp}")
        else:
            print(f"   📅 Sin 0DTE hoy — usando el vencimiento más próximo: {exp} ({dias}d)")

    return exp, dias


def _snapshot_a_dataframe(contratos, tipo):
    """
    Convierte la lista de resultados del snapshot de opciones de Polygon
    (ya filtrados por tipo call/put) en un DataFrame con las columnas que
    el resto del script espera.
    """
    filas = []
    for c in contratos:
        det   = c.get("details", {}) or {}
        if det.get("contract_type") != tipo:
            continue
        day   = c.get("day", {}) or {}
        greek = c.get("greeks", {}) or {}
        trade = c.get("last_trade", {}) or {}
        quote = c.get("last_quote", {}) or {}

        # lastPrice: preferir último trade; si no hay, punto medio bid/ask; si no, cierre del día
        last_price = trade.get("price")
        if not last_price:
            bid, ask = quote.get("bid"), quote.get("ask")
            if bid and ask:
                last_price = (bid + ask) / 2
        if not last_price:
            last_price = day.get("close")

        filas.append({
            "strike": det.get("strike_price"),
            "volume": day.get("volume"),
            "openInterest": c.get("open_interest"),
            "impliedVolatility": c.get("implied_volatility"),
            "lastPrice": last_price,
            "delta_polygon": greek.get("delta"),
            "gamma_polygon": greek.get("gamma"),
        })
    return pd.DataFrame(filas)


def obtener_cadena_0dte(ticker, vencimiento, S, dias, r=0.045):
    """
    Descarga y prepara la cadena completa (calls/puts) del vencimiento
    0DTE/más próximo: greeks BSM, GEX por contrato y clasificación de
    flujo (volumen/OI).
    """
    try:
        contratos = _polygon_get_all_pages(
            f"/v3/snapshot/options/{ticker.upper()}",
            params={"expiration_date": vencimiento, "limit": 250},
        )

        if not contratos:
            print(f"   ⚠️  Polygon no devolvió contratos para {vencimiento}")
            return None, None

        calls = _snapshot_a_dataframe(contratos, "call")
        puts  = _snapshot_a_dataframe(contratos, "put")

        print(f"   📦 Cadena recibida — calls: {len(calls)} filas | puts: {len(puts)} filas")

        cols_req = {"strike", "volume", "openInterest", "impliedVolatility", "lastPrice"}
        for nombre, df in [("calls", calls), ("puts", puts)]:
            faltantes = cols_req - set(df.columns)
            if faltantes:
                print(f"   ⚠️  {nombre}: faltan columnas {faltantes}")

        for col in ["volume", "openInterest", "impliedVolatility", "lastPrice"]:
            if col in calls.columns:
                calls[col] = pd.to_numeric(calls[col], errors="coerce").fillna(0)
            if col in puts.columns:
                puts[col]  = pd.to_numeric(puts[col],  errors="coerce").fillna(0)

        if calls.empty and puts.empty:
            print(f"   ⚠️  Cadena vacía para {vencimiento}")
            return None, None

        # ── Diagnóstico de calidad de datos ──────────────────────────
        oi_calls_total = calls["openInterest"].sum()
        oi_puts_total  = puts["openInterest"].sum()
        vol_calls_total = calls["volume"].sum()
        vol_puts_total  = puts["volume"].sum()
        iv_validas_calls = (calls["impliedVolatility"] > 0).sum()
        iv_validas_puts  = (puts["impliedVolatility"] > 0).sum()

        print(f"   📊 OI total  — calls: {oi_calls_total:,.0f} | puts: {oi_puts_total:,.0f}")
        print(f"   📊 Vol total (hoy) — calls: {vol_calls_total:,.0f} | puts: {vol_puts_total:,.0f}")
        print(f"   📊 IV > 0 (Polygon) — calls: {iv_validas_calls} filas | puts: {iv_validas_puts} filas")

        # 0DTE real → T mínimo (fracción de día) en vez de 1 día completo,
        # para no subestimar la gamma que domina al acercarse al cierre.
        T = max(dias / 365.0, 0.3 / 365.0)

        sin_iv_polygon = (iv_validas_calls + iv_validas_puts) == 0
        if sin_iv_polygon:
            # Polygon no calculó greeks para esta cadena (típico fuera de
            # horario, feriados, o con opciones de índice). Se infiere la
            # IV invirtiendo BSM sobre el último precio disponible en vez
            # de descartar el análisis por completo.
            calls, n_inf_c = inferir_iv_faltante(calls, S, T, r, "call")
            puts,  n_inf_p = inferir_iv_faltante(puts,  S, T, r, "put")
            print(f"   🔧 Polygon no trajo IV — inferida vía BSM inverso (fallback): "
                  f"calls {n_inf_c} | puts {n_inf_p}")
            if (n_inf_c + n_inf_p) == 0:
                print(f"   ⚠️  Tampoco hay lastPrice utilizable — no se puede inferir IV. Saltando.")
                return None, None

        calls = calcular_greeks_cadena(calls, S, T, r, tipo="call")
        puts  = calcular_greeks_cadena(puts,  S, T, r, tipo="put")
        calls = clasificar_flujo(calls, tipo="call")
        puts  = clasificar_flujo(puts,  tipo="put")

        return calls, puts

    except Exception as e:
        print(f"   ⚠️  Error obteniendo cadena para {vencimiento}: {e}")
        import traceback
        traceback.print_exc()
        return None, None


# ─────────────────────────────────────────────
#  ANÁLISIS
# ─────────────────────────────────────────────

def calcular_max_pain(calls, puts, S=None):
    """
    Max Pain sobre strikes con OI > 0 (o volumen si no hay OI), filtrado
    a un rango razonable alrededor del spot para evitar strikes exóticos.
    """
    calls_oi = calls[calls["openInterest"] > 0].copy()
    puts_oi  = puts[puts["openInterest"]  > 0].copy()

    strikes = sorted(set(calls_oi["strike"]) | set(puts_oi["strike"]))

    if not strikes:
        calls_oi = calls[calls["volume"] > 0].copy()
        puts_oi  = puts[puts["volume"]   > 0].copy()
        strikes  = sorted(set(calls_oi["strike"]) | set(puts_oi["strike"]))

    if not strikes:
        strikes = sorted(set(calls["strike"]) | set(puts["strike"]))
        calls_oi, puts_oi = calls, puts

    if not strikes:
        return S if S else 0.0

    if S:
        rango = [k for k in strikes if S * 0.60 <= k <= S * 1.40]
        if len(rango) >= 3:
            strikes = rango

    min_loss, max_pain = float("inf"), strikes[len(strikes)//2]
    for s in strikes:
        cl = (calls_oi[calls_oi["strike"] < s]["openInterest"] *
              (s - calls_oi[calls_oi["strike"] < s]["strike"])).sum()
        pl = (puts_oi[puts_oi["strike"]  > s]["openInterest"] *
              (puts_oi[puts_oi["strike"]  > s]["strike"] - s)).sum()
        total = cl + pl
        if total < min_loss:
            min_loss = total
            max_pain = s
    return max_pain


def calcular_net_gex(calls, puts):
    if "gex" not in calls.columns or "gex" not in puts.columns:
        return pd.DataFrame()
    gex = pd.concat([
        calls[["strike", "gex"]],
        puts[["strike", "gex"]]
    ]).groupby("strike")["gex"].sum().reset_index()
    gex.columns = ["strike", "net_gex"]
    return gex.sort_values("strike").reset_index(drop=True)


def calcular_zero_gamma_level(gex_df):
    """
    Encuentra el "Zero Gamma Level": el strike (interpolado) donde el Net
    GEX cruza de positivo a negativo (o viceversa). Es el punto de
    inflexión entre régimen Long Gamma (precio contenido) y Short Gamma
    (movimientos amplificados) — clave para leer inestabilidad 0DTE.
    Devuelve None si no hay cruce dentro del rango de strikes disponible.
    """
    if gex_df is None or gex_df.empty or len(gex_df) < 2:
        return None

    df = gex_df.sort_values("strike").reset_index(drop=True)
    for i in range(len(df) - 1):
        k1, g1 = df.loc[i, "strike"], df.loc[i, "net_gex"]
        k2, g2 = df.loc[i + 1, "strike"], df.loc[i + 1, "net_gex"]
        if g1 == 0:
            return float(k1)
        if (g1 < 0) != (g2 < 0):
            frac = -g1 / (g2 - g1) if (g2 - g1) != 0 else 0
            return float(k1 + frac * (k2 - k1))
    return None


def identificar_muros_gex(gex_df):
    """
    Muro de Calls (resistencia): strike con el Net GEX positivo más alto.
    Muro de Puts (soporte): strike con el Net GEX negativo más profundo.
    """
    if gex_df is None or gex_df.empty:
        return None, None
    positivos = gex_df[gex_df["net_gex"] > 0]
    negativos = gex_df[gex_df["net_gex"] < 0]
    call_wall = float(positivos.loc[positivos["net_gex"].idxmax(), "strike"]) if not positivos.empty else None
    put_wall  = float(negativos.loc[negativos["net_gex"].idxmin(), "strike"]) if not negativos.empty else None
    return call_wall, put_wall


def posicionamiento_dealers_real(calls, puts):
    if "gex" not in calls.columns:
        return "SIN DATOS", 0, "No calculado"
    net = calls["gex"].sum() + puts["gex"].sum()
    if net > 5e8:
        status, comp = "LONG GAMMA FUERTE", "Reversiones rápidas, precio contenido"
    elif net > 0:
        status, comp = "LONG GAMMA DÉBIL", "Estabilización leve, puede rotar"
    elif net > -5e8:
        status, comp = "SHORT GAMMA DÉBIL", "Amplificación moderada de movimientos"
    else:
        status, comp = "SHORT GAMMA FUERTE", "Volatilidad explosiva, breakouts amplificados"
    return status, net, comp


def ratio_pc_enriquecido(calls, puts):
    merged = pd.merge(
        calls[["strike", "volume", "openInterest", "tipo_flujo", "señal_direccional"]],
        puts[["strike", "volume", "openInterest", "tipo_flujo", "señal_direccional"]],
        on="strike", suffixes=("_call", "_put"), how="outer"
    ).fillna(0)

    vol_total_sum = merged["volume_call"].sum() + merged["volume_put"].sum()
    oi_total_sum  = merged["openInterest_call"].sum() + merged["openInterest_put"].sum()

    # 0DTE: priorizar SIEMPRE el volumen del día; el OI solo como respaldo si el mercado está cerrado.
    if vol_total_sum > 0:
        merged["activity_call"] = merged["volume_call"]
        merged["activity_put"]  = merged["volume_put"]
        label_ratio = "pc_vol"
    elif oi_total_sum > 0:
        merged["activity_call"] = merged["openInterest_call"]
        merged["activity_put"]  = merged["openInterest_put"]
        label_ratio = "pc_oi"
    else:
        return pd.DataFrame()

    merged["pc_ratio"]       = merged["activity_put"] / merged["activity_call"].replace(0, 1)
    merged["activity_total"] = merged["activity_call"] + merged["activity_put"]
    merged = merged[merged["activity_total"] > merged["activity_total"].quantile(0.7)]
    merged["sentimiento"] = merged["pc_ratio"].apply(
        lambda x: "BAJISTA 🔴" if x > 1.5 else ("ALCISTA 🟢" if x < 0.7 else "NEUTRAL ⚖️")
    )
    merged = merged.rename(columns={"pc_ratio": label_ratio})
    return merged.sort_values("activity_total", ascending=False)[
        [label_ratio, "strike", "sentimiento", "tipo_flujo_call", "señal_direccional_call",
         "tipo_flujo_put", "señal_direccional_put"]
    ].head(10)


def detectar_smart_money(calls, puts):
    def filtrar(df, tipo):
        d = df.copy()
        d["iv_pct"] = d["impliedVolatility"].rank(pct=True)

        uso_oi = d["volume"].sum() == 0 and d["openInterest"].sum() > 0
        if uso_oi:
            d["activity"]     = d["openInterest"]
            d["activity_pct"] = d["openInterest"].rank(pct=True)
            d["premium_paid"] = d["openInterest"] * d["lastPrice"] * 100
        else:
            d["activity"]     = d["volume"]
            d["activity_pct"] = d["volume"].rank(pct=True)
            d["premium_paid"] = d["volume"] * d["lastPrice"] * 100

        smart = d[(d["activity_pct"] > 0.6) & (d["iv_pct"] > 0.4) & (d["lastPrice"] > 0.10)].copy()
        col_act = "openInterest" if uso_oi else "volume"
        cols = ["strike", col_act, "impliedVolatility", "premium_paid", "tipo_flujo", "señal_direccional"]
        smart = smart.rename(columns={col_act: "volume"})
        cols_norm = ["strike", "volume", "impliedVolatility", "premium_paid", "tipo_flujo", "señal_direccional"]
        return smart.nlargest(5, "premium_paid")[cols_norm] if not smart.empty else pd.DataFrame(columns=cols_norm)

    return filtrar(calls, "call"), filtrar(puts, "put")


def consenso_direccional_0dte(max_pain, precio, status_gamma, pc_df, smart_calls, smart_puts,
                               zero_gamma, call_wall, put_wall):
    """
    Consenso direccional intradía. Pesa Max Pain, régimen de gamma de
    dealers, posición del spot frente al Zero Gamma Level y los muros de
    Calls/Puts, PC ratio y actividad de smart money (apertura nueva).
    """
    votos   = {"ALCISTA": 0, "BAJISTA": 0, "NEUTRAL": 0}
    bullets = []

    dist = precio - max_pain
    if dist > 2:
        votos["BAJISTA"] += 2
        bullets.append(f"Max Pain 0DTE en ${max_pain:.0f} → presión bajista intradía")
    elif dist < -2:
        votos["ALCISTA"] += 2
        bullets.append(f"Max Pain 0DTE en ${max_pain:.0f} → presión alcista intradía")
    else:
        votos["NEUTRAL"] += 1
        bullets.append(f"Precio cerca del Max Pain 0DTE ${max_pain:.0f} → pin neutro")

    if "LONG" in status_gamma:
        votos["NEUTRAL"] += 1
        bullets.append("Dealers Long Gamma → movimientos contenidos, sin dirección clara")
    else:
        bullets.append("Dealers Short Gamma → amplificación de movimientos, riesgo de breakout")

    if zero_gamma is not None:
        if precio > zero_gamma:
            votos["NEUTRAL"] += 1
            bullets.append(f"Spot por encima del Zero Gamma (${zero_gamma:.0f}) → régimen long gamma local")
        else:
            bullets.append(f"Spot por debajo del Zero Gamma (${zero_gamma:.0f}) → régimen short gamma local")

    if call_wall is not None and precio < call_wall:
        bullets.append(f"Muro de Calls (resistencia) en ${call_wall:.0f}")
    if put_wall is not None and precio > put_wall:
        bullets.append(f"Muro de Puts (soporte) en ${put_wall:.0f}")

    if not pc_df.empty:
        bearish = (pc_df["sentimiento"] == "BAJISTA 🔴").sum()
        bullish = (pc_df["sentimiento"] == "ALCISTA 🟢").sum()
        if bearish > bullish:
            votos["BAJISTA"] += 1
            bullets.append(f"PC Ratio: {bearish} strikes bajistas vs {bullish} alcistas")
        elif bullish > bearish:
            votos["ALCISTA"] += 1
            bullets.append(f"PC Ratio: {bullish} strikes alcistas vs {bearish} bajistas")

    if not smart_calls.empty:
        ap_calls = (smart_calls["tipo_flujo"] == "APERTURA NUEVA").sum()
        if ap_calls > 0:
            votos["ALCISTA"] += 1
            bullets.append(f"Smart money: {ap_calls} aperturas nuevas en calls")
    if not smart_puts.empty:
        ap_puts = (smart_puts["tipo_flujo"] == "APERTURA NUEVA").sum()
        if ap_puts > 0:
            votos["BAJISTA"] += 1
            bullets.append(f"Smart money: {ap_puts} aperturas nuevas en puts")

    total     = sum(votos.values()) or 1
    direccion = max(votos, key=votos.get)
    confianza = round((votos[direccion] / total) * 100)
    return direccion, confianza, bullets


# ─────────────────────────────────────────────
#  PREPARACIÓN DE DATOS PARA VISUALIZACIÓN
# ─────────────────────────────────────────────

def _preparar_merged_0dte(calls, puts, S):
    """
    Construye el DataFrame 'merged' (calls+puts por strike, con GEX,
    flujo, premium, etc.) acotado a un rango ±3%/±5% alrededor del spot
    — el rango relevante para leer muros de gamma intradía. Si ese rango
    queda con muy pocos strikes activos, se expande progresivamente.
    Devuelve (merged, uso_oi, modo_label) o (None, None, None).
    """
    for margen in (0.03, 0.05, 0.08, 0.15):
        calls_r = calls[(calls["strike"] >= S * (1 - margen)) & (calls["strike"] <= S * (1 + margen))].copy()
        puts_r  = puts[(puts["strike"]  >= S * (1 - margen)) & (puts["strike"]  <= S * (1 + margen))].copy()
        if len(calls_r) + len(puts_r) >= 6:
            break

    if calls_r.empty: calls_r = calls.copy()
    if puts_r.empty:  puts_r  = puts.copy()
    if calls_r.empty and puts_r.empty:
        return None, None, None

    merged = pd.merge(
        calls_r[["strike", "volume", "openInterest", "impliedVolatility",
                  "lastPrice", "delta", "gamma_bsm", "gex", "tipo_flujo", "señal_direccional"]],
        puts_r[["strike",  "volume", "openInterest", "impliedVolatility",
                "lastPrice", "delta", "gamma_bsm", "gex", "tipo_flujo", "señal_direccional"]],
        on="strike", how="outer", suffixes=("_c", "_p")
    ).fillna(0).sort_values("strike")

    merged["vol_total"]     = merged["volume_c"] + merged["volume_p"]
    merged["oi_total"]      = merged["openInterest_c"] + merged["openInterest_p"]
    merged["net_gex_local"] = merged["gex_c"] + merged["gex_p"]

    iv_mask = (merged["impliedVolatility_c"] > 0) | (merged["impliedVolatility_p"] > 0)
    merged_activo = merged[iv_mask].copy()
    if len(merged_activo) >= 5:
        merged = merged_activo

    uso_oi     = (merged["vol_total"].sum() == 0) and (merged["oi_total"].sum() > 0)
    modo_label = "OI (proxy — mercado cerrado)" if uso_oi else "Volumen de hoy (0DTE)"

    if uso_oi:
        merged["premium_c"] = merged["openInterest_c"] * merged["lastPrice_c"] * 100
        merged["premium_p"] = merged["openInterest_p"] * merged["lastPrice_p"] * 100
    else:
        merged["premium_c"] = merged["volume_c"] * merged["lastPrice_c"] * 100
        merged["premium_p"] = merged["volume_p"] * merged["lastPrice_p"] * 100
    merged["net_prem"] = merged["premium_c"] - merged["premium_p"]

    merged["color_gex"]  = ["#22c55e" if x > 0 else "#ef4444" for x in merged["net_gex_local"]]
    merged["color_prem"] = ["#22c55e" if x > 0 else "#ef4444" for x in merged["net_prem"]]

    def flujo_neto(row):
        if uso_oi:
            vc, vp = row["openInterest_c"], row["openInterest_p"]
        else:
            vc, vp = row["volume_c"], row["volume_p"]
        tc, tp = row["tipo_flujo_c"], row["tipo_flujo_p"]
        if max(vc, vp) == 0:
            return "SIN DATOS"
        return tc if vc >= vp else tp
    merged["flujo_neto"] = merged.apply(flujo_neto, axis=1)

    color_flujo = {"APERTURA NUEVA": "#22c55e", "CIERRE/ROTACIÓN": "#f59e0b",
                   "OI ALTO": "#22c55e", "OI BAJO": "#94a3b8",
                   "LIQUIDACIÓN": "#94a3b8", "SIN DATOS": "#475569"}
    merged["color_flujo"] = [color_flujo.get(f, "#475569") for f in merged["flujo_neto"]]
    merged["bar_y_flujo"] = merged["oi_total"] if uso_oi else merged["vol_total"]

    return merged, uso_oi, modo_label


def _tabla_gex_html(merged, S, max_pain, zero_gamma, call_wall, put_wall):
    """
    Tabla HTML pura (sin JS) con barras de color por CSS, resaltando
    spot, Max Pain, Zero Gamma Level, Call Wall y Put Wall — respaldo
    100% confiable del gráfico principal de Net GEX.
    """
    if merged is None or merged.empty:
        return "<p style='color:#8b949e'>Sin datos suficientes para esta tabla.</p>"

    max_abs = merged["net_gex_local"].abs().max()
    if not max_abs or max_abs <= 0:
        max_abs = 1

    def _cerca(valor, objetivo, tol_pct=0.0015):
        return objetivo is not None and abs(valor - objetivo) < max(objetivo * tol_pct, 0.01)

    filas = ""
    for _, row in merged.sort_values("strike").iterrows():
        strike   = row["strike"]
        net_gex  = row["net_gex_local"]
        oi_c     = row["openInterest_c"]
        oi_p     = row["openInterest_p"]
        color    = row["color_gex"]
        ancho    = min(abs(net_gex) / max_abs * 100, 100)

        etiquetas = []
        marcador = ""
        if _cerca(strike, S):
            marcador = " style='background:#1c2333;'"
            etiquetas.append("SPOT")
        elif _cerca(strike, max_pain):
            marcador = " style='background:#241b0d;'"
            etiquetas.append("MAX PAIN")
        elif _cerca(strike, call_wall):
            marcador = " style='background:#152016;'"
            etiquetas.append("MURO CALLS")
        elif _cerca(strike, put_wall):
            marcador = " style='background:#20161a;'"
            etiquetas.append("MURO PUTS")
        elif _cerca(strike, zero_gamma):
            marcador = " style='background:#1a1a2e;'"
            etiquetas.append("ZERO GAMMA")

        tag = f" <span style='color:#58a6ff;font-size:10px'>[{' / '.join(etiquetas)}]</span>" if etiquetas else ""

        filas += f"""
        <tr{marcador}>
          <td class="col-strike">${strike:,.0f}{tag}</td>
          <td class="col-oi">{oi_c:,.0f}</td>
          <td class="col-oi">{oi_p:,.0f}</td>
          <td class="col-gex-num" style="color:{color}">{net_gex:,.0f}</td>
          <td class="col-bar">
            <div class="bar-track">
              <div class="bar-fill" style="width:{ancho:.1f}%; background:{color};"></div>
            </div>
          </td>
        </tr>"""

    zg_txt = f"${zero_gamma:,.2f}" if zero_gamma is not None else "sin cruce en rango"
    cw_txt = f"${call_wall:,.0f}" if call_wall is not None else "n/d"
    pw_txt = f"${put_wall:,.0f}" if put_wall is not None else "n/d"

    return f"""
    <div class="gex-table-wrap">
      <table class="gex-table">
        <thead>
          <tr>
            <th class="col-strike">Strike</th>
            <th class="col-oi">OI Calls</th>
            <th class="col-oi">OI Puts</th>
            <th class="col-gex-num">Net GEX ($)</th>
            <th class="col-bar">Muro (relativo)</th>
          </tr>
        </thead>
        <tbody>{filas}</tbody>
      </table>
      <div class="gex-table-legend">
        <span><span class="leg-dot" style="background:#1c2333;border:1px solid #30363d"></span>Spot (${S:,.2f})</span>
        <span><span class="leg-dot" style="background:#241b0d;border:1px solid #30363d"></span>Max Pain (${max_pain:,.2f})</span>
        <span><span class="leg-dot" style="background:#152016;border:1px solid #30363d"></span>Muro Calls ({cw_txt})</span>
        <span><span class="leg-dot" style="background:#20161a;border:1px solid #30363d"></span>Muro Puts ({pw_txt})</span>
        <span><span class="leg-dot" style="background:#1a1a2e;border:1px solid #30363d"></span>Zero Gamma ({zg_txt})</span>
      </div>
    </div>"""


# ─────────────────────────────────────────────
#  FIGURA PLOTLY — DASHBOARD ÚNICO 0DTE
# ─────────────────────────────────────────────

def _build_dashboard_fig(merged, ticker, exp, dias, S, max_pain, zero_gamma, call_wall, put_wall,
                          status, direccion, confianza, modo_label):
    """
    Figura consolidada de 3 paneles para el vencimiento 0DTE/más próximo:
      ① Net GEX por strike, con Zero Gamma Level y muros de Calls/Puts.
      ② Volumen Call vs Put por strike.
      ③ Premium $ neto (Calls - Puts) por strike.
    """
    strike_l        = merged["strike"].tolist()
    net_gex_l       = merged["net_gex_local"].tolist()
    vol_c_l         = merged["volume_c"].tolist()
    vol_p_l         = merged["volume_p"].tolist()
    premium_c_l     = merged["premium_c"].tolist()
    premium_p_neg_l = (-merged["premium_p"]).tolist()
    net_prem_l      = merged["net_prem"].tolist()

    color_gex   = merged["color_gex"].tolist()
    color_prem  = merged["color_prem"].tolist()
    symbol_prem = ["triangle-up" if x > 0 else "triangle-down" for x in net_prem_l]

    fig = make_subplots(
        rows=3, cols=1,
        subplot_titles=(
            "① NET GEX (Gamma Exposure BSM) — Zero Gamma Level y Muros Calls/Puts",
            f"② VOLUMEN Call vs Put — {modo_label}",
            "③ PREMIUM NETO $ (Calls − Puts) — dónde va el dinero intradía"
        ),
        vertical_spacing=0.09,
        row_heights=[0.4, 0.28, 0.32]
    )

    # ① Net GEX + muros + zero gamma
    fig.add_trace(go.Bar(x=strike_l, y=net_gex_l, name="Net GEX",
        marker=dict(color=color_gex, line=dict(color="rgba(0,0,0,0.3)", width=0.5)),
        hovertemplate="<b>$%{x:.0f}</b><br>Net GEX: %{y:,.0f}<extra></extra>"), row=1, col=1)

    if call_wall is not None:
        fig.add_vline(x=call_wall, line=dict(color="#22c55e", width=2, dash="dashdot"),
                      annotation_text=f"Muro Calls ${call_wall:.0f}", annotation_position="top left",
                      row=1, col=1)
    if put_wall is not None:
        fig.add_vline(x=put_wall, line=dict(color="#ef4444", width=2, dash="dashdot"),
                      annotation_text=f"Muro Puts ${put_wall:.0f}", annotation_position="bottom left",
                      row=1, col=1)
    if zero_gamma is not None:
        fig.add_vline(x=zero_gamma, line=dict(color="#a78bfa", width=2, dash="dash"),
                      annotation_text=f"Zero Gamma ${zero_gamma:.0f}", annotation_position="top right",
                      row=1, col=1)

    # ② Volumen Call vs Put
    fig.add_trace(go.Bar(x=strike_l, y=vol_c_l, name="Vol Calls", marker_color="#4ade80",
        hovertemplate="<b>$%{x:.0f}</b><br>Vol Calls: %{y:,.0f}<extra></extra>"), row=2, col=1)
    fig.add_trace(go.Bar(x=strike_l, y=vol_p_l, name="Vol Puts", marker_color="#f87171",
        hovertemplate="<b>$%{x:.0f}</b><br>Vol Puts: %{y:,.0f}<extra></extra>"), row=2, col=1)

    # ③ Premium neto
    fig.add_trace(go.Bar(x=strike_l, y=premium_c_l, name="Premium Calls", marker_color="#16a34a",
        hovertemplate="<b>$%{x:.0f}</b><br>Premium calls: $%{y:,.0f}<extra></extra>"), row=3, col=1)
    fig.add_trace(go.Bar(x=strike_l, y=premium_p_neg_l, name="Premium Puts", marker_color="#dc2626",
        hovertemplate="<b>$%{x:.0f}</b><br>Premium puts: $%{y:,.0f}<extra></extra>"), row=3, col=1)
    fig.add_trace(go.Scatter(x=strike_l, y=net_prem_l, name="Neto (C-P)", mode="lines+markers",
        line=dict(color="white", width=2, dash="dot"),
        marker=dict(size=8, color=color_prem, symbol=symbol_prem),
        hovertemplate="<b>$%{x:.0f}</b><br>Neto: $%{y:,.0f}<extra></extra>"), row=3, col=1)

    for row_n in [1, 2, 3]:
        fig.add_vline(x=S, line=dict(color="#fbbf24", width=2, dash="dash"),
                      annotation_text=f"Spot ${S:.2f}" if row_n == 1 else "",
                      annotation_position="top right" if row_n != 1 else "bottom right",
                      row=row_n, col=1)
        fig.add_vline(x=max_pain, line=dict(color="#fb923c", width=1.5, dash="dot"),
                      annotation_text=f"Max Pain ${max_pain:.0f}" if row_n == 2 else "",
                      annotation_position="bottom right", row=row_n, col=1)
        fig.add_vrect(x0=S*0.99, x1=S*1.01, fillcolor="#fbbf24", opacity=0.04,
                      layer="below", line_width=0, row=row_n, col=1)

    emoji_dir = "🟢" if direccion == "ALCISTA" else ("🔴" if direccion == "BAJISTA" else "⚖️")
    fig.add_annotation(x=0.99, y=0.98, xref="paper", yref="paper",
        text=f"<b>{emoji_dir} {direccion}</b><br>Confianza: {confianza}%<br>{status}",
        showarrow=False, font=dict(size=12, color="white"), align="right",
        bgcolor="rgba(20,20,30,0.88)", bordercolor="#475569", borderwidth=1, borderpad=8)

    fig.update_layout(
        title=dict(text=f"<b>{ticker.upper()} | {exp} ({dias}d) — 0DTE / Intradía</b>",
                   x=0.5, xanchor="center", font=dict(size=15)),
        height=1050, template="plotly_dark", barmode="overlay",
        showlegend=True, hovermode="x unified",
        legend=dict(orientation="h", y=-0.04, x=0.5, xanchor="center"),
        margin=dict(t=60, b=50, l=55, r=55)
    )

    # Rango de eje X compartido y explícito en los 3 paneles: evita que una
    # anotación (spot, max pain, zero gamma, muros) fuera del rango de
    # strikes graficado estire un panel más que los otros.
    x_min, x_max = min(strike_l), max(strike_l)
    margen_x = (x_max - x_min) * 0.03 if x_max > x_min else 1
    x_range = [x_min - margen_x, x_max + margen_x]
    for row_n in [1, 2, 3]:
        fig.update_xaxes(range=x_range, row=row_n, col=1)

    fig.update_yaxes(title_text="GEX ($)", row=1, col=1)
    fig.update_yaxes(title_text="Volumen", row=2, col=1)
    fig.update_yaxes(title_text="Premium $", row=3, col=1)
    fig.update_xaxes(title_text="Strike ($)", row=3, col=1)
    return fig


# ─────────────────────────────────────────────
#  DASHBOARD HTML — VISTA ÚNICA (SIN PESTAÑAS)
# ─────────────────────────────────────────────

def crear_dashboard_0dte(data, ticker, output_dir=None, refresh_seconds=None):
    """
    Construye el dashboard HTML consolidado en una sola vista:
      - Panel superior: Spot, Max Pain 0DTE, Posicionamiento de dealers,
        Consenso direccional 0DTE con confianza.
      - Tabla + gráfico de Net GEX / Muros por strike.
      - Gráfico de Volumen Call vs Put y Premium $ neto intradía.
    """
    exp, dias, S       = data["exp"], data["dias"], data["S"]
    max_pain           = data["max_pain"]
    status             = data["status_gamma"]
    direccion          = data["direccion"]
    confianza          = data["confianza"]
    zero_gamma         = data["zero_gamma"]
    call_wall          = data["call_wall"]
    put_wall           = data["put_wall"]
    bullets            = data["bullets"]
    merged             = data["merged"]
    modo_label         = data["modo_label"]

    if merged is None or merged.empty:
        print("❌ Sin datos suficientes para construir el dashboard.")
        return None

    tabla_gex = _tabla_gex_html(merged, S, max_pain, zero_gamma, call_wall, put_wall)
    fig       = _build_dashboard_fig(merged, ticker, exp, dias, S, max_pain, zero_gamma,
                                      call_wall, put_wall, status, direccion, confianza, modo_label)
    fig_json  = fig.to_json()

    ts = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    if refresh_seconds:
        refresh_ms = int(refresh_seconds * 1000)
        auto_refresh_js = f"""
setTimeout(function() {{ location.reload(); }}, {refresh_ms});
var __seg_restantes = {refresh_seconds};
var __contador_el = document.getElementById('countdown');
setInterval(function() {{
  __seg_restantes -= 1;
  if (__seg_restantes < 0) __seg_restantes = 0;
  if (__contador_el) __contador_el.textContent = __seg_restantes + 's';
}}, 1000);
"""
        countdown_html = f'<span id="countdown">{refresh_seconds}s</span>'
    else:
        auto_refresh_js = ""
        countdown_html = "manual"

    def card(label, value, sub="", color="#22c55e"):
        return f"""
        <div class="metric-card">
          <div class="metric-label">{label}</div>
          <div class="metric-value" style="color:{color}">{value}</div>
          <div class="metric-sub">{sub}</div>
        </div>"""

    dir_color = "#22c55e" if direccion == "ALCISTA" else ("#ef4444" if direccion == "BAJISTA" else "#94a3b8")
    emoji_dir = "🟢" if direccion == "ALCISTA" else ("🔴" if direccion == "BAJISTA" else "⚖️")
    etiqueta_venc = "0DTE (vence hoy)" if dias == 0 else f"vencimiento más próximo ({dias}d)"

    metric_cards = (
        card("Precio Spot", f"${S:.2f}", etiqueta_venc, "#f0f6fc")
        + card("Max Pain 0DTE", f"${max_pain:.0f}", f"Distancia al spot: {S - max_pain:+.2f}", "#fb923c")
        + card("Posicionamiento Dealers", status, "Gamma neto de la cadena 0DTE", "#58a6ff")
        + card(f"{emoji_dir} Consenso Direccional 0DTE", f"{direccion}  ·  {confianza}%",
               "Nivel de confianza del consenso", dir_color)
    )

    bullets_html = ""
    for b in bullets:
        bullets_html += f'<div class="bullet-item">• {b}</div>'

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{ticker.upper()} — 0DTE Dashboard</title>
<script src="https://cdn.plot.ly/plotly-3.0.1.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0d1117; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 0; }}
  .header {{ background: #161b22; border-bottom: 1px solid #30363d; padding: 18px 28px; display: flex; align-items: center; justify-content: space-between; }}
  .header-title {{ font-size: 20px; font-weight: 600; color: #f0f6fc; }}
  .header-sub {{ font-size: 13px; color: #8b949e; margin-top: 3px; }}
  .header-price {{ font-size: 26px; font-weight: 700; color: #f0f6fc; }}
  .header-change {{ font-size: 13px; color: #8b949e; }}
  .decision-panel {{ padding: 20px 28px; background: #0d1117; border-bottom: 1px solid #21262d; }}
  .panel-title {{ font-size: 11px; font-weight: 500; letter-spacing: .07em; text-transform: uppercase; color: #8b949e; margin-bottom: 14px; }}
  .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-bottom: 16px; }}
  .metric-card {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 14px 16px; }}
  .metric-label {{ font-size: 12px; color: #8b949e; margin-bottom: 5px; }}
  .metric-value {{ font-size: 18px; font-weight: 600; }}
  .metric-sub {{ font-size: 11px; color: #6e7681; margin-top: 4px; }}
  .bullets-section {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 12px 14px; }}
  .bullet-item {{ font-size: 12px; color: #8b949e; line-height: 1.7; }}
  .legend-row {{ display: flex; gap: 18px; flex-wrap: wrap; padding: 10px 28px 0; font-size: 12px; color: #8b949e; }}
  .leg-dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 5px; vertical-align: middle; }}
  .status-banner {{ background: #1c2333; border: 1px solid #30363d; border-radius: 6px; padding: 8px 16px; margin: 10px 28px; font-size: 12px; color: #8b949e; }}
  .status-banner b {{ color: #58a6ff; }}
  .main-content {{ padding: 20px 28px 40px; }}
  .section-title {{ font-size: 13px; font-weight: 600; color: #f0f6fc; margin: 22px 0 10px; }}
  .gex-table-wrap {{ padding: 4px 4px 18px; max-height: 480px; overflow-y: auto; border: 1px solid #21262d; border-radius: 8px; }}
  .gex-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .gex-table th {{ position: sticky; top: 0; background: #161b22; color: #8b949e; text-align: left;
                    font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
                    padding: 8px 10px; border-bottom: 1px solid #30363d; }}
  .gex-table td {{ padding: 6px 10px; border-bottom: 1px solid #21262d; color: #e6edf3; white-space: nowrap; }}
  .gex-table tr:hover {{ background: #161b22; }}
  .col-strike {{ font-weight: 600; width: 140px; }}
  .col-oi {{ color: #8b949e; width: 100px; text-align: right; }}
  .col-gex-num {{ text-align: right; width: 150px; font-variant-numeric: tabular-nums; }}
  .col-bar {{ width: 40%; }}
  .bar-track {{ background: #21262d; border-radius: 3px; height: 14px; width: 100%; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 3px; }}
  .gex-table-legend {{ display: flex; gap: 16px; flex-wrap: wrap; font-size: 11px; color: #8b949e; padding: 10px 10px 0; }}
  #plot-main {{ margin-top: 18px; }}
</style>
</head>
<body>

<div class="header">
  <div>
    <div class="header-title">{ticker.upper()} — 0DTE / Intradía Dashboard</div>
    <div class="header-sub">Análisis institucional · {ts} · Vencimiento {exp} ({etiqueta_venc}) · Gamma BSM real</div>
  </div>
  <div style="text-align:right">
    <div class="header-price">${S:.2f}</div>
    <div class="header-change">Precio spot (último disponible)</div>
  </div>
</div>

<div class="status-banner">
  ℹ️ Los datos de opciones corresponden al <b>último registro disponible en Polygon.io</b> (plan Starter: ~15 min de delay).
  Para 0DTE el <b>volumen del día</b> es la métrica reina — el OI de la mañana proviene del cierre anterior.
  &nbsp;·&nbsp; 🔄 Próxima actualización en: <b>{countdown_html}</b>
</div>

<div class="decision-panel">
  <div class="panel-title">Panel de decisión — 0DTE</div>
  <div class="metrics-grid">
    {metric_cards}
  </div>
  <div class="panel-title" style="margin-top:16px">Señales detectadas</div>
  <div class="bullets-section">{bullets_html}</div>
</div>

<div class="legend-row">
  <span><span class="leg-dot" style="background:#fbbf24"></span>Precio spot</span>
  <span><span class="leg-dot" style="background:#fb923c"></span>Max Pain</span>
  <span><span class="leg-dot" style="background:#22c55e"></span>Muro Calls / Alcista</span>
  <span><span class="leg-dot" style="background:#ef4444"></span>Muro Puts / Bajista</span>
  <span><span class="leg-dot" style="background:#a78bfa"></span>Zero Gamma Level</span>
</div>

<div class="main-content">
  <div class="section-title">Net GEX y Muros por Strike (±3–5% del spot)</div>
  {tabla_gex}
  <div id="plot-main"></div>
</div>

<script>
var figDataMain = {fig_json};
Plotly.newPlot("plot-main", figDataMain.data, figDataMain.layout, {{responsive:true}});
{auto_refresh_js}
</script>
</body>
</html>"""

    if len(html) < 5000:
        print("⚠️  HTML generado parece demasiado pequeño — puede haber un problema.")

    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(__file__))

    fname = os.path.join(output_dir, f"{ticker.upper()}_0DTE_dashboard.html")
    with open(fname, "w", encoding="utf-8") as fh:
        fh.write(html)

    size_kb = os.path.getsize(fname) / 1024
    print(f"   ✅ Dashboard guardado: {fname}  ({size_kb:.1f} KB)")

    if size_kb < 10:
        print("   ⚠️  Archivo muy pequeño — verifica que los datos se obtuvieron correctamente.")

    return fname


# ─────────────────────────────────────────────
#  SERVIDOR LOCAL (resuelve el problema file://)
# ─────────────────────────────────────────────

def servir_dashboard(filepath, port=8765):
    """
    Levanta un mini servidor HTTP en localhost para servir el dashboard.
    Esto evita las restricciones de seguridad de los navegadores con file://.
    """
    directorio = os.path.dirname(os.path.abspath(filepath))
    filename   = os.path.basename(filepath)
    url        = f"http://localhost:{port}/{filename}?v={int(time.time())}"

    class SilentHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directorio, **kwargs)
        def log_message(self, format, *args):
            pass
        def end_headers(self):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            super().end_headers()

    def _run():
        with socketserver.TCPServer(("", port), SilentHandler) as httpd:
            httpd.serve_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    print(f"\n🌐 Servidor local activo: {url}")
    print(f"   (el servidor se cierra cuando termines el script)\n")
    time.sleep(0.5)
    webbrowser.open(url)
    return url


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main(ticker, refresh_seconds=60):
    print(f"\n{'='*80}")
    print(f"   ANÁLISIS INSTITUCIONAL DE OPCIONES — 0DTE / INTRADÍA — {ticker.upper()}")
    print(f"   Gamma BSM real · Zero Gamma Level · Muros Calls/Puts · Flujo Vol/OI")
    print(f"   Auto-actualización cada {refresh_seconds}s")
    print(f"{'='*80}\n")

    if not POLYGON_API_KEY:
        print("❌ No se encontró POLYGON_API_KEY en las variables de entorno.")
        print("   Define la variable de entorno, ej: export POLYGON_API_KEY='tu_api_key'")
        return

    output_dir = os.path.dirname(os.path.abspath(__file__))
    server_iniciado = False
    ciclo = 0

    try:
        while True:
            ciclo += 1
            inicio_ciclo = time.time()
            print(f"\n{'#'*80}")
            print(f"🔄 CICLO #{ciclo} — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
            print(f"{'#'*80}")

            print("📡 Conectando con Polygon.io…")
            S = obtener_precio(ticker)
            if not S:
                print(f"   ⏳ Reintentando en {refresh_seconds}s…")
                time.sleep(refresh_seconds)
                continue

            exp, dias = seleccionar_vencimiento_0dte(ticker)
            if not exp:
                print(f"   ⏳ Reintentando en {refresh_seconds}s…")
                time.sleep(refresh_seconds)
                continue

            print(f"\n{'─'*60}")
            print(f"🔍 Analizando vencimiento 0DTE/más próximo: {exp} ({dias}d)")

            calls, puts = obtener_cadena_0dte(ticker, exp, S, dias)
            if calls is None:
                print(f"   ⛔ Sin cadena disponible para {exp}")
                print(f"   ⏳ Reintentando en {refresh_seconds}s…")
                time.sleep(refresh_seconds)
                continue

            merged, uso_oi, modo_label = _preparar_merged_0dte(calls, puts, S)
            if merged is None:
                print(f"   ⛔ Sin datos suficientes en la ventana ±3-5% del spot para {exp}")
                print(f"   ⏳ Reintentando en {refresh_seconds}s…")
                time.sleep(refresh_seconds)
                continue

            # Zero Gamma Level y muros se calculan SOBRE LA MISMA VENTANA que
            # se grafica (no sobre toda la cadena) — así el panel de Net GEX
            # comparte el mismo rango de strikes que Volumen y Premium, y se
            # evita "ruido" de strikes muy OTM con IV inferida poco confiable.
            gex_ventana = (merged[["strike", "net_gex_local"]]
                           .rename(columns={"net_gex_local": "net_gex"})
                           .sort_values("strike").reset_index(drop=True))

            max_pain               = calcular_max_pain(calls, puts, S=S)
            zero_gamma             = calcular_zero_gamma_level(gex_ventana)
            call_wall, put_wall    = identificar_muros_gex(gex_ventana)
            status_g, net_g, comp_g = posicionamiento_dealers_real(calls, puts)
            pc_df                  = ratio_pc_enriquecido(calls, puts)
            sm_calls, sm_puts      = detectar_smart_money(calls, puts)
            direccion, confianza, bullets = consenso_direccional_0dte(
                max_pain, S, status_g, pc_df, sm_calls, sm_puts, zero_gamma, call_wall, put_wall
            )

            dist = S - max_pain
            print(f"\n  Max Pain 0DTE: ${max_pain:.2f}  (distancia: {dist:+.2f})")
            print(f"  Posicionamiento dealers: {status_g}")
            print(f"  Net GEX real: {net_g:,.0f}")
            print(f"  Zero Gamma Level: {'$'+format(zero_gamma, ',.2f') if zero_gamma is not None else 'sin cruce en rango'}")
            print(f"  Muro Calls (resistencia): {'$'+format(call_wall, ',.0f') if call_wall is not None else 'n/d'}")
            print(f"  Muro Puts (soporte): {'$'+format(put_wall, ',.0f') if put_wall is not None else 'n/d'}")

            emoji = "🟢" if direccion == "ALCISTA" else ("🔴" if direccion == "BAJISTA" else "⚖️")
            print(f"  {emoji} CONSENSO 0DTE: {direccion}  |  Confianza: {confianza}%")

            data = {
                "exp": exp, "dias": dias, "calls": calls, "puts": puts, "S": S,
                "max_pain": max_pain,
                "merged": merged, "uso_oi": uso_oi, "modo_label": modo_label,
                "zero_gamma": zero_gamma, "call_wall": call_wall, "put_wall": put_wall,
                "status_gamma": status_g, "net_gex": net_g,
                "pc_df": pc_df, "smart_calls": sm_calls, "smart_puts": sm_puts,
                "direccion": direccion, "confianza": confianza, "bullets": bullets
            }

            print(f"\n{'='*80}")
            print("🎨 REGENERANDO DASHBOARD 0DTE…")
            print(f"{'='*80}")

            filepath = crear_dashboard_0dte(data, ticker, output_dir=output_dir,
                                             refresh_seconds=refresh_seconds)

            if filepath and not server_iniciado:
                servir_dashboard(filepath, port=8765)
                server_iniciado = True
                print("   La página se recargará sola cada ciclo — no hace falta reabrirla.")

            elapsed = time.time() - inicio_ciclo
            espera = max(refresh_seconds - elapsed, 1)
            print(f"\n✅ Ciclo #{ciclo} completo en {elapsed:.1f}s — próxima actualización en {espera:.0f}s.")
            print("   Presiona Ctrl+C para detener.")
            time.sleep(espera)

    except KeyboardInterrupt:
        print("\n\n🛑 Detenido por el usuario. Servidor cerrado.")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("   ANÁLISIS INSTITUCIONAL DE OPCIONES — 0DTE / INTRADÍA")
    print("   Gamma BSM · Zero Gamma Level · Muros Calls/Puts · Flujo Vol/OI")
    print("   Auto-actualización periódica")
    print("="*80 + "\n")

    ticker = input("Ticker (ej. SPY, QQQ, AAPL): ").strip().upper()
    if not ticker:
        print("❌ Ticker inválido.")
        sys.exit(1)

    intervalo_input = input("Intervalo de actualización en segundos (Enter = 60): ").strip()
    refresh_seconds = int(intervalo_input) if intervalo_input.isdigit() else 60

    main(ticker, refresh_seconds=refresh_seconds)
