"""
src/data/models.py
-------------------
Estructuras de datos limpias que aíslan al resto del sistema (SVI, GEX,
dashboard) de la forma cruda en que Polygon.io entrega la información.

Cualquier bloque futuro (2, 3, 4, 5) trabaja SOLO contra estas clases,
nunca contra el JSON crudo de la API.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, Literal
import pandas as pd


ContractType = Literal["call", "put"]


@dataclass
class OptionContract:
    """Un solo contrato de opción con su snapshot de mercado más reciente."""

    ticker: str                      # ej. "O:SPY250117C00580000"
    underlying_ticker: str            # ej. "SPY"
    contract_type: ContractType
    strike: float
    expiration: date

    # --- Mercado ---
    last_price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None

    # --- Griegas e IV que Polygon ya calcula (útiles como referencia/QA,
    # aunque en el Bloque 2-3 recalcularemos IV con SVI para consistencia) ---
    implied_volatility: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    theta: Optional[float] = None

    snapshot_time: Optional[datetime] = None

    # Cierre del día del contrato (día en curso o último): respaldo de precio
    # cuando no hay last trade ni quote (se usa en la paridad put-call del SPX).
    day_close: Optional[float] = None

    @property
    def mid_price(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last_price

    @property
    def days_to_expiration(self) -> int:
        return (self.expiration - date.today()).days

    @property
    def time_to_expiration_years(self) -> float:
        return max(self.days_to_expiration, 0) / 365.0


@dataclass
class UnderlyingSnapshot:
    """Snapshot del activo subyacente."""

    ticker: str
    spot_price: float
    snapshot_time: datetime
    # Trazabilidad del spot. Para SPX: spot_price = raw_price (mid de US500) + basis.
    source: str = "Capital.com (mid bid/offer)"
    raw_price: Optional[float] = None   # mid del epic de Capital.com, sin base
    basis: float = 0.0                  # puntos sumados a raw_price (0 si no aplica)
    basis_source: str = ""              # "paridad put-call ..." o "parámetro (respaldo): motivo"


@dataclass
class OptionChainSnapshot:
    """
    Cadena completa de opciones para un subyacente, en un instante de tiempo.
    Esta es la unidad fundamental que consumirán los Bloques 2, 3 y 4.
    """

    underlying: UnderlyingSnapshot
    contracts: list[OptionContract] = field(default_factory=list)

    def to_dataframe(self) -> pd.DataFrame:
        """Convierte la cadena a un DataFrame limpio, una fila por contrato."""
        rows = [
            {
                "ticker": c.ticker,
                "underlying": c.underlying_ticker,
                "type": c.contract_type,
                "strike": c.strike,
                "expiration": c.expiration,
                "dte": c.days_to_expiration,
                "tte_years": c.time_to_expiration_years,
                "bid": c.bid,
                "ask": c.ask,
                "mid": c.mid_price,
                "last": c.last_price,
                "volume": c.volume,
                "open_interest": c.open_interest,
                "iv": c.implied_volatility,
                "delta": c.delta,
                "gamma": c.gamma,
                "vega": c.vega,
                "theta": c.theta,
            }
            for c in self.contracts
        ]
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values(["expiration", "strike", "type"]).reset_index(drop=True)
        return df

    def expirations(self) -> list[date]:
        return sorted({c.expiration for c in self.contracts})

    def filter_by_expiration(self, expiration: date) -> "OptionChainSnapshot":
        filtered = [c for c in self.contracts if c.expiration == expiration]
        return OptionChainSnapshot(underlying=self.underlying, contracts=filtered)
