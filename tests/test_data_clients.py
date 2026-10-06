"""
tests/test_data_clients.py
---------------------------
Pruebas sin red de los clientes de datos: requests se reemplaza por mocks.
Cubren el cliente de Capital.com (spot), la condición de corte de la
paginación de vencimientos, el tope de contratos del snapshot y los
reintentos ante 429 / 5xx de Polygon.
"""

import json
import logging
from datetime import date, timedelta
from unittest.mock import Mock

import pytest
import requests

from config import Settings
from src.data import polygon_client
from src.data.capital_client import CapitalClient, CapitalClientError
from src.data.models import UnderlyingSnapshot
from src.data.polygon_client import PolygonClient, PolygonClientError

SETTINGS = Settings(
    polygon_api_key="test-key",
    capital_api_key="cap-key",
    capital_identifier="user@example.com",
    capital_api_password="secret",
)
CAPITAL_URL = SETTINGS.capital_api_url


def _resp(status=200, payload=None, headers=None):
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(payload if payload is not None else {}).encode()
    resp.headers.update(headers or {})
    resp.url = "https://example.test"
    return resp


def _login_ok():
    return _resp(200, {"accountType": "CFD"}, {"CST": "cst-1", "X-SECURITY-TOKEN": "xst-1"})


def _market(bid=774.0, offer=774.5):
    return _resp(200, {
        "instrument": {"epic": "SPY"},
        "snapshot": {"bid": bid, "offer": offer, "marketStatus": "TRADEABLE",
                     "updateTime": "2026-10-05T15:30:00.000"},
    })


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(polygon_client.time, "sleep", sleeps.append)
    return sleeps


# ----------------------------------------------------------------------
# Capital.com
# ----------------------------------------------------------------------
def test_capital_missing_credentials_raises_clear_error():
    with pytest.raises(CapitalClientError, match="CAPITAL_IDENTIFIER, CAPITAL_API_PASSWORD"):
        CapitalClient(Settings(polygon_api_key="k", capital_api_key="cap-key"))


def test_capital_spot_is_mid_of_bid_offer():
    client = CapitalClient(SETTINGS)
    client._session.post = Mock(return_value=_login_ok())
    client._session.get = Mock(return_value=_market(774.0, 774.5))

    snap = client.get_underlying_snapshot("SPY")

    assert snap.spot_price == pytest.approx(774.25)
    assert snap.snapshot_time.isoformat() == "2026-10-05T15:30:00"

    post_args, post_kwargs = client._session.post.call_args
    assert post_args[0] == f"{CAPITAL_URL}/session"
    assert post_kwargs["headers"] == {"X-CAP-API-KEY": "cap-key"}
    assert post_kwargs["json"] == {"identifier": "user@example.com", "password": "secret"}
    assert post_kwargs["timeout"] == SETTINGS.request_timeout_seconds

    get_args, get_kwargs = client._session.get.call_args
    assert get_args[0] == f"{CAPITAL_URL}/markets/SPY"
    assert get_kwargs["headers"] == {"CST": "cst-1", "X-SECURITY-TOKEN": "xst-1"}
    assert get_kwargs["timeout"] == SETTINGS.request_timeout_seconds


def test_capital_reuses_session_and_relogs_once_on_401():
    client = CapitalClient(SETTINGS)
    client._session.post = Mock(return_value=_login_ok())
    client._session.get = Mock(side_effect=[_market(), _resp(401), _market(780.0, 781.0)])

    client.get_underlying_snapshot("SPY")
    snap = client.get_underlying_snapshot("SPY")

    assert snap.spot_price == pytest.approx(780.5)
    assert client._session.post.call_count == 2  # login inicial + renovación tras el 401


def test_capital_rejected_login_raises():
    client = CapitalClient(SETTINGS)
    client._session.post = Mock(return_value=_resp(401, {"errorCode": "error.invalid.details"}))
    with pytest.raises(CapitalClientError, match="HTTP 401"):
        client.get_underlying_snapshot("SPY")


def test_capital_missing_quote_raises():
    client = CapitalClient(SETTINGS)
    client._session.post = Mock(return_value=_login_ok())
    client._session.get = Mock(return_value=_resp(200, {"snapshot": {"bid": None, "offer": 1.0}}))
    with pytest.raises(CapitalClientError, match="bid/offer"):
        client.get_underlying_snapshot("SPY")


def test_polygon_client_takes_spot_from_capital_without_polygon_calls():
    spot_client = Mock()
    spot_client.get_underlying_snapshot.return_value = UnderlyingSnapshot("SPY", 775.0, None)
    client = PolygonClient(SETTINGS, spot_client=spot_client)
    client._session.get = Mock()

    assert client.get_underlying_snapshot("SPY").spot_price == 775.0
    client._session.get.assert_not_called()


# ----------------------------------------------------------------------
# Paginación de vencimientos
# ----------------------------------------------------------------------
def _ref_page(dates, next_url=None):
    payload = {"results": [{"expiration_date": d} for d in dates]}
    if next_url:
        payload["next_url"] = next_url
    return _resp(200, payload)


def test_expirations_stop_paging_once_enough_dates_collected():
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(side_effect=[
        _ref_page(["2026-10-05"] * 600 + ["2026-10-06"] * 400, "https://api.polygon.io/next?cursor=2"),
        _ref_page(["2026-10-06"] * 100 + ["2026-10-07"] * 500 + ["2026-10-08"] * 400,
                  "https://api.polygon.io/next?cursor=3"),
        _ref_page(["2026-10-09"] * 1000, "https://api.polygon.io/next?cursor=4"),
    ])

    result = client.get_available_expirations("SPY", 3)

    assert result == [date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)]
    assert client._session.get.call_count == 2  # la página 3 nunca se pide

    first_params = client._session.get.call_args_list[0].kwargs["params"]
    assert first_params["expiration_date.gte"] == date.today().isoformat()
    assert first_params["sort"] == "expiration_date"
    assert first_params["order"] == "asc"
    assert client._session.get.call_args_list[1].args[0] == "https://api.polygon.io/next?cursor=2"


def test_expirations_stop_when_pages_run_out():
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(side_effect=[
        _ref_page(["2026-10-05", "2026-10-06"], "https://api.polygon.io/next?cursor=2"),
        _ref_page(["2026-10-07"]),
    ])

    assert len(client.get_available_expirations("SPY", 10)) == 3
    assert client._session.get.call_count == 2


# ----------------------------------------------------------------------
# Snapshot de la cadena: paginación completa y tope max_contracts
# ----------------------------------------------------------------------
EXP = date.today() + timedelta(days=30)


def _snap_page(strikes, next_url=None):
    payload = {"results": [
        {
            "details": {"contract_type": "call", "strike_price": k,
                        "expiration_date": EXP.isoformat(), "ticker": f"O:SPYC{k}"},
            "open_interest": 10, "implied_volatility": 0.2, "greeks": {"gamma": 0.01}, "day": {},
        }
        for k in strikes
    ]}
    if next_url:
        payload["next_url"] = next_url
    return _resp(200, payload)


UNDERLYING = UnderlyingSnapshot("SPY", 775.0, None)


def test_chain_for_expiration_pages_until_done_without_cap():
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(side_effect=[
        _snap_page(range(500, 750), "https://api.polygon.io/next?cursor=2"),
        _snap_page(range(750, 1000), "https://api.polygon.io/next?cursor=3"),
        _snap_page(range(1000, 1100)),
    ])

    chain = client.get_option_chain_snapshot("SPY", expiration_date=EXP, underlying=UNDERLYING)

    assert len(chain.contracts) == 600
    assert max(c.strike for c in chain.contracts) == 1099
    assert client._session.get.call_args_list[0].kwargs["params"]["expiration_date"] == EXP.isoformat()


def test_chain_cap_truncates_and_warns(caplog):
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(side_effect=[
        _snap_page(range(500, 750), "https://api.polygon.io/next?cursor=2"),
        _snap_page(range(750, 1000), "https://api.polygon.io/next?cursor=3"),
    ])

    with caplog.at_level(logging.WARNING, logger=polygon_client.__name__):
        chain = client.get_option_chain_snapshot(
            "SPY", expiration_date=EXP, max_contracts=300, underlying=UNDERLYING
        )

    assert len(chain.contracts) == 300
    assert client._session.get.call_count == 2
    assert "max_contracts=300" in caplog.text


def test_chain_cap_not_reached_does_not_warn(caplog):
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(return_value=_snap_page(range(500, 600)))

    with caplog.at_level(logging.WARNING, logger=polygon_client.__name__):
        chain = client.get_option_chain_snapshot(
            "SPY", expiration_date=EXP, max_contracts=1000, underlying=UNDERLYING
        )

    assert len(chain.contracts) == 100
    assert "max_contracts" not in caplog.text


# ----------------------------------------------------------------------
# Reintentos 429 / 5xx
# ----------------------------------------------------------------------
def test_429_honors_retry_after(no_sleep):
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(side_effect=[_resp(429, headers={"Retry-After": "7"}), _ref_page(["2026-10-05"])])

    assert client.get_available_expirations("SPY", 1) == [date(2026, 10, 5)]
    assert no_sleep == [7.0]


def test_429_without_retry_after_uses_exponential_backoff_with_jitter(no_sleep):
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(side_effect=[_resp(429), _resp(429), _resp(429), _ref_page(["2026-10-05"])])

    client.get_available_expirations("SPY", 1)

    assert len(no_sleep) == 3
    for attempt, wait in enumerate(no_sleep):
        base = polygon_client.BACKOFF_BASE_SECONDS * 2 ** attempt
        assert base <= wait <= 2 * base


def test_429_on_next_url_page_is_retried(no_sleep):
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(side_effect=[
        _ref_page(["2026-10-05"], "https://api.polygon.io/next?cursor=2"),
        _resp(429, headers={"Retry-After": "1"}),
        _ref_page(["2026-10-06"]),
    ])

    assert len(client.get_available_expirations("SPY", 2)) == 2
    assert no_sleep == [1.0]


def test_transient_5xx_is_retried(no_sleep):
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(side_effect=[_resp(503), _resp(502), _ref_page(["2026-10-05"])])

    client.get_available_expirations("SPY", 1)
    assert len(no_sleep) == 2


def test_client_error_is_not_retried(no_sleep):
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(return_value=_resp(403, {"status": "NOT_AUTHORIZED"}))

    with pytest.raises(PolygonClientError, match="403"):
        client.get_available_expirations("SPY", 1)
    assert client._session.get.call_count == 1
    assert no_sleep == []


def test_gives_up_after_max_retries(no_sleep):
    client = PolygonClient(SETTINGS)
    client._session.get = Mock(return_value=_resp(429))

    with pytest.raises(PolygonClientError, match="429"):
        client.get_available_expirations("SPY", 1)
    assert client._session.get.call_count == polygon_client.MAX_RETRIES + 1
    assert len(no_sleep) == polygon_client.MAX_RETRIES
