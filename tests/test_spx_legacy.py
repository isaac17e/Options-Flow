"""
tests/test_spx_legacy.py
-------------------------
Pruebas sin red del soporte SPX en legacy_0dte/Options_Trade_polygon.py sobre el
resolver de tickers.py: epic US500 con verificación de tipo, ticker de referencia
vs snapshot, base CFD - paridad con el US500 de hace 15 min (y respaldo
SPX_BASIS), y umbrales adimensionales (SPY y SPX se comportan igual).
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

_PATH = Path(__file__).resolve().parents[1] / "legacy_0dte" / "Options_Trade_polygon.py"
_spec = importlib.util.spec_from_file_location("legacy_0dte_spx", _PATH)
legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)

FORWARD = 7777.4
NOW = datetime(2026, 10, 5, 20, 15, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RoutedCapitalHttp:
    """Sesión HTTP falsa de Capital.com: responde según el path pedido."""

    def __init__(self, market=None, prices=None):
        self.market, self.prices, self.urls = market, prices, []

    def post(self, url, headers=None, json=None, timeout=None):
        return FakeResponse(200, {}, {"CST": "c", "X-SECURITY-TOKEN": "t"})

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        if "/markets/" in url:
            return FakeResponse(200, self.market)
        if "/prices/" in url:
            if self.prices is None:
                return FakeResponse(200, {"prices": []})
            return self.prices if isinstance(self.prices, FakeResponse) else FakeResponse(200, self.prices)
        raise AssertionError(f"llamada inesperada: {url}")

    def delete(self, url, headers=None, timeout=None):
        return FakeResponse(200)


def _market(epic, type_, bid, offer):
    instrument = {"epic": epic}
    if type_:
        instrument["type"] = type_
    return {"instrument": instrument,
            "snapshot": {"bid": bid, "offer": offer, "marketStatus": "TRADEABLE",
                         "updateTime": "2026-10-05T20:15:00.000"}}


def _prices(*bars):
    return {"prices": [{"snapshotTimeUTC": s, "closePrice": {"bid": b, "ask": a}} for s, b, a in bars]}


BARRAS_OK = _prices(
    ("2026-10-05T19:57:00", 7770.0, 7770.4),
    ("2026-10-05T19:59:00", 7776.2, 7776.6),       # cierra 20:00 → mid 7776.4
)


@pytest.fixture
def capital(monkeypatch):
    for nombre in ("CAPITAL_API_KEY", "CAPITAL_IDENTIFIER", "CAPITAL_API_PASSWORD"):
        monkeypatch.setenv(nombre, "x")
    monkeypatch.setattr(legacy.atexit, "register", lambda fn: None)
    sesion = legacy.CapitalSession(base_url="https://capital.test/api/v1")
    monkeypatch.setattr(legacy, "_capital", sesion)
    monkeypatch.setattr(legacy, "SPX_BASIS_DEFAULT", 1.0)
    monkeypatch.setattr(legacy, "_bases", {})
    # Resolver real (tickers.py), sin caché en disco y sobre la sesión falsa.
    monkeypatch.setattr(legacy, "_resolver", legacy._tk.TickerResolver(sesion.get, cache_path=None))
    return sesion


def _cadena_paridad(forward=FORWARD, strikes=range(7750, 7805, 5), volume=5):
    contratos = []
    for k in strikes:
        diff = forward - k
        for tipo, precio in (("call", 40 + diff / 2), ("put", 40 - diff / 2)):
            contratos.append({
                "details": {"contract_type": tipo, "strike_price": float(k), "expiration_date": "2026-10-06",
                            "ticker": f"O:SPXW261006{tipo[0].upper()}{k * 1000:08d}"},
                "day": {"volume": volume, "close": precio},
                "open_interest": 10, "implied_volatility": 0.1,
            })
    return contratos


# ─────────────────────────────────────────────
#  Epic de Capital.com + tipo de instrumento
# ─────────────────────────────────────────────

def test_spx_usa_us500_nunca_el_epic_spx(capital):
    http = RoutedCapitalHttp(market=_market("US500", "INDICES", 7782.0, 7783.0))
    capital._http = http
    precio, hora, estado, res, base = legacy._precio_via_capital("SPX")   # sin cadena: sin base medida
    assert res.capital_epic == "US500" and res.polygon_snapshot_ticker == "I:SPX"
    assert base == pytest.approx(-1.0)          # SPX_BASIS=+1 (SPX − US500) → CFD − SPX = −1
    assert precio == pytest.approx(7782.5 + 1.0)
    assert all("/markets/SPX" not in u for u in http.urls)                # SPX en Capital.com = Spirax Sarco


def test_tipo_de_instrumento_incorrecto_se_rechaza(capital):
    capital._http = RoutedCapitalHttp(market=_market("US500", "SHARES", 2300.0, 2301.0))
    with pytest.raises(legacy._tk.TickerResolutionError, match="INDICES"):
        legacy._precio_via_capital("SPX")


def test_precio_cfd_lejos_de_la_paridad_se_rechaza(capital):
    capital._http = RoutedCapitalHttp(market=_market("US500", "INDICES", 6000.0, 6001.0))
    with pytest.raises(legacy._tk.TickerResolutionError, match="colisión"):
        legacy._precio_via_capital("SPX", _cadena_paridad())


def test_obtener_precio_sin_cfd_seguro_cae_a_la_paridad(capital, capsys):
    capital._http = RoutedCapitalHttp(market=_market("US500", "SHARES", 2300.0, 2301.0))
    S, fuente, _ = legacy.obtener_precio("SPX", _cadena_paridad())
    assert "SIN CFD SEGURO" in capsys.readouterr().out
    assert S == pytest.approx(FORWARD, abs=0.01)


# ─────────────────────────────────────────────
#  Polygon: ticker de referencia vs snapshot
# ─────────────────────────────────────────────

def test_spx_referencia_usa_spx_y_snapshot_usa_i_spx(monkeypatch):
    llamadas = []

    def fake_get(url, params=None, timeout=None):
        llamadas.append((url, params))
        if "reference" in url:
            return FakeResponse(200, {"status": "OK", "results": [{"expiration_date": "2099-01-01"}]})
        return FakeResponse(200, {"status": "OK", "results": [{"x": 1}]})

    monkeypatch.setattr(legacy.requests, "get", fake_get)
    legacy.seleccionar_vencimiento_0dte("spx", silencioso=True)
    legacy.descargar_cadena_0dte("spx", "2026-10-06")

    (url_ref, params_ref), (url_snap, params_snap) = llamadas
    assert params_ref["underlying_ticker"] == "SPX"
    assert url_snap.endswith("/v3/snapshot/options/I:SPX")
    assert params_snap["expiration_date"] == "2026-10-06"


def test_spy_referencia_y_snapshot_son_spy(monkeypatch):
    llamadas = []

    def fake_get(url, params=None, timeout=None):
        llamadas.append((url, params))
        return FakeResponse(200, {"status": "OK", "results": [{"expiration_date": "2099-01-01"}]})

    monkeypatch.setattr(legacy.requests, "get", fake_get)
    legacy.seleccionar_vencimiento_0dte("SPY", silencioso=True)
    legacy.descargar_cadena_0dte("SPY", "2026-10-06")

    assert llamadas[0][1]["underlying_ticker"] == "SPY"
    assert llamadas[1][0].endswith("/v3/snapshot/options/SPY")


# ─────────────────────────────────────────────
#  Paridad y base CFD − índice
# ─────────────────────────────────────────────

def test_parity_spot_de_la_cadena_spx():
    assert legacy._tk.parity_spot(_cadena_paridad()) == pytest.approx(FORWARD, abs=1e-6)
    assert legacy._tk.parity_spot([]) is None


def test_parity_spot_no_mezcla_spx_y_spxw():
    contratos = _cadena_paridad()
    contratos.append({
        "details": {"contract_type": "call", "strike_price": 7775.0, "ticker": "O:SPX261006C07775000"},
        "day": {"volume": 3, "close": 400.0},
    })
    assert legacy._tk.parity_spot(contratos) == pytest.approx(FORWARD, abs=1e-6)


def test_base_usa_el_cfd_de_hace_15_minutos(capital):
    http = RoutedCapitalHttp(market=_market("US500", "INDICES", 7782.0, 7783.0), prices=BARRAS_OK)
    capital._http = http

    precio, _, _, res, base = legacy._precio_via_capital("SPX", _cadena_paridad(), ahora=NOW)

    assert base == pytest.approx(7776.4 - FORWARD)                  # CFD(t−15) − paridad = −1.0
    assert precio == pytest.approx(7782.5 - base)
    prices_url = next(u for u in http.urls if "/prices/" in u)
    assert "/prices/US500" in prices_url and "resolution=MINUTE" in prices_url
    assert "from=2026-10-05T19:54:00" in prices_url and "to=2026-10-05T20:06:00" in prices_url


def test_base_es_la_mediana_de_las_muestras_de_los_ciclos(capital):
    mercado = _market("US500", "INDICES", 7782.0, 7783.0)
    capital._http = RoutedCapitalHttp(market=mercado, prices=BARRAS_OK)
    legacy._precio_via_capital("SPX", _cadena_paridad(), ahora=NOW)                        # −1.0
    capital._http = RoutedCapitalHttp(market=mercado, prices=_prices(("2026-10-05T19:59:00", 7775.2, 7775.6)))
    legacy._precio_via_capital("SPX", _cadena_paridad(), ahora=NOW)                        # −2.0
    capital._http = RoutedCapitalHttp(market=mercado, prices=BARRAS_OK)
    _, _, _, _, base = legacy._precio_via_capital("SPX", _cadena_paridad(), ahora=NOW)     # −1.0
    assert base == pytest.approx(7776.4 - FORWARD)                  # mediana de (−1.0, −2.0, −1.0)


@pytest.mark.parametrize("prices", [
    {"prices": []},                                                       # sin barras
    _prices(("2026-10-05T19:30:00", 7776.2, 7776.6)),                     # barra demasiado lejos
    _prices(("2026-10-05T19:59:00", 7000.0, 7000.4)),                     # base absurda (> 1 %)
    FakeResponse(500),                                                    # error de red
])
def test_base_sin_muestra_usa_spx_basis(capital, prices):
    capital._http = RoutedCapitalHttp(market=_market("US500", "INDICES", 7782.0, 7783.0), prices=prices)
    precio, _, _, _, base = legacy._precio_via_capital("SPX", _cadena_paridad(), ahora=NOW)
    assert base == pytest.approx(-1.0)                                    # SPX_BASIS=+1.0 → CFD − SPX = −1
    assert precio == pytest.approx(7783.5)


def test_obtener_precio_spx_muestra_epic_y_base(capital):
    capital._http = RoutedCapitalHttp(market=_market("US500", "INDICES", 7782.0, 7783.0))
    S, fuente, _ = legacy.obtener_precio("SPX", _cadena_paridad())        # sin barra (reloj real) → SPX_BASIS
    assert S == pytest.approx(7783.5)
    assert "US500" in fuente and "base -1.00" in fuente


def test_spy_no_lleva_base(capital, monkeypatch):
    class ResolverFijo:
        def resolve(self, ticker, ref_price=None, refresh=False):
            return legacy._tk.Resolution(ticker.upper(), ticker.upper(), ticker.upper(), ticker.upper(),
                                         "SHARES", "share_cfd_parity_rolling")

    monkeypatch.setattr(legacy, "_resolver", ResolverFijo())
    capital._http = RoutedCapitalHttp(market=_market("SPY", None, 669.0, 671.0))
    S, fuente, _ = legacy.obtener_precio("SPY", [])
    assert S == 670.0
    assert fuente == "Capital.com SPY (mid bid/offer)"


def test_cli_ticker_por_defecto_y_opciones():
    args = legacy._parse_args([])
    assert args.ticker is None and legacy.TICKER_POR_DEFECTO == "SPY"
    args = legacy._parse_args(["--ticker", "spx", "--refresh", "30", "--spx-basis", "1.5"])
    assert (args.ticker, args.refresh, args.spx_basis) == ("SPX", 30, 1.5)


# ─────────────────────────────────────────────
#  Umbrales adimensionales
# ─────────────────────────────────────────────

def _consenso(S, max_pain):
    vacio = pd.DataFrame(columns=["tipo_flujo"])
    direccion, _, bullets = legacy.consenso_direccional_0dte(
        max_pain, S, "LONG GAMMA DÉBIL", pd.DataFrame(), vacio, vacio, None, None, None)
    return direccion, bullets


def test_consenso_spy_se_comporta_como_con_los_2_dolares_fijos():
    # 0,3 % de 670 ≈ $2,0
    assert _consenso(670.0, 669.0)[0] == "NEUTRAL"           # dist +1: neutro
    assert _consenso(670.0, 667.0)[0] == "BAJISTA"           # dist +3 > 2
    assert _consenso(670.0, 673.0)[0] == "ALCISTA"           # dist −3 < −2


def test_consenso_spx_ya_no_vota_siempre_el_max_pain():
    # Con $2 fijos, 7 puntos en SPX (0,09 %) votaban bajista; el umbral ahora es 0,3 % (≈ 23 puntos).
    direccion, bullets = _consenso(7777.0, 7770.0)
    assert direccion == "NEUTRAL" and "pin neutro" in bullets[0]
    assert _consenso(7777.0, 7700.0)[0] == "BAJISTA"
    assert _consenso(7777.0, 7850.0)[0] == "ALCISTA"


def _gex(call, put):
    return pd.DataFrame({"gex": [call]}), pd.DataFrame({"gex": [put]})


def test_regimen_de_dealers_es_invariante_a_la_escala():
    for escala in (1.0, 1e-2, 1e3):      # el gex del script cambia de magnitud con el subyacente
        assert legacy.posicionamiento_dealers_real(*_gex(8e8 * escala, -2e8 * escala))[0] == "LONG GAMMA FUERTE"
        assert legacy.posicionamiento_dealers_real(*_gex(6e8 * escala, -4e8 * escala))[0] == "LONG GAMMA DÉBIL"
        assert legacy.posicionamiento_dealers_real(*_gex(4e8 * escala, -6e8 * escala))[0] == "SHORT GAMMA DÉBIL"
        assert legacy.posicionamiento_dealers_real(*_gex(2e8 * escala, -8e8 * escala))[0] == "SHORT GAMMA FUERTE"


def _filas_smart(precio):
    n = 10
    return pd.DataFrame({
        "strike": [100.0 + i for i in range(n)],
        "volume": [10 * (i + 1) for i in range(n)],
        "openInterest": [100] * n,
        "impliedVolatility": [0.1 + 0.01 * i for i in range(n)],
        "lastPrice": [precio] * n,
        "tipo_flujo": ["APERTURA NUEVA"] * n,
        "señal_direccional": ["ALCISTA 🟢"] * n,
    })


def test_smart_money_precio_minimo_escala_con_el_spot():
    df = _filas_smart(0.5)
    # SPY@670: mínimo 0,015 % ≈ $0,10 → 0,5 cuenta; sin spot se conserva $0,10.
    assert not legacy.detectar_smart_money(df, df, S=670.0)[0].empty
    assert not legacy.detectar_smart_money(df, df)[0].empty
    # SPX@7770: el mínimo pasa a ≈ $1,17, así que un precio de $0,5 ya no cuenta.
    assert legacy.detectar_smart_money(df, df, S=7770.0)[0].empty
    assert not legacy.detectar_smart_money(_filas_smart(3.0), _filas_smart(3.0), S=7770.0)[0].empty
