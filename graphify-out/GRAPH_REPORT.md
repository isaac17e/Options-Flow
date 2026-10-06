# Graph Report - Options-Flow  (2026-10-06)

## Corpus Check
- Corpus is ~38,399 words - fits in a single context window. You may not need a graph.

## Summary
- 621 nodes · 1412 edges · 20 communities (18 shown, 2 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 49 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- SVI Smile & Risk-Neutral Density
- Capital.com & Data Clients
- Levels & Price Scaling
- README: Architecture & Concepts
- Trading State & Price Checks
- HTTP Session Wrappers
- Option Chain & Black-76
- Scanner & Level Rules
- Parity Chain & Reference Data
- HTTP Session Wrappers (legacy)
- Option Contracts & SVI Inputs
- Options Trade Polygon (legacy 0DTE)
- Max Pain, Zero Gamma & Smart Money
- Capital Session & Login
- Community 14
- Community 15
- Community 16
- Community 17

## God Nodes (most connected - your core abstractions)
1. `PolygonClient` - 51 edges
2. `CapitalClient` - 30 edges
3. `calibrate_svi()` - 25 edges
4. `svi_total_variance()` - 24 edges
5. `UnderlyingSnapshot` - 18 edges
6. `build_svi_inputs()` - 17 edges
7. `main()` - 16 edges
8. `OptionContract` - 16 edges
9. `OptionChainSnapshot` - 16 edges
10. `RoutedCapitalHttp` - 15 edges

## Surprising Connections (you probably didn't know these)
- `Capital.com spot with put-call parity fallback` --semantically_similar_to--> `Capital.com real-time spot source`  [INFERRED] [semantically similar]
  legacy_0dte/README.md → README.md
- `GEX per strike and Net GEX (Black-Scholes)` --semantically_similar_to--> `GEX / VEX / CEX dealer exposure`  [INFERRED] [semantically similar]
  legacy_0dte/README.md → README.md
- `Gamma flip (closest crossing to spot)` --semantically_similar_to--> `Gamma flip level`  [INFERRED] [semantically similar]
  legacy_0dte/README.md → README.md
- `BasisTracker (CFD minus index basis)` --semantically_similar_to--> `SPX handling via US500 CFD plus parity basis`  [INFERRED] [semantically similar]
  legacy_0dte/README.md → README.md
- `get_client()` --uses--> `PolygonClient`  [INFERRED]
  app.py → src/data/polygon_client.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Risk-neutral density pipeline: SVI smile to Black-76 to Breeden-Litzenberger** — readme_svi_calibration, readme_black_76, readme_breeden_litzenberger, readme_risk_neutral_probability_map [EXTRACTED 1.00]
- **Directional consensus inputs: Max Pain, gamma flip, walls, smart money** — legacy_0dte_readme_max_pain, legacy_0dte_readme_gamma_flip, legacy_0dte_readme_call_put_walls, legacy_0dte_readme_smart_money, legacy_0dte_readme_directional_consensus [EXTRACTED 1.00]
- **Levels to decision rules to scanner flow** — legacy_0dte_readme_levels, legacy_0dte_readme_decision_rules, legacy_0dte_readme_multi_asset_scanner [EXTRACTED 1.00]

## Communities (20 total, 2 thin omitted)

### Community 0 - "SVI Smile & Risk-Neutral Density"
Cohesion: 0.07
Nodes (36): black76_call_price(), extract_risk_neutral_pdf(), RiskNeutralPDF, _second_derivative_nonuniform(), calibrate_svi(), arbitrage_constraint(), objective(), check_butterfly_arbitrage() (+28 more)

### Community 1 - "Capital.com & Data Clients"
Cohesion: 0.06
Nodes (34): Settings, CapitalClient, CapitalClientError, UnderlyingSnapshot, PolygonClient, PolygonClientError, main(), _login_ok() (+26 more)

### Community 2 - "Levels & Price Scaling"
Cohesion: 0.08
Nodes (38): attractive_strikes(), choose_tp(), info(), decide(), hold_minutes(), _iv_at(), manage(), _next_attractive() (+30 more)

### Community 3 - "README: Architecture & Concepts"
Cohesion: 0.06
Nodes (38): _env(), _env_float(), load_settings(), Legacy 0DTE Dashboard README, 0DTE HTML dashboard (Options_Trade_polygon.py), BasisTracker (CFD minus index basis), Call/Put gamma walls, Capital.com spot with put-call parity fallback (+30 more)

### Community 4 - "Trading State & Price Checks"
Cohesion: 0.07
Nodes (22): BasisTracker, _last_price(), _mid(), normalize(), parity_spot(), polygon_tickers(), PolygonTickers, Resolution (+14 more)

### Community 5 - "HTTP Session Wrappers"
Cohesion: 0.09
Nodes (28): _cadena_paridad(), capital(), _consenso(), FakeResponse, _filas_smart(), _gex(), _market(), _prices() (+20 more)

### Community 6 - "Option Chain & Black-76"
Cohesion: 0.09
Nodes (16): get_client(), load_chain_for_expiration(), load_dealer_chain(), load_expirations(), load_parity_underlying(), load_underlying(), OptionChainSnapshot, aggregate_dealer_exposure() (+8 more)

### Community 7 - "Scanner & Level Rules"
Cohesion: 0.09
Nodes (25): Entry/TP/stop decision rules (decision.py), compute_levels (levels.py), Multi-asset scanner (scanner.py), Scanner states: CANDIDATE, WAIT, WALL, ACCELERATOR, EXPIRED, _confidence(), evaluate(), key_strike(), select() (+17 more)

### Community 8 - "Parity Chain & Reference Data"
Cohesion: 0.14
Nodes (26): polygon_tickers(), _capital(), _contract(), _login_ok(), _market(), _parity_chain(), _prices_payload(), _ref_page() (+18 more)

### Community 9 - "HTTP Session Wrappers (legacy)"
Cohesion: 0.09
Nodes (18): capital(), _contrato(), FakeCapitalHttp, FakeResponse, sleeps(), test_dashboard_muestra_fuente_spot_ventana_y_aviso(), test_inversion_fallida_cuenta_como_excluida_y_sin_oi_no_cuenta(), test_iv_inferida_por_contrato_aunque_la_cadena_tenga_iv() (+10 more)

### Community 10 - "Option Contracts & SVI Inputs"
Cohesion: 0.11
Nodes (6): OptionContract, compute_basis(), _option_root(), parity_forward(), _price(), SVIInputData

### Community 11 - "Options Trade Polygon (legacy 0DTE)"
Cohesion: 0.12
Nodes (8): _bs_price(), bsm_greeks(), calcular_greeks_cadena(), _implied_vol_bisection(), inferir_iv_faltante(), norm_cdf(), norm_pdf(), _parse_args()

### Community 12 - "Max Pain, Zero Gamma & Smart Money"
Cohesion: 0.13
Nodes (11): compute_levels(), calcular_max_pain(), calcular_zero_gamma_level(), consenso_direccional_0dte(), detectar_smart_money(), identificar_muros_gex(), identificar_picos_gamma_bruta(), main() (+3 more)

### Community 13 - "Capital Session & Login"
Cohesion: 0.20
Nodes (4): CapitalSession, servir_dashboard(), end_headers(), __init__()

### Community 14 - "Community 14"
Cohesion: 0.18
Nodes (6): _backoff_seconds(), descargar_cadena_0dte(), _parse_retry_after(), _polygon_get(), _polygon_get_all_pages(), seleccionar_vencimiento_0dte()

### Community 15 - "Community 15"
Cohesion: 0.18
Nodes (5): clasificar_flujo(), obtener_cadena_0dte(), obtener_precio(), _precio_via_paridad_put_call(), _snapshot_a_dataframe()

### Community 16 - "Community 16"
Cohesion: 0.25
Nodes (3): _build_dashboard_fig(), crear_dashboard_0dte(), _tabla_gex_html()

### Community 17 - "Community 17"
Cohesion: 0.29
Nodes (4): _basis_tracker(), _get_resolver(), _mid_capital_en(), _precio_via_capital()

## Knowledge Gaps
- **7 isolated node(s):** `Options Flow README`, `Dealers long calls / short puts convention`, `Five-block build architecture`, `Flow classification by Volume/OI`, `Scanner states: CANDIDATE, WAIT, WALL, ACCELERATOR, EXPIRED` (+2 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 187 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PolygonClient` connect `Capital.com & Data Clients` to `SVI Smile & Risk-Neutral Density`, `Parity Chain & Reference Data`, `Option Contracts & SVI Inputs`, `Option Chain & Black-76`?**
  _High betweenness centrality (0.081) - this node is a cross-community bridge._
- **Are the 6 inferred relationships involving `PolygonClient` (e.g. with `get_client()` and `Settings`) actually correct?**
  _`PolygonClient` has 6 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Options Flow README`, `Dealers long calls / short puts convention`, `Five-block build architecture` to the rest of the system?**
  _7 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `SVI Smile & Risk-Neutral Density` be split into smaller, more focused modules?**
  _Cohesion score 0.06654622101776574 - nodes in this community are weakly interconnected._
- **Why does `0DTE HTML dashboard (Options_Trade_polygon.py)` connect `README: Architecture & Concepts` to `Options Trade Polygon (legacy 0DTE)`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `CapitalClient` (e.g. with `Settings` and `OptionContract`) actually correct?**
  _`CapitalClient` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Should `Capital.com & Data Clients` be split into smaller, more focused modules?**
  _Cohesion score 0.05647517039922103 - nodes in this community are weakly interconnected._