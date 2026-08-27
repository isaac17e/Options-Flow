"""
src/models/dealer_exposure.py
--------------------------------
Agrega, nivel por nivel de strike, la exposición neta de los dealers en
Gamma (GEX), Vanna (VEX) y Charm (CEX).

Convención estándar de la industria (SqueezeMetrics / SpotGamma-style):
  - Calls: se asume que los dealers quedan LARGOS esa griega
           (el público suele vender calls cubiertas -> dealers compran).
  - Puts:  se asume que los dealers quedan CORTOS esa griega
           (el público compra puts de protección -> dealers las venden).

GEX usa el Gamma que Polygon ya calcula por contrato (confiable). Vanna
y Charm no vienen en el plan de Polygon, así que se calculan con
nuestro propio modelo Black-76 (src/models/greeks_black76.py), usando
la IV que cada contrato ya trae.

Fórmulas de exposición en dólares (por contrato, luego sumadas por strike):
  GEX = signo × Gamma_polygon × OI × 100 × Spot² × 0.01
  VEX = signo × Vanna × OI × 100 × Spot × 0.01   (exposición por 1 punto de IV)
  CEX = signo × Charm × OI × 100 × Spot          (exposición por día que pasa)

signo = +1 para calls, -1 para puts.
"""

from __future__ import annotations
import math

import numpy as np
import pandas as pd

from config import SETTINGS
from src.data.models import OptionChainSnapshot
from src.models.greeks_black76 import vanna, charm

CONTRACT_MULTIPLIER = 100  # 100 acciones por contrato, estándar en equities/ETFs


def aggregate_dealer_exposure(
    chain: OptionChainSnapshot,
    spot_price: float,
    risk_free_rate: float = SETTINGS.default_risk_free_rate,
    dividend_yield: float = SETTINGS.default_dividend_yield,
    min_open_interest: int = 1,
) -> pd.DataFrame:
    """
    Devuelve un DataFrame indexado por strike con columnas GEX, VEX, CEX,
    sumando la contribución de todos los contratos (todas las
    expiraciones presentes en `chain`) para cada nivel de strike.
    """
    df = chain.to_dataframe()

    if df.empty:
        raise ValueError("La cadena de opciones está vacía.")

    # --- Filtros básicos de calidad de datos ---
    df = df[df["open_interest"].fillna(0) >= min_open_interest]
    df = df[df["iv"].notna() & (df["iv"] > 0.001) & (df["iv"] < 5.0)]
    df = df[df["gamma"].notna()]
    df = df[df["tte_years"] > 0]

    if df.empty:
        raise ValueError("No quedaron contratos válidos tras filtrar.")

    # --- Forward por contrato, vía costo de acarreo (igual que Bloque 1) ---
    df = df.copy()
    df["forward"] = spot_price * np.exp((risk_free_rate - dividend_yield) * df["tte_years"])

    # --- Signo por convención de dealer ---
    df["dealer_sign"] = np.where(df["type"] == "call", 1.0, -1.0)

    # --- Vanna y Charm propios (vectorizados sobre toda la cadena a la vez) ---
    vanna_values = vanna(
        df["forward"].to_numpy(), df["strike"].to_numpy(), df["tte_years"].to_numpy(),
        df["iv"].to_numpy(), risk_free_rate, df["type"].to_numpy(),
    )
    charm_values = charm(
        df["forward"].to_numpy(), df["strike"].to_numpy(), df["tte_years"].to_numpy(),
        df["iv"].to_numpy(), risk_free_rate, df["type"].to_numpy(),
    )

    oi = df["open_interest"].fillna(0).to_numpy()
    sign = df["dealer_sign"].to_numpy()
    gamma_polygon = df["gamma"].to_numpy()

    df["GEX"] = sign * gamma_polygon * oi * CONTRACT_MULTIPLIER * (spot_price ** 2) * 0.01
    df["VEX"] = sign * vanna_values * oi * CONTRACT_MULTIPLIER * spot_price * 0.01
    df["CEX"] = sign * charm_values * oi * CONTRACT_MULTIPLIER * spot_price

    profile = df.groupby("strike")[["GEX", "VEX", "CEX"]].sum().reset_index()
    profile = profile.sort_values("strike").reset_index(drop=True)

    return profile


def find_gamma_flip(profile: pd.DataFrame, spot_price: float) -> float:
    """
    Encuentra el "gamma flip point": el strike donde el PERFIL de GEX
    (no acumulado) cruza de negativo a positivo — el nivel que separa
    la zona de Gamma Negativo (acelerador, típicamente por debajo del
    spot en un smile con skew de puts) de la zona de Gamma Positivo
    (amortiguador).
    """
    sorted_profile = profile.sort_values("strike")
    gex = sorted_profile["GEX"].to_numpy()
    strikes = sorted_profile["strike"].to_numpy()

    sign_changes = np.where(np.diff(np.sign(gex)) != 0)[0]
    if len(sign_changes) == 0:
        return float(strikes[np.argmin(np.abs(strikes - spot_price))])

    # Si hay varios cruces, elegimos el más cercano al spot (el relevante para trading)
    candidates = []
    for idx in sign_changes:
        x0, x1 = strikes[idx], strikes[idx + 1]
        y0, y1 = gex[idx], gex[idx + 1]
        if y1 == y0:
            candidates.append(x0)
        else:
            candidates.append(x0 + (0 - y0) * (x1 - x0) / (y1 - y0))

    candidates = np.array(candidates)
    closest = candidates[np.argmin(np.abs(candidates - spot_price))]
    return float(closest)
