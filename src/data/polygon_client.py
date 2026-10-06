"""
src/data/polygon_client.py
----------------------------
Cliente REST hacia Polygon.io para opciones y datos de referencia.

Métodos clave para el Bloque 1:
    - get_underlying_snapshot(ticker)      -> UnderlyingSnapshot (spot de Capital.com)
    - get_available_expirations(ticker, n) -> los n vencimientos más cercanos
    - get_option_chain_snapshot(ticker)    -> OptionChainSnapshot (limpio)
    - estimate_forward_price(...)          -> float (costo de acarreo)

El spot NO sale de Polygon: el plan no incluye barras de acciones del día
(403), así que se delega en src/data/capital_client.py.

Nota de diseño: Polygon pagina las respuestas (next_url). Cada página pasa
por _request, que reintenta 429 (respetando Retry-After) y 5xx transitorios
con backoff exponencial + jitter.
"""

from __future__ import annotations
import logging
import random
import time
from datetime import datetime, date
from typing import Optional

import requests

from config import Settings, SETTINGS
from src.data.capital_client import CapitalClient
from src.data.models import OptionContract, OptionChainSnapshot, UnderlyingSnapshot

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
RETRY_AFTER_MAX_SECONDS = 120.0  # tope de cordura para un Retry-After del servidor


# ticker -> (ticker para contratos de referencia, ticker para el snapshot).
# SPX: la referencia con "SPX" trae también SPXW, pero el snapshot con "SPX"
# llega sin IV ni griegas; hay que pedirlo como "I:SPX".
POLYGON_TICKERS = {
    "SPX": ("SPX", "I:SPX"),
    "SPY": ("SPY", "SPY"),
}


def polygon_tickers(ticker: str) -> tuple:
    """(ticker de referencia, ticker de snapshot); lo no listado usa el mismo para ambos."""
    ticker = ticker.upper()
    return POLYGON_TICKERS.get(ticker, (ticker, ticker))


class PolygonClientError(Exception):
    """Error de comunicación o de datos al hablar con Polygon.io"""
    pass


class PolygonClient:
    def __init__(self, settings: Settings = SETTINGS, spot_client: Optional[CapitalClient] = None):
        self._settings = settings
        self._session = requests.Session()
        self._session.params = {"apiKey": self._settings.polygon_api_key}
        # Se crea al pedir el spot por primera vez: así las credenciales de
        # Capital.com solo se exigen a quien realmente necesita el spot.
        self._spot_client = spot_client

    # ------------------------------------------------------------------
    # Bajo nivel: request con reintentos (429 y 5xx transitorios)
    # ------------------------------------------------------------------
    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        return self._request(f"{self._settings.polygon_base_url}{path}", params)

    def _request(self, url: str, params: Optional[dict] = None) -> dict:
        """GET con reintentos. Sirve igual para un path propio que para el
        next_url de paginación (que ya trae dominio y querystring)."""
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            retry_after = None
            try:
                resp = self._session.get(
                    url, params=params, timeout=self._settings.request_timeout_seconds
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = str(exc)
            else:
                if resp.status_code not in RETRYABLE_STATUS:
                    try:
                        resp.raise_for_status()
                    except requests.HTTPError as exc:
                        # 4xx (p. ej. 403 por plan): reintentar no cambia nada.
                        raise PolygonClientError(f"Fallo al consultar {url}: {exc}") from exc
                    return resp.json()
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code == 429:
                    retry_after = self._parse_retry_after(resp.headers.get("Retry-After"))

            if attempt == MAX_RETRIES:
                break
            wait = retry_after if retry_after is not None else self._backoff_seconds(attempt)
            logger.warning("Polygon %s; reintento %d/%d en %.1f s", last_error, attempt + 1, MAX_RETRIES, wait)
            time.sleep(wait)

        raise PolygonClientError(f"Fallo al consultar {url} tras {MAX_RETRIES} reintentos: {last_error}")

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        base = min(BACKOFF_BASE_SECONDS * 2 ** attempt, BACKOFF_MAX_SECONDS)
        return base + random.uniform(0, base)

    @staticmethod
    def _parse_retry_after(value: Optional[str]) -> Optional[float]:
        # Solo la forma en segundos; la forma de fecha HTTP cae al backoff.
        try:
            return min(max(float(value), 0.0), RETRY_AFTER_MAX_SECONDS)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Subyacente (Capital.com)
    # ------------------------------------------------------------------
    def _capital(self) -> CapitalClient:
        if self._spot_client is None:
            self._spot_client = CapitalClient(self._settings)
        return self._spot_client

    def get_underlying_snapshot(self, ticker: str, default_basis: Optional[float] = None) -> UnderlyingSnapshot:
        """
        Spot en tiempo real desde Capital.com (ver capital_client.py). Para
        SPX, `default_basis` es la base de respaldo (por defecto SETTINGS.spx_basis).
        """
        if default_basis is None:
            return self._capital().get_underlying_snapshot(ticker)
        return self._capital().get_underlying_snapshot(ticker, default_basis=default_basis)

    def refine_underlying_basis(
        self, underlying: UnderlyingSnapshot, contracts: list
    ) -> UnderlyingSnapshot:
        """Base SPX - US500 por paridad con la cadena del vencimiento más próximo (ver capital_client.py)."""
        return self._capital().apply_parity_basis(underlying, contracts)

    # ------------------------------------------------------------------
    # Listado de vencimientos disponibles (endpoint de referencia, liviano)
    # ------------------------------------------------------------------
    def get_available_expirations(self, ticker: str, max_expirations: int) -> list:
        """
        Devuelve los `max_expirations` vencimientos vigentes más cercanos,
        usando el endpoint de referencia de contratos (liviano: sin
        greeks/IV/OI).

        Se filtra expiration_date >= hoy y se ordena por vencimiento
        ascendente, así que las fechas llegan en orden y se deja de paginar
        en cuanto se han visto `max_expirations` fechas distintas.
        """
        path = "/v3/reference/options/contracts"
        params = {
            "underlying_ticker": polygon_tickers(ticker)[0],
            "expiration_date.gte": date.today().isoformat(),
            "expired": "false",
            "sort": "expiration_date",
            "order": "asc",
            "limit": 1000,
        }

        expirations = set()
        data = self._get(path, params=params)
        while True:
            for item in data.get("results", []):
                exp_str = item.get("expiration_date")
                if exp_str:
                    expirations.add(datetime.strptime(exp_str, "%Y-%m-%d").date())

            next_url = data.get("next_url")
            if not next_url or len(expirations) >= max_expirations:
                break
            data = self._request(next_url)

        return sorted(expirations)[:max_expirations]

    # ------------------------------------------------------------------
    # Cadena de opciones (snapshot completo, paginado)
    # ------------------------------------------------------------------
    def get_option_chain_snapshot(
        self,
        underlying_ticker: str,
        expiration_date: Optional[date] = None,
        max_contracts: Optional[int] = None,
        underlying: Optional[UnderlyingSnapshot] = None,
    ) -> OptionChainSnapshot:
        """
        Trae la cadena completa de opciones vía el endpoint de snapshot,
        manejando paginación automáticamente.

        Parameters
        ----------
        underlying_ticker : str
            Ej. "SPY", "NVDA", "SPX" (se consulta como "I:SPX", ver POLYGON_TICKERS).
        expiration_date : date, opcional
            Si se especifica, filtra solo esa fecha de vencimiento
            (reduce drásticamente el volumen de datos).
        max_contracts : int, opcional
            Límite de seguridad para pruebas rápidas. Por defecto no hay
            tope: con `expiration_date` se pagina hasta el final para no
            perder strikes. Si el tope corta la cadena se emite un warning
            (al ir ordenado por strike, se pierden los strikes altos).
        underlying : UnderlyingSnapshot, opcional
            Si ya tienes el spot obtenido (ej. en un loop sobre varias
            expiraciones), pásalo aquí para evitar pedirlo de nuevo en
            cada llamada y así no gastar tu límite de tasa de la API.
        """
        if underlying is None:
            underlying = self.get_underlying_snapshot(underlying_ticker)

        params = {"limit": 250, "order": "asc", "sort": "strike_price"}
        if expiration_date is not None:
            params["expiration_date"] = expiration_date.isoformat()

        contracts: list[OptionContract] = []
        skipped = 0
        path = f"/v3/snapshot/options/{polygon_tickers(underlying_ticker)[1]}"
        data = self._get(path, params=params)

        while True:
            results = data.get("results", [])
            for item in results:
                contract = self._parse_contract_snapshot(item, underlying_ticker)
                if contract is not None:
                    contracts.append(contract)
                else:
                    skipped += 1

            next_url = data.get("next_url")
            if max_contracts and len(contracts) >= max_contracts:
                if len(contracts) > max_contracts or next_url:
                    logger.warning(
                        "%s %s: tope max_contracts=%d alcanzado, la cadena quedó truncada "
                        "(faltan strikes por encima de %.2f)",
                        underlying_ticker, expiration_date or "(todos)", max_contracts,
                        contracts[max_contracts - 1].strike,
                    )
                contracts = contracts[:max_contracts]
                break

            if not next_url:
                break
            # next_url ya viene con querystring completo de Polygon
            data = self._request(next_url)

        if skipped:
            logger.warning(
                "%s: %d contrato(s) descartado(s) por datos mal formados de %d recibidos",
                underlying_ticker, skipped, skipped + len(contracts),
            )

        return OptionChainSnapshot(underlying=underlying, contracts=contracts)

    @staticmethod
    def _parse_contract_snapshot(item: dict, underlying_ticker: str) -> Optional[OptionContract]:
        try:
            details = item.get("details", {})
            day = item.get("day", {})
            greeks = item.get("greeks", {})
            last_quote = item.get("last_quote", {})
            last_trade = item.get("last_trade", {})

            contract_type = details.get("contract_type")  # "call" | "put"
            strike = details.get("strike_price")
            expiration_str = details.get("expiration_date")  # "YYYY-MM-DD"
            ticker = details.get("ticker")

            if contract_type is None or strike is None or expiration_str is None:
                logger.debug("Contrato descartado (campos requeridos faltantes): %s", ticker or item)
                return None

            expiration = datetime.strptime(expiration_str, "%Y-%m-%d").date()

            return OptionContract(
                ticker=ticker,
                underlying_ticker=underlying_ticker,
                contract_type=contract_type,
                strike=float(strike),
                expiration=expiration,
                last_price=last_trade.get("price"),
                bid=last_quote.get("bid"),
                ask=last_quote.get("ask"),
                volume=day.get("volume"),
                open_interest=item.get("open_interest"),
                implied_volatility=item.get("implied_volatility"),
                delta=greeks.get("delta"),
                gamma=greeks.get("gamma"),
                vega=greeks.get("vega"),
                theta=greeks.get("theta"),
                snapshot_time=datetime.now(),
                day_close=day.get("close"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            # Un contrato mal formado no debe tumbar toda la cadena.
            logger.debug("Contrato descartado (error de parseo): %s — %s", exc, item)
            return None

    # ------------------------------------------------------------------
    # Forward price vía costo de acarreo (Bloques 2 y 3)
    # ------------------------------------------------------------------
    def estimate_forward_price(
        self,
        spot_price: float,
        time_to_expiration_years: float,
        risk_free_rate: Optional[float] = None,
        dividend_yield: float = 0.0,
    ) -> float:
        """
        F = S * e^((r - q) * T)

        Nota: no usamos paridad Put-Call porque tu plan de Polygon no
        incluye bid/ask ni last_trade de opciones (solo greeks/IV/OI).
        Esta fórmula de costo de acarreo es el estándar cuando no se
        dispone de precios de mercado suficientemente líquidos para
        una paridad confiable, y es la misma que usaremos como input
        de F en el Bloque 2 para calcular k = ln(K/F).
        """
        import math

        r = risk_free_rate if risk_free_rate is not None else self._settings.default_risk_free_rate
        return spot_price * math.exp((r - dividend_yield) * time_to_expiration_years)
