# Options Flow — 0DTE Dashboard (Polygon.io)

Dashboard institucional en tiempo casi real para lectura de flujo de opciones **0DTE / vencimiento más próximo**, construido como un único script Python sobre la API de [Polygon.io](https://polygon.io). Genera un panel HTML autoactualizable (Plotly + tabla) con GEX por strike, Gamma Flip, Max Pain, muros de Calls/Puts y un consenso direccional intradía.

> **Origen:** este directorio viene de la rama huérfana `legacy-single-script` (commit `148de628`), copiado a `main` sin merge. Es independiente del dashboard Streamlit de la raíz del repo (SVI, PDF risk-neutral) y tiene sus propias dependencias.

## Qué calcula

- **Precio spot** de **Capital.com** (punto medio bid/offer del CFD que corresponde al subyacente, resuelto por `tickers.py`; en índices se resta la base CFD − índice). Si Capital.com falla, se estima por paridad put-call sobre la cadena ya descargada en el ciclo (sin llamadas extra). El dashboard muestra la fuente y la hora del spot.
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

La sesión de Capital.com se abre una vez, se reutiliza entre ciclos, se renueva si caduca (401) y se cierra al salir. Los tokens nunca se imprimen. El epic de Capital.com lo resuelve `tickers.py` (ver «Multi-ticker»).

## Uso

```bash
python Options_Trade_polygon.py                          # interactivo (Enter = SPY)
python Options_Trade_polygon.py --ticker SPX --refresh 60 --spx-basis 1.0
```

El subyacente también se puede fijar con la variable `OPTIONS_TICKER`. Sin argumentos el script pide:
1. **Ticker** (ej. `SPY`, `SPX`, `QQQ`, `AAPL`; Enter = `SPY`).
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

## Multi-ticker (`tickers.py`)
- **Índices** (tabla fija). La referencia de contratos usa el ticker sin prefijo y el snapshot el ticker de índice, porque con `SPX` Polygon no trae IV ni griegas. El CFD debe ser de tipo `INDICES`.

  | Ticker | Referencia | Snapshot | CFD |
  |---|---|---|---|
  | SPX (incluye SPXW) | `SPX` | `I:SPX` | `US500` |
  | NDX | `NDX` | `I:NDX` | `US100` |
  | RUT | `RUT` | `I:RUT` | `RTY` |

- **ETFs y acciones**: el mismo ticker en Polygon. En Capital.com se busca con `GET /markets?searchTerm=<ticker>` y solo se acepta un instrumento `SHARES` con epic idéntico. Si no lo hay, vale el único `SHARES` cuyo precio esté a ≤1 % del spot.
- **Chequeo de precio:** el precio del CFD siempre se compara con el spot implícito por paridad de la propia cadena (mediana de los 7 strikes más ATM), con tolerancia de 1 %. Así se descartan colisiones de símbolo como `SPX` = Spirax Sarco. Si no hay coincidencia segura se lanza `TickerResolutionError`; el panel lo muestra en rojo y usa la paridad.
- **Caché:** el mapa resuelto se guarda en `legacy_0dte/.ticker_map.json` (ignorado por git; se puede cambiar con `OPTIONS_FLOW_TICKER_CACHE`). El precio se vuelve a comprobar en cada resolución.
- **Base** (índices): CFD − subyacente, medida con el CFD del instante al que corresponden los precios de opciones. En cada ciclo se compara la paridad put-call de la cadena (mediana de `K + C − P` en los 7 strikes ATM; SPX y SPXW no se mezclan) con el mid del CFD de hace 15 minutos (`GET /prices`, para compensar el retraso de Polygon); el spot en vivo es `mid(CFD) − base`, con la mediana móvil de las últimas muestras (`BasisTracker`, se descartan bases > 1 % del precio). Sin muestras (mercado cerrado, sin barra, error de red) se usa `SPX_BASIS` / `--spx-basis` (por defecto `1.0`, solo para SPX; 0 en NDX/RUT). El panel muestra el epic y la base.
- Limitación: en vencimientos mensuales conviven SPX (AM) y SPXW (PM) con los mismos strikes y el análisis por strike los junta; la paridad sí los separa.

## Umbrales adimensionales
Todos los umbrales escalan con el precio o son adimensionales, así que valen para SPY, SPX o una acción de 100 USD:
- `MAX_PAIN_PIN_PCT = 0.3 %` del spot: distancia al max pain para el voto direccional (antes eran 2 USD fijos, escala SPY).
- `GEX_FUERTE_RATIO = 0.25`: |Net GEX| / Σ|GEX| para «FUERTE» (antes ±5e8 USD fijos).
- `SMART_MONEY_MIN_PRICE_PCT = 0.015 %` del spot: prima mínima para smart money (antes 0,10 USD).

## Decisión (`levels.py`, `decision.py`)
- `levels.compute_levels(contratos, vencimiento, dias, S)` devuelve los niveles del panel (flip, muros, max pain, picos de gamma bruta, Net GEX, IV ATM y perfil por strike) en un dict.
- `decision.decide(levels, live, state)` y `decision.manage(position, levels, live)` aplican las reglas de entrada, TP/stop y gestión. No colocan órdenes.
  - Régimen LONG/SHORT gamma.
  - Rechazo del siguiente strike atractivo (A/B) o ruptura del flip (C).
  - Stop estructural que nunca se ensancha.
  - TP en el siguiente strike atractivo, o en el strike más probable que pague R:R ≥ 1.
  - Holding máximo de 75 min y entradas solo de 08:45 a 10:00 COT.
- Todas las distancias son `max(pct·S, k·σ_h)`, con σ_h la desviación esperada en la ventana de holding a partir de la IV ATM. Por eso funcionan igual en un índice de 7.800 y en una acción de 100 USD (ver `tests/test_decision.py`).
- `decision.position_size(...)`: 5 % del balance como margen al apalancamiento de la cuenta, redondeado hacia abajo al incremento y con el mínimo del broker.
- `decision.manage` acepta `regime_at_entry` en la posición: si viene (trades del escáner), se sale cuando el régimen deja de ser el de la entrada.

## Escáner multi-activo (`scanner.py`)
Lógica genérica del modo escáner; no coloca órdenes (el orquestador vive fuera del repo).
- `key_strike(levels, spot, now)`: entre los strikes atractivos, el de mayor gamma bruta × P(toque) en el tiempo que queda.
- `evaluate(ticker, levels, SessionRange(open, high, low), live, now)`: con el máximo/mínimo del CFD desde las 08:30 COT y la base (CFD = strike + base):
  - **no tocado** → `CANDIDATE` hacia el strike (TP en el strike, stop detrás del nivel atractivo previo), o `WAIT` si no pasa P(toque) ≥ 0,30 y R:R ≥ 1,0;
  - **tocado** (lado de llegada = el de la apertura) y Net GEX local > 0 con rechazo → `WALL`: reversión hacia el siguiente nivel (TP con `decision.choose_tp`), stop más allá del muro;
  - **tocado** y Net GEX local < 0, cruzado ≥ `break_margin` → `ACCELERATOR`: continuación al siguiente strike atractivo, stop de vuelta al otro lado;
  - muro cruzado ≥ `break_margin` (aunque vuelva), acelerador rechazado, o siguiente movimiento sin filtros → `EXPIRED` para el día; precio aún en el nivel → `WAIT`.
- `select(evaluaciones)`: el candidato con mayor P(toque) × min(R:R, 3).
- Tests sintéticos de todos los caminos en `tests/test_scanner.py`.
