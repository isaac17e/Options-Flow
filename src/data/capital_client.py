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

Ticker -> epic: el epic "SPX" de Capital.com es Spirax Sarco (una acción),
no el índice. SPX se toma del CFD US500 (tipo INDICES) más una base; ver
CAPITAL_INSTRUMENTS y src/data/spot_basis.py.

No hay respaldo: el snapshot de opciones de Polygon (underlying_asset) no
trae precio en este plan, así que si Capital.com falla se reporta el error.
"""

from __future__ import annotations
import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from config import Settings, SETTINGS
from src.data.models import OptionContract, UnderlyingSnapshot
from src.data.spot_basis import BASIS_TICKERS, compute_basis, parity_forward

logger = logging.getLogger(__name__)

# ticker -> (epic de Capital.com, tipo de instrumento esperado o None).
# Con tipo esperado, un epic de otro tipo (p. ej. una acción homónima) se
# rechaza. SPY no se verifica: su tipo en Capital.com no está documentado aquí.
CAPITAL_INSTRUMENTS = {
    "SPX": ("US500", "INDICES"),
    "SPY": ("SPY", None),
}

# La barra histórica de US500 usada para la base debe estar a lo sumo a este
# número de minutos del instante buscado (si no, mercado cerrado / sin datos).
MAX_BAR_GAP_MINUTES = 5


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
    def resolve_instrument(self, ticker: str) -> tuple:
        """(epic, tipo esperado o None). SPY respeta CAPITAL_EPIC; lo no listado usa su símbolo."""
        ticker = ticker.upper()
        epic, expected_type = CAPITAL_INSTRUMENTS.get(ticker, (ticker, None))
        if ticker == "SPY":
            epic = self._settings.capital_epic
        return epic, expected_type

    def get_underlying_snapshot(
        self, ticker: str, default_basis: Optional[float] = None
    ) -> UnderlyingSnapshot:
        """
        Spot en tiempo real = punto medio (bid + offer) / 2 del snapshot de
        mercado del epic del ticker (ver CAPITAL_INSTRUMENTS). Para SPX se
        suma además `default_basis` (por defecto SETTINGS.spx_basis); la base
        calculada por paridad se aplica después con apply_parity_basis.
        """
        epic, expected_type = self.resolve_instrument(ticker)
        data = self._get(f"/markets/{epic}")
        self._check_instrument_type(epic, expected_type, data)
        snapshot = data.get("snapshot") or {}

        bid = snapshot.get("bid")
        offer = snapshot.get("offer")
        if bid is None or offer is None:
            raise CapitalClientError(f"Capital.com no devolvió bid/offer para {epic}")

        status = snapshot.get("marketStatus")
        if status and status != "TRADEABLE":
            logger.warning("%s: mercado %s en Capital.com, el spot puede no estar al día", epic, status)

        mid = (float(bid) + float(offer)) / 2
        update_time = self._parse_update_time(snapshot.get("updateTime"))
        if ticker.upper() not in BASIS_TICKERS:
            return UnderlyingSnapshot(
                ticker=ticker, spot_price=mid, snapshot_time=update_time,
                source=f"Capital.com {epic} (mid bid/offer)", raw_price=mid,
            )

        basis = self._settings.spx_basis if default_basis is None else float(default_basis)
        return UnderlyingSnapshot(
            ticker=ticker, spot_price=mid + basis, snapshot_time=update_time,
            source=f"Capital.com {epic} (mid bid/offer) + base",
            raw_price=mid, basis=basis,
            basis_source="parámetro (respaldo): base por paridad aún no calculada",
        )

    @staticmethod
    def _check_instrument_type(epic: str, expected: Optional[str], data: dict) -> None:
        if expected is None:
            return
        actual = (data.get("instrument") or {}).get("type")
        if actual is None:
            logger.warning("%s: Capital.com no informó el tipo de instrumento (se esperaba %s)", epic, expected)
        elif actual != expected:
            raise CapitalClientError(
                f"El epic {epic} de Capital.com es de tipo {actual}, se esperaba {expected}: "
                "no es el instrumento correcto para usarlo como spot."
            )

    # ------------------------------------------------------------------
    # Base SPX - US500 por paridad put-call
    # ------------------------------------------------------------------
    def apply_parity_basis(
        self,
        snapshot: UnderlyingSnapshot,
        contracts: list[OptionContract],
        now: Optional[datetime] = None,
    ) -> UnderlyingSnapshot:
        """
        Recalcula la base del snapshot: mediana del forward por paridad de los
        strikes ATM de `contracts` (cadena de UN vencimiento, el más próximo)
        menos el mid de US500 de hace `options_delay_minutes`. Si algo falla
        se devuelve el snapshot con la base de respaldo y el motivo en
        basis_source.
        """
        if snapshot.raw_price is None or snapshot.ticker.upper() not in BASIS_TICKERS:
            return snapshot

        def fallback(reason: str) -> UnderlyingSnapshot:
            logger.warning(
                "Base %s por paridad no disponible (%s); se usa %+.2f", snapshot.ticker, reason, snapshot.basis
            )
            return dataclasses.replace(snapshot, basis_source=f"parámetro (respaldo): {reason}")

        forward = parity_forward(contracts)
        if forward is None:
            return fallback("sin strikes ATM con precio de call y put en la cadena")

        delay = self._settings.options_delay_minutes
        target = (now or datetime.now(timezone.utc)) - timedelta(minutes=delay)
        epic, _ = self.resolve_instrument(snapshot.ticker)
        try:
            past_mid = self.get_mid_at(epic, target)
        except CapitalClientError as exc:
            return fallback(f"sin precio histórico de {epic}: {exc}")
        if past_mid is None:
            return fallback(f"sin barra de {epic} a ±{MAX_BAR_GAP_MINUTES} min de t-{delay} min")

        basis = compute_basis(forward, past_mid)
        if basis is None:
            return fallback(f"base absurda (forward {forward:.2f} vs {epic} {past_mid:.2f})")

        return dataclasses.replace(
            snapshot,
            spot_price=snapshot.raw_price + basis,
            basis=basis,
            basis_source=f"paridad put-call (forward {forward:.2f}) - {epic} de t-{delay} min ({past_mid:.2f})",
        )

    def get_mid_at(self, epic: str, when: datetime) -> Optional[float]:
        """
        Mid (bid+ask)/2 del cierre de la barra de 1 minuto más cercana a `when`
        (UTC; sin tzinfo se asume UTC), vía GET /prices/{epic}. None si no hay
        barra a menos de MAX_BAR_GAP_MINUTES (mercado cerrado o sin datos).
        """
        when = when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)
        fmt = "%Y-%m-%dT%H:%M:%S"
        window = timedelta(minutes=MAX_BAR_GAP_MINUTES + 1)
        path = (
            f"/prices/{epic}?resolution=MINUTE&max=30"
            f"&from={(when - window).strftime(fmt)}&to={(when + window).strftime(fmt)}"
        )
        best, best_gap = None, timedelta(minutes=MAX_BAR_GAP_MINUTES)
        for bar in self._get(path).get("prices") or []:
            try:
                # snapshotTimeUTC es el inicio de la barra; su cierre es un minuto después.
                start = datetime.fromisoformat(bar["snapshotTimeUTC"]).replace(tzinfo=timezone.utc)
                close = bar["closePrice"]
                mid = (float(close["bid"]) + float(close["ask"])) / 2
            except (KeyError, TypeError, ValueError):
                continue
            gap = abs(start + timedelta(minutes=1) - when)
            if gap <= best_gap:
                best, best_gap = mid, gap
        return best

    @staticmethod
    def _parse_update_time(value: Optional[str]) -> datetime:
        if value:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                logger.debug("updateTime no reconocido: %s", value)
        return datetime.now()
