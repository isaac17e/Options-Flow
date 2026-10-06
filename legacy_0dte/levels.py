"""
levels.py — niveles del script legacy en un dict listo para decision.py, para cualquier ticker.

    compute_levels(contratos, vencimiento, dias, S) → {
        spot, expiration, flip, call_wall, put_wall, max_pain, gross_peaks, dealer_status,
        net_gex, atm_iv, excluded, n_contracts, n_with_iv, oi_total, profile=[{strike, net_gex,
        gross_gex, oi_total, iv}]}

Usa exactamente las funciones del script (IV por contrato, GEX BSM, ventana ±3 %…), así que los
niveles son los mismos que muestra el panel. S es el spot del subyacente (para índices, CFD − base).
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import Options_Trade_polygon as ot  # noqa: E402


def compute_levels(contratos, vencimiento, dias, S, r=0.045):
    calls, puts, n_excl = ot.obtener_cadena_0dte(contratos, vencimiento, S, dias, r)
    if calls is None:
        raise ValueError(f"cadena sin datos utilizables para {vencimiento}")
    merged, _modo, ventana = ot._preparar_merged_0dte(calls, puts, S)
    if merged is None:
        raise ValueError("sin strikes suficientes alrededor del spot")
    g = (merged[["strike", "net_gex_local"]].rename(columns={"net_gex_local": "net_gex"})
         .sort_values("strike").reset_index(drop=True))
    flip = ot.calcular_zero_gamma_level(g, S=S)
    cw, pw = ot.identificar_muros_gex(g)
    status, net, _ = ot.posicionamiento_dealers_real(calls, puts)

    iv = {}
    for df in (calls, puts):
        for k, v in zip(df["strike"], df["impliedVolatility"]):
            if v > 0:
                iv.setdefault(float(k), []).append(float(v))
    near = sorted(iv, key=lambda k: abs(k - S))[:4]
    atm_iv = float(np.median([np.mean(iv[k]) for k in near])) if near else 0.0

    profile = [dict(strike=float(r_.strike), net_gex=float(r_.net_gex_local), gross_gex=float(r_.gross_gex),
                    oi_total=float(r_.oi_total), iv=float(np.mean(iv.get(float(r_.strike), [0.0]))))
               for r_ in merged.itertuples()]
    n_iv = int((calls["impliedVolatility"] > 0).sum() + (puts["impliedVolatility"] > 0).sum())
    return dict(spot=float(S), expiration=vencimiento, dias=dias, flip=flip, call_wall=cw, put_wall=pw,
                max_pain=float(ot.calcular_max_pain(calls, puts, S=S)),
                gross_peaks=[float(s) for s, _ in ot.identificar_picos_gamma_bruta(merged, top_n=3)],
                dealer_status=status, net_gex=float(net), atm_iv=atm_iv, excluded=int(n_excl),
                n_contracts=int(len(calls) + len(puts)), n_with_iv=n_iv,
                oi_total=float(calls["openInterest"].sum() + puts["openInterest"].sum()),
                window=ventana, profile=profile)
