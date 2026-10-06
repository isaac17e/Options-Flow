"""
tests/test_spx_data.py
-----------------------
Pruebas sin red del soporte SPX en el dashboard (src/): mapa ticker -> epic de
Capital.com con verificación del tipo de instrumento, separación del ticker de
referencia y de snapshot en Polygon, y la base SPX - US500 por paridad put-call
con su respaldo.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests

from config import Settings
from src.data import capital_client, spot_basis
from src.data.capital_client import CapitalClient, CapitalClientError
from src.data.models import OptionContract, UnderlyingSnapshot
from src.data.polygon_client import PolygonClient, polygon_tickers

SETTINGS = Settings(
    polygon_api_key="test-key",
    capital_api_key="cap-key",
    capital_identifier="user@example.com",
    capital_api_password="secret",
    spx_basis=1.0,
    options_delay_minutes=15,
)


def _resp(status=200, payload=None, headers=None):
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(payload if payload is not None else {}).encode()
    resp.headers.update(headers or {})
    resp.url = "https://example.test"
    return resp


def _login_ok():
    return _resp(200, {}, {"CST": "cst-1", "X-SECURITY-TOKEN": "xst-1"})


def _market(epic, type_, bid, offer):
    instrument = {"epic": epic}
    if type_:
        instrument["type"] = type_
    return _resp(200, {
        "instrument": instrument,
        "snapshot": {"bid": bid, "offer": offer, "marketStatus": "TRADEABLE",
                     "updateTime": "2026-10-05T20:15:00.000"},
    })


def _capital(get):
    client = CapitalClient(SETTINGS)
    client._session.post = Mock(return_value=_login_ok())
    client._session.get = get
    return client


# ----------------------------------------------------------------------
# Epic de Capital.com + tipo de instrumento
# ----------------------------------------------------------------------
def test_spx_uses_us500_epic_not_the_spx_stock():
    client = _capital(Mock(return_value=_market("US500", "INDICES", 7782.0, 7783.0)))

    snap = client.get_underlying_snapshot("SPX")

    assert client._session.get.call_args.args[0].endswith("/markets/US500")
    assert snap.raw_price == pytest.approx(7782.5)
    assert snap.basis == pytest.approx(1.0)              # SETTINGS.spx_basis
    assert snap.spot_price == pytest.approx(7783.5)      # mid + base
    assert "US500" in snap.source
    assert snap.ticker == "SPX"


def test_default_basis_parameter_overrides_settings():
    client = _capital(Mock(return_value=_market("US500", "INDICES", 7782.0, 7783.0)))
    snap = client.get_underlying_snapshot("SPX", default_basis=-2.5)
    assert snap.spot_price == pytest.approx(7780.0)
    assert snap.basis == -2.5


def test_spy_epic_unchanged_and_has_no_basis():
    client = _capital(Mock(return_value=_market("SPY", None, 774.0, 774.5)))

    snap = client.get_underlying_snapshot("spy")

    assert client._session.get.call_args.args[0].endswith("/markets/SPY")
    assert snap.spot_price == pytest.approx(774.25)
    assert snap.basis == 0.0 and snap.raw_price == pytest.approx(774.25)


def test_spy_epic_respects_capital_epic_override():
    custom = Settings(polygon_api_key="k", capital_api_key="a", capital_identifier="b",
                      capital_api_password="c", capital_epic="SPY.X")
    client = CapitalClient(custom)
    assert client.resolve_instrument("SPY") == ("SPY.X", None)


def test_unlisted_ticker_uses_its_own_symbol():
    assert CapitalClient(SETTINGS).resolve_instrument("qqq") == ("QQQ", None)


def test_wrong_instrument_type_is_rejected():
    # El mismo epic devolviendo una acción: nunca debe usarse como spot del índice.
    client = _capital(Mock(return_value=_market("US500", "SHARES", 2300.0, 2301.0)))
    with pytest.raises(CapitalClientError, match="tipo SHARES, se esperaba INDICES"):
        client.get_underlying_snapshot("SPX")


def test_missing_instrument_type_only_warns(caplog):
    client = _capital(Mock(return_value=_market("US500", None, 7782.0, 7783.0)))
    with caplog.at_level(logging.WARNING, logger=capital_client.__name__):
        snap = client.get_underlying_snapshot("SPX")
    assert snap.raw_price == pytest.approx(7782.5)
    assert "no informó el tipo de instrumento" in caplog.text


# ----------------------------------------------------------------------
# Polygon: ticker de referencia vs ticker de snapshot
# ----------------------------------------------------------------------
def test_polygon_ticker_mapping():
    assert polygon_tickers("SPX") == ("SPX", "I:SPX")
    assert polygon_tickers("spy") == ("SPY", "SPY")
    assert polygon_tickers("QQQ") == ("QQQ", "QQQ")


def _ref_page(dates):
    return _resp(200, {"results": [{"expiration_date": d} for d in dates]})


def _snap_item(root, kind, strike, price=10.0, volume=5):
    exp = date(2026, 10, 6)
    return {
        "details": {"contract_type": kind, "strike_price": strike, "expiration_date": exp.isoformat(),
                    "ticker": f"O:{root}261006{kind[0].upper()}{int(strike * 1000):08d}"},
        "open_interest": 10, "implied_volatility": 0.1, "greeks": {"gamma": 0.001},
        "day": {"volume": volume, "close": price},
    }


def test_spx_reference_uses_spx_and_snapshot_uses_i_spx():
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(side_effect=[
        _ref_page(["2026-10-06", "2026-10-07"]),
        _resp(200, {"results": [_snap_item("SPXW", "call", 7775.0)]}),
    ])

    expirations = client.get_available_expirations("SPX", 2)
    chain = client.get_option_chain_snapshot(
        "SPX", expiration_date=date(2026, 10, 6),
        underlying=UnderlyingSnapshot("SPX", 7777.0, None),
    )

    ref_call, snap_call = client._session.get.call_args_list
    assert ref_call.args[0].endswith("/v3/reference/options/contracts")
    assert ref_call.kwargs["params"]["underlying_ticker"] == "SPX"
    assert snap_call.args[0].endswith("/v3/snapshot/options/I:SPX")
    assert expirations == [date(2026, 10, 6), date(2026, 10, 7)]
    assert chain.contracts[0].underlying_ticker == "SPX"     # el nombre lógico, no I:SPX
    assert chain.contracts[0].day_close == 10.0


def test_spy_reference_and_snapshot_both_use_spy():
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(side_effect=[
        _ref_page(["2026-10-06"]),
        _resp(200, {"results": [_snap_item("SPY", "call", 775.0)]}),
    ])

    client.get_available_expirations("SPY", 1)
    client.get_option_chain_snapshot("SPY", expiration_date=date(2026, 10, 6),
                                     underlying=UnderlyingSnapshot("SPY", 775.0, None))

    ref_call, snap_call = client._session.get.call_args_list
    assert ref_call.kwargs["params"]["underlying_ticker"] == "SPY"
    assert snap_call.args[0].endswith("/v3/snapshot/options/SPY")


# ----------------------------------------------------------------------
# Base por paridad put-call
# ----------------------------------------------------------------------
FORWARD = 7777.4


def _contract(kind, strike, price, root="SPXW", volume=5, expiration=date(2026, 10, 6)):
    return OptionContract(
        ticker=f"O:{root}261006{kind[0].upper()}{int(strike * 1000):08d}",
        underlying_ticker="SPX", contract_type=kind, strike=strike, expiration=expiration,
        last_price=price, volume=volume,
    )


def _parity_chain(forward=FORWARD, strikes=range(7750, 7805, 5)):
    contracts = []
    for k in strikes:
        diff = forward - k            # C - P = F - K
        contracts += [_contract("call", float(k), 40 + diff / 2), _contract("put", float(k), 40 - diff / 2)]
    return contracts


def test_parity_forward_is_median_of_atm_strikes():
    contracts = _parity_chain()
    # Un strike ATM con precio viejo y absurdo no mueve la mediana.
    contracts[8] = _contract("call", contracts[8].strike, contracts[8].last_price + 50)
    assert spot_basis.parity_forward(contracts) == pytest.approx(FORWARD, abs=1e-6)


def test_parity_forward_needs_min_strikes_prices_and_volume():
    assert spot_basis.parity_forward(_parity_chain(strikes=[7775, 7780])) is None
    assert spot_basis.parity_forward([]) is None
    no_volume = [_contract(c.contract_type, c.strike, c.last_price, volume=0) for c in _parity_chain()]
    assert spot_basis.parity_forward(no_volume) is None
    no_price = [_contract(c.contract_type, c.strike, None) for c in _parity_chain()]
    assert spot_basis.parity_forward(no_price) is None


def test_parity_forward_does_not_mix_spx_and_spxw_at_same_strike():
    contracts = _parity_chain()
    # SPX (AM) con otro precio en los mismos strikes: sin pareja call/put completa propia.
    contracts += [_contract("call", 7775.0, 400.0, root="SPX")]
    assert spot_basis.parity_forward(contracts) == pytest.approx(FORWARD, abs=1e-6)


def test_compute_basis_and_sanity_limit():
    assert spot_basis.compute_basis(7777.4, 7776.4) == pytest.approx(1.0)
    assert spot_basis.compute_basis(None, 7776.4) is None
    assert spot_basis.compute_basis(7777.4, None) is None
    assert spot_basis.compute_basis(7777.4, 7000.0) is None     # > 0,5% del precio


NOW = datetime(2026, 10, 5, 20, 15, tzinfo=timezone.utc)


def _prices_payload(*bars):
    return _resp(200, {"prices": [
        {"snapshotTimeUTC": start, "closePrice": {"bid": bid, "ask": ask}} for start, bid, ask in bars
    ]})


def _router(prices_response):
    def get(url, headers=None, timeout=None):
        if "/prices/US500" in url:
            get.price_urls.append(url)
            return prices_response
        raise AssertionError(f"llamada inesperada: {url}")
    get.price_urls = []
    return get


def _spx_snapshot(client):
    client._session.get = Mock(return_value=_market("US500", "INDICES", 7782.0, 7783.0))
    return client.get_underlying_snapshot("SPX")


def test_basis_from_parity_uses_us500_from_delay_minutes_ago():
    client = CapitalClient(SETTINGS)
    client._session.post = Mock(return_value=_login_ok())
    snap = _spx_snapshot(client)                         # US500 mid ahora = 7782.5, base de respaldo +1
    router = _router(_prices_payload(
        ("2026-10-05T19:57:00", 7770.0, 7770.4),         # lejos del instante buscado (20:00)
        ("2026-10-05T19:59:00", 7776.2, 7776.6),         # cierra a las 20:00 → mid 7776.4
    ))
    client._session.get = router

    out = client.apply_parity_basis(snap, _parity_chain(), now=NOW)

    assert out.basis == pytest.approx(FORWARD - 7776.4)  # ≈ +1.0
    assert out.spot_price == pytest.approx(7782.5 + FORWARD - 7776.4)
    assert out.raw_price == pytest.approx(7782.5)
    assert out.basis_source.startswith("paridad put-call")
    # t = 20:15, retraso 15 min → ventana alrededor de 20:00 (±6 min)
    assert "from=2026-10-05T19:54:00" in router.price_urls[0]
    assert "to=2026-10-05T20:06:00" in router.price_urls[0]
    assert "resolution=MINUTE" in router.price_urls[0]
    assert snap.basis == 1.0                              # el snapshot original no se muta


@pytest.mark.parametrize("contracts, prices, reason", [
    (_parity_chain(strikes=[7775]), _prices_payload(("2026-10-05T19:59:00", 7776.2, 7776.6)), "sin strikes ATM"),
    (_parity_chain(), _resp(200, {"prices": []}), "sin barra"),
    (_parity_chain(), _prices_payload(("2026-10-05T19:30:00", 7776.2, 7776.6)), "sin barra"),   # barra vieja
    (_parity_chain(), _prices_payload(("2026-10-05T19:59:00", 7000.0, 7000.4)), "base absurda"),
    (_parity_chain(), _resp(500, {}), "sin precio histórico"),
])
def test_basis_falls_back_to_parameter(contracts, prices, reason):
    client = CapitalClient(SETTINGS)
    client._session.post = Mock(return_value=_login_ok())
    snap = _spx_snapshot(client)
    client._session.get = _router(prices)

    out = client.apply_parity_basis(snap, contracts, now=NOW)

    assert out.basis == 1.0
    assert out.spot_price == pytest.approx(7783.5)       # mid + base de respaldo
    assert out.basis_source.startswith("parámetro (respaldo)")
    assert reason in out.basis_source


def test_apply_parity_basis_ignores_non_basis_tickers():
    client = CapitalClient(SETTINGS)
    spy = UnderlyingSnapshot("SPY", 774.0, None, raw_price=774.0)
    assert client.apply_parity_basis(spy, []) is spy


def test_polygon_client_delegates_basis_to_capital_client():
    spot_client = Mock()
    client = PolygonClient(SETTINGS, spot_client=spot_client)
    snap = UnderlyingSnapshot("SPX", 7783.5, None, raw_price=7782.5, basis=1.0)

    client.refine_underlying_basis(snap, ["c"])
    client.get_underlying_snapshot("SPX", default_basis=0.5)

    spot_client.apply_parity_basis.assert_called_once_with(snap, ["c"])
    spot_client.get_underlying_snapshot.assert_called_once_with("SPX", default_basis=0.5)
