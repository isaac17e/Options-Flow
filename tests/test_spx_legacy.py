"""
tests/test_spx_legacy.py
-------------------------
Pruebas sin red del soporte SPX en legacy_0dte/Options_Trade_polygon.py: epic de
Capital.com con verificación de tipo, ticker de referencia vs snapshot, base
SPX - US500 por paridad con respaldo, y umbrales escalados (SPY igual que antes,
SPX sin votar siempre).
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


@pytest.fixture
def capital(monkeypatch):
    for nombre in ("CAPITAL_API_KEY", "CAPITAL_IDENTIFIER", "CAPITAL_API_PASSWORD"):
        monkeypatch.setenv(nombre, "x")
    monkeypatch.setattr(legacy.atexit, "register", lambda fn: None)
    sesion = legacy.CapitalSession(base_url="https://capital.test/api/v1")
    monkeypatch.setattr(legacy, "_capital", sesion)
    monkeypatch.setattr(legacy, "SPX_BASIS_DEFAULT", 1.0)
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

def test_epic_spx_es_us500_y_spy_es_spy(capital):
    assert legacy.epic_capital("spx") == ("US500", "INDICES")
    assert legacy.epic_capital("SPY") == ("SPY", None)
    assert legacy.epic_capital("qqq") == ("QQQ", None)

    http = RoutedCapitalHttp(market=_market("US500", "INDICES", 7782.0, 7783.0))
    capital._http = http
    precio, hora, estado = legacy._precio_via_capital("SPX")
    assert precio == pytest.approx(7782.5)
    assert http.urls[0].endswith("/markets/US500")     # nunca /markets/SPX (Spirax Sarco)


def test_tipo_de_instrumento_incorrecto_se_rechaza(capital):
    capital._http = RoutedCapitalHttp(market=_market("US500", "SHARES", 2300.0, 2301.0))
    with pytest.raises(RuntimeError, match="tipo SHARES, se esperaba INDICES"):
        legacy._precio_via_capital("SPX")


def test_tipo_de_instrumento_ausente_solo_avisa(capital, capsys):
    capital._http = RoutedCapitalHttp(market=_market("US500", None, 7782.0, 7783.0))
    assert legacy._precio_via_capital("SPX")[0] == pytest.approx(7782.5)
    assert "no informó el tipo de instrumento" in capsys.readouterr().out


# ─────────────────────────────────────────────
#  Polygon: ticker de referencia vs snapshot
# ─────────────────────────────────────────────

def test_mapa_de_tickers_polygon():
    assert legacy.tickers_polygon("spx") == ("SPX", "I:SPX")
    assert legacy.tickers_polygon("SPY") == ("SPY", "SPY")
    assert legacy.tickers_polygon("qqq") == ("QQQ", "QQQ")


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
#  Base SPX − US500
# ─────────────────────────────────────────────

def test_forward_paridad_mediana_y_filtros():
    assert legacy.forward_paridad(_cadena_paridad()) == pytest.approx(FORWARD, abs=1e-6)
    assert legacy.forward_paridad(_cadena_paridad(strikes=[7775, 7780])) is None   # < 3 strikes
    assert legacy.forward_paridad(_cadena_paridad(volume=0)) is None               # sin volumen
    assert legacy.forward_paridad([]) is None


def test_forward_paridad_no_mezcla_spx_y_spxw():
    contratos = _cadena_paridad()
    contratos.append({
        "details": {"contract_type": "call", "strike_price": 7775.0, "ticker": "O:SPX261006C07775000"},
        "day": {"volume": 3, "close": 400.0},
    })
    assert legacy.forward_paridad(contratos) == pytest.approx(FORWARD, abs=1e-6)


def test_base_por_paridad_usa_us500_de_hace_15_minutos(capital):
    http = RoutedCapitalHttp(prices=_prices(
        ("2026-10-05T19:57:00", 7770.0, 7770.4),
        ("2026-10-05T19:59:00", 7776.2, 7776.6),       # cierra 20:00 → mid 7776.4
    ))
    capital._http = http

    base, fuente = legacy.calcular_base_spx(_cadena_paridad(), "US500", ahora=NOW)

    assert base == pytest.approx(FORWARD - 7776.4)
    assert fuente.startswith("paridad put-call")
    assert "/prices/US500" in http.urls[0] and "resolution=MINUTE" in http.urls[0]
    assert "from=2026-10-05T19:54:00" in http.urls[0] and "to=2026-10-05T20:06:00" in http.urls[0]


@pytest.mark.parametrize("contratos, prices, motivo", [
    (_cadena_paridad(strikes=[7775]), _prices(("2026-10-05T19:59:00", 7776.2, 7776.6)), "sin strikes ATM"),
    (_cadena_paridad(), {"prices": []}, "sin barra"),
    (_cadena_paridad(), _prices(("2026-10-05T19:30:00", 7776.2, 7776.6)), "sin barra"),
    (_cadena_paridad(), _prices(("2026-10-05T19:59:00", 7000.0, 7000.4)), "base absurda"),
    (_cadena_paridad(), FakeResponse(500), "sin precio histórico"),
])
def test_base_cae_al_parametro(capital, contratos, prices, motivo):
    capital._http = RoutedCapitalHttp(prices=prices)

    base, fuente = legacy.calcular_base_spx(contratos, "US500", ahora=NOW, base_defecto=1.0)

    assert base == 1.0
    assert "respaldo" in fuente and motivo in fuente


def test_obtener_precio_spx_suma_la_base_al_mid_de_us500(capital, monkeypatch):
    capital._http = RoutedCapitalHttp(market=_market("US500", "INDICES", 7782.0, 7783.0))
    monkeypatch.setattr(legacy, "_mid_capital_en", lambda epic, instante: 7776.4)

    S, fuente, _ = legacy.obtener_precio("SPX", _cadena_paridad())

    assert S == pytest.approx(7782.5 + FORWARD - 7776.4)
    assert "US500" in fuente and "paridad put-call" in fuente and "+1.00" in fuente


def test_obtener_precio_spx_sin_paridad_usa_parametro_y_spy_no_lleva_base(capital, monkeypatch):
    capital._http = RoutedCapitalHttp(market=_market("US500", "INDICES", 7782.0, 7783.0))
    monkeypatch.setattr(legacy, "SPX_BASIS_DEFAULT", 2.0)

    S, fuente, _ = legacy.obtener_precio("SPX", [])           # sin cadena
    assert S == pytest.approx(7784.5)
    assert "respaldo" in fuente

    capital._http = RoutedCapitalHttp(market=_market("SPY", None, 669.0, 671.0))
    S, fuente, _ = legacy.obtener_precio("SPY", [])
    assert S == 670.0
    assert fuente == "Capital.com (mid bid/offer)"


def test_cli_ticker_por_defecto_y_opciones():
    args = legacy._parse_args([])
    assert args.ticker is None and legacy.TICKER_POR_DEFECTO == "SPY"
    args = legacy._parse_args(["--ticker", "spx", "--refresh", "30", "--spx-basis", "1.5"])
    assert (args.ticker, args.refresh, args.spx_basis) == ("SPX", 30, 1.5)


# ─────────────────────────────────────────────
#  Umbrales escalados
# ─────────────────────────────────────────────

def _par(strike, call, put):
    return (pd.DataFrame({"strike": [strike], "lastPrice": [call]}),
            pd.DataFrame({"strike": [strike], "lastPrice": [put]}))


def _consenso(S, max_pain, umbral=None):
    vacio = pd.DataFrame(columns=["tipo_flujo"])
    direccion, _, bullets = legacy.consenso_direccional_0dte(
        max_pain, S, "LONG GAMMA DÉBIL", pd.DataFrame(), vacio, vacio, None, None, None,
        umbral_max_pain=umbral)
    return direccion, bullets


def test_umbral_max_pain_spy_sigue_siendo_unos_2_dolares():
    calls, puts = _par(670.0, 2.1, 2.1)                      # straddle ≈ 4,2 (SPY a 1DTE)
    assert legacy.calcular_umbral_max_pain(670.0, calls, puts) == pytest.approx(2.1)
    # sin straddle utilizable: 0,3% del spot = $2,00 en SPY@670, igual que el valor fijo anterior
    assert legacy.calcular_umbral_max_pain(670.0) == pytest.approx(2.0)
    assert legacy.calcular_umbral_max_pain(670.0, *_par(670.0, 0.0, 0.0)) == pytest.approx(2.0)


def test_umbral_max_pain_spx_escala_con_el_straddle():
    calls, puts = _par(7775.0, 15.0, 15.5)                   # straddle 30,5 medido el 2026-10-05
    assert legacy.calcular_umbral_max_pain(7777.4, calls, puts) == pytest.approx(15.25)
    # acotado a [0,1%, 0,6%] del spot si el último precio es absurdo
    assert legacy.calcular_umbral_max_pain(7777.4, *_par(7775.0, 0.05, 0.05)) == pytest.approx(7.7774)
    assert legacy.calcular_umbral_max_pain(7777.4, *_par(7775.0, 400.0, 400.0)) == pytest.approx(46.6644)


def test_consenso_spy_igual_que_con_el_umbral_fijo_de_2():
    # Sin umbral explícito: 0,3% del spot ≈ $2 en SPY.
    assert _consenso(670.0, 669.0)[0] == "NEUTRAL"           # dist +1: neutro (antes también)
    assert _consenso(670.0, 667.0)[0] == "BAJISTA"           # dist +3 > 2 (antes también)
    assert _consenso(670.0, 673.0)[0] == "ALCISTA"           # dist −3 < −2 (antes también)


def test_consenso_spx_ya_no_vota_siempre_el_max_pain():
    # Con el umbral fijo de $2 una distancia de 7 puntos en SPX (0,09%) votaba bajista.
    direccion, bullets = _consenso(7777.0, 7770.0, umbral=15.25)
    assert direccion == "NEUTRAL"
    assert "pin neutro" in bullets[0]
    # Una distancia grande de verdad (72 puntos) sí vota.
    assert _consenso(7777.0, 7705.0, umbral=15.25)[0] == "BAJISTA"
    # Sin umbral explícito el respaldo escala con el spot (0,3% = 23 puntos).
    assert _consenso(7777.0, 7770.0)[0] == "NEUTRAL"
    assert _consenso(7777.0, 7700.0)[0] == "BAJISTA"


def _gex(valor):
    return (pd.DataFrame({"gex": [valor]}), pd.DataFrame({"gex": [0.0]}))


def test_umbral_gex_fuerte_spy_igual_y_spx_escalado():
    assert legacy.umbral_gex_fuerte() == pytest.approx(5e8)             # sin spot: valor original
    assert legacy.umbral_gex_fuerte(670.0) == pytest.approx(5e8)        # SPY@670: igual que antes
    assert legacy.umbral_gex_fuerte(7770.0) == pytest.approx(5e8 * 670 / 7770)
    # Equivale al mismo umbral en dólares por 1% de movimiento
    assert legacy.umbral_gex_fuerte(7770.0) * 7770.0 * 0.01 == pytest.approx(5e8 * 670 * 0.01)


def test_posicionamiento_dealers_spy_sin_cambios_y_spx_escalado():
    calls, puts = _gex(6e8)
    assert legacy.posicionamiento_dealers_real(calls, puts)[0] == "LONG GAMMA FUERTE"
    assert legacy.posicionamiento_dealers_real(calls, puts, S=670.0)[0] == "LONG GAMMA FUERTE"
    calls, puts = _gex(4e8)
    assert legacy.posicionamiento_dealers_real(calls, puts, S=670.0)[0] == "LONG GAMMA DÉBIL"
    calls, puts = _gex(-4e8)
    assert legacy.posicionamiento_dealers_real(calls, puts, S=670.0)[0] == "SHORT GAMMA DÉBIL"
    calls, puts = _gex(-6e8)
    assert legacy.posicionamiento_dealers_real(calls, puts, S=670.0)[0] == "SHORT GAMMA FUERTE"
    # SPX: 6e7 (en unidades del script) ya es fuerte (umbral ≈ 4,3e7); 3e7 todavía no.
    assert legacy.posicionamiento_dealers_real(*_gex(6e7), S=7770.0)[0] == "LONG GAMMA FUERTE"
    assert legacy.posicionamiento_dealers_real(*_gex(3e7), S=7770.0)[0] == "LONG GAMMA DÉBIL"


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
    # SPY: 0,5 > 0,10 → hay candidatos (igual que antes); también sin spot.
    assert not legacy.detectar_smart_money(df, df, S=670.0)[0].empty
    assert not legacy.detectar_smart_money(df, df)[0].empty
    # SPX@7770: el mínimo pasa a ≈ $1,16, así que un precio de $0,5 ya no cuenta.
    assert legacy.detectar_smart_money(df, df, S=7770.0)[0].empty
    assert not legacy.detectar_smart_money(_filas_smart(3.0), _filas_smart(3.0), S=7770.0)[0].empty
