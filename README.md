# Quant Microstructure — Guía de referencia

## Activar el entorno (cada vez que retomes el proyecto)

```bash
cd /Users/isaac17/Downloads/quant_microstructure
source venv/bin/activate
```

Confirma que el prompt muestre `(venv)` al inicio.

## Configurar la API key (solo la primera vez, o si la rotas)

```bash
cat > .env << 'EOF'
POLYGON_API_KEY=TU_KEY_REAL_AQUI
EOF
```

## Instalar/actualizar dependencias

```bash
pip install -r requirements.txt
```

## Scripts de prueba, en orden de construcción

```bash
# Bloque 1 — Conexión a Polygon
python3 src/scripts/test_connection.py

# Bloque 2 — Calibración SVI (smile de volatilidad)
python3 src/scripts/test_svi.py

# Bloque 3 — PDF neutral al riesgo (Breeden-Litzenberger)
python3 src/scripts/test_pdf.py

# Bloque 4 — GEX / VEX / CEX (dealers)
python3 src/scripts/test_greeks_black76.py   # valida el motor de griegas
python3 src/scripts/test_dealer_exposure.py  # perfil completo + gráfica

# Bloque 5 — Dashboard interactivo
python3 -m streamlit run app.py
```

## Scripts de diagnóstico (usados durante la depuración del Bloque 3)

```bash
python3 src/scripts/diagnose_arbitrage.py       # visualiza g(k) y detecta huecos de arbitraje
python3 src/scripts/explore_svi_landscape.py    # explora múltiples puntos de partida SVI
python3 src/scripts/inspect_raw_data.py         # imprime la cadena cruda (strike, IV, OI)
```

## Estructura del proyecto

```
quant_microstructure/
├── .env                          # tu API key (NO se sube a git)
├── .env.template                 # plantilla de ejemplo
├── .gitignore
├── requirements.txt
├── config.py                     # configuración central (API key, tasa libre de riesgo)
├── app.py                        # Dashboard Streamlit (Bloque 5)
└── src/
    ├── data/
    │   ├── models.py              # OptionContract, OptionChainSnapshot, UnderlyingSnapshot
    │   └── polygon_client.py      # Cliente REST hacia Polygon.io
    ├── models/
    │   ├── svi.py                 # Calibración SVI + no-arbitraje (Bloque 2)
    │   ├── svi_inputs.py          # Filtrado de liquidez y transformación a (k, w)
    │   ├── black76.py             # Pricing Black-76 (Bloque 3)
    │   ├── pdf_extraction.py      # Breeden-Litzenberger (Bloque 3)
    │   ├── greeks_black76.py      # Delta/Gamma/Vanna/Charm (Bloque 4)
    │   └── dealer_exposure.py     # Agregación GEX/VEX/CEX (Bloque 4)
    └── scripts/
        ├── test_connection.py
        ├── test_svi.py
        ├── test_pdf.py
        ├── test_greeks_black76.py
        ├── test_dealer_exposure.py
        └── (scripts de diagnóstico)
```

## Notas importantes

- Tu plan de Polygon trae datos con **~15 minutos de retraso**. El dashboard
  cachea las llamadas por 60 segundos — no tiene sentido refrescar más rápido.
- `python-dotenv`, `streamlit`, `plotly` deben estar instalados en el
  **mismo venv** desde el que corres los scripts (usa `python3 -m streamlit`
  en vez de solo `streamlit` si tienes dudas de qué entorno se está usando).
- Si rotas tu API key en Polygon.io, solo necesitas actualizar `.env`
  (paso de arriba) — no hay que tocar ningún otro archivo.
