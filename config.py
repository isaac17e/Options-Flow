"""
config.py
---------
Punto único de configuración del proyecto. Todo lo que dependa de
credenciales o parámetros globales (tasa libre de riesgo, timezone, etc.)
se lee desde aquí, para no tener "API keys" regadas por el código.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()  # Lee el archivo .env en la raíz del proyecto


@dataclass(frozen=True)
class Settings:
    polygon_api_key: str
    polygon_base_url: str = "https://api.polygon.io"
    polygon_ws_options_url: str = "wss://socket.polygon.io/options"
    polygon_ws_stocks_url: str = "wss://socket.polygon.io/stocks"

    # Tasa libre de riesgo por defecto (se puede sobreescribir por llamada).
    # Útil para Black-Scholes / paridad Put-Call en Bloques 2 y 3.
    default_risk_free_rate: float = 0.045

    # Dividend yield por defecto para el cálculo del forward (F = S*e^{(r-q)T}).
    # Aproximación razonable para ETFs de índice tipo SPY; sobreescribir por
    # ticker desde la UI cuando el subyacente tenga un yield distinto.
    default_dividend_yield: float = 0.013

    # Timeout de cada petición HTTP: (conexión, lectura) en segundos. Sin él,
    # requests espera para siempre en una conexión TCP muerta (p. ej. tras
    # suspender la máquina con una conexión keep-alive abierta).
    request_timeout: tuple = (5.0, 20.0)

    # --- Capital.com: fuente del precio spot en tiempo real ---
    # Las credenciales no se exigen al importar (solo al pedir el spot, ver
    # src/data/capital_client.py) para no romper módulos que no las usan.
    capital_api_key: str = ""
    capital_identifier: str = ""
    capital_api_password: str = ""
    capital_api_url: str = "https://demo-api-capital.backend-capital.com/api/v1"
    # Epic de Capital.com usado como spot de SPY. SPX usa US500 (en Capital.com
    # el epic "SPX" es Spirax Sarco, una acción; ver CAPITAL_INSTRUMENTS en
    # src/data/capital_client.py). Cualquier otro ticker usa su propio símbolo.
    capital_epic: str = "SPY"
    # Base SPX - US500 en puntos (SPX = mid de US500 + base). Es el respaldo
    # cuando no se puede calcular la base por paridad put-call en el ciclo.
    # Medido el 2026-10-05: US500 ≈ SPX - 1, o sea base ≈ +1.
    spx_basis: float = 1.0
    # Retraso de las opciones de Polygon (plan delayed): la base se calcula
    # contra el precio de US500 de hace este número de minutos.
    options_delay_minutes: int = 15


_PLACEHOLDER_PREFIX = "tu_"  # valores de ejemplo de .env.template


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, "")
    if not value or value.startswith(_PLACEHOLDER_PREFIX):
        return default
    return value


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        raise EnvironmentError(f"{name}={value!r} no es un número.") from None


def load_settings() -> Settings:
    api_key = _env("POLYGON_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "POLYGON_API_KEY no está configurada. "
            "Copia .env.template a .env y coloca tu key real."
        )
    return Settings(
        polygon_api_key=api_key,
        capital_api_key=_env("CAPITAL_API_KEY"),
        capital_identifier=_env("CAPITAL_IDENTIFIER"),
        capital_api_password=_env("CAPITAL_API_PASSWORD"),
        capital_api_url=_env("CAPITAL_API_URL", Settings.capital_api_url),
        capital_epic=_env("CAPITAL_EPIC", Settings.capital_epic),
        spx_basis=_env_float("SPX_BASIS", Settings.spx_basis),
        options_delay_minutes=int(_env_float("OPTIONS_DELAY_MINUTES", Settings.options_delay_minutes)),
    )


SETTINGS = load_settings()
