"""
decision.py — reglas de entrada, TP/stop y gestión intradía sobre la salida de legacy_0dte,
para cualquier subyacente (índice → CFD de índice, ETF/acción → CFD de acción).

No coloca órdenes. Entradas:
  levels : niveles del script en unidades del SUBYACENTE (ver levels.py: spot_spx/spot, flip,
           call_wall, put_wall, max_pain, gross_peaks, net_gex, atm_iv, profile[strike, net_gex,
           gross_gex, oi_total, iv]).
  live   : precio del CFD en vivo y velas (unidades del CFD) + base = CFD − subyacente.
  state  : trades del día, P&L del día, posición abierta.
Salida de decide(): {action, side, plan, entry, stop, tp, size, reason, ...} en unidades del CFD.

Todos los umbrales son adimensionales: cada distancia es max(pct·S, k·σ_h), donde σ_h es la
desviación esperada (en puntos) durante la ventana de holding, con σ_día = S·IV·√(1/365)
repartida en una sesión de 390 min. Así la misma regla vale para un índice de 7.800 y para una
acción de 100 USD (ver test_decision.py::test_thresholds_scale_with_price).

Reglas (ver legacy_0dte/README.md):
  * Régimen LONG gamma si net GEX > 0 y spot > flip; si no, SHORT.
  * LONG → reversión: A = venta del rechazo del siguiente strike atractivo por encima;
    B = compra del rechazo del siguiente strike atractivo por debajo.
  * C = momentum: cierre de 15 min por debajo del flip (venta); C+ = por encima con régimen SHORT.
  * Stop estructural: nivel ± stop_buffer, del otro lado del nivel. Nunca se ensancha.
  * TP: siguiente strike atractivo en la dirección del trade que cumpla R:R ≥ rr_min; si su
    P(toque en la ventana) < p_min, el strike más lejano (y más cercano que él) con P ≥ p_min
    y R:R ≥ rr_min; si no hay, no se opera.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from typing import Optional, Tuple


@dataclass(frozen=True)
class Params:
    rr_min: float = 1.0                 # R:R mínimo neto de spread
    p_min: float = 0.30                 # P(toque) mínima del TP en la ventana de holding
    p_tie: float = 0.02                 # empate de P → mayor gamma bruta, luego más cercano
    hold_minutes: int = 75
    session_minutes: int = 390
    # distancias = max(pct·S, kσ·σ_h)
    touch_band: Tuple[float, float] = (0.0002, 0.15)    # "tocar" el nivel
    confirm_min: Tuple[float, float] = (0.0001, 0.10)   # cierre de vuelta ≥ esto del nivel
    confirm_max: Tuple[float, float] = (0.0008, 0.60)   # …y ≤ esto (no perseguir)
    break_margin: Tuple[float, float] = (0.0003, 0.30)  # ruptura del flip
    stop_buffer: Tuple[float, float] = (0.0010, 0.50)   # stop más allá del nivel
    min_tp_extra: Tuple[float, float] = (0.0002, 0.15)  # TP a ≥ spread + esto
    front_run: Tuple[float, float] = (0.0001, 0.05)     # TP un poco antes del strike
    default_spread_pct: float = 0.0001  # si live no trae bid/offer
    attractive_rel: float = 0.35        # pico local ≥ 35 % del máximo de la ventana
    attractive_radius: int = 2          # pico local sobre ±2 strikes vecinos
    window_pct: float = 0.03            # ventana de strikes: max(3 % del spot, 4 σ_día)
    window_sigmas: float = 4.0
    max_trades: int = 3
    daily_loss_pct: float = 0.02
    first_entry: time = time(8, 45)     # hora Colombia (COT)
    last_entry: time = time(10, 0)
    flat_time: time = time(14, 45)
    point_value: float = 1.0            # USD por punto por unidad del CFD
    decimals: int = 2


# ─────────────────────────── utilidades ───────────────────────────
def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def spot_of(levels: dict) -> float:
    return float(levels.get("spot", levels.get("spot_spx")))


def sigma_window(spot: float, iv: float, minutes: float, p: Params = Params()) -> float:
    """σ en puntos para una ventana intradía: σ_día = S·IV·√(1/365) repartida en la sesión."""
    return spot * iv * math.sqrt(1.0 / 365.0) * math.sqrt(max(minutes, 0.0) / p.session_minutes)


def scaled(spec: Tuple[float, float], spot: float, sigma_h: float) -> float:
    pct, k = spec
    return max(pct * spot, k * sigma_h)


def p_touch(spot: float, strike: float, iv: float, minutes: float, p: Params = Params()) -> float:
    """Barrera sin drift: P(tocar K antes de t) ≈ 2·P(S_t más allá de K)."""
    if minutes <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    s = iv * math.sqrt(1.0 / 365.0) * math.sqrt(minutes / p.session_minutes)
    return min(1.0, 2.0 * (1.0 - _phi(abs(math.log(strike / spot)) / s)))


def regime(levels: dict) -> str:
    """LONG si el Net GEX es positivo y el spot está sobre el flip (o no hay flip en la ventana,
    es decir, todo el perfil cercano es de gamma positiva); si no, SHORT."""
    flip = levels.get("flip")
    if levels.get("net_gex", 0) > 0 and (flip is None or spot_of(levels) > flip):
        return "LONG"
    return "SHORT"


def _window(levels: dict, p: Params) -> float:
    S = spot_of(levels)
    return max(p.window_pct * S, p.window_sigmas * sigma_window(S, levels["atm_iv"], p.session_minutes, p))


def attractive_strikes(levels: dict, p: Params = Params()) -> list:
    """Picos locales (±attractive_radius strikes) de gamma bruta o |net GEX| con ≥ attractive_rel
    del máximo de la ventana, más muros de calls/puts y picos de gamma bruta del script."""
    S, w = spot_of(levels), _window(levels, p)
    prof = sorted((r for r in levels["profile"] if abs(r["strike"] - S) <= w), key=lambda r: r["strike"])
    if not prof:
        return []
    gmax = max(r["gross_gex"] for r in prof) or 1.0
    nmax = max(abs(r["net_gex"]) for r in prof) or 1.0
    out = {}
    for i, r in enumerate(prof):
        nb = prof[max(i - p.attractive_radius, 0): i + p.attractive_radius + 1]
        is_g = r["gross_gex"] >= p.attractive_rel * gmax and r["gross_gex"] == max(x["gross_gex"] for x in nb)
        is_n = abs(r["net_gex"]) >= p.attractive_rel * nmax and abs(r["net_gex"]) == max(abs(x["net_gex"]) for x in nb)
        if is_g or is_n:
            out[r["strike"]] = r
    for k in [levels.get("call_wall"), levels.get("put_wall"), *levels.get("gross_peaks", [])]:
        if k is not None:
            row = next((r for r in prof if r["strike"] == float(k)), None)
            if row:
                out[float(k)] = row
    return [out[k] for k in sorted(out)]


def _iv_at(levels: dict, strike: float) -> float:
    row = next((r for r in levels["profile"] if r["strike"] == strike), None)
    return row["iv"] if row and row.get("iv", 0) > 0 else levels["atm_iv"]


def hold_minutes(now: datetime, p: Params = Params()) -> float:
    flat = now.replace(hour=p.flat_time.hour, minute=p.flat_time.minute, second=0, microsecond=0)
    return max(0.0, min(p.hold_minutes, (flat - now).total_seconds() / 60.0))


def _spread(live: dict, ref: float, p: Params) -> float:
    if live.get("bid") is not None and live.get("offer") is not None:
        return max(float(live["offer"]) - float(live["bid"]), 0.0)
    return p.default_spread_pct * ref


# ─────────────────────────── TP ───────────────────────────
def choose_tp(levels: dict, side: str, entry: float, stop: float, minutes: float, base: float,
              spread: float, p: Params = Params()) -> dict:
    """entry/stop/tp en unidades del CFD; strikes en unidades del subyacente (CFD = strike + base)."""
    d = 1 if side == "BUY" else -1
    S_u = entry - base
    sig = sigma_window(S_u, levels["atm_iv"], minutes, p)
    risk = abs(entry - stop) + spread
    min_dist = spread + scaled(p.min_tp_extra, S_u, sig)
    fr = scaled(p.front_run, S_u, sig)
    att = {r["strike"] for r in attractive_strikes(levels, p)}
    w = _window(levels, p)
    rows = sorted((r for r in levels["profile"]
                   if d * (r["strike"] + base - d * fr - entry) >= min_dist and abs(r["strike"] - S_u) <= w),
                  key=lambda r: d * r["strike"])

    def info(r):
        tp = round(r["strike"] + base - d * fr, p.decimals)
        reward = abs(tp - entry) - spread
        return dict(strike=r["strike"], tp=tp, attractive=r["strike"] in att,
                    p_touch=round(p_touch(S_u, r["strike"], _iv_at(levels, r["strike"]), minutes, p), 3),
                    rr=round(reward / risk, 2) if risk > 0 else 0.0, gross=r["gross_gex"])

    cands = [info(r) for r in rows]
    att_ok = [c for c in cands if c["attractive"] and c["rr"] >= p.rr_min]
    if not att_ok:
        return dict(tp=None, candidates=cands, reason="ningún strike atractivo cumple R:R mínimo")
    k_att = att_ok[0]
    if k_att["p_touch"] >= p.p_min:
        chosen, why = k_att, "siguiente strike atractivo (R:R y P(toque) OK)"
    else:
        closer = [c for c in cands if d * c["strike"] < d * k_att["strike"]
                  and c["rr"] >= p.rr_min and c["p_touch"] >= p.p_min]
        if not closer:
            return dict(tp=None, candidates=cands,
                        reason=f"atractivo {k_att['strike']:g} con P(toque) {k_att['p_touch']:.2f} < {p.p_min} "
                               f"y ningún strike más cercano paga R:R ≥ {p.rr_min}")
        far = closer[-1]
        ties = [c for c in closer if abs(c["p_touch"] - far["p_touch"]) <= p.p_tie]
        chosen = max(ties, key=lambda c: (c["gross"], -abs(c["strike"] - S_u)))
        why = f"strike más probable que paga (atractivo {k_att['strike']:g} solo P={k_att['p_touch']:.2f})"
    return dict(tp=chosen["tp"], strike=chosen["strike"], p_touch=chosen["p_touch"], rr=chosen["rr"],
                candidates=cands, reason=why)


# ─────────────────────────── entradas ───────────────────────────
def _next_attractive(levels: dict, up: bool, p: Params) -> Optional[float]:
    S = spot_of(levels)
    ks = [r["strike"] for r in attractive_strikes(levels, p)]
    ks = [k for k in ks if (k > S if up else k < S)]
    return (min(ks) if up else max(ks)) if ks else None


def decide(levels: dict, live: dict, state: dict, p: Params = Params()) -> dict:
    """
    live : {"now": datetime COT, "bid", "offer", "base" (CFD − subyacente),
            "bar5": {"high","low","close"} (última vela de 5 min cerrada, CFD),
            "close15": último cierre de 15 min (CFD)}
    state: {"trades_today", "pnl_today", "balance", "position", "size"}
    """
    now, base = live["now"], float(live.get("base", 0.0))
    out = dict(action="NO_TRADE", side=None, entry=None, stop=None, tp=None, size=0.0)
    if state.get("position"):
        return {**out, "action": "HOLD", "reason": "ya hay posición abierta (usar manage())"}
    if not (p.first_entry <= now.time() <= p.last_entry):
        return {**out, "reason": f"fuera de la ventana de entradas {p.first_entry:%H:%M}–{p.last_entry:%H:%M} COT"}
    if state.get("trades_today", 0) >= p.max_trades:
        return {**out, "reason": "máximo de trades del día alcanzado"}
    balance = float(state.get("balance", 1000.0))
    if state.get("pnl_today", 0.0) <= -p.daily_loss_pct * balance:
        return {**out, "reason": "límite de pérdida diaria alcanzado"}

    S = spot_of(levels)
    minutes = hold_minutes(now, p)
    sig = sigma_window(S, levels["atm_iv"], minutes, p)
    buf = scaled(p.stop_buffer, S, sig)
    tb, cmin, cmax = (scaled(x, S, sig) for x in (p.touch_band, p.confirm_min, p.confirm_max))
    brk = scaled(p.break_margin, S, sig)
    reg = regime(levels)
    bar, c15 = live["bar5"], live.get("close15")
    flip_c = levels["flip"] + base if levels.get("flip") is not None else None
    signal = None

    if reg == "LONG":
        up, dn = _next_attractive(levels, True, p), _next_attractive(levels, False, p)
        if up is not None:
            L = up + base
            if bar["high"] >= L - tb and L - cmax <= bar["close"] <= L - cmin:
                signal = ("SELL", "A", L + buf, f"rechazo del strike atractivo {up:g} (régimen LONG)")
        if signal is None and dn is not None:
            L = dn + base
            if bar["low"] <= L + tb and L + cmin <= bar["close"] <= L + cmax:
                signal = ("BUY", "B", L - buf, f"rechazo del strike atractivo {dn:g} (régimen LONG)")
    if signal is None and flip_c is not None and c15 is not None:
        if c15 <= flip_c - brk:
            signal = ("SELL", "C", flip_c + buf, f"cierre 15m bajo el flip {levels['flip']:.2f} → momentum")
        elif reg == "SHORT" and c15 >= flip_c + brk:
            signal = ("BUY", "C+", flip_c - buf, f"cierre 15m sobre el flip {levels['flip']:.2f} → momentum")

    if signal is None:
        return {**out, "regime": reg, "reason": "sin señal (zona media / sin rechazo ni ruptura)"}

    side, plan, stop, why = signal
    stop = round(stop, p.decimals)
    entry = float(live["offer"] if side == "BUY" else live["bid"])
    spread = _spread(live, entry, p)
    tp = choose_tp(levels, side, entry, stop, minutes, base, spread, p)
    if tp["tp"] is None:
        return {**out, "regime": reg, "plan": plan, "reason": f"{why}; SKIP: {tp['reason']}",
                "candidates": tp["candidates"]}
    size = float(state.get("size", 0.0))
    risk_pts, reward_pts = abs(entry - stop) + spread, abs(tp["tp"] - entry) - spread
    return dict(action=side, side=side, plan=plan, regime=reg, entry=round(entry, p.decimals), stop=stop,
                tp=tp["tp"], tp_strike=tp["strike"], p_touch=tp["p_touch"], rr=tp["rr"], size=size,
                usd_at_tp=round(reward_pts * size * p.point_value, 2),
                usd_at_stop=round(-risk_pts * size * p.point_value, 2),
                max_exit_time=(now + timedelta(minutes=minutes)).strftime("%H:%M"),
                reason=f"{why}; TP: {tp['reason']}", candidates=tp["candidates"])


# ─────────────────────────── gestión ───────────────────────────
def manage(position: dict, levels: dict, live: dict, p: Params = Params()) -> dict:
    """
    position: {"side","plan","entry","stop","tp","opened": datetime}
    Cada ciclo: EXIT (TP / stop / 14:45 / tiempo máximo / cambio de régimen), MOVE_TP o HOLD.
    El stop nunca se ensancha (manage() nunca devuelve otro stop).
    """
    now, base = live["now"], float(live.get("base", 0.0))
    side, d = position["side"], (1 if position["side"] == "BUY" else -1)
    mark = float(live["bid"] if side == "BUY" else live["offer"])
    if d * (mark - position["tp"]) >= 0:
        return dict(action="EXIT", reason="TP alcanzado", price=mark)
    if d * (mark - position["stop"]) <= 0:
        return dict(action="EXIT", reason="stop alcanzado", price=mark)
    if now.time() >= p.flat_time:
        return dict(action="EXIT", reason=f"hora de cierre {p.flat_time:%H:%M} COT", price=mark)
    if now - position["opened"] >= timedelta(minutes=p.hold_minutes):
        return dict(action="EXIT", reason=f"tiempo máximo {p.hold_minutes} min", price=mark)
    reg = regime(levels)
    if position["plan"] in ("A", "B") and reg != "LONG":
        return dict(action="EXIT", reason="el régimen dejó de ser LONG gamma", price=mark)
    if position["plan"] == "C" and reg == "LONG":
        return dict(action="EXIT", reason="el régimen volvió a LONG gamma", price=mark)

    remaining = p.hold_minutes - (now - position["opened"]).total_seconds() / 60.0
    minutes = max(1.0, min(remaining, hold_minutes(now, p)))
    spread = _spread(live, mark, p)
    q = replace(p, rr_min=-1e9)          # desde aquí no se exige R:R, solo dirección y alcanzabilidad
    new = choose_tp(levels, side, mark, position["stop"], minutes, base, spread, q)
    hold = dict(action="HOLD", stop=position["stop"], tp=position["tp"], reason="sin cambios")
    S_u = mark - base
    tick = max(10 ** -p.decimals, 0.0001 * S_u)
    if new["tp"] is None or abs(new["tp"] - position["tp"]) < tick:
        return hold
    fr = scaled(p.front_run, S_u, sigma_window(S_u, levels["atm_iv"], minutes, p))
    cur_strike = position.get("tp_strike") or (position["tp"] - base + d * fr)
    att = {a["strike"] for a in attractive_strikes(levels, p)}
    p_cur = p_touch(S_u, cur_strike, _iv_at(levels, cur_strike), minutes, p)
    cur_att = any(abs(cur_strike - a) <= tick for a in att)
    if d * (new["tp"] - position["tp"]) > 0:      # alejar el TP
        if cur_att or p_cur >= p.p_min:
            return hold
        why = f"TP actual {cur_strike:g} ya no es atractivo ni probable (P={p_cur:.2f})"
    else:                                          # acercar el TP
        if new["strike"] in att:
            why = f"apareció un strike atractivo más cercano ({new['strike']:g})"
        elif p_cur < p.p_min:
            why = f"TP actual improbable en el tiempo restante (P={p_cur:.2f})"
        else:
            return hold
    return dict(action="MOVE_TP", tp=new["tp"], tp_strike=new["strike"], stop=position["stop"], reason=why)


# ─────────────────────────── tamaño ───────────────────────────
def position_size(balance: float, leverage: float, price: float, min_size: float, increment: float,
                  margin_pct: float = 0.05, margin_adjust: float = 1.0) -> float:
    """
    size = margin_pct·balance·leverage / price (margen = margin_pct del balance), ajustado por
    margin_adjust (= margen objetivo / margen efectivo medido en el primer fill), redondeado hacia
    abajo al incremento. Devuelve 0.0 si queda por debajo del mínimo.
    """
    if balance <= 0 or leverage <= 0 or price <= 0 or increment <= 0:
        return 0.0
    raw = margin_pct * balance * leverage / price * margin_adjust
    steps = math.floor(raw / increment + 1e-9)
    size = round(steps * increment, 10)
    return size if size >= min_size - 1e-12 else 0.0
