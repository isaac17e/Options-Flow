"""
Tests de legacy_0dte/decision.py con niveles reales (cadena SPXW 2026-10-06 descargada el
2026-10-05, niveles recalculados en cada spot: tests/fixtures/levels_spx_20261005.json) y con
el mismo escenario reescalado a una acción de ~100 USD para comprobar que los umbrales escalan.
"""
import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "legacy_0dte"))
import decision as D  # noqa: E402

F = json.loads((ROOT / "tests" / "fixtures" / "levels_spx_20261005.json").read_text())["levels"]
NOW = datetime(2026, 10, 6, 9, 30)
P_US500 = D.Params(decimals=1)
BASE = -1.0                                   # US500 − SPX
FLAT = {"trades_today": 0, "pnl_today": 0.0, "position": None, "balance": 1000.0, "size": 0.64}
LIVE = {
    "A": dict(bid=7793.7, offer=7794.3, bar5=dict(high=7797.5, low=7792.0, close=7794.0), close15=7794.0),
    "B": dict(bid=7751.7, offer=7752.3, bar5=dict(high=7755.0, low=7749.5, close=7752.0), close15=7752.0),
    "C": dict(bid=7696.7, offer=7697.3, bar5=dict(high=7700.0, low=7696.0, close=7697.0), close15=7697.0),
    "mid": dict(bid=7776.1, offer=7776.7, bar5=dict(high=7779.0, low=7774.0, close=7776.4), close15=7776.4),
}


def run(fx, key, now=NOW, state=FLAT, p=P_US500):
    return D.decide(F[fx], {**LIVE[key], "now": now, "base": BASE}, state, p)


# ── utilidades ──
def test_p_touch_monotone_and_bounded():
    ps = [D.p_touch(7777, k, 0.10, 75) for k in (7780, 7790, 7800, 7820)]
    assert all(0 <= x <= 1 for x in ps) and ps == sorted(ps, reverse=True)
    assert D.p_touch(7777, 7777.01, 0.10, 75) > 0.99
    assert D.p_touch(7777, 7800, 0.10, 0) == 0.0


def test_regime_today_and_without_flip():
    assert D.regime(F["close"]) == "LONG"
    assert D.regime(F["C_7699"]) == "SHORT"
    no_flip = {**F["close"], "flip": None}
    assert D.regime(no_flip) == "LONG"              # todo el perfil cercano con gamma positiva
    assert D.regime({**no_flip, "net_gex": -1.0}) == "SHORT"


def test_attractive_includes_walls_and_peaks():
    ks = [a["strike"] for a in D.attractive_strikes(F["close"])]
    for k in (7750, 7800, 7825):
        assert k in ks


def test_position_size_us500_and_shares():
    assert D.position_size(1000, 100, 7782.4, 0.01, 0.01) == 0.64     # US500, 100:1
    assert D.position_size(1000, 20, 333.0, 0.1, 0.01) == 3.0         # AAPL, 20:1
    assert D.position_size(1000, 20, 240.1, 0.1, 0.1) == 4.1          # NVDA, incremento 0,1
    assert D.position_size(1000, 100, 7782.4, 0.01, 0.01, margin_adjust=0.2) == 0.12
    assert D.position_size(10, 1, 7782.4, 0.01, 0.01) == 0.0          # por debajo del mínimo


# ── decisiones ──
def test_mid_zone_no_trade():
    r = run("close", "mid")
    assert r["action"] == "NO_TRADE" and "sin señal" in r["reason"]


def test_plan_A_skipped_by_rr_and_probability():
    r = run("A_7795", "A")
    assert r["action"] == "NO_TRADE" and r["plan"] == "A" and "SKIP" in r["reason"]


def test_plan_B_buy_with_probable_tp():
    r = run("B_7753", "B")
    assert r["action"] == "BUY" and r["plan"] == "B"
    assert r["entry"] == 7752.3 and r["stop"] < 7749 and r["tp_strike"] == 7770.0
    assert r["rr"] >= 1.0 and r["p_touch"] >= 0.30 and r["size"] == 0.64
    assert r["usd_at_tp"] > 0 > r["usd_at_stop"]


def test_plan_C_sell_momentum_to_put_wall():
    r = run("C_7699", "C")
    assert r["action"] == "SELL" and r["plan"] == "C"
    # el muro de puts 7675 queda en R:R 0,98 → la regla toma el strike probable más lejano que paga
    assert r["tp_strike"] == 7670.0 and r["stop"] > 7705 and r["rr"] >= 1.0 and r["p_touch"] >= 0.30


def test_gates():
    assert run("B_7753", "B", now=datetime(2026, 10, 6, 8, 35))["action"] == "NO_TRADE"
    assert run("B_7753", "B", now=datetime(2026, 10, 6, 10, 1))["action"] == "NO_TRADE"   # solo mañana
    assert run("B_7753", "B", state={**FLAT, "trades_today": 3})["action"] == "NO_TRADE"
    assert run("B_7753", "B", state={**FLAT, "pnl_today": -20.0})["action"] == "NO_TRADE"
    assert run("B_7753", "B", state={**FLAT, "position": {"side": "BUY"}})["action"] == "HOLD"


# ── escalado: mismo escenario como acción de ~100 USD ──
def _rescale_levels(L, f):
    L = copy.deepcopy(L)
    for k in ("spot", "flip", "call_wall", "put_wall", "max_pain"):
        if L.get(k) is not None:
            L[k] *= f
    L["gross_peaks"] = [x * f for x in L["gross_peaks"]]
    for r in L["profile"]:
        r["strike"] *= f
    return L


@pytest.mark.parametrize("fx,key", [("B_7753", "B"), ("C_7699", "C"), ("A_7795", "A")])
def test_decision_is_scale_invariant(fx, key):
    f = 100.0 / F[fx]["spot"]
    big = run(fx, key)
    live = {k: (v * f if isinstance(v, float) else v) for k, v in LIVE[key].items()}
    live["bar5"] = {k: v * f for k, v in LIVE[key]["bar5"].items()}
    small = D.decide(_rescale_levels(F[fx], f), {**live, "now": NOW, "base": BASE * f},
                     {**FLAT, "size": 0.64 / f}, D.Params(decimals=4))
    assert small["action"] == big["action"]
    if big["action"] != "NO_TRADE":
        for k in ("entry", "stop", "tp"):
            assert small[k] == pytest.approx(big[k] * f, abs=0.06 * f)
        assert small["usd_at_stop"] == pytest.approx(big["usd_at_stop"], rel=0.03)


def test_thresholds_scale_with_price():
    p = D.Params()
    for S, iv in ((7800.0, 0.10), (100.0, 0.40)):
        sig = D.sigma_window(S, iv, 75)
        stop = D.scaled(p.stop_buffer, S, sig)
        tp_min = D.scaled(p.min_tp_extra, S, sig)
        assert 0.0009 <= stop / S <= 0.01          # entre 0,09 % y 1 % del precio
        assert tp_min < stop
    # el piso porcentual manda cuando la vol es muy baja
    assert D.scaled(p.stop_buffer, 100.0, 0.0) == pytest.approx(0.1)


# ── gestión ──
@pytest.fixture
def pos_B():
    r = run("B_7753", "B")
    return dict(side="BUY", plan="B", entry=r["entry"], stop=r["stop"], tp=r["tp"], tp_strike=r["tp_strike"], opened=NOW)


def _live(bid, offer, minute):
    return dict(bid=bid, offer=offer, base=BASE, now=datetime(2026, 10, 6, 9 + (30 + minute) // 60, (30 + minute) % 60))


def test_manage_tp_stop_time(pos_B):
    L = F["B_7753"]
    assert D.manage(pos_B, L, _live(pos_B["tp"] + 0.1, pos_B["tp"] + 0.7, 10), P_US500)["reason"] == "TP alcanzado"
    assert D.manage(pos_B, L, _live(pos_B["stop"] - 0.1, pos_B["stop"] + 0.5, 10), P_US500)["reason"] == "stop alcanzado"
    assert "tiempo máximo" in D.manage(pos_B, L, _live(7755, 7755.6, 76), P_US500)["reason"]
    late = dict(bid=7755, offer=7755.6, base=BASE, now=datetime(2026, 10, 6, 14, 45))
    assert "14:45" in D.manage({**pos_B, "opened": datetime(2026, 10, 6, 14, 0)}, L, late, P_US500)["reason"]


def test_manage_regime_flip_exits_mean_reversion(pos_B):
    r = D.manage(pos_B, F["C_7699"], _live(7745, 7745.6, 5), P_US500)
    assert r["action"] == "EXIT" and "régimen" in r["reason"]


def test_manage_never_widens_stop(pos_B):
    for lv, px in (("B_7753", 7755), ("close", 7760), ("A_7795", 7762)):
        r = D.manage(pos_B, F[lv], _live(px, px + 0.6, 5), P_US500)
        assert "stop" not in r or r["stop"] == pos_B["stop"]


def test_manage_keeps_tp_when_price_approaches_it(pos_B):
    r = D.manage(pos_B, F["close"], _live(7765, 7765.6, 10), P_US500)
    assert r["action"] == "HOLD" and r["tp"] == pos_B["tp"]


def test_manage_pulls_tp_in_to_new_closer_attractive_strike(pos_B):
    far = {**pos_B, "tp": 7798.0, "tp_strike": 7800.0}
    r = D.manage(far, F["close"], _live(7760, 7760.6, 10), P_US500)
    assert r["action"] == "MOVE_TP" and r["tp_strike"] == 7770.0 and r["stop"] == pos_B["stop"]
