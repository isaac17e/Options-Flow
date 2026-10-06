# Options Flow — 0DTE Dashboard (Polygon.io)

Dashboard institucional en tiempo casi real para lectura de flujo de opciones **0DTE / vencimiento más próximo**, construido como un único script Python sobre la API de [Polygon.io](https://polygon.io). Genera un panel HTML autoactualizable (Plotly + tabla) con GEX por strike, Gamma Flip, Max Pain, muros de Calls/Puts y un consenso direccional intradía.

> **Origen:** este directorio viene de la rama huérfana `legacy-single-script` (commit `148de628`), copiado a `main` sin merge. Es independiente del dashboard Streamlit de la raíz del repo (SVI, PDF risk-neutral) y tiene sus propias dependencias.

## Qué calcula

- **Precio spot** de **Capital.com** (punto medio bid/offer de `GET /markets/{ticker}`). Si Capital.com falla, se estima por paridad put-call sobre la cadena ya descargada en el ciclo (sin llamadas extra). El dashboard muestra la fuente y la hora del spot.
- **Vencimiento 0DTE real** si existe (vence hoy); si no, cae automáticamente al vencimiento vigente más próximo. Se obtiene con una sola llamada al endpoint de referencia.
- **Greeks Black-Scholes** (delta, gamma) por contrato. En cada contrato sin IV de Polygon la IV se **infiere invirtiendo BSM** sobre el último precio disponible. Los contratos con OI en los que la inversión falla quedan fuera del GEX y se cuentan en un aviso visible del dashboard.
- **GEX (Gamma Exposure)** por strike y **Net GEX**, con convención estándar de posicionamiento de dealers: gamma de calls positiva, gamma de puts negativa.
- **Gamma Flip** (Zero Gamma Level): el strike donde el Net GEX cruza de positivo a negativo. Si hay varios cruces (ruido de IV inferida en strikes OTM ilíquidos con OI alto), se toma el **más cercano al spot**, que es el económicamente relevante.
- **Max Pain** intradía, acotado a un rango razonable alrededor del spot.
- **Muros de Calls/Puts**: strikes con el Net GEX positivo/negativo más extremo — resistencia/soporte de gamma.
- **Clasificación de flujo** por strike (Volumen/OI) — prioriza volumen del día, que es la métrica relevante en 0DTE ya que el OI de la mañana es del cierre anterior.
- **Smart money**: strikes con alta actividad relativa + IV alta + prima pagada significativa.
- **Consenso direccional** ponderado (Max Pain, régimen de gamma, Gamma Flip, muros, PC ratio, aperturas nuevas de smart money) con nivel de confianza.

## Requisitos

- Python 3.10+
- Una API key de [Polygon.io](https://polygon.io) con plan de **Options** (Starter o superior). Las peticiones a Polygon reintentan 429 (respetando `Retry-After`) y 5xx con backoff exponencial + jitter.
- Una cuenta de [Capital.com](https://capital.com) con API key para el spot (por defecto, la API demo).

## Instalación

```bash
cd legacy_0dte
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuración

Variables de entorno (el script no lee `.env`):

```bash
export POLYGON_API_KEY="tu_api_key"
export CAPITAL_API_KEY="tu_api_key_capital"
export CAPITAL_IDENTIFIER="tu_email"
export CAPITAL_API_PASSWORD="tu_password_api"
# Opcional (por defecto la demo): https://demo-api-capital.backend-capital.com/api/v1
export CAPITAL_API_URL="https://demo-api-capital.backend-capital.com/api/v1"
```

La sesión de Capital.com se abre una vez, se reutiliza entre ciclos, se renueva si caduca (401) y se cierra al salir. Los tokens nunca se imprimen. El ticker se usa como epic de Capital.com (`SPY` → `SPY`).

## Uso

```bash
python Options_Trade_polygon.py
```

El script pide:
1. **Ticker** (ej. `SPY`, `QQQ`, `AAPL`).
2. **Intervalo de actualización** en segundos (Enter = 60; mínimo 15).

Para automatizarlo sin prompts: `python -c "import Options_Trade_polygon as m; m.main('SPY', 60)"`. Importar el módulo no tiene efectos secundarios. Los tests offline viven en `tests/test_legacy_0dte.py` en la raíz del repo.

Genera `{TICKER}_0DTE_dashboard.html` en el directorio del script, levanta un servidor local (`http://localhost:8765`) para evitar las restricciones de `file://` en el navegador, y lo abre automáticamente. El dashboard se recarga solo en cada ciclo — no hace falta reabrirlo. `Ctrl+C` detiene el monitor y cierra el servidor.

## Sobre tickers sin 0DTE real

El script no está limitado a índices — corre para cualquier ticker con opciones listadas en Polygon. Si el subyacente no tiene contratos que venzan hoy (la mayoría de acciones individuales fuera de los nombres más líquidos), cae automáticamente al vencimiento más próximo disponible y lo etiqueta como tal en el dashboard. Ten en cuenta que los umbrales de clasificación de flujo (ratio Volumen/OI) están calibrados para el comportamiento intradía de un contrato que nace y muere el mismo día — en vencimientos semanales/mensuales siguen siendo una señal útil, pero pierden algo de precisión.

## Limitaciones conocidas

- Los datos de opciones de Polygon llevan el retraso propio del plan; sin last trade/quote, el precio del contrato es el cierre del día.
- La T de Black-Scholes usa días enteros (un 0DTE usa un mínimo de 0,3 días, sea la hora que sea).
- La IV inferida en strikes muy OTM/ilíquidos puede ser ruidosa entre ciclos (el `lastPrice` usado para invertir BSM no siempre refleja el mercado en tiempo real).
- El consenso direccional es una heurística de lectura de flujo, no una señal de trading garantizada.
