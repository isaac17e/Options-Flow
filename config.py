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


def load_settings() -> Settings:
    api_key = os.getenv("POLYGON_API_KEY")
    if not api_key or api_key == "tu_api_key_aqui":
        raise EnvironmentError(
            "POLYGON_API_KEY no está configurada. "
            "Copia .env.template a .env y coloca tu key real."
        )
    return Settings(polygon_api_key=api_key)


SETTINGS = load_settings()
