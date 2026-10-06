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

    request_timeout_seconds: int = 10

    # --- Capital.com: fuente del precio spot en tiempo real ---
    # Las credenciales no se exigen al importar (solo al pedir el spot, ver
    # src/data/capital_client.py) para no romper módulos que no las usan.
    capital_api_key: str = ""
    capital_identifier: str = ""
    capital_api_password: str = ""
    capital_api_url: str = "https://demo-api-capital.backend-capital.com/api/v1"
    # Epic de Capital.com usado como spot de SPY. Cualquier otro ticker se
    # pide usando su propio símbolo como epic.
    capital_epic: str = "SPY"


_PLACEHOLDER_PREFIX = "tu_"  # valores de ejemplo de .env.template


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, "")
    if not value or value.startswith(_PLACEHOLDER_PREFIX):
        return default
    return value


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
    )


SETTINGS = load_settings()
