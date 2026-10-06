"""
Resolución de tickers: subyacente de opciones (Polygon) → CFD de Capital.com.

    resolve(ticker, ref_price) → Resolution(
        ticker, polygon_reference_ticker, polygon_snapshot_ticker,
        capital_epic, instrument_type, basis_method, cfd_price, ref_price, deviation)

* Índices (tabla fija): SPX → I:SPX / US500, NDX → I:NDX / US100, RUT → I:RUT / RTY.
  La referencia de contratos de Polygon usa el ticker sin prefijo (SPX incluye SPXW) y el
  snapshot el ticker de índice (con "SPX" Polygon no devuelve IV ni griegas). En Capital.com
  el instrumento debe ser de tipo INDICES.
* ETFs y acciones: el mismo ticker en Polygon; en Capital.com se busca con
  GET /markets?searchTerm=<ticker> y se acepta solo un instrumento SHARES cuyo epic sea
  exactamente el ticker (o, si no existe, el único SHARES con precio dentro de la tolerancia).
* Siempre que haya precio de referencia (spot implícito por paridad de la propia cadena) el
  CFD debe estar a ≤ tolerancia (1 % por defecto). Así se descartan colisiones de símbolo
  como SPX = Spirax Sarco en Capital.com. Si no hay coincidencia segura → TickerResolutionError.
* El mapa resuelto se cachea en JSON (por defecto legacy_0dte/.ticker_map.json, o
  OPTIONS_FLOW_TICKER_CACHE); el chequeo de precio se repite en cada resolución con ref_price.

Base (basis) = precio del CFD − spot del subyacente (por paridad), medido con el CFD en el
instante al que corresponden los precios de opciones (Polygon va ~15 min retrasado).
Spot en vivo del subyacente = CFD − base (mediana móvil de las muestras).
"""
from __future__ import annotations

import json
import os
import re
import statistics
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable, Optional

DEFAULT_TOLERANCE = 0.01

# ticker → (referencia Polygon, snapshot Polygon, epic Capital.com)
INDEX_MAP = {
    "SPX":  ("SPX", "I:SPX", "US500"),
    "SPXW": ("SPX", "I:SPX", "US500"),
    "NDX":  ("NDX", "I:NDX", "US100"),
    "NDXP": ("NDX", "I:NDX", "US100"),
    "RUT":  ("RUT", "I:RUT", "RTY"),
}
# Epic → tipo Capital.com esperado para los índices.
SHARE_TYPES = {"SHARES"}
INDEX_TYPES = {"INDICES"}

_DEFAULT_CACHE = os.environ.get(
    "OPTIONS_FLOW_TICKER_CACHE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ticker_map.json"),
)


class TickerResolutionError(RuntimeError):
    """No hay un instrumento de Capital.com que sea, con seguridad, el subyacente."""


@dataclass
class PolygonTickers:
    reference: str
    snapshot: str
    is_index: bool


@dataclass
class Resolution:
    ticker: str
    polygon_reference_ticker: str
    polygon_snapshot_ticker: str
    capital_epic: str
    instrument_type: str
    basis_method: str
    cfd_price: Optional[float] = None
    ref_price: Optional[float] = None
    deviation: Optional[float] = None
    resolved_at: Optional[float] = None


def normalize(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if t.startswith("I:"):
        t = t[2:]
    if not t:
        raise TickerResolutionError("Ticker vacío")
    return t


def polygon_tickers(ticker: str) -> PolygonTickers:
    t = normalize(ticker)
    if t in INDEX_MAP:
        ref, snap, _ = INDEX_MAP[t]
        return PolygonTickers(ref, snap, True)
    return PolygonTickers(t, t, False)


def _mid(m: dict) -> Optional[float]:
    snap = m.get("snapshot", m)
    bid, offer = snap.get("bid"), snap.get("offer")
    if bid is None or offer is None:
        return None
    return (float(bid) + float(offer)) / 2.0


class TickerResolver:
    """
    capital_get: función path → JSON (p. ej. CapitalSession.get del script legacy o
    CapitalBroker.get del trader). La búsqueda usa la ruta "/markets?searchTerm=<t>".
    """

    def __init__(self, capital_get: Callable[[str], dict], cache_path: Optional[str] = _DEFAULT_CACHE,
                 tolerance: float = DEFAULT_TOLERANCE):
        self._get = capital_get
        self.cache_path = cache_path
        self.tolerance = tolerance
        self._cache = self._load()

    # ── caché ──
    def _load(self) -> dict:
        if self.cache_path and os.path.exists(self.cache_path):
            try:
                with open(self.cache_path) as f:
                    return json.load(f)
            except (OSError, ValueError):
                return {}
        return {}

    def _save(self):
        if not self.cache_path:
            return
        tmp = self.cache_path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self._cache, f, indent=1, sort_keys=True)
            os.replace(tmp, self.cache_path)
        except OSError:
            pass

    # ── chequeos ──
    def _check_price(self, t: str, epic: str, cfd: Optional[float], ref: Optional[float]) -> Optional[float]:
        if ref is None:
            return None
        if cfd is None:
            raise TickerResolutionError(f"{t}: el CFD {epic} no tiene bid/offer para verificar contra el spot {ref:.2f}")
        dev = abs(cfd / ref - 1.0)
        if dev > self.tolerance:
            raise TickerResolutionError(
                f"{t}: el CFD {epic} cotiza {cfd:.2f} y el spot por paridad es {ref:.2f} "
                f"(desvío {dev:.2%} > {self.tolerance:.0%}); posible colisión de símbolo, no se usa")
        return dev

    def _market(self, epic: str) -> dict:
        m = self._get(f"/markets/{epic}")
        inst = m.get("instrument", {})
        return {"epic": inst.get("epic", epic), "instrumentType": inst.get("type"),
                "bid": m.get("snapshot", {}).get("bid"), "offer": m.get("snapshot", {}).get("offer"),
                "instrumentName": inst.get("name")}

    # ── resolución ──
    def resolve(self, ticker: str, ref_price: Optional[float] = None, refresh: bool = False) -> Resolution:
        t = normalize(ticker)
        pt = polygon_tickers(t)
        cached = None if refresh else self._cache.get(t)

        if cached:
            m = self._market(cached["capital_epic"])
            expected = INDEX_TYPES if pt.is_index else SHARE_TYPES
            if m["instrumentType"] not in expected:
                raise TickerResolutionError(f"{t}: el epic cacheado {m['epic']} es {m['instrumentType']}, no {expected}")
            cfd = _mid(m)
            dev = self._check_price(t, m["epic"], cfd, ref_price)
            res = Resolution(**{**cached, "cfd_price": cfd, "ref_price": ref_price, "deviation": dev})
            return res

        if pt.is_index:
            epic = INDEX_MAP[t][2]
            m = self._market(epic)
            if m["instrumentType"] not in INDEX_TYPES:
                raise TickerResolutionError(f"{t}: {epic} en Capital.com es {m['instrumentType']}, no INDICES")
            itype, basis = m["instrumentType"], "index_cfd_parity_rolling"
        else:
            term = t
            found = self._get(f"/markets?searchTerm={term}").get("markets", []) or []
            shares = [x for x in found if x.get("instrumentType") in SHARE_TYPES]
            exact = [x for x in shares if (x.get("epic") or "").upper() == t]
            if exact:
                m = exact[0]
            else:
                if ref_price is None:
                    raise TickerResolutionError(
                        f"{t}: no hay un instrumento SHARES con epic {t} en Capital.com y sin spot de "
                        f"referencia no se puede elegir otro con seguridad (candidatos: "
                        f"{[x.get('epic') for x in shares][:6]})")
                close = [x for x in shares if _mid(x) and abs(_mid(x) / ref_price - 1) <= self.tolerance]
                if len(close) != 1:
                    raise TickerResolutionError(
                        f"{t}: {len(close)} candidatos SHARES dentro de ±{self.tolerance:.0%} de {ref_price:.2f} "
                        f"({[x.get('epic') for x in close]}); no hay coincidencia segura")
                m = close[0]
            epic, itype, basis = m["epic"], m["instrumentType"], "share_cfd_parity_rolling"

        cfd = _mid(m)
        if cfd is None:
            mm = self._market(epic)
            cfd = _mid(mm)
        dev = self._check_price(t, epic, cfd, ref_price)
        res = Resolution(t, pt.reference, pt.snapshot, epic, itype, basis, cfd, ref_price, dev, time.time())
        if ref_price is not None:          # solo se cachea lo verificado contra el spot
            self._cache[t] = {k: v for k, v in asdict(res).items() if k not in ("cfd_price", "ref_price", "deviation")}
            self._save()
        return res


# ─────────────────────────────────────────────
#  Spot implícito por paridad (robusto) y base CFD − subyacente
# ─────────────────────────────────────────────

def _last_price(c: dict) -> Optional[float]:
    tr = (c.get("last_trade") or {}).get("price")
    if tr:
        return float(tr)
    q = c.get("last_quote") or {}
    if q.get("bid") and q.get("ask"):
        return (float(q["bid"]) + float(q["ask"])) / 2
    close = (c.get("day") or {}).get("close")
    return float(close) if close else None


_ROOT_RE = re.compile(r"^O:([A-Z]+)\d{6}[CP]")


def parity_spot(contratos: list, n: int = 7) -> Optional[float]:
    """
    S ≈ K + C − P (sin descuento, vencimiento ≤ 1-2 días) en los n strikes donde call y put
    valen lo más parecido (ATM), exigiendo precio y volumen > 0 en ambos; devuelve la mediana.
    Las raíces SPX y SPXW (mismo strike en vencimientos mensuales) no se mezclan: call y put
    se emparejan solo dentro de la misma raíz.
    """
    calls, puts = {}, {}
    for c in contratos or []:
        det = c.get("details") or {}
        k, typ = det.get("strike_price"), det.get("contract_type")
        px, vol = _last_price(c), (c.get("day") or {}).get("volume") or 0
        if k is None or not px or px <= 0:
            continue
        m = _ROOT_RE.match(det.get("ticker") or "")
        key = (m.group(1) if m else "", float(k))
        (calls if typ == "call" else puts if typ == "put" else {})[key] = (px, vol)
    pares = []
    for key in set(calls) & set(puts):
        (pc, vc), (pp, vp) = calls[key], puts[key]
        if vc > 0 and vp > 0:
            pares.append((abs(pc - pp), key[1] + pc - pp))
    if not pares:   # sin volumen (fuera de horario): se aceptan precios sin volumen
        pares = [(abs(calls[k][0] - puts[k][0]), k[1] + calls[k][0] - puts[k][0])
                 for k in set(calls) & set(puts)]
    if not pares:
        return None
    pares.sort()
    est = statistics.median(v for _, v in pares[:n])
    return float(est) if est > 0 else None


class BasisTracker:
    """
    Base = CFD(t_ref) − spot_paridad, donde t_ref es el instante al que corresponden los
    precios de opciones (ahora − 15 min en horario, o el cierre de la sesión fuera de horario).
    La base vigente es la mediana de las últimas `window` muestras; mientras no haya muestras
    se usa `prior` (p. ej. la medida al cierre del día anterior).
    """

    def __init__(self, window: int = 6, prior: Optional[float] = None, max_abs_pct: float = 0.01):
        self.samples: deque = deque(maxlen=window)
        self.prior = prior
        self.max_abs_pct = max_abs_pct

    def add(self, cfd_at_ref: float, parity: float, t_ref: Optional[str] = None) -> Optional[float]:
        if not cfd_at_ref or not parity:
            return None
        b = cfd_at_ref - parity
        if abs(b) > self.max_abs_pct * parity:     # muestra absurda (cadena vieja, CFD equivocado…)
            return None
        self.samples.append((t_ref, b))
        return b

    @property
    def value(self) -> Optional[float]:
        if self.samples:
            return float(statistics.median(b for _, b in self.samples))
        return self.prior

    def underlying_spot(self, cfd_now: float) -> float:
        return cfd_now - (self.value or 0.0)
