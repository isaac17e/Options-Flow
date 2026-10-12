# Graph Report - Options-Flow  (2026-10-12)

## Corpus Check
- 39 files · ~39,035 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 7 file(s) not represented in the graph (top: (none) 4, .template 1, .ini 1)

## Summary
- 636 nodes · 1448 edges · 19 communities
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 53 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `fe88afdf`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- numpy
- PolygonClient
- decision.py
- config.py
- polygon_client.py
- test_spx_legacy.py
- app.py
- test_scanner.py
- test_spx_data.py
- test_legacy_0dte.py
- _precio_via_capital
- Options_Trade_polygon.py
- main
- CapitalSession
- _polygon_get
- obtener_cadena_0dte
- crear_dashboard_0dte
- tickers.py
- obtener_precio

## God Nodes (most connected - your core abstractions)
1. `PolygonClient` - 51 edges
2. `CapitalClient` - 33 edges
3. `calibrate_svi()` - 25 edges
4. `svi_total_variance()` - 24 edges
5. `UnderlyingSnapshot` - 18 edges
6. `build_svi_inputs()` - 17 edges
7. `main()` - 16 edges
8. `CapitalClientError` - 16 edges
9. `OptionContract` - 16 edges
10. `OptionChainSnapshot` - 16 edges

## Surprising Connections (you probably didn't know these)
- `BasisTracker (CFD minus index basis)` --semantically_similar_to--> `SPX handling via US500 CFD plus parity basis`  [INFERRED] [semantically similar]
  legacy_0dte/README.md → README.md
- `Capital.com spot with put-call parity fallback` --semantically_similar_to--> `Capital.com real-time spot source`  [INFERRED] [semantically similar]
  legacy_0dte/README.md → README.md
- `Gamma flip (closest crossing to spot)` --semantically_similar_to--> `Gamma flip level`  [INFERRED] [semantically similar]
  legacy_0dte/README.md → README.md
- `GEX per strike and Net GEX (Black-Scholes)` --semantically_similar_to--> `GEX / VEX / CEX dealer exposure`  [INFERRED] [semantically similar]
  legacy_0dte/README.md → README.md
- `get_client()` --uses--> `PolygonClient`  [INFERRED]
  app.py → src/data/polygon_client.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Directional consensus inputs: Max Pain, gamma flip, walls, smart money** — legacy_0dte_readme_max_pain, legacy_0dte_readme_gamma_flip, legacy_0dte_readme_call_put_walls, legacy_0dte_readme_smart_money, legacy_0dte_readme_directional_consensus [EXTRACTED 1.00]
- **Levels to decision rules to scanner flow** — legacy_0dte_readme_levels, legacy_0dte_readme_decision_rules, legacy_0dte_readme_multi_asset_scanner [EXTRACTED 1.00]
- **Risk-neutral density pipeline: SVI smile to Black-76 to Breeden-Litzenberger** — readme_svi_calibration, readme_black_76, readme_breeden_litzenberger, readme_risk_neutral_probability_map [EXTRACTED 1.00]

## Communities (19 total, 0 thin omitted)

### Community 0 - "numpy"
Cohesion: 0.06
Nodes (37): black76_call_price(), extract_risk_neutral_pdf(), RiskNeutralPDF, _second_derivative_nonuniform(), calibrate_svi(), arbitrage_constraint(), objective(), check_butterfly_arbitrage() (+29 more)

### Community 1 - "PolygonClient"
Cohesion: 0.06
Nodes (36): Settings, CapitalClient, CapitalClientError, UnderlyingSnapshot, PolygonClient, PolygonClientError, main(), _login_ok() (+28 more)

### Community 2 - "decision.py"
Cohesion: 0.08
Nodes (38): attractive_strikes(), choose_tp(), info(), decide(), hold_minutes(), _iv_at(), manage(), _next_attractive() (+30 more)

### Community 3 - "config.py"
Cohesion: 0.06
Nodes (38): _env(), _env_float(), load_settings(), Legacy 0DTE Dashboard README, 0DTE HTML dashboard (Options_Trade_polygon.py), BasisTracker (CFD minus index basis), Call/Put gamma walls, Capital.com spot with put-call parity fallback (+30 more)

### Community 4 - "polygon_client.py"
Cohesion: 0.10
Nodes (5): OptionContract, compute_basis(), _option_root(), parity_forward(), _price()

### Community 5 - "test_spx_legacy.py"
Cohesion: 0.09
Nodes (28): _cadena_paridad(), capital(), _consenso(), FakeResponse, _filas_smart(), _gex(), _market(), _prices() (+20 more)

### Community 6 - "app.py"
Cohesion: 0.09
Nodes (16): get_client(), load_chain_for_expiration(), load_dealer_chain(), load_expirations(), load_parity_underlying(), load_underlying(), OptionChainSnapshot, aggregate_dealer_exposure() (+8 more)

### Community 7 - "test_scanner.py"
Cohesion: 0.09
Nodes (25): Entry/TP/stop decision rules (decision.py), compute_levels (levels.py), Multi-asset scanner (scanner.py), Scanner states: CANDIDATE, WAIT, WALL, ACCELERATOR, EXPIRED, _confidence(), evaluate(), key_strike(), select() (+17 more)

### Community 8 - "test_spx_data.py"
Cohesion: 0.14
Nodes (26): polygon_tickers(), _capital(), _contract(), _login_ok(), _market(), _parity_chain(), _prices_payload(), _ref_page() (+18 more)

### Community 9 - "test_legacy_0dte.py"
Cohesion: 0.07
Nodes (25): capital(), _contrato(), FakeCapitalHttp, FakeResponse, sleeps(), test_capital_legacy_reintenta_fallos_de_red(), delete(), get() (+17 more)

### Community 10 - "_precio_via_capital"
Cohesion: 0.29
Nodes (4): _basis_tracker(), _get_resolver(), _mid_capital_en(), _precio_via_capital()

### Community 11 - "Options_Trade_polygon.py"
Cohesion: 0.13
Nodes (6): _bs_price(), bsm_greeks(), calcular_greeks_cadena(), norm_cdf(), norm_pdf(), _parse_args()

### Community 12 - "main"
Cohesion: 0.13
Nodes (11): compute_levels(), calcular_max_pain(), calcular_zero_gamma_level(), consenso_direccional_0dte(), detectar_smart_money(), identificar_muros_gex(), identificar_picos_gamma_bruta(), main() (+3 more)

### Community 13 - "CapitalSession"
Cohesion: 0.19
Nodes (4): CapitalSession, servir_dashboard(), end_headers(), __init__()

### Community 14 - "_polygon_get"
Cohesion: 0.18
Nodes (6): _backoff_seconds(), descargar_cadena_0dte(), _parse_retry_after(), _polygon_get(), _polygon_get_all_pages(), seleccionar_vencimiento_0dte()

### Community 15 - "obtener_cadena_0dte"
Cohesion: 0.22
Nodes (4): clasificar_flujo(), _implied_vol_bisection(), inferir_iv_faltante(), obtener_cadena_0dte()

### Community 16 - "crear_dashboard_0dte"
Cohesion: 0.25
Nodes (3): _build_dashboard_fig(), crear_dashboard_0dte(), _tabla_gex_html()

### Community 17 - "tickers.py"
Cohesion: 0.07
Nodes (22): BasisTracker, _last_price(), _mid(), normalize(), parity_spot(), polygon_tickers(), PolygonTickers, Resolution (+14 more)

### Community 18 - "obtener_precio"
Cohesion: 0.33
Nodes (3): obtener_precio(), _precio_via_paridad_put_call(), _snapshot_a_dataframe()

## Knowledge Gaps
- **7 isolated node(s):** `Flow classification by Volume/OI`, `Dealers long calls / short puts convention`, `Five-block build architecture`, `pytest dependency`, `matplotlib dependency (diagnostic scripts)` (+2 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 191 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PolygonClient` connect `PolygonClient` to `numpy`, `test_spx_data.py`, `polygon_client.py`, `app.py`?**
  _High betweenness centrality (0.080) - this node is a cross-community bridge._
- **Are the 6 inferred relationships involving `PolygonClient` (e.g. with `get_client()` and `Settings`) actually correct?**
  _`PolygonClient` has 6 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Flow classification by Volume/OI`, `Dealers long calls / short puts convention`, `Five-block build architecture` to the rest of the system?**
  _7 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `numpy` be split into smaller, more focused modules?**
  _Cohesion score 0.06470588235294118 - nodes in this community are weakly interconnected._
- **Why does `0DTE HTML dashboard (Options_Trade_polygon.py)` connect `config.py` to `Options_Trade_polygon.py`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `CapitalClient` (e.g. with `Settings` and `OptionContract`) actually correct?**
  _`CapitalClient` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Should `PolygonClient` be split into smaller, more focused modules?**
  _Cohesion score 0.061052631578947365 - nodes in this community are weakly interconnected._