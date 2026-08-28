# Options Flow — 0DTE Dashboard (Polygon.io)

Dashboard institucional en tiempo casi real para lectura de flujo de opciones **0DTE / vencimiento más próximo**, construido como un único script Python sobre la API de [Polygon.io](https://polygon.io). Genera un panel HTML autoactualizable (Plotly + tabla) con GEX por strike, Gamma Flip, Max Pain, muros de Calls/Puts y un consenso direccional intradía.

> **Nota sobre esta rama:** `legacy-single-script` es un proyecto independiente del que vive en `main` de este mismo repositorio (una reescritura modular distinta, `quant_microstructure`, con calibración SVI y extracción de PDF risk-neutral). No comparten historial ni están pensados para fusionarse.

## Qué calcula

- **Precio spot** con fallback en cascada: `prev close` de acciones → `underlying_asset.price` del snapshot de opciones → estimación por paridad put-call sobre el 0DTE si no hay ningún dato de acciones disponible.
- **Vencimiento 0DTE real** si existe (vence hoy); si no, cae automáticamente al vencimiento vigente más próximo.
- **Greeks Black-Scholes** (delta, gamma) por contrato. Si Polygon no trae IV en vivo para la cadena (típico fuera de horario, feriados, o ciertos subyacentes en el plan Starter), la IV se **infiere invirtiendo BSM** sobre el último precio disponible en vez de descartar el análisis.
- **GEX (Gamma Exposure)** por strike y **Net GEX**, con convención estándar de posicionamiento de dealers: gamma de calls positiva, gamma de puts negativa.
- **Gamma Flip** (Zero Gamma Level): el strike donde el Net GEX cruza de positivo a negativo. Si hay varios cruces (ruido de IV inferida en strikes OTM ilíquidos con OI alto), se toma el **más cercano al spot**, que es el económicamente relevante.
- **Max Pain** intradía, acotado a un rango razonable alrededor del spot.
- **Muros de Calls/Puts**: strikes con el Net GEX positivo/negativo más extremo — resistencia/soporte de gamma.
- **Clasificación de flujo** por strike (Volumen/OI) — prioriza volumen del día, que es la métrica relevante en 0DTE ya que el OI de la mañana es del cierre anterior.
- **Smart money**: strikes con alta actividad relativa + IV alta + prima pagada significativa.
- **Consenso direccional** ponderado (Max Pain, régimen de gamma, Gamma Flip, muros, PC ratio, aperturas nuevas de smart money) con nivel de confianza.

## Requisitos

- Python 3.10+
- Una API key de [Polygon.io](https://polygon.io) con plan de **Options** (Starter o superior). El plan Starter no incluye greeks en vivo para todos los subyacentes — el script lo compensa infiriendo la IV, y también funciona sin plan de acciones pago (fallback de precio spot).

## Instalación

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuración

```bash
export POLYGON_API_KEY="tu_api_key"
```

## Uso

```bash
python Options_Trade_polygon.py
```

El script pide:
1. **Ticker** (ej. `SPY`, `QQQ`, `SPX`, `AAPL`).
2. **Intervalo de actualización** en segundos (Enter = 60).

Genera `{TICKER}_0DTE_dashboard.html` en el directorio del script, levanta un servidor local (`http://localhost:8765`) para evitar las restricciones de `file://` en el navegador, y lo abre automáticamente. El dashboard se recarga solo en cada ciclo — no hace falta reabrirlo. `Ctrl+C` detiene el monitor y cierra el servidor.

## Sobre tickers sin 0DTE real

El script no está limitado a índices — corre para cualquier ticker con opciones listadas en Polygon. Si el subyacente no tiene contratos que venzan hoy (la mayoría de acciones individuales fuera de los nombres más líquidos), cae automáticamente al vencimiento más próximo disponible y lo etiqueta como tal en el dashboard. Ten en cuenta que los umbrales de clasificación de flujo (ratio Volumen/OI) están calibrados para el comportamiento intradía de un contrato que nace y muere el mismo día — en vencimientos semanales/mensuales siguen siendo una señal útil, pero pierden algo de precisión.

## Limitaciones conocidas

- Plan Starter de Polygon: datos con ~15 minutos de delay.
- La IV inferida en strikes muy OTM/ilíquidos puede ser ruidosa entre ciclos (el `lastPrice` usado para invertir BSM no siempre refleja el mercado en tiempo real).
- El consenso direccional es una heurística de lectura de flujo, no una señal de trading garantizada.
