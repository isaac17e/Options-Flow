import yfinance as yf
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import warnings
import time
import sys
warnings.filterwarnings('ignore')
# Añadir después de las otras importaciones
from scipy.stats import norm
import math
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px

# ============================================================================
# CONFIGURACIÓN DE RATE LIMITING
# ============================================================================
DELAY_ENTRE_REQUESTS = 2  # segundos entre llamadas a Yahoo Finance
MAX_REINTENTOS = 3
DELAY_REINTENTO = 5  # segundos de espera si falla

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
            if "Rate" in str(e) or "429" in str(e):
                if intento < max_intentos - 1:
                    espera = DELAY_REINTENTO * (2 ** intento)
                    print(f"\n⚠️  Rate limit detectado. Reintento {intento + 1}/{max_intentos} en {espera}s...")
                    time.sleep(espera)
                else:
                    raise Exception(f"❌ Rate limit persistente después de {max_intentos} intentos. "
                                    f"Espera 5-10 minutos y vuelve a intentar.")
            else:
                raise e

# ============================================================================
# FUNCIONES DE OBTENCIÓN DE DATOS (CON PROTECCIÓN)
# ============================================================================
def obtener_vencimiento_optimo(ticker):
    """Obtiene el vencimiento más cercano con protección contra rate limit"""
    try:
        print(f"📡 Conectando con Yahoo Finance para {ticker}...")
        activo = yf.Ticker(ticker)
        
        # Primera llamada: obtener expiraciones
        esperar_rate_limit()
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
        if "Rate" in str(e) or "429" in str(e):
            print(f"\n❌ ERROR: Yahoo Finance está bloqueando las peticiones.")
            print(f"   Soluciones:")
            print(f"   1. Espera 5-10 minutos antes de volver a intentar")
            print(f"   2. Verifica tu conexión a internet")
            print(f"   3. Si persiste, Yahoo puede tener restricciones temporales")
            sys.exit(1)
        else:
            raise e

def obtener_datos_opciones(activo, vencimiento):
    """Obtiene cadena de opciones con protección"""
    print(f"📊 Descargando datos de opciones para {vencimiento}...")
    esperar_rate_limit()
    
    cadena = reintentar_con_backoff(lambda: activo.option_chain(vencimiento))
    return cadena.calls.copy(), cadena.puts.copy()

def obtener_precio_actual(activo):
    """Obtiene precio actual con protección"""
    print(f"💰 Obteniendo precio actual...")
    esperar_rate_limit()
    
    historia = reintentar_con_backoff(lambda: activo.history(period="1d"))
    if historia.empty:
        raise Exception("No se pudo obtener el precio actual")
    
    return historia['Close'].iloc[-1]

# ============================================================================
# FUNCIONES DE ANÁLISIS (SIN CAMBIOS)
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
    """Detecta strikes donde los MM deben cubrir agresivamente"""
    
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

def detectar_smart_money(calls, puts):
    """Identifica compras agresivas de opciones caras (institucionales)"""
    
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
        mensaje = "VENCIMIENTO INMEDIATO: Max Pain es imán MUY fuerte"
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
    def calc_prob_itm(strike, precio, iv, dias):
        if dias <= 0:
            return 100.0 if strike <= precio else 0.0
        if iv <= 0:
            iv = 0.3
        try:
            from scipy.stats import norm
            T = dias / 365.0
            d2 = (np.log(precio / strike)) / (iv * np.sqrt(T))
            if strike <= precio:
                return norm.cdf(d2) * 100
            else:
                return norm.cdf(-d2) * 100
        except:
            dist_pct = abs(strike - precio) / precio
            return max(0, 100 - (dist_pct * 500))
    
    gamma_df['prob_itm'] = gamma_df.apply(
        lambda r: calc_prob_itm(r['strike'], precio_actual, r['iv_avg'], dias_restantes), axis=1
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
            '1️⃣ NET GAMMA PROFILE (Muros Institucionales)',
            '2️⃣ VOLUMEN vs OPEN INTEREST (Flujo vs Posiciones)',
            '3️⃣ VOL/OI RATIO (Detección de Aperturas)',
            '4️⃣ SMART MONEY FLOW (Premium Pagado)'
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
                      annotation_text=f"Testeo: ${mejor_strike:.2f}", row=4, col=1)
    
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
            '1️⃣ PROBABILIDAD ITM (Viabilidad de Strikes)',
            '2️⃣ IMPLIED VOLATILITY SURFACE (Opciones Caras vs Baratas)',
            '3️⃣ PRICE ACTION ZONES (Zonas de Operación)'
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
# MAIN (CON MANEJO DE ERRORES MEJORADO)
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
        direccion_mp = 'ALCISTA' if distancia < 0 else 'BAJISTA'
        
        print(f"{'='*80}")
        print(f"📊 MAX PAIN: ${max_pain:.2f} | Distancia: {distancia:+.2f} ({direccion_mp})")
        print(f"{mensaje_venc}")
        print(f"   Urgencia: {urgencia} | Presión gravitacional: {presion}")
        print(f"{'='*80}\n")
        
        status_gamma, net_gamma, comportamiento = posicionamiento_dealers(calls, puts)
        print(f"{'='*80}")
        print(f"🎯 POSICIONAMIENTO DE DEALERS (Market Makers)")
        print(f"{'='*80}")
        print(f"Status: {status_gamma}")
        print(f"Net Gamma: {net_gamma:,.0f}")
        print(f"Comportamiento esperado: {comportamiento}")
        print(f"Curvatura local: {curvatura_gamma(calls, puts, precio_actual)}\n")
        
        print(f"{'='*80}")
        print(f"🧱 GAMMA WALLS (Muros Institucionales)")
        print(f"{'='*80}")
        gamma_walls = identificar_gamma_walls(calls, puts, precio_actual)
        if not gamma_walls.empty:
            print(gamma_walls.to_string(index=False))
        else:
            print("   — No se detectaron muros significativos —")
        print()
        
        print(f"{'='*80}")
        print(f"📈 PUT/CALL RATIO POR STRIKE (Sentimiento Institucional)")
        print(f"{'='*80}")
        pc_analysis = ratio_put_call_strike(calls, puts)
        if not pc_analysis.empty:
            print(pc_analysis.to_string(index=False))
            print("\n   Interpretación:")
            print("   • Ratio > 1.5 = Cobertura bajista (protección institucional)")
            print("   • Ratio < 0.7 = Apuesta alcista agresiva")
        else:
            print("   — No hay datos suficientes —")
        print()
        
        print(f"{'='*80}")
        print(f"💰 SMART MONEY DETECTION (Flujos Institucionales)")
        print(f"{'='*80}")
        smart_calls, smart_puts = detectar_smart_money(calls, puts)
        
        print("CALLS institucionales (apuestas alcistas):")
        if not smart_calls.empty:
            print(smart_calls.to_string(index=False))
        else:
            print("   — No detectado —")
        
        print("\nPUTS institucionales (cobertura/apuestas bajistas):")
        if not smart_puts.empty:
            print(smart_puts.to_string(index=False))
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
        print(f"🎯 STRIKE DE MAYOR PROBABILIDAD DE TESTEO")
        print(f"{'='*80}")
        if mejor_strike:
            dist = mejor_strike - precio_actual
            direccion = "↑ alcista" if dist > 0 else "↓ bajista"
            tipo = "SOPORTE" if dist < 0 else "RESISTENCIA" if dist > 0 else "EN PRECIO"
            print(f"Strike: ${mejor_strike:.2f}")
            print(f"Distancia: {dist:+.2f} {direccion}")
            print(f"Tipo: {tipo}")
            print(f"Puntaje: {score:.1f}/300")
            print(f"   (Factores: Volumen + Flujo + Cercanía + Max Pain ajustado + Gamma)")
        else:
            print("   — No se pudo determinar —")
        
        print(f"\n{'='*80}")
        print(f"💡 ESTRATEGIA RECOMENDADA")
        print(f"{'='*80}")
        
        if dias_restantes <= 2:
            print(f"⚠️  VENCIMIENTO INMEDIATO - Alta probabilidad de movimiento hacia Max Pain")
            if distancia > 0:
                print(f"   → Escenario base: Precio debería BAJAR de ${precio_actual:.2f} hacia ${max_pain:.2f}")
                print(f"   → Considerar: Short en rebotes / Long puts / Evitar calls")
            else:
                print(f"   → Escenario base: Precio debería SUBIR de ${precio_actual:.2f} hacia ${max_pain:.2f}")
                print(f"   → Considerar: Long en caídas / Long calls / Venta de puts")
        
        if status_gamma == "SHORT GAMMA FUERTE":
            print(f"\n⚠️  DEALERS EN SHORT GAMMA - Movimientos pueden ser EXPLOSIVOS")
            print(f"   → Alta volatilidad esperada")
            print(f"   → Los breakouts se amplificarán")
            print(f"   → Usar stops más amplios")
        elif status_gamma == "LONG GAMMA FUERTE":
            print(f"\n✅ DEALERS EN LONG GAMMA - Movimientos contenidos/estabilizados")
            print(f"   → Reversiones rápidas probables")
            print(f"   → Ideal para mean reversion")
            print(f"   → Cuidado con perseguir breakouts")
        
        if not gamma_walls.empty:
            resistencias = gamma_walls[gamma_walls['strike'] > precio_actual].head(2)
            soportes = gamma_walls[gamma_walls['strike'] < precio_actual].head(2)
            
            if not resistencias.empty:
                print(f"\n🔴 Resistencias institucionales (gamma walls):")
                for _, row in resistencias.iterrows():
                    print(f"   • ${row['strike']:.2f} (distancia: ${row['distancia']:.2f}) - MM venderán contra rallies")
            
            if not soportes.empty:
                print(f"\n🟢 Soportes institucionales (gamma walls):")
                for _, row in soportes.iterrows():
                    print(f"   • ${row['strike']:.2f} (distancia: ${row['distancia']:.2f}) - MM comprarán en caídas")
        
        print(f"\n{'='*80}")
        print("GENERANDO DASHBOARDS INTERACTIVOS...")
        print(f"{'='*80}")
        try:
            crear_dashboard_interactivo(calls, puts, precio_actual, max_pain, mejor_strike, dias_restantes, ticker)
        except Exception as e:
            print(f"   — Error al generar mapa: {e} —")
            import traceback
            traceback.print_exc()

        print(f"\n{'='*80}")
        print("✅ Análisis completo. Usa estos datos para posicionarte con ventaja institucional.")
        print(f"{'='*80}\n")
        
    except KeyboardInterrupt:
        print("\n\n❌ Análisis interrumpido por el usuario.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ ERROR INESPERADO: {e}")
        print(f"   Tipo de error: {type(e).__name__}")
        sys.exit(1)

if __name__ == "__main__":
    print("\n" + "="*80)
    print("   ANÁLISIS INSTITUCIONAL DE OPCIONES")
    print("   Con protección anti-rate-limit")
    print("="*80 + "\n")
    
    ticker = input("Ticker: ").strip().upper()
    if not ticker:
        print("❌ Debes ingresar un ticker válido")
        sys.exit(1)
    
    main(ticker)
