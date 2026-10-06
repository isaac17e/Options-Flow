import argparse
import atexit
import random
import re
import statistics
import requests
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta, timezone
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import warnings
import time
import sys
import os
import json
import threading
import http.server
import socketserver
import webbrowser

# ─────────────────────────────────────────────
#  CONFIGURACIÓN POLYGON.IO
# ─────────────────────────────────────────────

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "").strip()
POLYGON_BASE_URL = "https://api.polygon.io"

# ticker → (ticker para contratos de referencia, ticker para el snapshot).
# SPX: la referencia con "SPX" incluye SPXW, pero el snapshot con "SPX" llega
# sin IV ni griegas; hay que pedirlo como "I:SPX".
POLYGON_TICKERS = {
    "SPX": ("SPX", "I:SPX"),
    "SPY": ("SPY", "SPY"),
}

# Subyacente por defecto si no se indica otro (CLI --ticker, env OPTIONS_TICKER o prompt).
TICKER_POR_DEFECTO = "SPY"


def tickers_polygon(ticker):
    """(ticker de referencia, ticker de snapshot); lo no listado usa el mismo para ambos."""
    t = ticker.upper()
    return POLYGON_TICKERS.get(t, (t, t))


# Reintentos ante 429 (rate limit) y 5xx transitorios.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
RETRY_AFTER_MAX_SECONDS = 120.0

# Intervalo mínimo entre ciclos: con menos, el bucle martillea a Polygon.
MIN_REFRESH_SECONDS = 15


def _backoff_seconds(intento):
    """Backoff exponencial con jitter: base·2^n más un aleatorio en [0, base·2^n]."""
    base = min(BACKOFF_BASE_SECONDS * 2 ** intento, BACKOFF_MAX_SECONDS)
    return base + random.uniform(0, base)


def _parse_retry_after(value):
    # Solo la forma en segundos; la forma de fecha HTTP cae al backoff.
    try:
        return min(max(float(value), 0.0), RETRY_AFTER_MAX_SECONDS)
    except (TypeError, ValueError):
        return None


def _polygon_get(path, params=None, full_url=None):
    """
    Hace un GET contra la API de Polygon. Si se pasa full_url (p.ej. el
    'next_url' de una respuesta paginada), lo usa directamente y solo
    añade el apiKey. Reintenta 429 (respetando Retry-After), 5xx y fallos
    de conexión con backoff exponencial + jitter; otros 4xx fallan ya.
    """
    params = dict(params or {})
    params["apiKey"] = POLYGON_API_KEY
    url = full_url if full_url else f"{POLYGON_BASE_URL}{path}"

    ultimo_error = None
    for intento in range(MAX_RETRIES + 1):
        retry_after = None
        try:
            resp = requests.get(url, params=params, timeout=15)
        except (requests.ConnectionError, requests.Timeout) as e:
            ultimo_error = str(e)
        else:
            if resp.status_code not in RETRYABLE_STATUS:
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") not in ("OK", "DELAYED"):
                    raise RuntimeError(f"Polygon respondió status={data.get('status')}: {data.get('error', data)}")
                return data
            ultimo_error = f"HTTP {resp.status_code}"
            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))

        if intento == MAX_RETRIES:
            break
        espera = retry_after if retry_after is not None else _backoff_seconds(intento)
        print(f"   ⏳ Polygon {ultimo_error} — reintento {intento + 1}/{MAX_RETRIES} en {espera:.1f}s")
        time.sleep(espera)

    raise RuntimeError(f"Polygon falló tras {MAX_RETRIES} reintentos: {ultimo_error}")


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
    if data.get("next_url"):
        print(f"   ⚠️  Paginación cortada en {max_pages} páginas: la cadena puede estar incompleta")
    return resultados


# ─────────────────────────────────────────────
#  CAPITAL.COM — FUENTE DEL SPOT
# ─────────────────────────────────────────────
#
# El plan de Polygon solo cubre opciones: el prev close de acciones es el
# cierre de ayer y underlying_asset.price viene vacío. El spot se toma del
# punto medio bid/offer de Capital.com. La sesión (CST + X-SECURITY-TOKEN)
# se reutiliza entre ciclos, se renueva ante un 401 (caduca tras ~10 min
# sin uso) y se cierra al salir (atexit). Los tokens nunca se imprimen.
#
# Es el mismo protocolo que src/data/capital_client.py del dashboard, pero
# autocontenido: ese cliente depende de config.py (python-dotenv y el .env
# de la raíz) y este script solo necesita legacy_0dte/requirements.txt.

CAPITAL_API_URL = os.environ.get(
    "CAPITAL_API_URL", "https://demo-api-capital.backend-capital.com/api/v1"
).strip().rstrip("/")


class CapitalSession:
    def __init__(self, base_url=CAPITAL_API_URL, timeout=10):
        self.base_url = base_url
        self.timeout = timeout
        self._http = requests.Session()
        self._auth = None
        self._logout_registrado = False

    @staticmethod
    def _credenciales():
        cred = {n: os.environ.get(n, "").strip()
                for n in ("CAPITAL_API_KEY", "CAPITAL_IDENTIFIER", "CAPITAL_API_PASSWORD")}
        faltan = [n for n, v in cred.items() if not v]
        if faltan:
            raise RuntimeError(f"Faltan credenciales de Capital.com: {', '.join(faltan)}")
        return cred

    def _login(self):
        cred = self._credenciales()
        resp = self._http.post(
            f"{self.base_url}/session",
            headers={"X-CAP-API-KEY": cred["CAPITAL_API_KEY"]},
            json={"identifier": cred["CAPITAL_IDENTIFIER"], "password": cred["CAPITAL_API_PASSWORD"]},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Capital.com rechazó la sesión (HTTP {resp.status_code})")
        cst, token = resp.headers.get("CST"), resp.headers.get("X-SECURITY-TOKEN")
        if not cst or not token:
            raise RuntimeError("Capital.com no devolvió los tokens de sesión")
        self._auth = {"CST": cst, "X-SECURITY-TOKEN": token}
        if not self._logout_registrado:
            atexit.register(self.logout)
            self._logout_registrado = True
        return self._auth

    def get(self, path):
        for intento in range(2):
            headers = self._auth or self._login()
            resp = self._http.get(f"{self.base_url}{path}", headers=headers, timeout=self.timeout)
            if resp.status_code == 401 and intento == 0:
                self._auth = None   # sesión caducada → re-login una sola vez
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"Capital.com respondió HTTP {resp.status_code} en {path}")
            return resp.json()
        raise RuntimeError(f"Capital.com rechazó la sesión renovada en {path}")

    def logout(self):
        if not self._auth:
            return
        try:
            self._http.delete(f"{self.base_url}/session", headers=self._auth, timeout=self.timeout)
        except requests.RequestException:
            pass
        self._auth = None


_capital = CapitalSession()

# ticker → (epic de Capital.com, tipo de instrumento esperado o None).
# En Capital.com el epic "SPX" es Spirax Sarco (una acción), no el índice:
# SPX se toma del CFD US500 (INDICES) más una base. Lo no listado usa su
# propio símbolo como epic, sin verificar el tipo.
CAPITAL_EPICS = {
    "SPX": ("US500", "INDICES"),
    "SPY": ("SPY", None),
}

# Tickers cuyo spot es mid(epic) + base (SPX = US500 + base).
TICKERS_CON_BASE = {"SPX"}

# Base SPX − US500 en puntos (respaldo si no se puede calcular por paridad).
# Medido el 2026-10-05: US500 ≈ SPX − 1, o sea base ≈ +1. Se cambia con la
# variable SPX_BASIS o con --spx-basis.
def _leer_base_spx():
    try:
        return float(os.environ.get("SPX_BASIS", "").strip() or 1.0)
    except ValueError:
        print("   ⚠️  SPX_BASIS no es un número — se usa 1.0")
        return 1.0

SPX_BASIS_DEFAULT = _leer_base_spx()

# Las opciones de Polygon van ~15 min por detrás: la base se calcula contra el
# US500 de hace estos minutos. Una barra a más de MAX_BRECHA_BARRA_MIN del
# instante buscado (mercado cerrado) se descarta.
RETRASO_OPCIONES_MIN = 15
MAX_BRECHA_BARRA_MIN = 5
PARIDAD_N_STRIKES = 7
PARIDAD_MIN_STRIKES = 3
BASE_MAX_FRACCION = 0.005   # una base mayor al 0,5% del precio se descarta como dato malo


def epic_capital(ticker):
    """(epic, tipo esperado o None) para el ticker."""
    t = ticker.upper()
    return CAPITAL_EPICS.get(t, (t, None))


def _verificar_tipo_instrumento(epic, esperado, data):
    if esperado is None:
        return
    real = (data.get("instrument") or {}).get("type")
    if real is None:
        print(f"   ⚠️  Capital.com no informó el tipo de instrumento de {epic} (se esperaba {esperado})")
    elif real != esperado:
        raise RuntimeError(f"El epic {epic} de Capital.com es de tipo {real}, se esperaba {esperado}: "
                           "no es el instrumento correcto para usarlo como spot")


def _precio_via_capital(ticker):
    """
    Spot = (bid + offer) / 2 de GET /markets/{epic}, con el epic de
    CAPITAL_EPICS (SPY → SPY, SPX → US500) y verificando el tipo de
    instrumento. Devuelve (precio, hora, estado del mercado); para SPX el
    precio es el del US500, sin base.
    """
    epic, esperado = epic_capital(ticker)
    data = _capital.get(f"/markets/{epic}")
    _verificar_tipo_instrumento(epic, esperado, data)
    snap = data.get("snapshot") or {}
    bid, offer = snap.get("bid"), snap.get("offer")
    if bid is None or offer is None:
        raise RuntimeError(f"Capital.com no devolvió bid/offer para {epic}")
    hora = (snap.get("updateTime") or "").split(".")[0].replace("T", " ")
    return (float(bid) + float(offer)) / 2, hora, snap.get("marketStatus")


def _mid_capital_en(epic, instante_utc):
    """
    Mid (bid+ask)/2 del cierre de la barra de 1 minuto más cercana a
    `instante_utc` (datetime con tz UTC), vía GET /prices/{epic}. None si no
    hay barra a menos de MAX_BRECHA_BARRA_MIN.
    """
    fmt = "%Y-%m-%dT%H:%M:%S"
    ventana = timedelta(minutes=MAX_BRECHA_BARRA_MIN + 1)
    data = _capital.get(f"/prices/{epic}?resolution=MINUTE&max=30"
                        f"&from={(instante_utc - ventana).strftime(fmt)}"
                        f"&to={(instante_utc + ventana).strftime(fmt)}")
    mejor, mejor_brecha = None, timedelta(minutes=MAX_BRECHA_BARRA_MIN)
    for barra in data.get("prices") or []:
        try:
            # snapshotTimeUTC es el inicio de la barra; su cierre es un minuto después.
            inicio = datetime.fromisoformat(barra["snapshotTimeUTC"]).replace(tzinfo=timezone.utc)
            cierre = barra["closePrice"]
            mid = (float(cierre["bid"]) + float(cierre["ask"])) / 2
        except (KeyError, TypeError, ValueError):
            continue
        brecha = abs(inicio + timedelta(minutes=1) - instante_utc)
        if brecha <= mejor_brecha:
            mejor, mejor_brecha = mid, brecha
    return mejor


def forward_paridad(contratos, n_strikes=PARIDAD_N_STRIKES, min_strikes=PARIDAD_MIN_STRIKES):
    """
    Mediana de K + C − P sobre los `n_strikes` strikes donde call y put cuestan
    lo más parecido (los ATM), con la cadena cruda de UN vencimiento. Solo
    cuentan contratos con precio (último trade, mid bid/ask o cierre del día)
    y volumen > 0. SPX y SPXW (mismo strike) no se mezclan. None si hay menos
    de `min_strikes` strikes utilizables.
    """
    lados = {}
    for c in contratos:
        det = c.get("details", {}) or {}
        tipo, strike = det.get("contract_type"), det.get("strike_price")
        if tipo not in ("call", "put") or strike is None:
            continue
        day = c.get("day", {}) or {}
        if not (day.get("volume") or 0) > 0:
            continue
        trade = (c.get("last_trade", {}) or {}).get("price")
        quote = c.get("last_quote", {}) or {}
        precio = trade or ((quote["bid"] + quote["ask"]) / 2 if quote.get("bid") and quote.get("ask") else None) \
            or day.get("close")
        if not precio or precio <= 0:
            continue
        m = re.match(r"^O:([A-Z]+)\d{6}[CP]", det.get("ticker") or "")
        lados.setdefault((m.group(1) if m else "", float(strike)), {})[tipo] = float(precio)

    filas = [(k, l["call"], l["put"]) for (_, k), l in lados.items() if "call" in l and "put" in l]
    if len(filas) < min_strikes:
        return None
    filas.sort(key=lambda f: abs(f[1] - f[2]))
    return float(statistics.median(k + c - p for k, c, p in filas[:n_strikes]))


def calcular_base_spx(contratos, epic, ahora=None, base_defecto=None):
    """
    Base SPX − US500 del ciclo: mediana del forward por paridad de la cadena
    menos el mid de US500 de hace RETRASO_OPCIONES_MIN minutos (compensa el
    retraso de Polygon). Si no se puede calcular (sin strikes ATM, sin barra,
    base absurda, error de red) usa `base_defecto` (SPX_BASIS_DEFAULT).
    Devuelve (base, descripción de la fuente).
    """
    base_defecto = SPX_BASIS_DEFAULT if base_defecto is None else base_defecto

    def respaldo(motivo):
        return base_defecto, f"parámetro {base_defecto:+.2f} (respaldo: {motivo})"

    forward = forward_paridad(contratos) if contratos else None
    if forward is None:
        return respaldo("sin strikes ATM con call y put en la cadena")
    objetivo = (ahora or datetime.now(timezone.utc)) - timedelta(minutes=RETRASO_OPCIONES_MIN)
    try:
        mid_pasado = _mid_capital_en(epic, objetivo)
    except Exception as e:
        return respaldo(f"sin precio histórico de {epic}: {e}")
    if mid_pasado is None:
        return respaldo(f"sin barra de {epic} cerca de t-{RETRASO_OPCIONES_MIN} min")
    base = forward - mid_pasado
    if abs(base) > BASE_MAX_FRACCION * mid_pasado:
        return respaldo(f"base absurda: forward {forward:.2f} vs {epic} {mid_pasado:.2f}")
    return base, f"paridad put-call (forward {forward:.2f}) − {epic} de t-{RETRASO_OPCIONES_MIN} min ({mid_pasado:.2f})"


# ─────────────────────────────────────────────
#  MATH HELPERS
# ─────────────────────────────────────────────

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ─────────────────────────────────────────────
#  PRECIO SPOT — Capital.com, con paridad put-call de respaldo
# ─────────────────────────────────────────────

def _precio_via_paridad_put_call(contratos):
    """
    Respaldo si Capital.com falla: estima el spot con paridad put-call sobre
    la cadena ya descargada en este ciclo (sin llamadas extra a Polygon):
        S ≈ K + C - P   (aproximación sin descuento, válida para el corto plazo)
    usando el strike donde call y put tienen precios más parecidos (ATM).
    """
    calls = _snapshot_a_dataframe(contratos, "call")
    puts  = _snapshot_a_dataframe(contratos, "put")
    if calls.empty or puts.empty:
        return None

    merged = pd.merge(calls[["strike", "lastPrice"]], puts[["strike", "lastPrice"]],
                       on="strike", suffixes=("_c", "_p")).dropna()
    merged = merged[(merged["lastPrice_c"] > 0) & (merged["lastPrice_p"] > 0)]
    if merged.empty:
        return None
    merged["diff"] = (merged["lastPrice_c"] - merged["lastPrice_p"]).abs()
    fila = merged.loc[merged["diff"].idxmin()]
    S_est = fila["strike"] + fila["lastPrice_c"] - fila["lastPrice_p"]
    return float(S_est) if S_est > 0 else None


def obtener_precio(ticker, contratos=None):
    """
    Spot del ciclo: primero Capital.com (mid bid/offer); si falla, paridad
    put-call sobre `contratos` (la cadena ya descargada en este ciclo).
    Devuelve (precio, fuente, hora) o (None, None, None).
    """
    try:
        val, hora, estado = _precio_via_capital(ticker)
        if val > 0:
            epic, _ = epic_capital(ticker)
            if ticker.upper() in TICKERS_CON_BASE:
                # Spot del índice = mid del CFD + base (por paridad en este ciclo, o el parámetro).
                base, fuente_base = calcular_base_spx(contratos, epic)
                fuente = (f"Capital.com {epic} (mid {val:.2f}) + base {base:+.2f} "
                          f"[{fuente_base}]")
                val += base
            else:
                fuente = "Capital.com (mid bid/offer)"
            if estado and estado != "TRADEABLE":
                fuente += f" · mercado {estado}"
            hora = hora or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"💰 Precio obtenido: ${val:.2f}  (vía {fuente}, {hora})")
            return val, fuente, hora
    except Exception as e:
        print(f"   ⚠️  Capital.com falló: {e}")

    if contratos:
        val = _precio_via_paridad_put_call(contratos)
        if val and val > 0:
            fuente = "paridad put-call (estimado sobre la cadena de Polygon)"
            hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"💰 Precio obtenido: ${val:.2f}  (vía {fuente})")
            return val, fuente, hora

    print("❌ Todos los métodos de precio fallaron.")
    return None, None, None


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
# de greeks en vivo, y puede venir vacío en contratos sueltos (p. ej. ITM)
# o en TODA la cadena cuando no hay NBBO en tiempo real (fin de semana,
# feriado, ciertos subyacentes/planes). Sin IV no hay gamma BSM ni GEX.
# En cada contrato sin IV pero con lastPrice (último trade o cierre del
# día) se infiere la IV invirtiendo Black-Scholes por bisección.

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
    donde Polygon no la trajo pero sí hay lastPrice utilizable. Si la
    inversión falla, la fila se queda con IV 0. Devuelve
    (df_actualizado, n_inferidas_con_éxito).
    """
    df = df.copy()
    df["impliedVolatility"] = df["impliedVolatility"].astype(float)
    mask = (df["impliedVolatility"] <= 0) & (df["lastPrice"] > 0) & (df["strike"] > 0)
    if not mask.any():
        return df, 0

    df.loc[mask, "impliedVolatility"] = df.loc[mask].apply(
        lambda row: _implied_vol_bisection(row["lastPrice"], S, row["strike"], T, r, tipo) or 0.0,
        axis=1
    )
    return df, int((df.loc[mask, "impliedVolatility"] > 0).sum())


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

    Una sola llamada: contratos con vencimiento >= hoy, ordenados por
    vencimiento ascendente y limit=1 → el primero es el más próximo.
    """
    hoy = datetime.now().date()
    try:
        data = _polygon_get(
            "/v3/reference/options/contracts",
            params={
                "underlying_ticker": tickers_polygon(ticker)[0],
                "expired": "false",
                "expiration_date.gte": hoy.isoformat(),
                "sort": "expiration_date",
                "order": "asc",
                "limit": 1,
            },
        )
    except Exception as e:
        if not silencioso:
            print(f"❌ No se pudieron obtener vencimientos desde Polygon: {e}")
        return None, None

    contratos = data.get("results", []) or []
    exps = sorted({c["expiration_date"] for c in contratos if c.get("expiration_date")})
    if not exps:
        if not silencioso:
            print("❌ La lista de vencimientos está vacía.")
        return None, None

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
        })
    return pd.DataFrame(filas)


def descargar_cadena_0dte(ticker, vencimiento):
    """
    Descarga (paginando) el snapshot completo de la cadena del vencimiento
    0DTE/más próximo. Devuelve la lista cruda de contratos.
    """
    return _polygon_get_all_pages(
        f"/v3/snapshot/options/{tickers_polygon(ticker)[1]}",
        params={"expiration_date": vencimiento, "limit": 250},
    )


def obtener_cadena_0dte(contratos, vencimiento, S, dias, r=0.045):
    """
    Prepara la cadena completa (calls/puts) ya descargada: IV inferida por
    contrato donde falte, greeks BSM, GEX por contrato y clasificación de
    flujo (volumen/OI). Devuelve (calls, puts, n_excluidos_gex), donde
    n_excluidos_gex cuenta los contratos con OI > 0 que siguen sin IV
    (ni de Polygon ni inferida) y por tanto quedan fuera del GEX.
    """
    try:
        if not contratos:
            print(f"   ⚠️  Polygon no devolvió contratos para {vencimiento}")
            return None, None, 0

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
            return None, None, 0

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

        # IV por contrato: donde Polygon no la trae (a menudo strikes ITM,
        # o toda la cadena fuera de horario) se infiere invirtiendo BSM
        # sobre el último precio disponible. Sin esto esos contratos
        # quedaban con gamma 0 y sesgaban el Net GEX.
        calls, n_inf_c = inferir_iv_faltante(calls, S, T, r, "call")
        puts,  n_inf_p = inferir_iv_faltante(puts,  S, T, r, "put")
        if n_inf_c + n_inf_p:
            print(f"   🔧 IV inferida vía BSM inverso en contratos sin IV: calls {n_inf_c} | puts {n_inf_p}")

        if not ((calls["impliedVolatility"] > 0).any() or (puts["impliedVolatility"] > 0).any()):
            print(f"   ⚠️  Ningún contrato tiene IV ni lastPrice utilizable — no se puede inferir IV. Saltando.")
            return None, None, 0

        # Los que siguen sin IV (inversión fallida: sin precio o fuera de
        # rango) quedan con gamma 0, es decir, fuera del GEX. Solo cuentan
        # los que tienen OI, que son los que aportarían GEX.
        excl_c = int(((calls["impliedVolatility"] <= 0) & (calls["openInterest"] > 0)).sum())
        excl_p = int(((puts["impliedVolatility"]  <= 0) & (puts["openInterest"]  > 0)).sum())
        if excl_c + excl_p:
            print(f"   ⚠️  Sin IV (inversión fallida), excluidos del GEX: calls {excl_c} | puts {excl_p}")

        calls = calcular_greeks_cadena(calls, S, T, r, tipo="call")
        puts  = calcular_greeks_cadena(puts,  S, T, r, tipo="put")
        calls = clasificar_flujo(calls, tipo="call")
        puts  = clasificar_flujo(puts,  tipo="put")

        return calls, puts, excl_c + excl_p

    except Exception as e:
        print(f"   ⚠️  Error preparando cadena para {vencimiento}: {e}")
        import traceback
        traceback.print_exc()
        return None, None, 0


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


def calcular_zero_gamma_level(gex_df, S=None):
    """
    Encuentra el "Gamma Flip" (Zero Gamma Level): el strike (interpolado)
    donde el Net GEX cruza de positivo a negativo (o viceversa). Es el
    punto de inflexión entre régimen Long Gamma (precio contenido) y Short
    Gamma (movimientos amplificados) — clave para leer inestabilidad 0DTE.

    Con IV inferida (fallback BSM inverso en strikes OTM ilíquidos, ver
    inferir_iv_faltante) puede haber más de un cruce de signo: ruido en la
    gamma de strikes muy alejados con OI alto genera cruces espurios ahí.
    Si se pasa `S`, se devuelve el cruce más cercano al spot — el único
    económicamente relevante — en vez del primero encontrado escaneando
    desde el strike más bajo. Devuelve None si no hay ningún cruce.
    """
    if gex_df is None or gex_df.empty or len(gex_df) < 2:
        return None

    df = gex_df.sort_values("strike").reset_index(drop=True)
    cruces = []
    for i in range(len(df) - 1):
        k1, g1 = df.loc[i, "strike"], df.loc[i, "net_gex"]
        k2, g2 = df.loc[i + 1, "strike"], df.loc[i + 1, "net_gex"]
        if g1 == 0:
            cruces.append(float(k1))
            continue
        if (g1 < 0) != (g2 < 0):
            frac = -g1 / (g2 - g1) if (g2 - g1) != 0 else 0
            cruces.append(float(k1 + frac * (k2 - k1)))

    if not cruces:
        return None
    if S is None:
        return cruces[0]
    return min(cruces, key=lambda k: abs(k - S))


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


def identificar_picos_gamma_bruta(merged, top_n=3):
    """
    Strikes con mayor gamma bruta (|GEX calls| + |GEX puts|): los puntos de
    mayor actividad TOTAL de cobertura de dealers, sin importar el signo
    neto. A diferencia del Net GEX —que puede esconder actividad cuando
    calls y puts se cancelan entre sí—, esto señala zonas de "pinning"/imán
    y mayor probabilidad de whipsaw intradía. Ver discusión en el README
    del panel: gross gamma = dónde hay fricción, net gamma = hacia dónde
    empuja esa fricción.
    """
    if merged is None or merged.empty or "gross_gex" not in merged.columns:
        return []
    top = merged[merged["gross_gex"] > 0].nlargest(top_n, "gross_gex")
    return list(zip(top["strike"].tolist(), top["gross_gex"].tolist()))


# ─────────────────────────────────────────────
#  UMBRALES ESCALADOS (SPY sin cambios, SPX comparable)
# ─────────────────────────────────────────────
#
# Los umbrales eran constantes en escala SPY (≈ $670). Con SPX (≈ $7.800,
# contratos ≈ 10× más grandes en dólares) quedaban siempre disparados. Ahora
# se expresan relativos al spot o al movimiento esperado y, en el spot de
# referencia, reproducen exactamente los valores anteriores.

SPOT_REFERENCIA = 670.0            # SPY en el momento en que se fijaron los umbrales antiguos

# Max Pain: distancia mínima spot-max pain para votar. Antes: $2 fijos.
# Ahora: MAX_PAIN_FRACCION_STRADDLE × straddle ATM (≈ $2,1 en SPY a 1DTE),
# acotado a [0,1%, 0,6%] del spot por si el último precio de las opciones
# ATM es viejo; sin straddle utilizable: 0,3% del spot ($2,0 en SPY@670).
MAX_PAIN_FRACCION_STRADDLE = 0.5
MAX_PAIN_MIN_FRAC_SPOT = 0.001
MAX_PAIN_MAX_FRAC_SPOT = 0.006
MAX_PAIN_FRAC_SPOT_RESPALDO = 2.0 / SPOT_REFERENCIA

# Posicionamiento de dealers: umbral de Net GEX "fuerte". Antes: ±5e8 sobre el
# gex del script (OI·gamma·100·S, delta-shares-por-unidad-relativa). Ahora se
# mide en dólares por 1% de movimiento (gex · S · 0,01), la unidad comparable
# entre subyacentes: 5e8 · 670 · 0,01 = 3,35e9 $ por 1%.
GEX_FUERTE_USD_POR_1PCT = 5e8 * SPOT_REFERENCIA * 0.01

# Smart money: precio mínimo de la opción. Antes: $0,10 fijo (SPY). Ahora
# 0,10 · S / SPOT_REFERENCIA (≈ $1,16 en SPX@7.800).
SMART_MONEY_PRECIO_MIN_SPY = 0.10


def straddle_atm(calls, puts, S):
    """
    Precio del straddle del strike más cercano al spot con precio de call y
    put (lastPrice > 0). None si no hay ninguno.
    """
    if calls is None or puts is None or calls.empty or puts.empty:
        return None
    m = pd.merge(calls[["strike", "lastPrice"]], puts[["strike", "lastPrice"]],
                 on="strike", suffixes=("_c", "_p")).dropna()
    m = m[(m["lastPrice_c"] > 0) & (m["lastPrice_p"] > 0)]
    if m.empty:
        return None
    fila = m.loc[(m["strike"] - S).abs().idxmin()]
    return float(fila["lastPrice_c"] + fila["lastPrice_p"])


def calcular_umbral_max_pain(S, calls=None, puts=None):
    """Distancia spot-max pain (en $) a partir de la cual el max pain vota. Ver MAX_PAIN_*."""
    straddle = straddle_atm(calls, puts, S)
    if straddle is None:
        return S * MAX_PAIN_FRAC_SPOT_RESPALDO
    umbral = MAX_PAIN_FRACCION_STRADDLE * straddle
    return min(max(umbral, S * MAX_PAIN_MIN_FRAC_SPOT), S * MAX_PAIN_MAX_FRAC_SPOT)


def umbral_gex_fuerte(S=None):
    """Umbral de Net GEX fuerte en unidades del gex del script. Sin spot: el valor SPY original (5e8)."""
    if not S:
        return GEX_FUERTE_USD_POR_1PCT / (SPOT_REFERENCIA * 0.01)
    return GEX_FUERTE_USD_POR_1PCT / (S * 0.01)


def posicionamiento_dealers_real(calls, puts, S=None):
    if "gex" not in calls.columns:
        return "SIN DATOS", 0, "No calculado"
    net = calls["gex"].sum() + puts["gex"].sum()
    fuerte = umbral_gex_fuerte(S)
    if net > fuerte:
        status, comp = "LONG GAMMA FUERTE", "Reversiones rápidas, precio contenido"
    elif net > 0:
        status, comp = "LONG GAMMA DÉBIL", "Estabilización leve, puede rotar"
    elif net > -fuerte:
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


def detectar_smart_money(calls, puts, S=None):
    precio_min = SMART_MONEY_PRECIO_MIN_SPY * (S / SPOT_REFERENCIA if S else 1.0)

    def filtrar(df):
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

        smart = d[(d["activity_pct"] > 0.6) & (d["iv_pct"] > 0.4) & (d["lastPrice"] > precio_min)].copy()
        col_act = "openInterest" if uso_oi else "volume"
        smart = smart.rename(columns={col_act: "volume"})
        cols_norm = ["strike", "volume", "impliedVolatility", "premium_paid", "tipo_flujo", "señal_direccional"]
        return smart.nlargest(5, "premium_paid")[cols_norm] if not smart.empty else pd.DataFrame(columns=cols_norm)

    return filtrar(calls), filtrar(puts)


def consenso_direccional_0dte(max_pain, precio, status_gamma, pc_df, smart_calls, smart_puts,
                               zero_gamma, call_wall, put_wall, umbral_max_pain=None):
    """
    Consenso direccional intradía. Pesa Max Pain, régimen de gamma de
    dealers, posición del spot frente al Gamma Flip y los muros de
    Calls/Puts, PC ratio y actividad de smart money (apertura nueva).

    umbral_max_pain: distancia spot-max pain (en $) que activa el voto del
    max pain (ver calcular_umbral_max_pain). Sin valor: 0,3% del spot.
    """
    votos   = {"ALCISTA": 0, "BAJISTA": 0, "NEUTRAL": 0}
    bullets = []

    if umbral_max_pain is None:
        umbral_max_pain = precio * MAX_PAIN_FRAC_SPOT_RESPALDO
    dist = precio - max_pain
    if dist > umbral_max_pain:
        votos["BAJISTA"] += 2
        bullets.append(f"Max Pain 0DTE en ${max_pain:.0f} → presión bajista intradía")
    elif dist < -umbral_max_pain:
        votos["ALCISTA"] += 2
        bullets.append(f"Max Pain 0DTE en ${max_pain:.0f} → presión alcista intradía")
    else:
        votos["NEUTRAL"] += 1
        bullets.append(f"Precio cerca del Max Pain 0DTE ${max_pain:.0f} (±{umbral_max_pain:.2f}) → pin neutro")

    if "LONG" in status_gamma:
        votos["NEUTRAL"] += 1
        bullets.append("Dealers Long Gamma → movimientos contenidos, sin dirección clara")
    else:
        bullets.append("Dealers Short Gamma → amplificación de movimientos, riesgo de breakout")

    if zero_gamma is not None:
        if precio > zero_gamma:
            votos["NEUTRAL"] += 1
            bullets.append(f"Spot por encima del Gamma Flip (${zero_gamma:.0f}) → régimen long gamma local")
        else:
            bullets.append(f"Spot por debajo del Gamma Flip (${zero_gamma:.0f}) → régimen short gamma local")

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

    total       = sum(votos.values()) or 1
    max_votos   = max(votos.values())
    ganadores   = [d for d, v in votos.items() if v == max_votos]
    # Empate entre ALCISTA/BAJISTA (o los tres) → no hay consenso direccional real.
    direccion   = ganadores[0] if len(ganadores) == 1 else "NEUTRAL"
    confianza   = round((max_votos / total) * 100)
    return direccion, confianza, bullets


# ─────────────────────────────────────────────
#  PREPARACIÓN DE DATOS PARA VISUALIZACIÓN
# ─────────────────────────────────────────────

def _preparar_merged_0dte(calls, puts, S):
    """
    Construye el DataFrame 'merged' (calls+puts por strike, con GEX,
    flujo, premium, etc.) acotado a ±3% alrededor del spot — el rango
    relevante para leer muros de gamma intradía. Si ese rango queda con
    muy pocos strikes, se expande a ±5%, ±8% y ±15%.
    Devuelve (merged, modo_label, ventana_label) o (None, None, None);
    ventana_label describe el rango realmente usado (p. ej. "±5% del spot").
    """
    for margen in (0.03, 0.05, 0.08, 0.15):
        calls_r = calls[(calls["strike"] >= S * (1 - margen)) & (calls["strike"] <= S * (1 + margen))].copy()
        puts_r  = puts[(puts["strike"]  >= S * (1 - margen)) & (puts["strike"]  <= S * (1 + margen))].copy()
        if len(calls_r) + len(puts_r) >= 6:
            break
    ventana_label = f"±{margen * 100:.0f}% del spot"

    if calls_r.empty or puts_r.empty:
        ventana_label = "cadena completa"
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
    merged["gross_gex"]     = merged["gex_c"].abs() + merged["gex_p"].abs()

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

    return merged, modo_label, ventana_label


def _tabla_gex_html(merged, S, max_pain, zero_gamma, call_wall, put_wall):
    """
    Tabla HTML pura (sin JS) con barras de color por CSS, resaltando
    spot, Max Pain, Gamma Flip, Call Wall y Put Wall — respaldo
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
            etiquetas.append("GAMMA FLIP")

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
        <span><span class="leg-dot" style="background:#1a1a2e;border:1px solid #30363d"></span>Gamma Flip ({zg_txt})</span>
      </div>
    </div>"""


# ─────────────────────────────────────────────
#  FIGURA PLOTLY — DASHBOARD ÚNICO 0DTE
# ─────────────────────────────────────────────

def _build_dashboard_fig(merged, ticker, exp, dias, S, max_pain, zero_gamma, call_wall, put_wall,
                          status, direccion, confianza, modo_label, picos_gamma_bruta=None):
    """
    Figura consolidada de 4 paneles para el vencimiento 0DTE/más próximo:
      ① Net GEX por strike, con Gamma Flip y muros de Calls/Puts.
      ② Gamma bruta (|GEX calls| + |GEX puts|) — dónde hay más actividad
         TOTAL de cobertura de dealers, con o sin cancelarse en el neto.
         Señala zonas de "pinning"/imán y mayor probabilidad de whipsaw.
      ③ Volumen Call vs Put por strike.
      ④ Premium $ neto (Calls - Puts) por strike.
    """
    picos_gamma_bruta = picos_gamma_bruta or []
    strike_l        = merged["strike"].tolist()
    net_gex_l       = merged["net_gex_local"].tolist()
    gross_gex_l     = merged["gross_gex"].tolist()
    vol_c_l         = merged["volume_c"].tolist()
    vol_p_l         = merged["volume_p"].tolist()
    premium_c_l     = merged["premium_c"].tolist()
    premium_p_neg_l = (-merged["premium_p"]).tolist()
    net_prem_l      = merged["net_prem"].tolist()

    color_gex   = merged["color_gex"].tolist()
    color_prem  = merged["color_prem"].tolist()
    symbol_prem = ["triangle-up" if x > 0 else "triangle-down" for x in net_prem_l]

    strikes_pico    = {s for s, _ in picos_gamma_bruta}
    color_gross     = ["#f59e0b" if s in strikes_pico else "#475569" for s in strike_l]
    line_gross      = ["#fbbf24" if s in strikes_pico else "rgba(0,0,0,0.3)" for s in strike_l]

    fig = make_subplots(
        rows=4, cols=1,
        subplot_titles=(
            "① NET GEX (Gamma Exposure BSM) — Gamma Flip y Muros Calls/Puts",
            "② GAMMA BRUTA (|Calls| + |Puts|) — picos de concentración / pinning",
            f"③ VOLUMEN Call vs Put — {modo_label}",
            "④ PREMIUM NETO $ (Calls − Puts) — dónde va el dinero intradía"
        ),
        vertical_spacing=0.07,
        row_heights=[0.30, 0.20, 0.22, 0.28]
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

    # ② Gamma bruta — picos de concentración resaltados
    fig.add_trace(go.Bar(x=strike_l, y=gross_gex_l, name="Gamma bruta",
        marker=dict(color=color_gross, line=dict(color=line_gross, width=1.2)),
        hovertemplate="<b>$%{x:.0f}</b><br>Gamma bruta: %{y:,.0f}<extra></extra>"), row=2, col=1)
    for strike_pico, valor_pico in picos_gamma_bruta:
        fig.add_annotation(x=strike_pico, y=valor_pico, row=2, col=1,
            text=f"🔥 ${strike_pico:.0f}", showarrow=True, arrowhead=2, arrowcolor="#f59e0b",
            ax=0, ay=-22, font=dict(size=10, color="#fbbf24"),
            bgcolor="rgba(20,20,30,0.85)", bordercolor="#f59e0b", borderwidth=1)

    # ③ Volumen Call vs Put
    fig.add_trace(go.Bar(x=strike_l, y=vol_c_l, name="Vol Calls", marker_color="#4ade80",
        hovertemplate="<b>$%{x:.0f}</b><br>Vol Calls: %{y:,.0f}<extra></extra>"), row=3, col=1)
    fig.add_trace(go.Bar(x=strike_l, y=vol_p_l, name="Vol Puts", marker_color="#f87171",
        hovertemplate="<b>$%{x:.0f}</b><br>Vol Puts: %{y:,.0f}<extra></extra>"), row=3, col=1)

    # ④ Premium neto
    fig.add_trace(go.Bar(x=strike_l, y=premium_c_l, name="Premium Calls", marker_color="#16a34a",
        hovertemplate="<b>$%{x:.0f}</b><br>Premium calls: $%{y:,.0f}<extra></extra>"), row=4, col=1)
    fig.add_trace(go.Bar(x=strike_l, y=premium_p_neg_l, name="Premium Puts", marker_color="#dc2626",
        hovertemplate="<b>$%{x:.0f}</b><br>Premium puts: $%{y:,.0f}<extra></extra>"), row=4, col=1)
    fig.add_trace(go.Scatter(x=strike_l, y=net_prem_l, name="Neto (C-P)", mode="lines+markers",
        line=dict(color="white", width=2, dash="dot"),
        marker=dict(size=8, color=color_prem, symbol=symbol_prem),
        hovertemplate="<b>$%{x:.0f}</b><br>Neto: $%{y:,.0f}<extra></extra>"), row=4, col=1)

    for row_n in [1, 2, 3, 4]:
        fig.add_vline(x=S, line=dict(color="#fbbf24", width=2, dash="dash"),
                      annotation_text=f"Spot ${S:.2f}" if row_n == 1 else "",
                      annotation_position="top right" if row_n != 1 else "bottom right",
                      row=row_n, col=1)
        fig.add_vline(x=max_pain, line=dict(color="#fb923c", width=1.5, dash="dot"),
                      annotation_text=f"Max Pain ${max_pain:.0f}" if row_n == 3 else "",
                      annotation_position="bottom right", row=row_n, col=1)
        if zero_gamma is not None:
            fig.add_vline(x=zero_gamma, line=dict(color="#a78bfa", width=2.5, dash="dashdot"),
                          annotation_text=f"⚡ Gamma Flip ${zero_gamma:.0f}" if row_n == 1 else "",
                          annotation_font=dict(size=13, color="#a78bfa"),
                          annotation_bgcolor="rgba(20,20,30,0.85)",
                          annotation_position="top left",
                          row=row_n, col=1)
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
        height=1300, template="plotly_dark", barmode="overlay",
        showlegend=True, hovermode="x unified",
        legend=dict(orientation="h", y=-0.03, x=0.5, xanchor="center"),
        margin=dict(t=60, b=50, l=55, r=55),
        uirevision="0dte-dashboard"
    )

    # Rango de eje X compartido y explícito en los 4 paneles: evita que una
    # anotación (spot, max pain, zero gamma, muros) fuera del rango de
    # strikes graficado estire un panel más que los otros.
    x_min, x_max = min(strike_l), max(strike_l)
    margen_x = (x_max - x_min) * 0.03 if x_max > x_min else 1
    x_range = [x_min - margen_x, x_max + margen_x]
    for row_n in [1, 2, 3, 4]:
        fig.update_xaxes(range=x_range, row=row_n, col=1)

    fig.update_yaxes(title_text="GEX ($)", row=1, col=1)
    fig.update_yaxes(title_text="Gamma bruta ($)", row=2, col=1)
    fig.update_yaxes(title_text="Volumen", row=3, col=1)
    fig.update_yaxes(title_text="Premium $", row=4, col=1)
    fig.update_xaxes(title_text="Strike ($)", row=4, col=1)
    return fig


# ─────────────────────────────────────────────
#  DASHBOARD HTML — VISTA ÚNICA (SIN PESTAÑAS)
# ─────────────────────────────────────────────

def crear_dashboard_0dte(data, ticker, output_dir=None, refresh_seconds=60):
    """
    Construye el dashboard HTML consolidado en una sola vista:
      - Panel superior: Spot, Max Pain 0DTE, Posicionamiento de dealers,
        Consenso direccional 0DTE con confianza.
      - Tabla + gráfico de Net GEX / Muros por strike.
      - Gráfico de Volumen Call vs Put y Premium $ neto intradía.
    """
    exp, dias, S       = data["exp"], data["dias"], data["S"]
    spot_fuente        = data["spot_fuente"]
    spot_hora          = data["spot_hora"]
    ventana_label      = data["ventana_label"]
    n_excluidos_gex    = data["n_excluidos_gex"]
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
    picos_gamma_bruta  = data.get("picos_gamma_bruta") or []

    if merged is None or merged.empty:
        print("❌ Sin datos suficientes para construir el dashboard.")
        return None

    tabla_gex = _tabla_gex_html(merged, S, max_pain, zero_gamma, call_wall, put_wall)
    fig       = _build_dashboard_fig(merged, ticker, exp, dias, S, max_pain, zero_gamma,
                                      call_wall, put_wall, status, direccion, confianza, modo_label,
                                      picos_gamma_bruta=picos_gamma_bruta)
    fig_json  = fig.to_json()

    ts = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    refresh_ms = int(refresh_seconds * 1000)
    auto_refresh_js = f"""
var __data_url    = "{ticker.upper()}_0DTE_data.json";
var __refresh_ms  = {refresh_ms};
var __seg_restantes = {refresh_seconds};
var __contador_el = document.getElementById('countdown');

function __actualizar_dom(d) {{
  document.getElementById('header-price').textContent = d.header_price;
  document.getElementById('header-sub').textContent   = d.header_sub;
  document.getElementById('spot-source').textContent  = d.spot_source;
  document.getElementById('data-warnings').innerHTML  = d.warnings_html;
  document.getElementById('gex-section-title').textContent = d.gex_section_title;
  document.getElementById('metrics-grid').innerHTML   = d.metrics_html;
  document.getElementById('bullets-section').innerHTML = d.bullets_html;
  document.getElementById('gex-table-container').innerHTML = d.tabla_html;
  Plotly.react('plot-main', d.fig.data, d.fig.layout, {{responsive:true}});
}}

async function __refrescar() {{
  try {{
    var resp = await fetch(__data_url + '?v=' + Date.now(), {{cache: 'no-store'}});
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    var d = await resp.json();
    __actualizar_dom(d);
  }} catch (e) {{
    console.warn('No se pudo refrescar datos en vivo:', e);
  }} finally {{
    __seg_restantes = {refresh_seconds};
  }}
}}

setInterval(__refrescar, __refresh_ms);
setInterval(function() {{
  __seg_restantes -= 1;
  if (__seg_restantes < 0) __seg_restantes = 0;
  if (__contador_el) __contador_el.textContent = __seg_restantes + 's';
}}, 1000);
"""
    countdown_html = f'<span id="countdown">{refresh_seconds}s</span>'

    spot_source = f"Spot: {spot_fuente} · {spot_hora}"
    gex_section_title = f"Net GEX y Muros por Strike ({ventana_label})"
    warnings_html = (
        f"⚠️ <b>{n_excluidos_gex} contratos con OI</b> sin IV (ni de Polygon ni inferida por BSM inverso) "
        f"quedaron <b>excluidos del GEX</b>." if n_excluidos_gex else ""
    )

    def card(label, value, sub="", color="#22c55e", accent=False):
        extra_class = " metric-card--accent" if accent else ""
        return f"""
        <div class="metric-card{extra_class}">
          <div class="metric-label">{label}</div>
          <div class="metric-value" style="color:{color}">{value}</div>
          <div class="metric-sub">{sub}</div>
        </div>"""

    dir_color = "#22c55e" if direccion == "ALCISTA" else ("#ef4444" if direccion == "BAJISTA" else "#94a3b8")
    emoji_dir = "🟢" if direccion == "ALCISTA" else ("🔴" if direccion == "BAJISTA" else "⚖️")
    etiqueta_venc = "0DTE (vence hoy)" if dias == 0 else f"vencimiento más próximo ({dias}d)"
    header_price = f"${S:.2f}"
    header_sub   = (f"Análisis institucional · {ts} · Vencimiento {exp} ({etiqueta_venc}) · "
                    f"Spot {spot_fuente} ({spot_hora}) · Gamma BSM real")

    if zero_gamma is not None:
        gamma_flip_val   = f"${zero_gamma:.0f}"
        if S > zero_gamma:
            gamma_flip_color = "#22c55e"
            gamma_flip_sub   = f"Spot en zona LONG GAMMA · dist: {S - zero_gamma:+.2f}"
        else:
            gamma_flip_color = "#ef4444"
            gamma_flip_sub   = f"Spot en zona SHORT GAMMA · dist: {S - zero_gamma:+.2f}"
    else:
        gamma_flip_val   = "N/D"
        gamma_flip_color = "#94a3b8"
        gamma_flip_sub   = "Sin cruce de signo en el rango graficado"

    if picos_gamma_bruta:
        pico_strike, pico_valor = picos_gamma_bruta[0]
        gamma_pico_val = f"${pico_strike:.0f}"
        gamma_pico_sub = f"Gamma bruta: {pico_valor:,.0f} · zona de pinning/whipsaw"
    else:
        gamma_pico_val = "N/D"
        gamma_pico_sub = "Sin concentración destacable en el rango"

    metric_cards = (
        card("Precio Spot", f"${S:.2f}", etiqueta_venc, "#f0f6fc")
        + card("Max Pain 0DTE", f"${max_pain:.0f}", f"Distancia al spot: {S - max_pain:+.2f}", "#fb923c")
        + card("⚡ Gamma Flip", gamma_flip_val, gamma_flip_sub, gamma_flip_color, accent=True)
        + card("🔥 Pico Gamma Bruta", gamma_pico_val, gamma_pico_sub, "#f59e0b", accent=True)
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
  .metric-card--accent {{ border: 1px solid #4c3d8f; border-left: 3px solid #a78bfa; background: #191830; box-shadow: 0 0 0 1px rgba(167,139,250,0.08); }}
  .metric-label {{ font-size: 12px; color: #8b949e; margin-bottom: 5px; }}
  .metric-value {{ font-size: 18px; font-weight: 600; }}
  .metric-sub {{ font-size: 11px; color: #6e7681; margin-top: 4px; }}
  .bullets-section {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 12px 14px; }}
  .bullet-item {{ font-size: 12px; color: #8b949e; line-height: 1.7; }}
  .gamma-flip-note {{ background: #191830; border: 1px solid #4c3d8f; border-radius: 6px; padding: 8px 14px; margin-bottom: 16px; font-size: 12px; color: #b3a5e0; line-height: 1.6; }}
  .gamma-flip-note b {{ color: #c4b5fd; }}
  .legend-row {{ display: flex; gap: 18px; flex-wrap: wrap; padding: 10px 28px 0; font-size: 12px; color: #8b949e; }}
  .leg-dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 5px; vertical-align: middle; }}
  .status-banner {{ background: #1c2333; border: 1px solid #30363d; border-radius: 6px; padding: 8px 16px; margin: 10px 28px; font-size: 12px; color: #8b949e; }}
  .status-banner b {{ color: #58a6ff; }}
  .status-banner--warn {{ border-color: #9a6700; color: #d29922; }}
  .status-banner--warn:empty {{ display: none; }}
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
    <div class="header-sub" id="header-sub">{header_sub}</div>
  </div>
  <div style="text-align:right">
    <div class="header-price" id="header-price">{header_price}</div>
    <div class="header-change" id="spot-source">{spot_source}</div>
  </div>
</div>

<div class="status-banner">
  ℹ️ Spot: <b>Capital.com</b> (punto medio bid/offer; SPX = US500 + base; si falla, paridad put-call estimada sobre la cadena).
  Opciones: <b>último snapshot de Polygon.io</b> (el retraso depende del plan; sin last trade/quote se usa el cierre del día del contrato).
  Para 0DTE el <b>volumen del día</b> es la métrica reina — el OI de la mañana proviene del cierre anterior.
  &nbsp;·&nbsp; 🔄 Próxima actualización en: <b>{countdown_html}</b>
</div>
<div class="status-banner status-banner--warn" id="data-warnings">{warnings_html}</div>

<div class="decision-panel">
  <div class="panel-title">Panel de decisión — 0DTE</div>
  <div class="metrics-grid" id="metrics-grid">
    {metric_cards}
  </div>
  <div class="gamma-flip-note">
    ⚡ <b>Gamma Flip</b>: strike donde el Net GEX cruza de positivo a negativo. Por encima → régimen <b>Long Gamma</b>
    (dealers absorben el movimiento, precio contenido). Por debajo → régimen <b>Short Gamma</b> (dealers amplifican
    el movimiento, mayor riesgo de breakout). Convención asumida: gamma de calls positiva / gamma de puts negativa
    (posicionamiento estándar de dealers), igual que en el resto del Net GEX de este dashboard.
  </div>
  <div class="panel-title" style="margin-top:16px">Señales detectadas</div>
  <div class="bullets-section" id="bullets-section">{bullets_html}</div>
</div>

<div class="legend-row">
  <span><span class="leg-dot" style="background:#fbbf24"></span>Precio spot</span>
  <span><span class="leg-dot" style="background:#fb923c"></span>Max Pain</span>
  <span><span class="leg-dot" style="background:#22c55e"></span>Muro Calls / Alcista</span>
  <span><span class="leg-dot" style="background:#ef4444"></span>Muro Puts / Bajista</span>
  <span><span class="leg-dot" style="background:#a78bfa"></span>Gamma Flip</span>
</div>

<div class="main-content">
  <div class="section-title" id="gex-section-title">{gex_section_title}</div>
  <div id="gex-table-container">{tabla_gex}</div>
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

    # Payload JSON con las piezas dinámicas: el navegador ya abierto lo
    # consulta por fetch() cada `refresh_seconds` y actualiza el DOM y el
    # gráfico (Plotly.react) in-place, sin recargar la página completa.
    payload = {
        "header_price": header_price,
        "header_sub": header_sub,
        "spot_source": spot_source,
        "warnings_html": warnings_html,
        "gex_section_title": gex_section_title,
        "metrics_html": metric_cards,
        "bullets_html": bullets_html,
        "tabla_html": tabla_gex,
        "fig": json.loads(fig_json),
    }
    fname_json = os.path.join(output_dir, f"{ticker.upper()}_0DTE_data.json")
    with open(fname_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)

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
        socketserver.TCPServer.allow_reuse_address = True
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
    refresh_seconds = max(int(refresh_seconds), MIN_REFRESH_SECONDS)
    print(f"\n{'='*80}")
    print(f"   ANÁLISIS INSTITUCIONAL DE OPCIONES — 0DTE / INTRADÍA — {ticker.upper()}")
    print(f"   Gamma BSM real · Gamma Flip · Muros Calls/Puts · Flujo Vol/OI")
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

            try:
                print("📡 Conectando con Polygon.io…")
                exp, dias = seleccionar_vencimiento_0dte(ticker)
                if not exp:
                    print(f"   ⏳ Reintentando en {refresh_seconds}s…")
                    time.sleep(refresh_seconds)
                    continue

                print(f"\n{'─'*60}")
                print(f"🔍 Analizando vencimiento 0DTE/más próximo: {exp} ({dias}d)")

                # La cadena se descarga antes del spot para que la paridad
                # put-call de respaldo la reutilice sin llamadas extra.
                contratos = descargar_cadena_0dte(ticker, exp)

                S, spot_fuente, spot_hora = obtener_precio(ticker, contratos)
                if not S:
                    print(f"   ⏳ Reintentando en {refresh_seconds}s…")
                    time.sleep(refresh_seconds)
                    continue

                calls, puts, n_excluidos_gex = obtener_cadena_0dte(contratos, exp, S, dias)
                if calls is None:
                    print(f"   ⛔ Sin cadena disponible para {exp}")
                    print(f"   ⏳ Reintentando en {refresh_seconds}s…")
                    time.sleep(refresh_seconds)
                    continue

                merged, modo_label, ventana_label = _preparar_merged_0dte(calls, puts, S)
                if merged is None:
                    print(f"   ⛔ Sin datos suficientes alrededor del spot para {exp}")
                    print(f"   ⏳ Reintentando en {refresh_seconds}s…")
                    time.sleep(refresh_seconds)
                    continue

                # Gamma Flip y muros se calculan SOBRE LA MISMA VENTANA que
                # se grafica (no sobre toda la cadena) — así el panel de Net GEX
                # comparte el mismo rango de strikes que Volumen y Premium, y se
                # evita "ruido" de strikes muy OTM con IV inferida poco confiable.
                gex_ventana = (merged[["strike", "net_gex_local"]]
                               .rename(columns={"net_gex_local": "net_gex"})
                               .sort_values("strike").reset_index(drop=True))

                max_pain               = calcular_max_pain(calls, puts, S=S)
                zero_gamma             = calcular_zero_gamma_level(gex_ventana, S=S)
                call_wall, put_wall    = identificar_muros_gex(gex_ventana)
                picos_gamma_bruta      = identificar_picos_gamma_bruta(merged, top_n=3)
                status_g, net_g, _     = posicionamiento_dealers_real(calls, puts, S=S)
                pc_df                  = ratio_pc_enriquecido(calls, puts)
                sm_calls, sm_puts      = detectar_smart_money(calls, puts, S=S)
                umbral_mp              = calcular_umbral_max_pain(S, calls, puts)
                direccion, confianza, bullets = consenso_direccional_0dte(
                    max_pain, S, status_g, pc_df, sm_calls, sm_puts, zero_gamma, call_wall, put_wall,
                    umbral_max_pain=umbral_mp
                )

                dist = S - max_pain
                print(f"\n  Max Pain 0DTE: ${max_pain:.2f}  (distancia: {dist:+.2f}, umbral ±{umbral_mp:.2f})")
                print(f"  Posicionamiento dealers: {status_g}")
                print(f"  Net GEX real: {net_g:,.0f}  ({net_g * S * 0.01:,.0f} $ por 1%)")
                print(f"  Gamma Flip: {'$'+format(zero_gamma, ',.2f') if zero_gamma is not None else 'sin cruce en rango'}")
                print(f"  Muro Calls (resistencia): {'$'+format(call_wall, ',.0f') if call_wall is not None else 'n/d'}")
                print(f"  Muro Puts (soporte): {'$'+format(put_wall, ',.0f') if put_wall is not None else 'n/d'}")
                if picos_gamma_bruta:
                    picos_txt = ", ".join(f"${s:.0f} ({v:,.0f})" for s, v in picos_gamma_bruta)
                    print(f"  🔥 Picos de gamma bruta: {picos_txt}")

                emoji = "🟢" if direccion == "ALCISTA" else ("🔴" if direccion == "BAJISTA" else "⚖️")
                print(f"  {emoji} CONSENSO 0DTE: {direccion}  |  Confianza: {confianza}%")

                data = {
                    "exp": exp, "dias": dias, "S": S,
                    "spot_fuente": spot_fuente, "spot_hora": spot_hora,
                    "max_pain": max_pain,
                    "merged": merged, "modo_label": modo_label, "ventana_label": ventana_label,
                    "n_excluidos_gex": n_excluidos_gex,
                    "zero_gamma": zero_gamma, "call_wall": call_wall, "put_wall": put_wall,
                    "picos_gamma_bruta": picos_gamma_bruta,
                    "status_gamma": status_g,
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
                    print("   La página se actualiza sola en vivo cada ciclo (sin recargar) — no hace falta reabrirla.")

                elapsed = time.time() - inicio_ciclo
                espera = max(refresh_seconds - elapsed, 1)
                print(f"\n✅ Ciclo #{ciclo} completo en {elapsed:.1f}s — próxima actualización en {espera:.0f}s.")
                print("   Presiona Ctrl+C para detener.")
                time.sleep(espera)

            except Exception as e:
                # Un error inesperado en un ciclo (respuesta corrupta, fallo de
                # red puntual, etc.) no debe tumbar todo el monitor continuo:
                # se registra y se reintenta en el siguiente ciclo.
                print(f"\n⚠️  Error inesperado en el ciclo #{ciclo}: {e}")
                import traceback
                traceback.print_exc()
                print(f"   ⏳ Reintentando en {refresh_seconds}s…")
                time.sleep(refresh_seconds)

    except KeyboardInterrupt:
        _capital.logout()
        print("\n\n🛑 Detenido por el usuario. Servidor cerrado.")


def _parse_args(argv):
    """
    CLI opcional; sin argumentos se conserva el modo interactivo.
    El subyacente también puede venir de OPTIONS_TICKER y la base SPX de SPX_BASIS.
    """
    p = argparse.ArgumentParser(description="Análisis 0DTE / intradía de opciones con Polygon.io")
    p.add_argument("--ticker", type=lambda s: s.strip().upper(), default=None,
                   help=f"Subyacente (SPY, SPX, ...). Por defecto {TICKER_POR_DEFECTO}.")
    p.add_argument("--refresh", type=int, default=None,
                   help=f"Segundos entre ciclos (mín. {MIN_REFRESH_SECONDS}).")
    p.add_argument("--spx-basis", type=float, default=None,
                   help="Base SPX − US500 en puntos, de respaldo si no se calcula por paridad "
                        f"(por defecto SPX_BASIS o {SPX_BASIS_DEFAULT:+.2f}).")
    return p.parse_args(argv)


if __name__ == "__main__":
    warnings.filterwarnings("ignore")

    print("\n" + "="*80)
    print("   ANÁLISIS INSTITUCIONAL DE OPCIONES — 0DTE / INTRADÍA")
    print("   Gamma BSM · Gamma Flip · Muros Calls/Puts · Flujo Vol/OI")
    print("   Auto-actualización periódica")
    print("="*80 + "\n")

    args = _parse_args(sys.argv[1:])
    if args.spx_basis is not None:
        SPX_BASIS_DEFAULT = args.spx_basis

    ticker = args.ticker or os.environ.get("OPTIONS_TICKER", "").strip().upper()
    if not ticker:
        ticker = input(f"Ticker (ej. SPY, SPX, QQQ; Enter = {TICKER_POR_DEFECTO}): ").strip().upper() \
            or TICKER_POR_DEFECTO

    if args.refresh is not None:
        refresh_seconds = args.refresh
    else:
        intervalo_input = input(f"Intervalo de actualización en segundos (mín. {MIN_REFRESH_SECONDS}, Enter = 60): ").strip()
        refresh_seconds = int(intervalo_input) if intervalo_input.isdigit() else 60
    if refresh_seconds < MIN_REFRESH_SECONDS:
        print(f"   Intervalo mínimo: {MIN_REFRESH_SECONDS}s — se usa {MIN_REFRESH_SECONDS}s.")
        refresh_seconds = MIN_REFRESH_SECONDS

    main(ticker, refresh_seconds=refresh_seconds)
