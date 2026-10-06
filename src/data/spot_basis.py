"""
src/data/spot_basis.py
------------------------
Base entre el índice SPX y el CFD US500 de Capital.com.

El spot de SPX sale de US500 (en Capital.com el epic "SPX" es una acción):
    SPX ≈ mid(US500) + base

La base se estima en cada ciclo con la propia cadena de opciones de Polygon:
    base = mediana del forward por paridad put-call en los strikes ATM
           - mid de US500 de hace `options_delay_minutes` minutos
Las opciones van ~15 min por detrás del mercado, así que se comparan con el
US500 de ese mismo instante y no con el actual.

El forward por paridad es F = K + C - P (sin descontar). Para vencimientos de
0-2 días la diferencia con el spot es de pocos décimos de punto y queda
absorbida en la base; por eso solo se usa la cadena del vencimiento más próximo.

Módulo puro: sin red ni estado, para poder probarlo con datos sintéticos.
"""

from __future__ import annotations
import re
import statistics
from collections import defaultdict
from typing import Iterable, Optional

from src.data.models import OptionContract

# Tickers cuyo spot sale de un epic con base (ticker -> epic en CAPITAL_INSTRUMENTS).
BASIS_TICKERS = frozenset({"SPX"})

PARITY_ATM_STRIKES = 7        # strikes con |C - P| mínimo que entran en la mediana
PARITY_MIN_STRIKES = 3        # menos que esto no es una estimación confiable
MAX_ABS_BASIS_FRACTION = 0.005  # una base mayor a 0,5% del precio se descarta como dato malo

_ROOT_RE = re.compile(r"^O:([A-Z]+)\d{6}[CP]")


def _option_root(ticker: Optional[str]) -> str:
    # SPX y SPXW comparten strikes en vencimientos mensuales: no se mezclan.
    match = _ROOT_RE.match(ticker or "")
    return match.group(1) if match else ""


def _price(contract: OptionContract) -> Optional[float]:
    price = contract.mid_price or contract.day_close
    return float(price) if price and price > 0 else None


def parity_forward(
    contracts: Iterable[OptionContract],
    n_strikes: int = PARITY_ATM_STRIKES,
    min_strikes: int = PARITY_MIN_STRIKES,
) -> Optional[float]:
    """
    Mediana de K + C - P sobre los `n_strikes` strikes donde call y put
    cuestan lo más parecido (los ATM). Solo cuentan los contratos con precio
    (mid bid/ask, último trade o cierre del día) y volumen distinto de cero.
    Devuelve None si hay menos de `min_strikes` strikes utilizables.
    """
    by_key: dict = defaultdict(dict)
    for c in contracts:
        if c.volume is not None and c.volume <= 0:
            continue
        price = _price(c)
        if price is None:
            continue
        by_key[(c.expiration, _option_root(c.ticker), c.strike)][c.contract_type] = price

    rows = [
        (strike, sides["call"], sides["put"])
        for (_, _, strike), sides in by_key.items()
        if "call" in sides and "put" in sides
    ]
    if len(rows) < min_strikes:
        return None

    rows.sort(key=lambda r: abs(r[1] - r[2]))
    estimates = [k + c - p for k, c, p in rows[:n_strikes]]
    return float(statistics.median(estimates))


def compute_basis(forward: Optional[float], reference_price: Optional[float]) -> Optional[float]:
    """
    forward - reference_price, o None si falta alguno o la base es absurda
    (> MAX_ABS_BASIS_FRACTION del precio: casi seguro un precio de opción viejo).
    """
    if forward is None or not reference_price or reference_price <= 0:
        return None
    basis = forward - reference_price
    if abs(basis) > MAX_ABS_BASIS_FRACTION * reference_price:
        return None
    return float(basis)
