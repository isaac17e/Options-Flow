import yfinance as yf
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import warnings
import time
import sys
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
warnings.filterwarnings('ignore')
from scipy.stats import norm
import math
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px

# ============================================================================
# CONFIGURACIÓN DE SESIÓN HTTP CON HEADERS (EVITA BLOQUEOS)
# ============================================================================
def crear_sesion_yfinance():
    """
    Crea una sesión HTTP con headers que Yahoo Finance acepta.
    Esto evita bloqueos por 'bot detection'
    """
    session = requests.Session()
    
    # Headers que simulan un navegador real
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0',
    })
    
    # Configurar reintentos automáticos
    retry_strategy = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"]
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session

# Crear sesión global para yfinance
print("🔧 Inicializando sesión anti-bloqueo...")
YF_SESSION = crear_sesion_yfinance()
# ============================================================================
# CONFIGURACIÓN DE SESIÓN HTTP CON HEADERS (EVITA BLOQUEOS)
# ============================================================================
def crear_sesion_yfinance():
    """
    Crea una sesión HTTP con headers que Yahoo Finance acepta.
    Esto evita bloqueos por 'bot detection'
    """
    session = requests.Session()
    
    # Headers que simulan un navegador real
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0',
    })
    
    # Configurar reintentos automáticos
    retry_strategy = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"]
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session

# Crear sesión global para yfinance
YF_SESSION = crear_sesion_yfinance()

# ============================================================================
# CONFIGURACIÓN DE RATE LIMITING (AUMENTADA PARA EVITAR BLOQUEOS)
# ============================================================================
DELAY_ENTRE_REQUESTS = 5  # Aumentado a 5 segundos
MAX_REINTENTOS = 5        # Más reintentos
DELAY_REINTENTO = 10      # Más espera entre reintentos

def esperar_rate_limit(segundos=DELAY_ENTRE_REQUESTS):
    """Pausa para evitar rate limiting"""
    print(f"   ⏳ Esperando {segundos}s para evitar bloqueos...", end='\r')
    time.sleep(segundos)
    print(" " * 60, end='\r')  # Limpiar línea

def reintentar_con_backoff(func, *args, max_intentos=MAX_REINTENTOS, **kwargs):
    """Reintenta una función con backoff exponencial"""
    for intento in range(max_intentos):
        try:
            resultado = func(*args, **kwargs)
            return resultado
        except Exception as e:
            error_str = str(e).lower()
            if "rate" in error_str or "429" in error_str or "blocked" in error_str or "forbidden" in error_str:
                if intento < max_intentos - 1:
                    espera = DELAY_REINTENTO * (2 ** intento)
                    print(f"\n⚠️  Rate limit detectado. Reintento {intento + 1}/{max_intentos} en {espera}s...")
                    time.sleep(espera)
                else:
                    print(f"\n{'='*80}")
                    print(f"❌ BLOQUEO PERSISTENTE DE YAHOO FINANCE")
                    print(f"{'='*80}")
                    print(f"Posibles soluciones:")
                    print(f"1. Tu IP puede estar bloqueada temporalmente (espera 30-60 minutos)")
                    print(f"2. Cambia de red WiFi o usa datos móviles")
                    print(f"3. Usa VPN (ProtonVPN, Windscribe, etc.)")
                    print(f"4. Limpia caché: rm -rf ~/.cache/py-yfinance")
                    print(f"5. Actualiza yfinance: pip install yfinance --upgrade")
                    print(f"{'='*80}\n")
                    raise Exception(f"Rate limit persistente después de {max_intentos} intentos. "
                                    f"Yahoo Finance bloqueó tu IP/sesión.")
            else:
                raise e

# ============================================================================
# FUNCIONES DE OBTENCIÓN DE DATOS (CON PROTECCIÓN)
# ============================================================================
def obtener_vencimiento_optimo(ticker):
    """Obtiene el vencimiento más cercano con protección contra rate limit"""
    try:
        print(f"📡 Conectando con Yahoo Finance para {ticker}...")
        print(f"   (Usando headers anti-bloqueo + sesión personalizada...)")
        
        # CRÍTICO: Pasar la sesión personalizada a yfinance
        activo = yf.Ticker(ticker, session=YF_SESSION)
        
        # Primera llamada: obtener expiraciones
        print(f"   Obteniendo vencimientos disponibles...")
        esperar_rate_limit(3)  # Delay inicial mayor
        expiraciones = reintentar_con_backoff(lambda: activo.options)
        
        if not expiraciones:
            return activo, None, None
        
        hoy = datetime.now().date()
        candidatos = []
        
        for exp in expiraciones:
            try:
                exp_date = datetime.strptime(exp, '%Y-%m-%d').date()
                dias = (exp_date - hoy).days
                if dias >= 0:
                    candidatos.append((exp, dias))
            except:
                continue
        
        if not candidatos:
            return activo, None, None
        
        candidatos.sort(key=lambda x: x[1])
        return activo, candidatos[0][0], candidatos[0][1]
        
    except Exception as e:
        error_str = str(e).lower()
        if "rate" in error_str or "429" in error_str or "blocked" in error_str:
            print(f"\n❌ ERROR: Yahoo Finance está bloqueando las peticiones.")
            print(f"   Error específico: {e}")
            print(f"\n   📋 CHECKLIST DE SOLUCIONES:")
            print(f"   □ ¿Actualizaste yfinance? → pip install yfinance --upgrade")
            print(f"   □ ¿Limpiaste el caché? → rm -rf ~/.cache/py-yfinance")
            print(f"   □ ¿Esperaste 30+ minutos desde el último intento?")
            print(f"   □ ¿Probaste desde otra red/VPN?")
            print(f"   □ ¿Verificaste que el ticker existe? (prueba con SPY o AAPL)")
            sys.exit(1)
        else:
            raise e

def obtener_datos_opciones(activo, vencimiento):
    """Obtiene cadena de opciones con protección"""
    print(f"📊 Descargando datos de opciones para {vencimiento}...")
    esperar_rate_limit(4)  # Delay mayor
    
    cadena = reintentar_con_backoff(lambda: activo.option_chain(vencimiento))
    print(f"   ✅ Cadena descargada correctamente")
    return cadena.calls.copy(), cadena.puts.copy()

def obtener_precio_actual(activo):
    """Obtiene precio actual con protección"""
    print(f"💰 Obteniendo precio actual...")
    esperar_rate_limit(3)
    
    try:
        # Intentar primero con info (más rápido y menos propenso a bloqueos)
        info = reintentar_con_backoff(lambda: activo.info)
        if 'currentPrice' in info:
            precio = info['currentPrice']
            print(f"   ✅ Precio obtenido: ${precio:.2f}")
            return precio
        elif 'regularMarketPrice' in info:
            precio = info['regularMarketPrice']
            print(f"   ✅ Precio obtenido: ${precio:.2f}")
            return precio
    except:
        print(f"   ⚠️  Método info falló, usando historial...")
    
    # Fallback: historial
    historia = reintentar_con_backoff(lambda: activo.history(period="1d"))
    if historia.empty:
        raise Exception("No se pudo obtener el precio actual")
    
    precio = historia['Close'].iloc[-1]
    print(f"   ✅ Precio obtenido: ${precio:.2f}")
    return precio

# ============================================================================
# FUNCIONES DE ANÁLISIS
# ============================================================================
def calcular_max_pain(calls, puts):
    strikes = sorted(set(calls['strike']) | set(puts['strike']))
    min_loss = float('inf')
    max_pain = None
    for strike in strikes:
        call_loss = sum(calls[calls['strike'] < strike]['openInterest'] * (strike - calls[calls['strike'] < strike]['strike']))
        put_loss = sum(puts[puts['strike'] > strike]['openInterest'] * (puts[puts['strike'] > strike]['strike'] - strike))
        total_loss = call_loss + put_loss
        if total_loss < min_loss:
            min_loss = total_loss
            max_pain = strike
    return max_pain

def ratio_put_call_strike(calls, puts):
    """Identifica cobertura alcista/bajista institucional por nivel"""
    merged = pd.merge(
        calls[['strike', 'volume', 'openInterest']],
        puts[['strike', 'volume', 'openInterest']],
        on='strike', suffixes=('_call', '_put'), how='outer'
    ).fillna(0)
    
    merged['pc_ratio_vol'] = merged['volume_put'] / merged['volume_call'].replace(0, 1)
    merged['pc_ratio_oi'] = merged['openInterest_put'] / merged['openInterest_call'].replace(0, 1)
    merged['vol_total'] = merged['volume_call'] + merged['volume_put']
    
    merged = merged[merged['vol_total'] > merged['vol_total'].quantile(0.7)]
    
    merged['sentiment'] = merged['pc_ratio_vol'].apply(
        lambda x: 'BEARISH (puts)' if x > 1.5 else 'BULLISH (calls)' if x < 0.7 else 'NEUTRAL'
    )
    
    return merged.sort_values('vol_total', ascending=False)[
        ['strike', 'pc_ratio_vol', 'pc_ratio_oi', 'sentiment']
    ].head(8)

def identificar_gamma_walls(calls, puts, precio_actual):
    """
    Identifica strikes con alta exposición gamma.
    
    Gamma walls son niveles donde market makers tienen grandes posiciones
    que requieren delta hedging continuo. Esto puede crear:
    - Soporte temporal (MM compran cuando el precio cae hacia el strike)
    - Resistencia temporal (MM venden cuando el precio sube hacia el strike)
    
    IMPORTANTE: 
    - NO son barreras infranqueables
    - NO predicen reversiones
    - Son zonas de actividad técnica aumentada
    - Más relevante cerca del vencimiento
    """
    
    calls_clean = calls[['strike', 'openInterest', 'impliedVolatility']].dropna()
    puts_clean = puts[['strike', 'openInterest', 'impliedVolatility']].dropna()
    
    calls_clean['gamma_exp'] = calls_clean['openInterest'] * calls_clean['impliedVolatility'] * 100
    puts_clean['gamma_exp'] = puts_clean['openInterest'] * puts_clean['impliedVolatility'] * 100 * -1
    
    gamma_total = pd.concat([
        calls_clean[['strike', 'gamma_exp']],
        puts_clean[['strike', 'gamma_exp']]
    ]).groupby('strike').sum().reset_index()
    
    rango_min = precio_actual * 0.85
    rango_max = precio_actual * 1.15
    gamma_total = gamma_total[
        (gamma_total['strike'] >= rango_min) & 
        (gamma_total['strike'] <= rango_max)
    ]
    
    if gamma_total.empty:
        return pd.DataFrame()
    
    def clasificar_wall(row):
        strike = row['strike']
        fuerza = abs(row['gamma_exp'])
        
        if strike < precio_actual:
            return f"SOPORTE (fuerza: {fuerza:,.0f})"
        elif strike > precio_actual:
            return f"RESISTENCIA (fuerza: {fuerza:,.0f})"
        else:
            return f"EN PRECIO (fuerza: {fuerza:,.0f})"
    
    gamma_total['tipo'] = gamma_total.apply(clasificar_wall, axis=1)
    gamma_total['fuerza'] = abs(gamma_total['gamma_exp'])
    gamma_total['distancia'] = abs(gamma_total['strike'] - precio_actual)
    
    resistencias = gamma_total[gamma_total['strike'] > precio_actual].nsmallest(4, 'distancia')
    soportes = gamma_total[gamma_total['strike'] < precio_actual].nsmallest(4, 'distancia')
    
    result = pd.concat([soportes, resistencias]).sort_values('distancia')
    
    return result[['strike', 'gamma_exp', 'distancia', 'tipo']]

def posicionamiento_dealers(calls, puts):
    """Determina si los MM están long/short gamma globalmente"""
    
    calls_clean = calls[['openInterest', 'impliedVolatility']].dropna()
    puts_clean = puts[['openInterest', 'impliedVolatility']].dropna()
    
    call_gamma = (calls_clean['openInterest'] * calls_clean['impliedVolatility']).sum()
    put_gamma = (puts_clean['openInterest'] * puts_clean['impliedVolatility']).sum() * -1
    
    net_gamma = call_gamma + put_gamma
    
    if net_gamma > 5000:
        status = "LONG GAMMA FUERTE"
        comportamiento = "Movimientos estabilizados, reversiones rápidas"
    elif net_gamma > 0:
        status = "LONG GAMMA DÉBIL"
        comportamiento = "Estabilización limitada, puede voltear a short gamma"
    elif net_gamma > -5000:
        status = "SHORT GAMMA DÉBIL"
        comportamiento = "Tendencia a amplificar movimientos moderadamente"
    else:
        status = "SHORT GAMMA FUERTE"
        comportamiento = "Volatilidad explosiva, movimientos amplificados"
    
    return status, net_gamma, comportamiento

def detectar_flujo_institucional(calls, puts):
    """
    Identifica volumen alto en opciones con IV elevada.
    
    IMPORTANTE: Esto NO indica dirección ni intención:
    - Puede ser cobertura (hedging), no especulación
    - Puede ser parte de spreads complejos
    - Institucionales compran puts para proteger portafolios largos
    
    Usa esto para identificar actividad inusual, no para predecir movimientos.
    """
    
    calls_clean = calls[['strike', 'volume', 'openInterest', 'impliedVolatility', 'lastPrice']].dropna()
    puts_clean = puts[['strike', 'volume', 'openInterest', 'impliedVolatility', 'lastPrice']].dropna()
    
    calls_clean['iv_percentile'] = calls_clean['impliedVolatility'].rank(pct=True)
    calls_clean['vol_percentile'] = calls_clean['volume'].rank(pct=True)
    
    smart_calls = calls_clean[
        (calls_clean['vol_percentile'] > 0.7) &
        (calls_clean['iv_percentile'] > 0.6) &
        (calls_clean['lastPrice'] > 0.5)
    ].copy()
    
    smart_calls['premium_paid'] = smart_calls['volume'] * smart_calls['lastPrice'] * 100
    
    puts_clean['iv_percentile'] = puts_clean['impliedVolatility'].rank(pct=True)
    puts_clean['vol_percentile'] = puts_clean['volume'].rank(pct=True)
    
    smart_puts = puts_clean[
        (puts_clean['vol_percentile'] > 0.7) &
        (puts_clean['iv_percentile'] > 0.6) &
        (puts_clean['lastPrice'] > 0.5)
    ].copy()
    
    smart_puts['premium_paid'] = smart_puts['volume'] * smart_puts['lastPrice'] * 100
    
    return (
        smart_calls.nlargest(5, 'premium_paid')[['strike', 'volume', 'impliedVolatility', 'premium_paid']],
        smart_puts.nlargest(5, 'premium_paid')[['strike', 'volume', 'impliedVolatility', 'premium_paid']]
    )

def ajustar_por_vencimiento(dias_restantes, max_pain, precio_actual):
    """El max pain es más relevante cerca del vencimiento"""
    
    if dias_restantes <= 2:
        peso_max_pain = 0.8
        icono = "⚠️ "
        mensaje = "VENCIMIENTO INMEDIATO: Max Pain puede tener influencia fuerte"
        urgencia = "CRÍTICO"
    elif dias_restantes <= 5:
        peso_max_pain = 0.5
        icono = "📍 "
        mensaje = "Vencimiento cercano: Max Pain es relevante"
        urgencia = "IMPORTANTE"
    elif dias_restantes <= 10:
        peso_max_pain = 0.3
        icono = "📅 "
        mensaje = "Vencimiento medio: Max Pain tiene influencia"
        urgencia = "MODERADO"
    else:
        peso_max_pain = 0.1
        icono = "📆 "
        mensaje = "Vencimiento lejano: Max Pain poco relevante"
        urgencia = "BAJO"
    
    distancia = abs(precio_actual - max_pain)
    presion = "ALTA" if distancia > 5 else "MEDIA" if distancia > 2 else "BAJA"
    
    return peso_max_pain, icono + mensaje, urgencia, presion

def curvatura_gamma(calls, puts, precio_actual):
    gex_calls = calls['openInterest'] * calls['impliedVolatility']
    gex_puts = puts['openInterest'] * puts['impliedVolatility']
    df = pd.DataFrame({'strike': calls['strike'], 'gex_call': gex_calls}).dropna()
    df_put = pd.DataFrame({'strike': puts['strike'], 'gex_put': gex_puts}).dropna()
    gex_total = pd.merge(df, df_put, on='strike', how='outer').fillna(0)
    gex_total['net_gex'] = gex_total['gex_call'] - gex_total['gex_put']
    gex_total['slope'] = gex_total['net_gex'].diff()
    cercanos = gex_total.iloc[(gex_total['strike'] - precio_actual).abs().argsort()[:3]]
    curva = cercanos['slope'].mean()
    return "LONG GAMMA (Estabiliza)" if curva > 0 else "SHORT GAMMA (Explosivo)"

def volumen_inusual(df, factor=2.0):
    if df['volume'].mean() == 0:
        return pd.DataFrame()
    avg_vol = df['volume'].mean()
    return df[df['volume'] > factor * avg_vol][['strike', 'volume', 'openInterest']].sort_values(by='volume', ascending=False).head(8)

def flujos_apertura(df, ratio_min=2.0):
    df = df[df['openInterest'] > 0].copy()
    df['vol/oi'] = df['volume'] / df['openInterest']
    return df[df['vol/oi'] > ratio_min][['strike', 'volume', 'openInterest', 'vol/oi']].sort_values(by='vol/oi', ascending=False).head(8)

def strike_mayor_probabilidad(calls, puts, precio_actual, max_pain, peso_max_pain):
    strikes = sorted(set(calls['strike']) | set(puts['strike']))
    if not strikes:
        return None, 0
    
    mejor_strike = None
    mejor_score = -1
    
    for strike in strikes:
        if abs(strike - precio_actual) > 50:
            continue
        
        score = 0.0
        
        vol_total = calls[calls['strike'] == strike]['volume'].sum() + puts[puts['strike'] == strike]['volume'].sum()
        score += min(vol_total / 20, 100)
        
        oi_total = calls[calls['strike'] == strike]['openInterest'].sum() + puts[puts['strike'] == strike]['openInterest'].sum()
        if oi_total > 0:
            score += min((vol_total / oi_total) * 25, 70)
        
        dist = abs(strike - precio_actual)
        score += max(0, 50 - dist * 5)
        
        score += max(0, (40 * peso_max_pain) - abs(strike - max_pain) * 4)
        
        if score > mejor_score:
            mejor_score = score
            mejor_strike = strike
    
    return mejor_strike, mejor_score
# ============================================================================
# FUNCIONES DE PROBABILIDAD ITM (CORREGIDAS)
# ============================================================================

def calc_prob_itm_call(strike, precio, iv, dias):
    """
    Probabilidad de que una CALL expire ITM
    Usa distribución log-normal del precio del activo
    """
    if dias <= 0:
        return 100.0 if precio > strike else 0.0
    if iv <= 0 or iv > 5:  # IV irreal
        iv = 0.3
    
    try:
        T = dias / 365.0
        # Black-Scholes: d2 determina probabilidad ITM
        d1 = (np.log(precio / strike) + (0.5 * iv**2) * T) / (iv * np.sqrt(T))
        d2 = d1 - iv * np.sqrt(T)
        
        # Para CALL: P(S_T > K) = N(d2)
        prob = norm.cdf(d2) * 100
        return max(0, min(100, prob))  # Clamp entre 0-100
    except:
        # Fallback: aproximación lineal
        dist_pct = abs(strike - precio) / precio
        if precio > strike:
            return max(50, 100 - (dist_pct * 200))
        else:
            return max(0, 50 - (dist_pct * 200))

def calc_prob_itm_put(strike, precio, iv, dias):
    """
    Probabilidad de que una PUT expire ITM
    """
    if dias <= 0:
        return 100.0 if precio < strike else 0.0
    if iv <= 0 or iv > 5:
        iv = 0.3
    
    try:
        T = dias / 365.0
        d1 = (np.log(precio / strike) + (0.5 * iv**2) * T) / (iv * np.sqrt(T))
        d2 = d1 - iv * np.sqrt(T)
        
        # Para PUT: P(S_T < K) = N(-d2)
        prob = norm.cdf(-d2) * 100
        return max(0, min(100, prob))
    except:
        dist_pct = abs(strike - precio) / precio
        if precio < strike:
            return max(50, 100 - (dist_pct * 200))
        else:
            return max(0, 50 - (dist_pct * 200))

def calc_prob_itm_promedio(strike, precio, iv, dias):
    """
    Probabilidad promedio (para cuando no sabemos si es call o put)
    Usa el promedio ponderado de ambas
    """
    prob_call = calc_prob_itm_call(strike, precio, iv, dias)
    prob_put = calc_prob_itm_put(strike, precio, iv, dias)
    
    # Si estamos ATM, ambas tienen ~50%
    # Si estamos OTM para calls, ITM para puts (y viceversa)
    if abs(strike - precio) / precio < 0.02:  # ±2% = ATM
        return 50.0
    elif strike > precio:  # OTM call, ITM put
        return prob_put  # Usamos la put que es ITM
    else:  # ITM call, OTM put
        return prob_call  # Usamos la call que es ITM

# ============================================================================
# DASHBOARD INTERACTIVO
# ============================================================================

def crear_dashboard_interactivo(calls, puts, precio_actual, max_pain, mejor_strike, dias_restantes, ticker):
    """
    Dashboard interactivo HTML con Plotly
    Genera 2 gráficos profesionales con interactividad completa
    """
    
    # ========================================================================
    # PREPARACIÓN DE DATOS
    # ========================================================================
    
    rango_pct = 0.10
    rango_min = precio_actual * (1 - rango_pct)
    rango_max = precio_actual * (1 + rango_pct)
    
    calls_rango = calls[
        (calls['strike'] >= rango_min) & 
        (calls['strike'] <= rango_max)
    ].copy()
    
    puts_rango = puts[
        (puts['strike'] >= rango_min) & 
        (puts['strike'] <= rango_max)
    ].copy()
    
    if calls_rango.empty or puts_rango.empty:
        print("❌ No hay datos suficientes en el rango ATM")
        return
    
    # Calcular métricas
    calls_rango['gamma_exp'] = calls_rango['openInterest'] * calls_rango['impliedVolatility'] * 100
    puts_rango['gamma_exp'] = puts_rango['openInterest'] * puts_rango['impliedVolatility'] * 100
    
    gamma_df = pd.merge(
        calls_rango[['strike', 'gamma_exp', 'volume', 'openInterest', 'impliedVolatility', 'lastPrice']],
        puts_rango[['strike', 'gamma_exp', 'volume', 'openInterest', 'impliedVolatility', 'lastPrice']],
        on='strike', how='outer', suffixes=('_call', '_put')
    ).fillna(0)
    
    gamma_df['net_gamma'] = gamma_df['gamma_exp_call'] - gamma_df['gamma_exp_put']
    gamma_df['total_volume'] = gamma_df['volume_call'] + gamma_df['volume_put']
    gamma_df['total_oi'] = gamma_df['openInterest_call'] + gamma_df['openInterest_put']
    gamma_df['pc_ratio'] = gamma_df['volume_put'] / gamma_df['volume_call'].replace(0, 1)
    gamma_df['vol_oi_ratio'] = gamma_df['total_volume'] / gamma_df['total_oi'].replace(0, 1)
    
    # Premium paid (flujo de dinero)
    gamma_df['premium_call'] = gamma_df['volume_call'] * gamma_df['lastPrice_call'] * 100
    gamma_df['premium_put'] = gamma_df['volume_put'] * gamma_df['lastPrice_put'] * 100
    gamma_df['premium_total'] = gamma_df['premium_call'] + gamma_df['premium_put']
    
    # IV promedio
    gamma_df['iv_avg'] = np.where(
        gamma_df['total_volume'] > 0,
        (gamma_df['impliedVolatility_call'] * gamma_df['volume_call'] + 
         gamma_df['impliedVolatility_put'] * gamma_df['volume_put']) / gamma_df['total_volume'],
        (gamma_df['impliedVolatility_call'] + gamma_df['impliedVolatility_put']) / 2
    )
    gamma_df['iv_avg'] = gamma_df['iv_avg'].replace(0, 0.3)
    
    # Probabilidad ITM corregida
    gamma_df['prob_itm'] = gamma_df.apply(
        lambda r: calc_prob_itm_promedio(r['strike'], precio_actual, r['iv_avg'], dias_restantes), axis=1
    )
    
    # Sentimiento
    def sent(ratio):
        if ratio > 1.5:
            return 'BEARISH 🐻'
        elif ratio < 0.7:
            return 'BULLISH 🐂'
        else:
            return 'NEUTRAL ⚖️'
    
    gamma_df['sentimiento'] = gamma_df['pc_ratio'].apply(sent)
    
    # Filtrar strikes con actividad
    umbral = gamma_df['total_volume'].quantile(0.2)
    gamma_df = gamma_df[gamma_df['total_volume'] >= umbral].sort_values('strike').reset_index(drop=True)
    
    # ========================================================================
    # GRÁFICO 1: BATTLEFIELD MAP (4 paneles)
    # ========================================================================
    
    fig1 = make_subplots(
        rows=4, cols=1,
        subplot_titles=(
            '1️⃣ NET GAMMA PROFILE (Exposición Institucional)',
            '2️⃣ VOLUMEN vs OPEN INTEREST (Flujo vs Posiciones)',
            '3️⃣ VOL/OI RATIO (Detección de Aperturas)',
            '4️⃣ FLUJO INSTITUCIONAL (Premium Pagado)'
        ),
        vertical_spacing=0.08,
        row_heights=[0.3, 0.25, 0.2, 0.25]
    )
    
    # --- PANEL 1: Net Gamma ---
    colors_gamma = ['#00FF00' if x > 0 else '#FF0000' for x in gamma_df['net_gamma']]
    
    fig1.add_trace(go.Bar(
        x=gamma_df['strike'],
        y=gamma_df['net_gamma'],
        name='Net Gamma',
        marker=dict(color=colors_gamma, line=dict(color='black', width=0.5)),
        hovertemplate='<b>Strike: $%{x:.2f}</b><br>Net Gamma: %{y:,.0f}<br><extra></extra>'
    ), row=1, col=1)
    
    # Línea de precio actual
    fig1.add_vline(x=precio_actual, line=dict(color='yellow', width=3, dash='dash'),
                   annotation_text=f"Precio: ${precio_actual:.2f}", 
                   annotation_position="top", row=1, col=1)
    
    # Max Pain
    fig1.add_vline(x=max_pain, line=dict(color='orange', width=2, dash='dot'),
                   annotation_text=f"Max Pain: ${max_pain:.2f}", 
                   annotation_position="bottom", row=1, col=1)
    
    # Zona caliente (±5%)
    zona_min = precio_actual * 0.95
    zona_max = precio_actual * 1.05
    fig1.add_vrect(x0=zona_min, x1=zona_max, fillcolor="yellow", opacity=0.1,
                   layer="below", line_width=0, row=1, col=1)
    
    # --- PANEL 2: Vol vs OI ---
    fig1.add_trace(go.Bar(
        x=gamma_df['strike'],
        y=gamma_df['volume_call'],
        name='Vol Calls',
        marker=dict(color='#66FF66', line=dict(color='darkgreen', width=0.5)),
        hovertemplate='<b>Strike: $%{x:.2f}</b><br>Vol Calls: %{y:,.0f}<br><extra></extra>'
    ), row=2, col=1)
    
    fig1.add_trace(go.Bar(
        x=gamma_df['strike'],
        y=gamma_df['volume_put'],
        name='Vol Puts',
        marker=dict(color='#FF6666', line=dict(color='darkred', width=0.5)),
        hovertemplate='<b>Strike: $%{x:.2f}</b><br>Vol Puts: %{y:,.0f}<br><extra></extra>'
    ), row=2, col=1)
    
    fig1.add_trace(go.Scatter(
        x=gamma_df['strike'],
        y=gamma_df['openInterest_call'],
        name='OI Calls',
        mode='lines+markers',
        line=dict(color='green', width=2),
        marker=dict(size=6, symbol='circle'),
        hovertemplate='<b>Strike: $%{x:.2f}</b><br>OI Calls: %{y:,.0f}<br><extra></extra>'
    ), row=2, col=1)
    
    fig1.add_trace(go.Scatter(
        x=gamma_df['strike'],
        y=gamma_df['openInterest_put'],
        name='OI Puts',
        mode='lines+markers',
        line=dict(color='red', width=2),
        marker=dict(size=6, symbol='circle'),
        hovertemplate='<b>Strike: $%{x:.2f}</b><br>OI Puts: %{y:,.0f}<br><extra></extra>'
    ), row=2, col=1)
    
    fig1.add_vline(x=precio_actual, line=dict(color='yellow', width=2, dash='dash'), row=2, col=1)
    
    # --- PANEL 3: Vol/OI Ratio ---
    colors_ratio = []
    for idx, row in gamma_df.iterrows():
        if row['vol_oi_ratio'] > 2.0:
            if row['volume_call'] > row['volume_put']:
                colors_ratio.append('#00FF00')  # Verde: Aperturas alcistas
            else:
                colors_ratio.append('#FF0000')  # Rojo: Aperturas bajistas
        else:
            colors_ratio.append('#888888')  # Gris: Posiciones antiguas
    
    fig1.add_trace(go.Bar(
        x=gamma_df['strike'],
        y=gamma_df['vol_oi_ratio'],
        name='Vol/OI Ratio',
        marker=dict(color=colors_ratio, line=dict(color='black', width=0.5)),
        hovertemplate='<b>Strike: $%{x:.2f}</b><br>Vol/OI: %{y:.2f}<br>%{text}<extra></extra>',
        text=gamma_df['sentimiento']
    ), row=3, col=1)
    
    fig1.add_hline(y=2.0, line=dict(color='white', width=1, dash='dash'),
                   annotation_text="Umbral Apertura (2.0)", row=3, col=1)
    fig1.add_vline(x=precio_actual, line=dict(color='yellow', width=2, dash='dash'), row=3, col=1)
    
    # --- PANEL 4: Premium Paid ---
    fig1.add_trace(go.Bar(
        x=gamma_df['strike'],
        y=gamma_df['premium_call'],
        name='Premium Calls',
        marker=dict(color='#00DD00', line=dict(color='darkgreen', width=0.5)),
        hovertemplate='<b>Strike: $%{x:.2f}</b><br>$ Calls: $%{y:,.0f}<br><extra></extra>'
    ), row=4, col=1)
    
    fig1.add_trace(go.Bar(
        x=gamma_df['strike'],
        y=gamma_df['premium_put'],
        name='Premium Puts',
        marker=dict(color='#DD0000', line=dict(color='darkred', width=0.5)),
        hovertemplate='<b>Strike: $%{x:.2f}</b><br>$ Puts: $%{y:,.0f}<br><extra></extra>'
    ), row=4, col=1)
    
    fig1.add_vline(x=precio_actual, line=dict(color='yellow', width=2, dash='dash'), row=4, col=1)
    
    if mejor_strike:
        fig1.add_vline(x=mejor_strike, line=dict(color='magenta', width=2, dash='dot'),
                      annotation_text=f"Nivel clave: ${mejor_strike:.2f}", row=4, col=1)
    
    # Layout Gráfico 1
    fig1.update_layout(
        title=dict(
            text=f'<b>BATTLEFIELD MAP - {ticker.upper()}</b><br>'
                 f'<sup>Precio: ${precio_actual:.2f} | Max Pain: ${max_pain:.2f} | Vencimiento: {dias_restantes}d</sup>',
            x=0.5,
            xanchor='center',
            font=dict(size=18)
        ),
        height=1400,
        showlegend=True,
        hovermode='x unified',
        template='plotly_dark',
        barmode='overlay'
    )
    
    fig1.update_xaxes(title_text="Strike Price ($)", row=4, col=1)
    fig1.update_yaxes(title_text="Net Gamma", row=1, col=1)
    fig1.update_yaxes(title_text="Contratos", row=2, col=1)
    fig1.update_yaxes(title_text="Ratio", row=3, col=1)
    fig1.update_yaxes(title_text="Premium ($)", row=4, col=1)
    
    # ========================================================================
    # GRÁFICO 2: PROBABILITY & RISK MAP (3 paneles)
    # ========================================================================
    
    fig2 = make_subplots(
        rows=3, cols=1,
        subplot_titles=(
            '1️⃣ PROBABILIDAD ITM (Viabilidad Estadística de Strikes)',
            '2️⃣ IMPLIED VOLATILITY SURFACE (Opciones Caras vs Baratas)',
            '3️⃣ PRICE ACTION ZONES (Zonas Técnicas de Referencia)'
        ),
        vertical_spacing=0.12,
        row_heights=[0.35, 0.3, 0.35]
    )
    
    # --- PANEL 1: Probabilidad ITM ---
    colors_prob = []
    for p in gamma_df['prob_itm']:
        if p > 60:
            colors_prob.append('#00FF00')
        elif p > 30:
            colors_prob.append('#FFDD00')
        else:
            colors_prob.append('#FF4444')
    
    fig2.add_trace(go.Bar(
        x=gamma_df['strike'],
        y=gamma_df['prob_itm'],
        name='Prob ITM',
        marker=dict(color=colors_prob, line=dict(color='black', width=0.5)),
        text=[f'{p:.0f}%' for p in gamma_df['prob_itm']],
        textposition='outside',
        hovertemplate='<b>Strike: $%{x:.2f}</b><br>Probabilidad ITM: %{y:.1f}%<br><extra></extra>'
    ), row=1, col=1)
    
    fig2.add_hline(y=60, line=dict(color='green', width=1, dash='dash'),
                   annotation_text="Alta Prob (>60%)", row=1, col=1)
    fig2.add_hline(y=30, line=dict(color='orange', width=1, dash='dash'),
                   annotation_text="Baja Prob (<30%)", row=1, col=1)
    fig2.add_hline(y=50, line=dict(color='white', width=1),
                   annotation_text="Fair Value (50%)", row=1, col=1)
    
    fig2.add_vline(x=precio_actual, line=dict(color='yellow', width=3, dash='dash'), row=1, col=1)
    
    # --- PANEL 2: IV Surface ---
    fig2.add_trace(go.Scatter(
        x=gamma_df['strike'],
        y=gamma_df['impliedVolatility_call'] * 100,
        name='IV Calls',
        mode='lines+markers',
        line=dict(color='#00FF00', width=3),
        marker=dict(size=8, symbol='circle'),
        hovertemplate='<b>Strike: $%{x:.2f}</b><br>IV Calls: %{y:.1f}%<br><extra></extra>'
    ), row=2, col=1)
    
    fig2.add_trace(go.Scatter(
        x=gamma_df['strike'],
        y=gamma_df['impliedVolatility_put'] * 100,
        name='IV Puts',
        mode='lines+markers',
        line=dict(color='#FF0000', width=3),
        marker=dict(size=8, symbol='diamond'),
        hovertemplate='<b>Strike: $%{x:.2f}</b><br>IV Puts: %{y:.1f}%<br><extra></extra>'
    ), row=2, col=1)
    
    iv_avg = gamma_df['iv_avg'].mean() * 100
    fig2.add_hline(y=iv_avg, line=dict(color='cyan', width=2, dash='dot'),
                   annotation_text=f"IV Promedio: {iv_avg:.1f}%", row=2, col=1)
    
    fig2.add_vline(x=precio_actual, line=dict(color='yellow', width=2, dash='dash'), row=2, col=1)
    
    # --- PANEL 3: Action Zones ---
    # Identificar zonas
    soportes = gamma_df[gamma_df['net_gamma'] < 0].nsmallest(3, 'net_gamma')
    resistencias = gamma_df[gamma_df['net_gamma'] > 0].nlargest(3, 'net_gamma')
    
    # Barras de Net Gamma de fondo
    fig2.add_trace(go.Bar(
        x=gamma_df['strike'],
        y=gamma_df['net_gamma'],
        name='Net Gamma',
        marker=dict(
            color=['#00FF00' if x > 0 else '#FF0000' for x in gamma_df['net_gamma']],
            opacity=0.3,
            line=dict(width=0)
        ),
        showlegend=False,
        hoverinfo='skip'
    ), row=3, col=1)
    
    # Anotar soportes
    for _, row in soportes.iterrows():
        fig2.add_annotation(
            x=row['strike'],
            y=row['net_gamma'],
            text=f"SOPORTE<br>${row['strike']:.0f}",
            showarrow=True,
            arrowhead=2,
            arrowcolor='green',
            font=dict(size=10, color='green'),
            row=3, col=1
        )
    
    # Anotar resistencias
    for _, row in resistencias.iterrows():
        fig2.add_annotation(
            x=row['strike'],
            y=row['net_gamma'],
            text=f"RESISTENCIA<br>${row['strike']:.0f}",
            showarrow=True,
            arrowhead=2,
            arrowcolor='red',
            font=dict(size=10, color='red'),
            row=3, col=1
        )
    
    # Precio actual
    fig2.add_vline(x=precio_actual, line=dict(color='yellow', width=4, dash='dash'),
                   annotation_text=f"PRECIO ACTUAL<br>${precio_actual:.2f}",
                   annotation_position="top", row=3, col=1)
    
    # Layout Gráfico 2
    fig2.update_layout(
        title=dict(
            text=f'<b>PROBABILITY & RISK MAP - {ticker.upper()}</b><br>'
                 f'<sup>Vencimiento: {dias_restantes} días | Análisis probabilístico</sup>',
            x=0.5,
            xanchor='center',
            font=dict(size=18)
        ),
        height=1200,
        showlegend=True,
        hovermode='x unified',
        template='plotly_dark'
    )
    
    fig2.update_xaxes(title_text="Strike Price ($)", row=3, col=1)
    fig2.update_yaxes(title_text="Probabilidad (%)", row=1, col=1, range=[0, 105])
    fig2.update_yaxes(title_text="IV (%)", row=2, col=1)
    fig2.update_yaxes(title_text="Net Gamma", row=3, col=1)
    
    # ========================================================================
    # EXPORTAR HTML
    # ========================================================================
    
    filename1 = f'{ticker.upper()}_battlefield_map.html'
    filename2 = f'{ticker.upper()}_probability_map.html'
    
    fig1.write_html(filename1)
    fig2.write_html(filename2)
    
    print(f"\n{'='*80}")
    print(f"✅ DASHBOARDS INTERACTIVOS GENERADOS:")
    print(f"{'='*80}")
    print(f"📊 Battlefield Map: {filename1}")
    print(f"📈 Probability Map: {filename2}")
    print(f"\n💡 Abre los archivos HTML en tu navegador para interactuar")
    print(f"   - Zoom: Arrastra con el ratón")
    print(f"   - Pan: Shift + Arrastra")
    print(f"   - Hover: Pasa el ratón sobre las barras")
    print(f"   - Reset: Doble click")
    print(f"{'='*80}\n")
    # =============================================================================
# MAIN (CON MANEJO DE ERRORES Y DISCLAIMERS CORRECTOS)
# =============================================================================
def main(ticker):
    print(f"\n{'='*80}")
    print(f"   ANÁLISIS INSTITUCIONAL DE OPCIONES - {ticker.upper()}")
    print(f"{'='*80}\n")
    
    try:
        # Fase 1: Obtener vencimientos
        activo, vencimiento, dias_restantes = obtener_vencimiento_optimo(ticker)
        if not vencimiento:
            print("❌ No hay vencimientos disponibles para este ticker.")
            return

        # Fase 2: Obtener precio actual
        precio_actual = obtener_precio_actual(activo)
        
        hoy = datetime.now().strftime('%Y-%m-%d %H:%M')
        print(f"\n✅ Datos obtenidos correctamente")
        print(f"Precio actual: ${precio_actual:.2f}")
        print(f"Fecha/Hora: {hoy}")
        print(f"Vencimiento: {vencimiento} ({dias_restantes} días restantes)\n")

        # Fase 3: Obtener cadena de opciones
        calls, puts = obtener_datos_opciones(activo, vencimiento)
        
        print(f"✅ Analizando {len(calls)} calls y {len(puts)} puts...\n")
        
        # Fase 4: Análisis
        max_pain = calcular_max_pain(calls, puts)
        
        peso_max_pain, mensaje_venc, urgencia, presion = ajustar_por_vencimiento(dias_restantes, max_pain, precio_actual)
        
        distancia = precio_actual - max_pain
        direccion_mp = 'por encima' if distancia > 0 else 'por debajo'
        
        print(f"{'='*80}")
        print(f"📊 MAX PAIN: ${max_pain:.2f} | Precio está {direccion_mp} (${abs(distancia):.2f})")
        print(f"{mensaje_venc}")
        print(f"   Relevancia temporal: {urgencia} | Distancia: {presion}")
        print(f"   ℹ️  Max Pain NO es predicción - es un nivel técnico de referencia")
        print(f"{'='*80}\n")
        
        status_gamma, net_gamma, comportamiento = posicionamiento_dealers(calls, puts)
        print(f"{'='*80}")
        print(f"🎯 POSICIONAMIENTO DE DEALERS (Market Makers)")
        print(f"{'='*80}")
        print(f"Status: {status_gamma}")
        print(f"Net Gamma: {net_gamma:,.0f}")
        print(f"Comportamiento esperado: {comportamiento}")
        print(f"Curvatura local: {curvatura_gamma(calls, puts, precio_actual)}")
        print(f"   ℹ️  Esto indica magnitud de movimientos, NO dirección\n")
        
        print(f"{'='*80}")
        print(f"🧱 GAMMA WALLS (Niveles con Alta Exposición Gamma)")
        print(f"{'='*80}")
        print(f"⚠️  Estos NO son muros infranqueables - son zonas de actividad técnica")
        gamma_walls = identificar_gamma_walls(calls, puts, precio_actual)
        if not gamma_walls.empty:
            print(gamma_walls.to_string(index=False))
            print(f"\n💡 Interpretación:")
            print(f"   • SOPORTE: MM pueden comprar si precio cae hacia ese strike")
            print(f"   • RESISTENCIA: MM pueden vender si precio sube hacia ese strike")
            print(f"   • Efecto más fuerte cerca del vencimiento")
            print(f"   • NO garantiza reversión - el precio puede romper estos niveles")
        else:
            print("   — No se detectaron niveles significativos en el rango ATM —")
        print()
        
        print(f"{'='*80}")
        print(f"📈 PUT/CALL RATIO POR STRIKE (Sentimiento de Mercado)")
        print(f"{'='*80}")
        pc_analysis = ratio_put_call_strike(calls, puts)
        if not pc_analysis.empty:
            print(pc_analysis.to_string(index=False))
            print("\n   Interpretación:")
            print("   • Ratio > 1.5 = Más actividad en puts (cobertura/protección)")
            print("   • Ratio < 0.7 = Más actividad en calls (posicionamiento alcista)")
            print("   ⚠️  NO asumas dirección - puede ser hedging, no especulación")
        else:
            print("   — No hay datos suficientes —")
        print()
        
        print(f"{'='*80}")
        print(f"💰 FLUJO INSTITUCIONAL DETECTADO (Vol × IV × Premium)")
        print(f"{'='*80}")
        print(f"⚠️  NOTA: Puede ser cobertura, arbitraje o especulación - no asumas dirección")
        flujo_calls, flujo_puts = detectar_flujo_institucional(calls, puts)
        
        print("\nCALLS con flujo institucional:")
        print("(NO necesariamente alcista - puede ser cobertura de cortos)")
        if not flujo_calls.empty:
            print(flujo_calls.to_string(index=False))
        else:
            print("   — No detectado —")
        
        print("\nPUTS con flujo institucional:")
        print("(NO necesariamente bajista - puede ser protección de portafolio)")
        if not flujo_puts.empty:
            print(flujo_puts.to_string(index=False))
        else:
            print("   — No detectado —")
        print()
        
        print(f"{'='*80}")
        print(f"📊 ANÁLISIS DE FLUJO Y VOLUMEN")
        print(f"{'='*80}")
        
        print(f"\nVOLUMEN INUSUAL (vol > 2x media):")
        vol_inusual = volumen_inusual(calls)
        print(vol_inusual.to_string(index=False) if not vol_inusual.empty else "   — Ninguno —")

        print(f"\nFLUJOS DE APERTURA (Vol/OI > 2):")
        flujos = flujos_apertura(calls)
        print(flujos.to_string(index=False) if not flujos.empty else "   — Ninguno —")
        
        mejor_strike, score = strike_mayor_probabilidad(calls, puts, precio_actual, max_pain, peso_max_pain)
        
        print(f"\n{'='*80}")
        print(f"📍 STRIKE CON MAYOR ACTIVIDAD PONDERADA (NO ES PREDICCIÓN)")
        print(f"{'='*80}")
        if mejor_strike:
            dist = mejor_strike - precio_actual
            direccion = "por encima" if dist > 0 else "por debajo"
            tipo = "POTENCIAL SOPORTE" if dist < 0 else "POTENCIAL RESISTENCIA" if dist > 0 else "EN PRECIO ACTUAL"
            print(f"\nStrike identificado: ${mejor_strike:.2f}")
            print(f"Ubicación: {direccion} del precio (${abs(dist):.2f})")
            print(f"Clasificación: {tipo}")
            print(f"Score de actividad: {score:.1f}/300")
            print(f"\n💡 Interpretación:")
            print(f"   • Este strike tiene alta actividad (volumen + OI + proximidad)")
            print(f"   • Puede actuar como nivel técnico relevante")
            print(f"   • NO predice que el precio llegará ahí")
            print(f"   • Úsalo como referencia para stops/targets, no como señal de entrada")
        else:
            print("   — No se pudo determinar —")
        
        print(f"\n{'='*80}")
        print(f"📊 INTERPRETACIÓN DE DATOS (NO ES PREDICCIÓN)")
        print(f"{'='*80}")

        print(f"\n⚠️  DISCLAIMER IMPORTANTE:")
        print(f"   Las opciones NO predicen dirección del precio.")
        print(f"   Este análisis identifica:")
        print(f"   • Niveles técnicos con alta actividad institucional")
        print(f"   • Volatilidad esperada por el mercado")
        print(f"   • Posicionamiento que puede generar soporte/resistencia")
        print(f"   • Contexto de mercado, NO señales de trading\n")

        if dias_restantes <= 2:
            print(f"⏰ VENCIMIENTO INMEDIATO ({dias_restantes}d)")
            print(f"   • Max Pain: ${max_pain:.2f} (distancia: {distancia:+.2f})")
            print(f"   • Cerca del vencimiento, strikes con mucho OI pueden actuar como 'imanes'")
            print(f"   • Razón: Delta hedging de market makers, NO manipulación")
            print(f"   • Úsalo como CONTEXTO, no como señal de entrada\n")

        if status_gamma == "SHORT GAMMA FUERTE":
            print(f"📈 DEALERS EN SHORT GAMMA")
            print(f"   • Net Gamma: {net_gamma:,.0f}")
            print(f"   • Comportamiento: {comportamiento}")
            print(f"   • Implicación: Movimientos de precio pueden amplificarse")
            print(f"   • NO predice dirección, solo magnitud potencial\n")
        elif status_gamma == "LONG GAMMA FUERTE":
            print(f"📉 DEALERS EN LONG GAMMA")
            print(f"   • Net Gamma: {net_gamma:,.0f}")
            print(f"   • Comportamiento: {comportamiento}")
            print(f"   • Implicación: Movimientos de precio pueden contenerse")
            print(f"   • Favorece estrategias de reversión a la media\n")
        
        if not gamma_walls.empty:
            resistencias = gamma_walls[gamma_walls['strike'] > precio_actual].head(2)
            soportes = gamma_walls[gamma_walls['strike'] < precio_actual].head(2)
            
            if not resistencias.empty:
                print(f"🔴 Resistencias potenciales (gamma exposure):")
                for _, row in resistencias.iterrows():
                    print(f"   • ${row['strike']:.2f} (+${row['distancia']:.2f})")
                print(f"   ℹ️  MM pueden vender si precio se acerca (delta hedging)")
                print(f"   ℹ️  NO garantiza reversión - úsalo como referencia técnica\n")
            
            if not soportes.empty:
                print(f"🟢 Soportes potenciales (gamma exposure):")
                for _, row in soportes.iterrows():
                    print(f"   • ${row['strike']:.2f} (-${row['distancia']:.2f})")
                print(f"   ℹ️  MM pueden comprar si precio se acerca (delta hedging)")
                print(f"   ℹ️  NO garantiza rebote - úsalo como referencia técnica\n")
        
        print(f"{'='*80}")
        print("GENERANDO DASHBOARDS INTERACTIVOS...")
        print(f"{'='*80}")
        try:
            crear_dashboard_interactivo(calls, puts, precio_actual, max_pain, mejor_strike, dias_restantes, ticker)
        except Exception as e:
            print(f"   — Error al generar dashboards: {e} —")
            import traceback
            traceback.print_exc()

        print(f"\n{'='*80}")
        print("✅ ANÁLISIS COMPLETO - ÚSALO RESPONSABLEMENTE")
        print(f"{'='*80}")
        print(f"\n⚠️  RECORDATORIO FINAL:")
        print(f"   • Este análisis identifica CONTEXTO de mercado, no señales")
        print(f"   • Las opciones NO predicen dirección del precio")
        print(f"   • Combina con análisis técnico/fundamental propio")
        print(f"   • Gestiona riesgo apropiadamente")
        print(f"   • Ninguna herramienta garantiza ganancias")
        print(f"{'='*80}\n")
        
    except KeyboardInterrupt:
        print("\n\n❌ Análisis interrumpido por el usuario.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ ERROR INESPERADO: {e}")
        print(f"   Tipo de error: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    print("\n" + "="*80)
    print("   ANÁLISIS INSTITUCIONAL DE OPCIONES")
    print("   Con protección anti-rate-limit")
    print("="*80)
    
    print("\n" + "⚠️ " * 20)
    print("   DISCLAIMER IMPORTANTE - LEER ANTES DE USAR")
    print("⚠️ " * 20)
    print("""
Este software es únicamente educativo y de análisis de mercado.

LAS OPCIONES NO PREDICEN DIRECCIÓN DEL PRECIO.

Este análisis identifica:
✓ Niveles técnicos con alta actividad institucional
✓ Volatilidad implícita y expectativas del mercado
✓ Contexto de posicionamiento (NO señales de trading)

NO sustituye:
✗ Análisis técnico o fundamental propio
✗ Gestión de riesgo profesional
✗ Asesoría financiera personalizada

El trading de opciones conlleva riesgo sustancial de pérdida.
Ninguna herramienta garantiza ganancias.
    """)
    print("="*80 + "\n")
    
    respuesta = input("¿Entiendes que esto NO es predicción de precio? (SI/NO): ").strip().upper()
    if respuesta != "SI":
        print("\n❌ Debes entender las limitaciones antes de usar esta herramienta.")
        sys.exit(0)
    
    print("\n")
    
    ticker = input("Ticker: ").strip().upper()
    if not ticker:
        print("❌ Debes ingresar un ticker válido")
        sys.exit(1)
    
    main(ticker)