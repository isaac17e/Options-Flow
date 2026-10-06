"""
Tests sintéticos de legacy_0dte/scanner.py: strike clave, tocado / no tocado, WALL, ACCELERATOR,
muro roto, acelerador rechazado, siguiente movimiento sin filtros (EXPIRED), traducción con base
y selección del candidato de mayor confianza. También el cambio de régimen en decision.manage.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "legacy_0dte"))
import decision as D  # noqa: E402
import scanner as SC  # noqa: E402

NOW = datetime(2026, 10, 6, 9, 30)
P = D.Params(decimals=2)


def mk(peaks, lo=90.0, hi=110.0, step=0.5, iv=1.0, spot=100.0, flip=96.0, net_total=1.0, shift=0.0):
    prof, k = [], lo
    while k <= hi + 1e-9:
        g, n = peaks.get(round(k, 2), (0.5, 0.05))
        prof.append(dict(strike=round(k, 2) + shift, net_gex=n, gross_gex=g, oi_total=100.0, iv=iv))
        k += step
    pos = [r for r in prof if r["net_gex"] > 0]
    neg = [r for r in prof if r["net_gex"] < 0]
    return dict(spot=spot + shift, flip=flip + shift, net_gex=net_total, atm_iv=iv, profile=prof, max_pain=spot + shift,
                gross_peaks=[r["strike"] for r in sorted(prof, key=lambda r: -r["gross_gex"])[:3]],
                call_wall=max(pos, key=lambda r: r["net_gex"])["strike"] if pos else None,
                put_wall=min(neg, key=lambda r: r["net_gex"])["strike"] if neg else None)


WALL_LV = mk({102.0: (20, 16), 100.0: (8, 4), 98.0: (8, -5)})            # 102 = muro (gamma +)
ACC_LV = mk({98.0: (20, -16), 100.0: (8, 4), 95.0: (8, -4)}, flip=101.0, net_total=-1.0)   # 98 = acelerador


def ev(levels, open_, high, low, mid, base=0.0, spread=0.04, ticker="SYN"):
    live = {"bid": mid - spread / 2, "offer": mid + spread / 2, "base": base}
    return SC.evaluate(ticker, levels, SC.SessionRange(open_, high, low), live, NOW, P)


def test_key_strike_weights_gamma_by_touch_probability():
    assert SC.key_strike(WALL_LV, 101.0, NOW, P)["strike"] == 102.0
    assert SC.key_strike(ACC_LV, 99.0, NOW, P)["strike"] == 98.0
    far = mk({109.0: (30, 20), 101.0: (6, 4), 96.0: (6, -5)})        # muro enorme pero lejano: no es el objetivo
    k = SC.key_strike(far, 100.0, NOW, P)
    assert k["strike"] == 101.0 and k["p_touch"] > 0.5


def test_untouched_candidate_toward_strike():
    r = ev(WALL_LV, 100.4, 100.9, 100.1, 100.3)
    assert (r["status"], r["behavior"], r["touched"], r["side"], r["plan"]) == ("CANDIDATE", "UNTOUCHED", False, "BUY", "TARGET")
    assert r["tp_strike"] == 102.0 and r["stop"] < 100.0 and r["p_touch"] >= 0.30 and r["rr"] >= 1.0


def test_untouched_failing_filters_waits_not_expired():
    r = ev(WALL_LV, 100.6, 101.2, 100.2, 100.9)
    assert (r["status"], r["behavior"]) == ("WAIT", "UNTOUCHED")


def test_touched_wall_rejected_reverses():
    r = ev(WALL_LV, 100.5, 102.1, 100.3, 101.7)
    assert (r["status"], r["behavior"], r["touched"], r["side"]) == ("CANDIDATE", "WALL", True, "SELL")
    assert r["stop"] > 102.0 and r["tp"] < r["entry"] and r["p_touch"] >= 0.30 and r["rr"] >= 1.0


def test_wall_next_move_fails_filters_expires():
    r = ev(mk({102.0: (20, 16), 100.0: (8, 4), 98.0: (8, -5)}, iv=0.5), 100.5, 102.1, 100.3, 101.3)
    assert (r["status"], r["behavior"]) == ("EXPIRED", "WALL")


def test_broken_wall_expires():
    r = ev(WALL_LV, 100.5, 103.5, 100.3, 103.4)
    assert (r["status"], r["behavior"]) == ("EXPIRED", "BROKEN_WALL")


def test_wall_pierced_then_back_is_broken_not_rejected():
    r = ev(WALL_LV, 100.5, 102.9, 100.3, 101.7)          # cruzó 0,9 > break_margin y volvió: no es rechazo
    assert (r["status"], r["behavior"]) == ("EXPIRED", "BROKEN_WALL")


def test_accelerator_continuation():
    r = ev(ACC_LV, 100.5, 100.8, 97.2, 97.25)
    assert (r["status"], r["behavior"], r["side"]) == ("CANDIDATE", "ACCELERATOR", "SELL")
    assert r["tp_strike"] == 95.0 and r["stop"] > 98.0


def test_accelerator_next_move_fails_expires():
    r = ev(ACC_LV, 100.5, 100.8, 97.2, 97.0)
    assert (r["status"], r["behavior"]) == ("EXPIRED", "ACCELERATOR")


def test_failed_accelerator_expires():
    r = ev(ACC_LV, 100.5, 100.8, 97.9, 98.4)
    assert (r["status"], r["behavior"]) == ("EXPIRED", "FAILED_ACCELERATOR")


def test_price_still_at_level_waits():
    r = ev(WALL_LV, 100.5, 102.1, 100.3, 102.05)
    assert (r["status"], r["behavior"]) == ("WAIT", "AT_LEVEL")


def test_basis_translation_index_like():
    """Índice con base −1: el strike 7802 es 7801 en el CFD; la banda de toque escala con el precio."""
    lv = mk({102.0: (20, 16), 100.0: (8, 4), 98.0: (8, -5)}, shift=7700.0, iv=0.10)
    tb = D.scaled(P.touch_band, 7800.0, D.sigma_window(7800.0, 0.10, 75))
    touched = ev(lv, 7799.5, 7801.0 - tb + 0.01, 7799.3, 7800.0, base=-1.0)
    assert touched["touched"] is True and touched["target_strike"] == 7802.0
    untouched = ev(lv, 7799.5, 7801.0 - tb - 0.01, 7799.3, 7798.5, base=-1.0)
    assert untouched["touched"] is False


def test_select_highest_confidence():
    a = {"status": "CANDIDATE", "score": 0.40, "p_touch": 0.5}
    b = {"status": "CANDIDATE", "score": 0.46, "p_touch": 0.35}
    c = {"status": "EXPIRED", "score": 0.0, "p_touch": None}
    assert SC.select([a, b, c]) is b
    assert SC.select([c]) is None


def test_manage_exits_on_regime_change_from_entry():
    lv = mk({102.0: (20, 16), 100.0: (8, 4), 98.0: (8, -5)})
    pos = dict(side="SELL", plan="WALL", entry=101.68, stop=103.17, tp=99.62, tp_strike=99.5, opened=NOW,
               regime_at_entry="LONG")
    live = {"now": NOW.replace(minute=40), "bid": 101.5, "offer": 101.54, "base": 0.0}
    assert D.manage(pos, lv, live, P)["action"] in ("HOLD", "MOVE_TP")
    flipped = {**lv, "net_gex": -1.0}
    r = D.manage(pos, flipped, live, P)
    assert r["action"] == "EXIT" and "régimen cambió" in r["reason"]
