import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np
from src.models.greeks_black76 import delta_call, delta_put, gamma, vanna, charm

F, K, T, sigma, r = 100.0, 105.0, 0.25, 0.20, 0.03

print("=== Test 1: Vanna debe ser idéntica para call y put ===")
v_call = vanna(F, K, T, sigma, r, "call")
v_put = vanna(F, K, T, sigma, r, "put")
print(f"Vanna call: {v_call:.6f}  |  Vanna put: {v_put:.6f}")
assert np.isclose(v_call, v_put, atol=1e-6), "FALLO: Vanna call y put deberían coincidir"
print("✅ OK\n")

print("=== Test 2: Teorema de Schwarz (d(Vanna)/dF == d(Gamma)/dsigma) ===")
h = 1e-4
dVanna_dF = (vanna(F + h, K, T, sigma, r, "call") - vanna(F - h, K, T, sigma, r, "call")) / (2 * h)
dGamma_dsigma = (gamma(F, K, T, sigma + h, r) - gamma(F, K, T, sigma - h, r)) / (2 * h)
print(f"d(Vanna)/dF   = {dVanna_dF:.6f}")
print(f"d(Gamma)/dsig = {dGamma_dsigma:.6f}")
assert np.isclose(dVanna_dF, dGamma_dsigma, rtol=1e-2), "FALLO: no se cumple el Teorema de Schwarz"
print("✅ OK\n")

print("=== Test 3: Charm call - Charm put debe ser exactamente r*e^(-rT) ===")
c_call = charm(F, K, T, sigma, r, "call")
c_put = charm(F, K, T, sigma, r, "put")
expected_diff = r * np.exp(-r * T)
actual_diff = c_call - c_put
print(f"Charm call: {c_call:.6f}  |  Charm put: {c_put:.6f}")
print(f"Diferencia real: {actual_diff:.6f}  |  Esperada: {expected_diff:.6f}")
assert np.isclose(actual_diff, expected_diff, rtol=1e-3), "FALLO: relación Charm call/put incorrecta"
print("✅ OK\n")

print("🎉 TODOS LOS TESTS PASARON — greeks_black76.py está guardado correctamente")
