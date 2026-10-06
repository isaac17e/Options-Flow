"""
scanner.py — modo escáner multi-activo sobre la salida de legacy_0dte (levels.py + decision.py).

Para cada ticker, en cada ciclo:
  1. Strike clave = entre los strikes atractivos (decision.attractive_strikes: picos de gamma bruta /
     |Net GEX|, muros) el de mayor gamma bruta × P(toque) en el tiempo que queda; empate → el más cercano.
  2. Con el máximo/mínimo del CFD desde la apertura (08:30 COT) y la base (CFD = strike + base):
     * NO tocado → candidato HACIA el strike (TP en el strike, stop estructural detrás del precio:
       el strike atractivo más cercano del otro lado ± stop_buffer). Si no pasa los filtros
       (P(toque) ≥ p_min y R:R ≥ rr_min) queda en espera y se reevalúa el ciclo siguiente.
     * Tocado (basta con tocar) → comportamiento según el signo del Net GEX local del strike y el
       lado por el que llegó el precio (el de la apertura):
         - gamma > 0 y el precio se devolvió sin haberlo cruzado ≥ break_margin → WALL: reversión
           hacia el siguiente nivel del lado contrario (decision.choose_tp), stop más allá del muro;
         - gamma < 0 y el precio lo atravesó con fuerza (≥ break_margin) → ACCELERATOR: continuación
           al siguiente strike atractivo, stop de vuelta al otro lado del strike;
         - comportamiento contrario a la teoría (muro cruzado ≥ break_margin aunque luego volviera,
           acelerador rechazado) o siguiente
           movimiento sin P ≥ p_min y R:R ≥ rr_min → EXPIRED para el día;
         - precio todavía en el nivel → espera.
  3. Selección: entre los candidatos, el de mayor confianza = P(toque) × min(R:R, rr_cap).

Precios de entrada/stop/TP en unidades del CFD; strikes en unidades del subyacente.
No coloca órdenes: el orquestador (run_live.py --scanner) decide y ejecuta.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

import decision as D

CANDIDATE, EXPIRED, WAIT, NONE = "CANDIDATE", "EXPIRED", "WAIT", "NONE"
UNTOUCHED, WALL, ACCELERATOR = "UNTOUCHED", "WALL", "ACCELERATOR"
BROKEN_WALL, FAILED_ACCELERATOR, AT_LEVEL = "BROKEN_WALL", "FAILED_ACCELERATOR", "AT_LEVEL"
RR_CAP = 3.0


@dataclass(frozen=True)
class SessionRange:
    """CFD desde la apertura del día: precio de apertura, máximo y mínimo (mid bid/ask)."""
    open: float
    high: float
    low: float


def key_strike(levels: dict, spot: float, now: datetime, p: D.Params = D.Params()) -> Optional[dict]:
    """
    Strike más atractivo: entre los atractivos de la ventana, el de mayor gamma bruta × P(toque)
    desde el spot actual del subyacente en el tiempo que queda (un muro enorme pero inalcanzable hoy
    no sirve de objetivo); empate → el más cercano. Devuelve la fila del perfil con "p_touch".
    """
    att = D.attractive_strikes(levels, p)
    if not att:
        return None
    S, minutes = spot, D.hold_minutes(now, p)
    rows = [{**r, "p_touch": D.p_touch(S, r["strike"], D._iv_at(levels, r["strike"]), minutes, p)} for r in att]
    return max(rows, key=lambda r: (r["gross_gex"] * r["p_touch"], -abs(r["strike"] - S)))


def _confidence(p_touch: float, rr: float) -> float:
    return round(p_touch * min(rr, RR_CAP), 4)


def evaluate(ticker: str, levels: dict, session: SessionRange, live: dict, now: datetime,
             p: D.Params = D.Params()) -> dict:
    """
    live: {"bid", "offer", "base"} del CFD. Devuelve un dict con status (CANDIDATE / EXPIRED /
    WAIT / NONE), behavior, target_strike, touched, side, plan, entry, stop, tp, tp_strike,
    p_touch, rr, score y reason.
    """
    base = float(live.get("base", 0.0))
    bid, offer = float(live["bid"]), float(live["offer"])
    mid, spread = (bid + offer) / 2.0, max(offer - bid, 0.0)
    out = dict(ticker=ticker, status=NONE, behavior=None, target_strike=None, touched=None, side=None,
               plan=None, entry=None, stop=None, tp=None, tp_strike=None, p_touch=None, rr=None, score=0.0,
               regime=D.regime(levels), reason="")
    S_u = mid - base
    key = key_strike(levels, S_u, now, p)
    if key is None:
        return {**out, "reason": "sin strikes atractivos en la ventana"}

    minutes = D.hold_minutes(now, p)
    sig = D.sigma_window(S_u, levels["atm_iv"], minutes, p)
    tb, cmin = D.scaled(p.touch_band, S_u, sig), D.scaled(p.confirm_min, S_u, sig)
    brk, buf = D.scaled(p.break_margin, S_u, sig), D.scaled(p.stop_buffer, S_u, sig)
    K = key["strike"]
    Kc = K + base
    touched = session.high >= Kc - tb and session.low <= Kc + tb
    out.update(target_strike=K, touched=touched)

    if not touched:
        d = 1 if Kc > mid else -1
        side = "BUY" if d > 0 else "SELL"
        entry = offer if d > 0 else bid
        behind = [a["strike"] for a in D.attractive_strikes(levels, p) if d * (a["strike"] + base - entry) < 0]
        if not behind:
            return {**out, "behavior": UNTOUCHED, "status": WAIT, "reason": "sin nivel estructural detrás para el stop"}
        level_behind = max(behind) if d > 0 else min(behind)
        stop = round(level_behind + base - d * buf, p.decimals)
        fr = D.scaled(p.front_run, S_u, sig)
        tp = round(Kc - d * fr, p.decimals)
        risk, reward = abs(entry - stop) + spread, abs(tp - entry) - spread
        rr = round(reward / risk, 2) if risk > 0 else 0.0
        pt = round(D.p_touch(S_u, K, D._iv_at(levels, K), minutes, p), 3)
        ok = pt >= p.p_min and rr >= p.rr_min
        return {**out, "behavior": UNTOUCHED, "status": CANDIDATE if ok else WAIT, "side": side, "plan": "TARGET",
                "entry": round(entry, p.decimals), "stop": stop, "tp": tp, "tp_strike": K, "p_touch": pt, "rr": rr,
                "score": _confidence(pt, rr) if ok else 0.0,
                "reason": (f"strike {K:g} sin tocar: candidato hacia él" if ok else
                           f"strike {K:g} sin tocar, pero P={pt:.2f} / R:R={rr:.2f} no pasan los filtros")}

    # Tocado: lado de llegada = el de la apertura.
    if abs(session.open - Kc) <= tb:
        return {**out, "behavior": AT_LEVEL, "status": WAIT, "reason": "abrió en el nivel: sin lado de llegada"}
    a = 1 if session.open < Kc else -1                  # +1: llegó desde abajo
    pierced = (session.high - Kc if a > 0 else Kc - session.low) >= brk     # lo cruzó con fuerza en algún momento
    through = a * (mid - Kc) >= brk                      # y sigue del otro lado
    rejected = a * (Kc - mid) >= cmin                    # o está de vuelta del lado de llegada
    gamma_pos = key["net_gex"] > 0

    if gamma_pos and pierced:
        return {**out, "behavior": BROKEN_WALL, "status": EXPIRED,
                "reason": f"muro {K:g} (gamma +) roto: contradice la teoría"}
    if not gamma_pos and rejected:
        return {**out, "behavior": FAILED_ACCELERATOR, "status": EXPIRED,
                "reason": f"acelerador {K:g} (gamma −) rechazado: contradice la teoría"}
    if not (through or rejected):
        return {**out, "behavior": AT_LEVEL, "status": WAIT, "reason": f"precio todavía en el nivel {K:g}"}

    behavior = WALL if gamma_pos else ACCELERATOR
    d = -a if behavior == WALL else a                    # WALL: reversión; ACCELERATOR: continuación
    side = "BUY" if d > 0 else "SELL"
    entry = offer if d > 0 else bid
    stop = round(Kc + a * buf if behavior == WALL else Kc - a * buf, p.decimals)
    choice = D.choose_tp(levels, side, entry, stop, minutes, base, spread, p)
    if choice["tp"] is None:
        return {**out, "behavior": behavior, "status": EXPIRED, "side": side,
                "reason": f"{behavior} en {K:g}, pero el siguiente movimiento no pasa los filtros: {choice['reason']}"}
    return {**out, "behavior": behavior, "status": CANDIDATE, "side": side, "plan": behavior,
            "entry": round(entry, p.decimals), "stop": stop, "tp": choice["tp"], "tp_strike": choice["strike"],
            "p_touch": choice["p_touch"], "rr": choice["rr"], "score": _confidence(choice["p_touch"], choice["rr"]),
            "reason": f"{behavior} en {K:g} → {'reversión' if behavior == WALL else 'continuación'} hacia "
                      f"{choice['strike']:g} ({choice['reason']})"}


def select(evaluations: Iterable[dict]) -> Optional[dict]:
    """El candidato de mayor confianza (P(toque) × min(R:R, 3)); empate → mayor P(toque)."""
    cands = [e for e in evaluations if e.get("status") == CANDIDATE]
    if not cands:
        return None
    return max(cands, key=lambda e: (e["score"], e["p_touch"]))
