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

def mostrar_mapa_calor(calls, puts, precio_actual, max_pain, mejor_strike):
    df_calls = calls[['strike', 'volume', 'openInterest']].copy()
    df_puts = puts[['strike', 'volume', 'openInterest']].copy()
    df_calls['type'] = 'call'
    df_puts['type'] = 'put'
    df = pd.concat([df_calls, df_puts], ignore_index=True)
    df['vol_oi'] = df['volume'] / df['openInterest'].replace(0, 1)
    
    df_group = df.groupby('strike').agg({
        'volume': 'sum',
        'openInterest': 'sum',
        'vol_oi': 'mean'
    }).reset_index()
    
    df_group['dist_price'] = abs(df_group['strike'] - precio_actual)
    df_group['dist_pain'] = abs(df_group['strike'] - max_pain)
    df_group['score'] = (
        np.minimum(df_group['volume'] / 20, 100) +
        np.minimum(df_group['vol_oi'] * 25, 70) +
        np.maximum(50 - df_group['dist_price'] * 5, 0) +
        np.maximum(40 - df_group['dist_pain'] * 4, 0)
    )
    
    centro = precio_actual
    df_plot = df_group[
        (df_group['strike'] >= centro - 15) & 
        (df_group['strike'] <= centro + 15)
    ].copy()
    
    if df_plot.empty:
        print("   — No hay datos para mapa de calor —")
        return
    
    df_plot = df_plot.sort_values('strike').reset_index(drop=True)
    strikes = df_plot['strike'].tolist()
    scores = df_plot['score'].tolist()
    strike_labels = [f"{s:.0f}" for s in strikes]
    
    data = np.array(scores).reshape(-1, 1)
    annot_data = np.array(strike_labels).reshape(-1, 1)
    
    plt.figure(figsize=(5, max(6, len(strikes) * 0.4)))
    ax = sns.heatmap(
        data,
        annot=annot_data,
        fmt='',
        cmap="RdYlBu_r",
        center=100,
        cbar_kws={'label': 'Probabilidad', 'shrink': 0.8},
        linewidths=0.5,
        linecolor='lightgray',
        xticklabels=False,
        yticklabels=False
    )
    
    plt.title(f"MAPA DE CALOR - ZONAS DE TESTEO\n(Precio: {precio_actual:.2f})", 
              pad=20, fontsize=12, fontweight='bold')
    
    if mejor_strike is not None and mejor_strike in strikes:
        mejor_idx = strikes.index(mejor_strike)
        plt.axhline(y=mejor_idx + 0.5, color='red', linewidth=1.5, linestyle='--', alpha=0.9)
        plt.text(1.1, 0.1, f"Max Pain: {max_pain}\nTesteo clave: {mejor_strike}", 
                 transform=ax.transAxes, color='red', fontsize=10, fontweight='bold', ha='left')
    else:
        plt.text(1.1, 0.1, f"Max Pain: {max_pain}", 
                 transform=ax.transAxes, color='red', fontsize=10, fontweight='bold', ha='left')
    
    plt.tight_layout()
    plt.show()

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
        print("GENERANDO MAPA DE CALOR...")
        print(f"{'='*80}")
        try:
            mostrar_mapa_calor(calls, puts, precio_actual, max_pain, mejor_strike)
        except Exception as e:
            print(f"   — Error al generar mapa: {e} —")

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
