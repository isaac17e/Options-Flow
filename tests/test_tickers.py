"""Tests offline de legacy_0dte/tickers.py (resolución ticker → CFD y base)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "legacy_0dte"))
import tickers as tk  # noqa: E402

MARKETS = {
    "US500": {"instrument": {"epic": "US500", "type": "INDICES", "name": "US 500"},
              "snapshot": {"bid": 7775.4, "offer": 7776.0}},
    "US100": {"instrument": {"epic": "US100", "type": "INDICES"}, "snapshot": {"bid": 31135.3, "offer": 31137.1}},
    "SPX": {"instrument": {"epic": "SPX", "type": "SHARES", "name": "Spirax Sarco"}, "snapshot": {"bid": None, "offer": None}},
    "AAPL": {"instrument": {"epic": "AAPL", "type": "SHARES"}, "snapshot": {"bid": 332.79, "offer": 333.1}},
}
SEARCH = {
    "SPX": [{"epic": "SPXC", "instrumentType": "SHARES", "bid": 175.9, "offer": 177.2},
            {"epic": "SPX", "instrumentType": "SHARES", "bid": None, "offer": None}],
    "AAPL": [{"epic": "AAPL", "instrumentType": "SHARES", "bid": 332.79, "offer": 333.1},
             {"epic": "APLE", "instrumentType": "SHARES", "bid": 16.37, "offer": 16.44}],
    "QQQ": [{"epic": "QQQ", "instrumentType": "SHARES", "bid": 756.17, "offer": 756.69},
            {"epic": "TQQQ", "instrumentType": "SHARES", "bid": 83.1, "offer": 83.34}],
    "BRKB": [{"epic": "BRK.B", "instrumentType": "SHARES", "bid": 480.0, "offer": 480.6},
             {"epic": "BRKA", "instrumentType": "SHARES", "bid": 720000.0, "offer": 721000.0}],
    "XYZ": [{"epic": "XYZW", "instrumentType": "SHARES", "bid": 50.0, "offer": 50.1},
            {"epic": "XYZQ", "instrumentType": "SHARES", "bid": 50.2, "offer": 50.3}],
}


class FakeCapital:
    def __init__(self):
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        if path.startswith("/markets?searchTerm="):
            return {"markets": SEARCH.get(path.split("=", 1)[1], [])}
        return MARKETS[path.split("/markets/")[1]]


@pytest.fixture
def cap():
    return FakeCapital()


def test_polygon_tickers_for_indices_and_stocks():
    assert tk.polygon_tickers("spx") == tk.PolygonTickers("SPX", "I:SPX", True)
    assert tk.polygon_tickers("I:NDX") == tk.PolygonTickers("NDX", "I:NDX", True)
    assert tk.polygon_tickers("RUT").snapshot == "I:RUT"
    assert tk.polygon_tickers("qqq") == tk.PolygonTickers("QQQ", "QQQ", False)


def test_spx_maps_to_us500_never_spirax(cap, tmp_path):
    r = tk.TickerResolver(cap.get, cache_path=str(tmp_path / "m.json")).resolve("SPX", ref_price=7777.4)
    assert (r.capital_epic, r.instrument_type, r.polygon_snapshot_ticker) == ("US500", "INDICES", "I:SPX")
    assert r.basis_method.startswith("index")
    assert not any("searchTerm=SPX" in p for p in cap.paths)


def test_index_price_far_from_parity_fails(cap, tmp_path):
    with pytest.raises(tk.TickerResolutionError):
        tk.TickerResolver(cap.get, cache_path=str(tmp_path / "m.json")).resolve("SPX", ref_price=6000.0)


def test_stock_exact_epic_and_price_check(cap, tmp_path):
    res = tk.TickerResolver(cap.get, cache_path=str(tmp_path / "m.json"))
    r = res.resolve("aapl", ref_price=332.98)
    assert (r.capital_epic, r.instrument_type) == ("AAPL", "SHARES") and r.deviation < 0.01
    with pytest.raises(tk.TickerResolutionError):
        res.resolve("QQQ", ref_price=83.2, refresh=True)        # precio de TQQQ, no de QQQ


def test_no_exact_epic_unique_price_match(cap, tmp_path):
    r = tk.TickerResolver(cap.get, cache_path=str(tmp_path / "m.json")).resolve("BRKB", ref_price=480.5)
    assert r.capital_epic == "BRK.B"


def test_ambiguous_or_missing_fails_loudly(cap, tmp_path):
    res = tk.TickerResolver(cap.get, cache_path=str(tmp_path / "m.json"))
    with pytest.raises(tk.TickerResolutionError):
        res.resolve("XYZ", ref_price=50.1)                       # dos candidatos dentro del 1 %
    with pytest.raises(tk.TickerResolutionError):
        res.resolve("XYZ")                                       # sin epic exacto ni precio
    with pytest.raises(tk.TickerResolutionError):
        res.resolve("NOPE", ref_price=10.0)


def test_cache_is_used_and_rechecked(cap, tmp_path):
    path = str(tmp_path / "m.json")
    tk.TickerResolver(cap.get, cache_path=path).resolve("AAPL", ref_price=332.98)
    cap.paths.clear()
    res2 = tk.TickerResolver(cap.get, cache_path=path)
    assert res2.resolve("AAPL", ref_price=333.0).capital_epic == "AAPL"
    assert cap.paths == ["/markets/AAPL"]                        # sin búsqueda, solo chequeo de precio


def _c(typ, k, close, vol=10):
    return {"details": {"contract_type": typ, "strike_price": k}, "day": {"close": close, "volume": vol}}


def test_parity_spot_median_near_atm():
    cs = [_c("call", 100, 3.0), _c("put", 100, 2.0), _c("call", 105, 1.0), _c("put", 105, 5.2),
          _c("call", 95, 6.4), _c("put", 95, 1.2), _c("call", 200, 0.05), _c("put", 200, 99.0, vol=0)]
    assert tk.parity_spot(cs, n=3) == pytest.approx(100.8, abs=0.01)


def test_basis_tracker_median_and_outlier_rejection():
    b = tk.BasisTracker(window=3, prior=-1.0)
    assert b.value == -1.0
    for cfd, par in ((7776.0, 7777.4), (7775.0, 7777.0), (7780.0, 7777.5)):
        b.add(cfd, par)
    assert b.value == pytest.approx(-1.4)
    assert b.add(7900.0, 7777.0) is None                        # >1 % → descartada
    assert b.underlying_spot(7782.6) == pytest.approx(7784.0)
