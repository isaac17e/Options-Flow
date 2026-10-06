"""
Tests offline del script legacy_0dte/Options_Trade_polygon.py: IV inferida
por contrato, reintentos de Polygon, vencimiento más próximo con una sola
llamada y spot de Capital.com (sesión reutilizada, re-login y paridad de
respaldo sobre la cadena del ciclo). No hay llamadas de red.
"""

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest
import requests

_PATH = Path(__file__).resolve().parents[1] / "legacy_0dte" / "Options_Trade_polygon.py"
_spec = importlib.util.spec_from_file_location("legacy_0dte_script", _PATH)
legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)


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
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture
def sleeps(monkeypatch):
    registro = []
    monkeypatch.setattr(legacy.time, "sleep", registro.append)
    return registro


def _contrato(tipo, strike, oi, iv=None, close=None):
    return {
        "details": {"contract_type": tipo, "strike_price": strike},
        "day": {"volume": 10, "close": close},
        "open_interest": oi,
        "implied_volatility": iv,
    }


# ─────────────────────────────────────────────
#  IV por contrato
# ─────────────────────────────────────────────

def test_iv_inferida_por_contrato_aunque_la_cadena_tenga_iv():
    S, dias, r, sigma = 100.0, 1, 0.045, 0.25
    T = max(dias / 365.0, 0.3 / 365.0)
    precio_102 = legacy._bs_price(S, 102.0, T, r, sigma, "call")

    contratos = [
        _contrato("call", 100.0, 500, iv=0.20, close=1.0),
        _contrato("call", 102.0, 800, iv=None, close=precio_102),  # sin IV → se infiere
        _contrato("call", 104.0, 300, iv=None, close=None),        # sin IV ni precio → excluido
        _contrato("put", 100.0, 400, iv=0.22, close=1.0),
    ]

    calls, puts, n_excluidos = legacy.obtener_cadena_0dte(contratos, "2026-10-06", S, dias, r)

    fila = calls.set_index("strike").loc[102.0]
    assert fila["impliedVolatility"] == pytest.approx(sigma, abs=1e-3)
    assert fila["gamma_bsm"] > 0
    assert fila["gex"] > 0
    # IV original de Polygon intacta
    assert calls.set_index("strike").loc[100.0, "impliedVolatility"] == pytest.approx(0.20)
    excluida = calls.set_index("strike").loc[104.0]
    assert excluida["gex"] == 0
    assert n_excluidos == 1


def test_inversion_fallida_cuenta_como_excluida_y_sin_oi_no_cuenta():
    S, dias = 100.0, 1
    contratos = [
        _contrato("call", 100.0, 500, iv=0.20, close=1.0),
        # Precio por debajo del intrínseco (80 ITM a 0,5): la inversión falla.
        _contrato("call", 80.0, 900, iv=None, close=0.5),
        # Sin OI: no aporta GEX, no se cuenta en el aviso.
        _contrato("call", 120.0, 0, iv=None, close=None),
        _contrato("put", 100.0, 400, iv=0.22, close=1.0),
    ]
    calls, _, n_excluidos = legacy.obtener_cadena_0dte(contratos, "2026-10-06", S, dias)
    assert calls.set_index("strike").loc[80.0, "impliedVolatility"] == 0
    assert n_excluidos == 1


# ─────────────────────────────────────────────
#  Reintentos de Polygon
# ─────────────────────────────────────────────

def test_polygon_reintenta_429_respetando_retry_after_y_luego_5xx(monkeypatch, sleeps):
    respuestas = iter([
        FakeResponse(429, headers={"Retry-After": "2"}),
        FakeResponse(503),
        FakeResponse(200, {"status": "OK", "results": [1]}),
    ])
    llamadas = []

    def fake_get(url, params=None, timeout=None):
        llamadas.append(url)
        return next(respuestas)

    monkeypatch.setattr(legacy.requests, "get", fake_get)
    data = legacy._polygon_get("/v3/x")

    assert data["results"] == [1]
    assert len(llamadas) == 3
    assert sleeps[0] == 2.0                 # Retry-After respetado
    assert 2.0 <= sleeps[1] <= 4.0          # backoff 2^1 + jitter en [0, 2]


def test_polygon_no_reintenta_4xx(monkeypatch, sleeps):
    llamadas = []

    def fake_get(url, params=None, timeout=None):
        llamadas.append(url)
        return FakeResponse(403)

    monkeypatch.setattr(legacy.requests, "get", fake_get)
    with pytest.raises(requests.HTTPError):
        legacy._polygon_get("/v3/x")
    assert len(llamadas) == 1
    assert sleeps == []


def test_polygon_agota_reintentos(monkeypatch, sleeps):
    monkeypatch.setattr(legacy.requests, "get", lambda *a, **k: FakeResponse(500))
    with pytest.raises(RuntimeError, match="reintentos"):
        legacy._polygon_get("/v3/x")
    assert len(sleeps) == legacy.MAX_RETRIES


# ─────────────────────────────────────────────
#  Vencimiento más próximo: una sola llamada
# ─────────────────────────────────────────────

def test_vencimiento_mas_proximo_con_una_sola_llamada(monkeypatch, sleeps):
    manana = (date.today() + timedelta(days=1)).isoformat()
    llamadas = []

    def fake_get(url, params=None, timeout=None):
        llamadas.append((url, params))
        return FakeResponse(200, {
            "status": "OK",
            "results": [{"expiration_date": manana}],
            "next_url": "https://api.polygon.io/v3/reference/options/contracts?cursor=abc",
        })

    monkeypatch.setattr(legacy.requests, "get", fake_get)
    exp, dias = legacy.seleccionar_vencimiento_0dte("spy", silencioso=True)

    assert (exp, dias) == (manana, 1)
    assert len(llamadas) == 1               # no sigue next_url
    url, params = llamadas[0]
    assert url.endswith("/v3/reference/options/contracts")
    assert params["underlying_ticker"] == "SPY"
    assert params["expiration_date.gte"] == date.today().isoformat()
    assert params["sort"] == "expiration_date"
    assert params["order"] == "asc"
    assert params["limit"] == 1


# ─────────────────────────────────────────────
#  Spot: Capital.com y paridad de respaldo
# ─────────────────────────────────────────────

class FakeCapitalHttp:
    def __init__(self, get_statuses):
        self.get_statuses = list(get_statuses)
        self.posts = 0
        self.deletes = []
        self.get_headers = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts += 1
        return FakeResponse(200, {}, {"CST": f"cst-secreto-{self.posts}",
                                      "X-SECURITY-TOKEN": f"tok-secreto-{self.posts}"})

    def get(self, url, headers=None, timeout=None):
        self.get_headers.append(dict(headers))
        status = self.get_statuses.pop(0)
        payload = {"snapshot": {"bid": 669.0, "offer": 671.0, "marketStatus": "TRADEABLE",
                                "updateTime": "2026-10-05T14:30:01.123"}}
        return FakeResponse(status, payload)

    def delete(self, url, headers=None, timeout=None):
        self.deletes.append((url, dict(headers)))
        return FakeResponse(200)


@pytest.fixture
def capital(monkeypatch):
    for nombre in ("CAPITAL_API_KEY", "CAPITAL_IDENTIFIER", "CAPITAL_API_PASSWORD"):
        monkeypatch.setenv(nombre, "x")
    monkeypatch.setattr(legacy.atexit, "register", lambda fn: None)
    sesion = legacy.CapitalSession(base_url="https://capital.test/api/v1")
    monkeypatch.setattr(legacy, "_capital", sesion)

    class ResolverFijo:     # SPY → epic SPY sin llamadas extra (tickers.py se prueba aparte)
        def resolve(self, ticker, ref_price=None, refresh=False):
            return legacy._tk.Resolution(ticker.upper(), ticker.upper(), ticker.upper(), ticker.upper(),
                                         "SHARES", "share_cfd_parity_rolling")

    monkeypatch.setattr(legacy, "_resolver", ResolverFijo())
    return sesion


def test_spot_capital_reutiliza_sesion_y_relogin_en_401(capital, capsys):
    http = FakeCapitalHttp([200, 200, 401, 200])
    capital._http = http

    S1, fuente, hora = legacy.obtener_precio("SPY")
    S2, _, _ = legacy.obtener_precio("SPY")
    assert (S1, S2) == (670.0, 670.0)
    assert http.posts == 1                   # sesión reutilizada entre ciclos
    assert fuente.startswith("Capital.com")
    assert hora == "2026-10-05 14:30:01"

    S3, _, _ = legacy.obtener_precio("SPY")  # 401 → re-login → 200
    assert S3 == 670.0
    assert http.posts == 2
    assert http.get_headers[-1]["CST"] == "cst-secreto-2"

    capital.logout()
    assert http.deletes == [("https://capital.test/api/v1/session",
                             {"CST": "cst-secreto-2", "X-SECURITY-TOKEN": "tok-secreto-2"})]
    assert "secreto" not in capsys.readouterr().out


def test_spot_paridad_reutiliza_la_cadena_sin_llamadas(capital, monkeypatch):
    monkeypatch.delenv("CAPITAL_API_KEY")    # Capital.com falla por credenciales

    def sin_red(*a, **k):
        raise AssertionError("no debe llamar a la red")

    monkeypatch.setattr(legacy.requests, "get", sin_red)
    contratos = [
        _contrato("call", 670.0, 100, iv=0.2, close=3.0),
        _contrato("put", 670.0, 100, iv=0.2, close=2.5),
        _contrato("call", 675.0, 100, iv=0.2, close=1.0),
        _contrato("put", 675.0, 100, iv=0.2, close=6.0),
    ]
    S, fuente, _ = legacy.obtener_precio("SPY", contratos)
    assert S == pytest.approx(670.5)
    assert "paridad" in fuente


def test_spot_sin_capital_ni_cadena_devuelve_none(capital, monkeypatch):
    monkeypatch.delenv("CAPITAL_API_KEY")
    assert legacy.obtener_precio("SPY") == (None, None, None)


# ─────────────────────────────────────────────
#  Dashboard: fuente del spot, ventana real y aviso de excluidos
# ─────────────────────────────────────────────

def test_dashboard_muestra_fuente_spot_ventana_y_aviso(tmp_path):
    import json

    S, dias = 670.0, 1
    T = max(dias / 365.0, 0.3 / 365.0)
    contratos = []
    for k in range(650, 691):
        for tipo in ("call", "put"):
            precio = legacy._bs_price(S, k, T, 0.045, 0.18, tipo)
            contratos.append(_contrato(tipo, float(k), 1000,
                                       iv=None if (tipo == "call" and k < 665) else 0.18,
                                       close=0.01 if k == 651 else round(precio, 2)))
    calls, puts, n_excluidos = legacy.obtener_cadena_0dte(contratos, "2026-10-06", S, dias)
    merged, modo_label, ventana_label = legacy._preparar_merged_0dte(calls, puts, S)
    assert n_excluidos >= 1
    assert ventana_label == "±3% del spot"

    data = {
        "exp": "2026-10-06", "dias": dias, "S": S,
        "spot_fuente": "Capital.com (mid bid/offer)", "spot_hora": "2026-10-05 14:30:01",
        "max_pain": legacy.calcular_max_pain(calls, puts, S=S), "merged": merged,
        "modo_label": modo_label, "ventana_label": ventana_label, "n_excluidos_gex": n_excluidos,
        "zero_gamma": None, "call_wall": None, "put_wall": None, "picos_gamma_bruta": [],
        "status_gamma": "LONG GAMMA DÉBIL", "direccion": "NEUTRAL", "confianza": 50, "bullets": [],
    }
    html_path = legacy.crear_dashboard_0dte(data, "SPY", output_dir=str(tmp_path), refresh_seconds=60)
    html = Path(html_path).read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "SPY_0DTE_data.json").read_text(encoding="utf-8"))

    assert "ts" not in payload
    assert payload["spot_source"] == "Spot: Capital.com (mid bid/offer) · 2026-10-05 14:30:01"
    assert payload["gex_section_title"] == "Net GEX y Muros por Strike (±3% del spot)"
    assert f"{n_excluidos} contratos con OI" in payload["warnings_html"]
    assert payload["spot_source"] in html
    assert "manual" not in html and "Starter" not in html
