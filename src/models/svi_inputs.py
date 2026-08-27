"""
src/models/svi_inputs.py
--------------------------
Transforma una cadena de opciones cruda (OptionChainSnapshot) en los
arreglos limpios (k, w, pesos) que necesita el calibrador SVI.

Reglas aplicadas:
  1. Solo se usa UN vencimiento a la vez (SVI calibra "slice por slice").
  2. Solo opciones OTM: puts si K < F, calls si K > F (más líquidas y
     con IV más confiable que las ITM).
  3. Se descartan contratos con IV inválida (<=0 o absurdamente alta)
     y con open_interest por debajo de un mínimo (proxy de liquidez,
     ya que no tenemos bid/ask en este plan de datos).
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from src.data.models import OptionChainSnapshot


@dataclass
class SVIInputData:
    k: np.ndarray                 # log-strike forward
    w: np.ndarray                 # varianza total observada
    strikes: np.ndarray           # strikes originales (para graficar después)
    open_interest: np.ndarray     # usado como pesos de calibración
    expiration: date
    forward: float
    time_to_expiration_years: float


def build_svi_inputs(
    chain: OptionChainSnapshot,
    expiration: date,
    forward: float,
    min_open_interest: int = 5,
    max_iv: float = 3.0,
    min_iv: float = 0.01,
    k_min: float = None,
    k_max: float = None,
) -> SVIInputData:
    """
    k_min, k_max : opcional, acota el rango de log-moneyness (k = ln(K/F))
              usado para calibrar. Cuando el dominio de strikes disponible
              es muy ancho y asimétrico (ej. puts muy profundos pero pocos
              calls lejanos, o viceversa), forzar una curva SVI de 5
              parámetros a cubrir TODO ese rango puede generar tensión
              entre "buen ajuste" y "libre de arbitraje". Acotar a la zona
              de mayor liquidez real suele resolver esa tensión sin perder
              la parte del smile que de verdad importa para el trading.
    """
    sub_chain = chain.filter_by_expiration(expiration)
    df = sub_chain.to_dataframe()

    if df.empty:
        raise ValueError(f"No hay contratos para la expiración {expiration}")

    tte_years = sub_chain.contracts[0].time_to_expiration_years

    # --- Filtro 1: liquidez mínima (proxy vía open_interest) ---
    df = df[df["open_interest"].fillna(0) >= min_open_interest]

    # --- Filtro 2: IV válida y dentro de rango razonable ---
    df = df[df["iv"].notna() & (df["iv"] > min_iv) & (df["iv"] < max_iv)]

    # --- Filtro 3: solo OTM (evita duplicar información y usar ITM ruidoso) ---
    is_otm_put = (df["type"] == "put") & (df["strike"] < forward)
    is_otm_call = (df["type"] == "call") & (df["strike"] >= forward)
    df = df[is_otm_put | is_otm_call].copy()

    if len(df) < 6:
        raise ValueError(
            f"Solo quedaron {len(df)} contratos líquidos y OTM para {expiration} "
            f"tras filtrar — insuficiente para calibrar SVI (mínimo 6). "
            f"Prueba bajando min_open_interest o elige otro vencimiento."
        )

    df = df.sort_values("strike").drop_duplicates(subset="strike")

    k_all = np.log(df["strike"].to_numpy() / forward)

    if k_min is not None or k_max is not None:
        lo = k_min if k_min is not None else -np.inf
        hi = k_max if k_max is not None else np.inf
        mask = (k_all >= lo) & (k_all <= hi)
        df = df[mask]
        k_all = k_all[mask]

    if len(df) < 6:
        raise ValueError(
            f"Tras acotar el rango de moneyness solo quedaron {len(df)} puntos "
            f"(mínimo 6). Amplía k_min/k_max."
        )

    k = k_all
    w = (df["iv"].to_numpy() ** 2) * tte_years
    open_interest = df["open_interest"].fillna(0).to_numpy()

    return SVIInputData(
        k=k,
        w=w,
        strikes=df["strike"].to_numpy(),
        open_interest=open_interest,
        expiration=expiration,
        forward=forward,
        time_to_expiration_years=tte_years,
    )
