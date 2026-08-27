"""
src/data/polygon_client.py
----------------------------
Cliente REST hacia Polygon.io. Encapsula TODA la comunicación HTTP.

Métodos clave para el Bloque 1:
    - get_underlying_snapshot(ticker)      -> UnderlyingSnapshot
    - get_option_chain_snapshot(ticker)    -> OptionChainSnapshot (limpio)
    - estimate_forward_price(chain, r)      -> float (paridad Put-Call)
    - get_historical_aggregates(...)        -> pd.DataFrame (para Bloque futuro)

Nota de diseño: Polygon pagina los snapshots de opciones (next_url).
Aquí manejamos la paginación de forma transparente para que el resto
del sistema reciba SIEMPRE la cadena completa, sin preocuparse por eso.
"""

from __future__ import annotations
import logging
import time
from datetime import datetime, date, timedelta
from typing import Optional

import requests

from config import Settings, SETTINGS
from src.data.models import OptionContract, OptionChainSnapshot, UnderlyingSnapshot

logger = logging.getLogger(__name__)


class PolygonClientError(Exception):
    """Error de comunicación o de datos al hablar con Polygon.io"""
    pass


class PolygonClient:
    def __init__(self, settings: Settings = SETTINGS):
        self._settings = settings
        self._session = requests.Session()
        self._session.params = {"apiKey": self._settings.polygon_api_key}

    # ------------------------------------------------------------------
    # Bajo nivel: request genérico con reintentos simples
    # ------------------------------------------------------------------
    def _get(self, path: str, params: Optional[dict] = None, retries: int = 3) -> dict:
        url = f"{self._settings.polygon_base_url}{path}"
        last_exc = None
        for attempt in range(retries):
            try:
                resp = self._session.get(
                    url, params=params, timeout=self._settings.request_timeout_seconds
                )
                if resp.status_code == 429:
                    # Rate limit -> backoff exponencial simple
                    wait = 2 ** attempt
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(1 + attempt)
        raise PolygonClientError(f"Fallo al consultar {url}: {last_exc}")

    # ------------------------------------------------------------------
    # Subyacente
    # ------------------------------------------------------------------
    def get_underlying_snapshot(self, ticker: str) -> UnderlyingSnapshot:
        """
        Obtiene el precio spot vía el endpoint de agregados de 1 minuto
        (compatible con planes de datos delayed, como el tuyo). Trae la
        barra de 1 minuto más reciente disponible y usa su cierre.
        """
        today = date.today()
        lookback_start = today - timedelta(days=5)  # margen por si el mercado
        # está cerrado (fin de semana/feriado) y no hay barras de hoy.

        path = (
            f"/v2/aggs/ticker/{ticker}/range/1/minute/"
            f"{lookback_start.isoformat()}/{today.isoformat()}"
        )
        data = self._get(path, params={"sort": "desc", "limit": 1})

        results = data.get("results", [])
        if not results:
            raise PolygonClientError(f"No hay barras recientes disponibles para {ticker}")

        last_bar = results[0]
        spot = last_bar.get("c")  # precio de cierre de la última barra
        bar_timestamp_ms = last_bar.get("t")

        if spot is None:
            raise PolygonClientError(f"No se pudo determinar el precio spot de {ticker}")

        bar_time = (
            datetime.fromtimestamp(bar_timestamp_ms / 1000)
            if bar_timestamp_ms is not None
            else datetime.now()
        )

        return UnderlyingSnapshot(ticker=ticker, spot_price=float(spot), snapshot_time=bar_time)

    # ------------------------------------------------------------------
    # Listado de vencimientos disponibles (endpoint de referencia, liviano)
    # ------------------------------------------------------------------
    def get_available_expirations(self, ticker: str, limit: int = 1000) -> list:
        """
        Trae la lista de vencimientos disponibles para un ticker usando el
        endpoint de referencia de contratos (liviano: sin greeks/IV/OI).

        Se ordena explícitamente por expiration_date (no por strike), así
        evitamos el problema de que un solo vencimiento "acapare" el límite
        de resultados por tener muchos strikes cercanos al ATM.
        """
        path = "/v3/reference/options/contracts"
        params = {
            "underlying_ticker": ticker,
            "limit": min(limit, 1000),
            "sort": "expiration_date",
            "order": "asc",
            "expired": "false",
        }

        expirations = set()
        next_url = None

        while True:
            if next_url:
                data = self._get_full_url(next_url)
            else:
                data = self._get(path, params=params)

            for item in data.get("results", []):
                exp_str = item.get("expiration_date")
                if exp_str:
                    expirations.add(datetime.strptime(exp_str, "%Y-%m-%d").date())

            next_url = data.get("next_url")
            if not next_url or len(expirations) >= limit:
                break

        return sorted(expirations)

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
            Ej. "SPY", "NVDA".
        expiration_date : date, opcional
            Si se especifica, filtra solo esa fecha de vencimiento
            (reduce drásticamente el volumen de datos).
        max_contracts : int, opcional
            Límite de seguridad para pruebas rápidas.
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
        path = f"/v3/snapshot/options/{underlying_ticker}"
        next_url = None

        while True:
            if next_url:
                # next_url ya viene con querystring completo de Polygon
                data = self._get_full_url(next_url)
            else:
                data = self._get(path, params=params)

            results = data.get("results", [])
            for item in results:
                contract = self._parse_contract_snapshot(item, underlying_ticker)
                if contract is not None:
                    contracts.append(contract)
                else:
                    skipped += 1

            if max_contracts and len(contracts) >= max_contracts:
                contracts = contracts[:max_contracts]
                break

            next_url = data.get("next_url")
            if not next_url:
                break

        if skipped:
            logger.warning(
                "%s: %d contrato(s) descartado(s) por datos mal formados de %d recibidos",
                underlying_ticker, skipped, skipped + len(contracts),
            )

        return OptionChainSnapshot(underlying=underlying, contracts=contracts)

    def _get_full_url(self, url: str) -> dict:
        """Para seguir el next_url de paginación (viene con dominio incluido)."""
        resp = self._session.get(url, timeout=self._settings.request_timeout_seconds)
        resp.raise_for_status()
        return resp.json()

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
