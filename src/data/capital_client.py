"""
src/data/capital_client.py
----------------------------
Cliente mínimo (solo lectura) hacia la API REST de Capital.com, usado como
fuente del precio spot en tiempo real del subyacente.

Motivo: el plan de Polygon solo cubre opciones y datos de referencia; las
barras de acciones del día en curso devuelven 403, así que el spot salía
de la sesión anterior. Capital.com da bid/offer en tiempo real.

Flujo:
    POST {CAPITAL_API_URL}/session   (header X-CAP-API-KEY, body identifier/password)
        -> tokens CST y X-SECURITY-TOKEN en los headers de la respuesta
    GET  {CAPITAL_API_URL}/markets/{epic}
        -> spot = (bid + offer) / 2 del bloque "snapshot"

No hay respaldo: el snapshot de opciones de Polygon (underlying_asset) no
trae precio en este plan, así que si Capital.com falla se reporta el error.
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional

import requests

from config import Settings, SETTINGS
from src.data.models import UnderlyingSnapshot

logger = logging.getLogger(__name__)


class CapitalClientError(Exception):
    """Error de credenciales, de comunicación o de datos con Capital.com"""
    pass


class CapitalClient:
    def __init__(self, settings: Settings = SETTINGS):
        missing = [
            name
            for name, value in (
                ("CAPITAL_API_KEY", settings.capital_api_key),
                ("CAPITAL_IDENTIFIER", settings.capital_identifier),
                ("CAPITAL_API_PASSWORD", settings.capital_api_password),
            )
            if not value
        ]
        if missing:
            raise CapitalClientError(
                f"Faltan credenciales de Capital.com: {', '.join(missing)}. "
                "Configúralas en .env (ver .env.template)."
            )
        self._settings = settings
        self._base_url = settings.capital_api_url.rstrip("/")
        self._session = requests.Session()
        self._auth_headers: Optional[dict] = None

    # ------------------------------------------------------------------
    # Sesión
    # ------------------------------------------------------------------
    def _login(self) -> dict:
        url = f"{self._base_url}/session"
        try:
            resp = self._session.post(
                url,
                headers={"X-CAP-API-KEY": self._settings.capital_api_key},
                json={
                    "identifier": self._settings.capital_identifier,
                    "password": self._settings.capital_api_password,
                },
                timeout=self._settings.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise CapitalClientError(f"Fallo al abrir sesión en Capital.com: {exc}") from exc

        if resp.status_code != 200:
            # No se incluye el body de la petición: lleva la contraseña.
            raise CapitalClientError(
                f"Capital.com rechazó la sesión (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        cst = resp.headers.get("CST")
        token = resp.headers.get("X-SECURITY-TOKEN")
        if not cst or not token:
            raise CapitalClientError("Capital.com no devolvió los tokens CST / X-SECURITY-TOKEN")

        self._auth_headers = {"CST": cst, "X-SECURITY-TOKEN": token}
        return self._auth_headers

    def _get(self, path: str) -> dict:
        url = f"{self._base_url}{path}"
        # Un 401 suele ser una sesión expirada (10 min de inactividad):
        # se reabre una sola vez.
        for attempt in range(2):
            headers = self._auth_headers or self._login()
            try:
                resp = self._session.get(
                    url, headers=headers, timeout=self._settings.request_timeout_seconds
                )
            except requests.RequestException as exc:
                raise CapitalClientError(f"Fallo al consultar {url}: {exc}") from exc

            if resp.status_code == 401 and attempt == 0:
                self._auth_headers = None
                continue
            if resp.status_code != 200:
                raise CapitalClientError(
                    f"Capital.com respondió HTTP {resp.status_code} en {path}: {resp.text[:200]}"
                )
            return resp.json()
        raise CapitalClientError(f"Capital.com rechazó la sesión renovada en {path}")

    # ------------------------------------------------------------------
    # Spot
    # ------------------------------------------------------------------
    def get_underlying_snapshot(self, ticker: str) -> UnderlyingSnapshot:
        """
        Spot en tiempo real = punto medio (bid + offer) / 2 del snapshot de
        mercado. Para SPY se usa el epic configurado (CAPITAL_EPIC); para
        cualquier otro ticker, el propio símbolo.
        """
        epic = self._settings.capital_epic if ticker.upper() == "SPY" else ticker.upper()
        data = self._get(f"/markets/{epic}")
        snapshot = data.get("snapshot") or {}

        bid = snapshot.get("bid")
        offer = snapshot.get("offer")
        if bid is None or offer is None:
            raise CapitalClientError(f"Capital.com no devolvió bid/offer para {epic}")

        status = snapshot.get("marketStatus")
        if status and status != "TRADEABLE":
            logger.warning("%s: mercado %s en Capital.com, el spot puede no estar al día", epic, status)

        return UnderlyingSnapshot(
            ticker=ticker,
            spot_price=(float(bid) + float(offer)) / 2,
            snapshot_time=self._parse_update_time(snapshot.get("updateTime")),
        )

    @staticmethod
    def _parse_update_time(value: Optional[str]) -> datetime:
        if value:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                logger.debug("updateTime no reconocido: %s", value)
        return datetime.now()
