# Graph Report - Options-Flow  (2026-10-08)

## Corpus Check
- 43 files · ~38,929 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 6 file(s) not represented in the graph (top: (none) 3, .template 1, .ini 1)

## Summary
- 631 nodes · 1422 edges · 27 communities (24 shown, 3 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 49 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `042e7eae`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- calibrate_svi
- PolygonClient
- decision.py
- config.py
- CapitalClient
- test_spx_legacy.py
- app.py
- test_scanner.py
- test_spx_data.py
- test_legacy_0dte.py
- RiskNeutralPDF
- Options_Trade_polygon.py
- main
- servir_dashboard
- _polygon_get
- obtener_cadena_0dte
- crear_dashboard_0dte
- _precio_via_capital
- pdf_extraction.py
- numpy
- Working with the graphify knowledge graph
- build_svi_inputs
- polygon_client.py
- .get_option_chain_snapshot
- UnderlyingSnapshot
- Working with the graphify knowledge graph

## God Nodes (most connected - your core abstractions)
1. `PolygonClient` - 51 edges
2. `CapitalClient` - 30 edges
3. `calibrate_svi()` - 25 edges
4. `svi_total_variance()` - 24 edges
5. `UnderlyingSnapshot` - 18 edges
6. `build_svi_inputs()` - 17 edges
7. `OptionChainSnapshot` - 16 edges
8. `OptionContract` - 16 edges
9. `main()` - 16 edges
10. `RoutedCapitalHttp` - 15 edges

## Surprising Connections (you probably didn't know these)
- `GEX per strike and Net GEX (Black-Scholes)` --semantically_similar_to--> `GEX / VEX / CEX dealer exposure`  [INFERRED] [semantically similar]
  legacy_0dte/README.md → README.md
- `BasisTracker (CFD minus index basis)` --semantically_similar_to--> `SPX handling via US500 CFD plus parity basis`  [INFERRED] [semantically similar]
  legacy_0dte/README.md → README.md
- `Capital.com spot with put-call parity fallback` --semantically_similar_to--> `Capital.com real-time spot source`  [INFERRED] [semantically similar]
  legacy_0dte/README.md → README.md
- `Gamma flip (closest crossing to spot)` --semantically_similar_to--> `Gamma flip level`  [INFERRED] [semantically similar]
  legacy_0dte/README.md → README.md
- `PolygonClient` --uses--> `get_client()`  [INFERRED]
  src/data/polygon_client.py → app.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Directional consensus inputs: Max Pain, gamma flip, walls, smart money** — legacy_0dte_readme_max_pain, legacy_0dte_readme_gamma_flip, legacy_0dte_readme_call_put_walls, legacy_0dte_readme_smart_money, legacy_0dte_readme_directional_consensus [EXTRACTED 1.00]
- **Levels to decision rules to scanner flow** — legacy_0dte_readme_levels, legacy_0dte_readme_decision_rules, legacy_0dte_readme_multi_asset_scanner [EXTRACTED 1.00]
- **Risk-neutral density pipeline: SVI smile to Black-76 to Breeden-Litzenberger** — readme_svi_calibration, readme_black_76, readme_breeden_litzenberger, readme_risk_neutral_probability_map [EXTRACTED 1.00]

## Communities (27 total, 3 thin omitted)

### Community 0 - "calibrate_svi"
Cohesion: 0.15
Nodes (20): calibrate_svi(), arbitrage_constraint(), objective(), check_butterfly_arbitrage(), svi_first_derivative(), svi_second_derivative(), svi_total_variance(), SVICalibrationResult (+12 more)

### Community 1 - "PolygonClient"
Cohesion: 0.20
Nodes (23): PolygonClient, _login_ok(), _market(), no_sleep(), _ref_page(), _resp(), _snap_page(), test_429_honors_retry_after() (+15 more)

### Community 2 - "decision.py"
Cohesion: 0.08
Nodes (38): attractive_strikes(), choose_tp(), info(), decide(), hold_minutes(), _iv_at(), manage(), _next_attractive() (+30 more)

### Community 3 - "config.py"
Cohesion: 0.06
Nodes (35): _env(), _env_float(), load_settings(), Legacy 0DTE Dashboard README, 0DTE HTML dashboard (Options_Trade_polygon.py), BasisTracker (CFD minus index basis), Call/Put gamma walls, Capital.com spot with put-call parity fallback (+27 more)

### Community 4 - "CapitalClient"
Cohesion: 0.16
Nodes (5): Settings, CapitalClient, CapitalClientError, test_capital_missing_credentials_raises_clear_error(), test_spy_epic_respects_capital_epic_override()

### Community 5 - "test_spx_legacy.py"
Cohesion: 0.09
Nodes (28): _cadena_paridad(), capital(), _consenso(), FakeResponse, _filas_smart(), _gex(), _market(), _prices() (+20 more)

### Community 6 - "app.py"
Cohesion: 0.12
Nodes (10): get_client(), load_chain_for_expiration(), load_dealer_chain(), load_expirations(), load_parity_underlying(), load_underlying(), OptionChainSnapshot, aggregate_dealer_exposure() (+2 more)

### Community 7 - "test_scanner.py"
Cohesion: 0.09
Nodes (25): Entry/TP/stop decision rules (decision.py), compute_levels (levels.py), Multi-asset scanner (scanner.py), Scanner states: CANDIDATE, WAIT, WALL, ACCELERATOR, EXPIRED, _confidence(), evaluate(), key_strike(), select() (+17 more)

### Community 8 - "test_spx_data.py"
Cohesion: 0.05
Nodes (50): _last_price(), _mid(), normalize(), parity_spot(), polygon_tickers(), PolygonTickers, Resolution, TickerResolutionError (+42 more)

### Community 9 - "test_legacy_0dte.py"
Cohesion: 0.09
Nodes (18): capital(), _contrato(), FakeCapitalHttp, FakeResponse, sleeps(), test_dashboard_muestra_fuente_spot_ventana_y_aviso(), test_inversion_fallida_cuenta_como_excluida_y_sin_oi_no_cuenta(), test_iv_inferida_por_contrato_aunque_la_cadena_tenga_iv() (+10 more)

### Community 11 - "Options_Trade_polygon.py"
Cohesion: 0.11
Nodes (8): _bs_price(), bsm_greeks(), calcular_greeks_cadena(), _implied_vol_bisection(), inferir_iv_faltante(), norm_cdf(), norm_pdf(), _parse_args()

### Community 12 - "main"
Cohesion: 0.13
Nodes (11): compute_levels(), calcular_max_pain(), calcular_zero_gamma_level(), consenso_direccional_0dte(), detectar_smart_money(), identificar_muros_gex(), identificar_picos_gamma_bruta(), main() (+3 more)

### Community 13 - "servir_dashboard"
Cohesion: 0.20
Nodes (4): CapitalSession, servir_dashboard(), end_headers(), __init__()

### Community 14 - "_polygon_get"
Cohesion: 0.18
Nodes (6): _backoff_seconds(), descargar_cadena_0dte(), _parse_retry_after(), _polygon_get(), _polygon_get_all_pages(), seleccionar_vencimiento_0dte()

### Community 15 - "obtener_cadena_0dte"
Cohesion: 0.18
Nodes (5): clasificar_flujo(), obtener_cadena_0dte(), obtener_precio(), _precio_via_paridad_put_call(), _snapshot_a_dataframe()

### Community 16 - "crear_dashboard_0dte"
Cohesion: 0.25
Nodes (3): _build_dashboard_fig(), crear_dashboard_0dte(), _tabla_gex_html()

### Community 17 - "_precio_via_capital"
Cohesion: 0.15
Nodes (5): _basis_tracker(), _get_resolver(), _mid_capital_en(), _precio_via_capital(), BasisTracker

### Community 18 - "pdf_extraction.py"
Cohesion: 0.14
Nodes (10): black76_call_price(), extract_risk_neutral_pdf(), _second_derivative_nonuniform(), main(), _pdf(), test_density_is_nonnegative(), test_mean_close_to_forward(), test_narrower_k_range_raises_no_error_but_shrinks_domain() (+2 more)

### Community 19 - "numpy"
Cohesion: 0.22
Nodes (9): Black-76 pricing and Greeks, Dealers long calls / short puts convention, GEX / VEX / CEX dealer exposure, charm(), _d1_d2(), delta_call(), delta_put(), gamma() (+1 more)

### Community 20 - "Working with the graphify knowledge graph"
Cohesion: 0.33
Nodes (5): Freshness check, Git hygiene, Navigating, Verify before asserting, Working with the graphify knowledge graph

### Community 21 - "build_svi_inputs"
Cohesion: 0.20
Nodes (6): build_svi_inputs(), SVIInputData, g_function(), main(), main(), main()

### Community 23 - ".get_option_chain_snapshot"
Cohesion: 0.14
Nodes (3): polygon_tickers(), PolygonClientError, test_polygon_ticker_mapping()

### Community 24 - "UnderlyingSnapshot"
Cohesion: 0.25
Nodes (3): UnderlyingSnapshot, test_apply_parity_basis_ignores_non_basis_tickers(), test_polygon_client_delegates_basis_to_capital_client()

### Community 25 - "Working with the graphify knowledge graph"
Cohesion: 0.33
Nodes (5): Freshness check, Git hygiene, Navigating, Verify before asserting, Working with the graphify knowledge graph

## Knowledge Gaps
- **15 isolated node(s):** `Navigating`, `Verify before asserting`, `Freshness check`, `Git hygiene`, `Navigating` (+10 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 195 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PolygonClient` connect `PolygonClient` to `calibrate_svi`, `CapitalClient`, `app.py`, `test_spx_data.py`, `pdf_extraction.py`, `build_svi_inputs`, `polygon_client.py`, `.get_option_chain_snapshot`, `UnderlyingSnapshot`, `.estimate_forward_price`?**
  _High betweenness centrality (0.079) - this node is a cross-community bridge._
- **Are the 6 inferred relationships involving `PolygonClient` (e.g. with `get_client()` and `Settings`) actually correct?**
  _`PolygonClient` has 6 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Navigating`, `Verify before asserting`, `Freshness check` to the rest of the system?**
  _15 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `decision.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08484848484848485 - nodes in this community are weakly interconnected._
- **Why does `0DTE HTML dashboard (Options_Trade_polygon.py)` connect `config.py` to `Options_Trade_polygon.py`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `CapitalClient` (e.g. with `Settings` and `OptionContract`) actually correct?**
  _`CapitalClient` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Should `config.py` be split into smaller, more focused modules?**
  _Cohesion score 0.055272108843537414 - nodes in this community are weakly interconnected._